"""Evidence intake boundary — backends produce candidates; this accepts or rejects."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from joymesh.runtime_v1.completion.models import (
    CandidateEvidence,
    CompletionContext,
    EvidenceEnvelope,
)
from joymesh.runtime_v1.completion.states import (
    TERMINAL_COMPLETION_STATES,
    CompletionLifecycleState,
    EvidenceRejectionReason,
    EvidenceTrustClassification,
)
from joymesh.security import redact_secrets


class EvidenceBoundary:
    """Central evidence acceptance for all backends."""

    def __init__(self, *, max_bytes: int = 2_000_000) -> None:
        self.max_bytes = max_bytes
        self._by_execution: dict[str, list[EvidenceEnvelope]] = {}
        self._fingerprints: dict[str, set[str]] = {}

    def list_accepted(self, execution_id: str) -> tuple[EvidenceEnvelope, ...]:
        rows = self._by_execution.get(execution_id, [])
        return tuple(item for item in rows if item.trust is EvidenceTrustClassification.ACCEPTED)

    def list_all(self, execution_id: str) -> tuple[EvidenceEnvelope, ...]:
        return tuple(self._by_execution.get(execution_id, []))

    def intake(
        self,
        candidates: Sequence[CandidateEvidence] | Sequence[Mapping[str, Any]],
        *,
        context: CompletionContext,
        lifecycle_state: CompletionLifecycleState,
        source_backend: str,
        source_harness: str | None,
    ) -> tuple[tuple[EvidenceEnvelope, ...], tuple[EvidenceEnvelope, ...]]:
        """Return (accepted, rejected). Idempotent for identical content hashes."""

        accepted: list[EvidenceEnvelope] = []
        rejected: list[EvidenceEnvelope] = []
        for raw in candidates:
            candidate = (
                raw
                if isinstance(raw, CandidateEvidence)
                else CandidateEvidence(
                    evidence_type=str(raw.get("evidence_type") or "unknown"),
                    content_ref=raw.get("content_ref")
                    if isinstance(raw.get("content_ref"), str)
                    else None,
                    content_hash=raw.get("content_hash")
                    if isinstance(raw.get("content_hash"), str)
                    else None,
                    size_bytes=int(raw.get("size_bytes") or 0),
                    sequence=int(raw.get("sequence") or 0),
                    provenance=dict(raw.get("provenance") or {}),
                    payload=_redact_payload(dict(raw.get("payload") or {})),
                    evidence_id=str(
                        raw.get("evidence_id") or f"evidence_{raw.get('sequence') or 0}"
                    ),
                )
            )
            envelope, ok, stored = self._accept_one(
                candidate,
                context=context,
                lifecycle_state=lifecycle_state,
                source_backend=source_backend,
                source_harness=source_harness,
            )
            if stored:
                self._by_execution.setdefault(context.execution_id, []).append(envelope)
            if ok:
                accepted.append(envelope)
            else:
                rejected.append(envelope)
        return tuple(accepted), tuple(rejected)

    def _accept_one(
        self,
        candidate: CandidateEvidence,
        *,
        context: CompletionContext,
        lifecycle_state: CompletionLifecycleState,
        source_backend: str,
        source_harness: str | None,
    ) -> tuple[EvidenceEnvelope, bool, bool]:
        """Return (envelope, accepted, newly_stored)."""

        reason: EvidenceRejectionReason | None = None
        if context.cancelled or lifecycle_state is CompletionLifecycleState.CANCELLED:
            reason = EvidenceRejectionReason.CANCELLED_EXECUTION
        elif lifecycle_state in TERMINAL_COMPLETION_STATES:
            reason = EvidenceRejectionReason.LATE_AFTER_TERMINAL
        elif context.attempt_id != context.authoritative_attempt_id:
            reason = EvidenceRejectionReason.STALE_ATTEMPT
        elif candidate.size_bytes > context.max_evidence_bytes:
            reason = EvidenceRejectionReason.OVERSIZED
        elif not candidate.evidence_type or candidate.evidence_type == "unknown":
            reason = EvidenceRejectionReason.UNSUPPORTED_TYPE
        elif not candidate.provenance and not candidate.content_hash and not candidate.payload:
            reason = EvidenceRejectionReason.MISSING_PROVENANCE

        # Tenant/ownership checks via context metadata expectations.
        expected_org = context.organisation_id
        claimed_org = candidate.provenance.get("organisation_id")
        if expected_org and claimed_org and claimed_org != expected_org:
            reason = EvidenceRejectionReason.WRONG_TENANT
        expected_project = context.project_id
        claimed_project = candidate.provenance.get("project_id")
        if expected_project and claimed_project and claimed_project != expected_project:
            reason = EvidenceRejectionReason.WRONG_PROJECT
        claimed_mission = candidate.provenance.get("mission_id")
        if claimed_mission and claimed_mission != context.mission_id:
            reason = EvidenceRejectionReason.WRONG_MISSION
        claimed_execution = candidate.provenance.get("execution_id")
        if claimed_execution and claimed_execution != context.execution_id:
            reason = EvidenceRejectionReason.WRONG_EXECUTION
        claimed_attempt = candidate.provenance.get("attempt_id")
        if claimed_attempt and claimed_attempt != context.attempt_id:
            reason = EvidenceRejectionReason.WRONG_ATTEMPT

        content_hash = candidate.content_hash or _hash_payload(candidate.payload)
        fingerprint = f"{candidate.evidence_type}:{content_hash}:{candidate.sequence}"
        existing = self._fingerprints.setdefault(context.execution_id, set())
        if fingerprint in existing and reason is None:
            # Idempotent duplicate — re-emit as accepted without storing again.
            for item in self._by_execution.get(context.execution_id, []):
                if (
                    item.content_hash == content_hash
                    and item.evidence_type == candidate.evidence_type
                    and item.sequence == candidate.sequence
                    and item.trust is EvidenceTrustClassification.ACCEPTED
                ):
                    return item, True, False

        # Conflicting duplicate: same sequence, different hash.
        if reason is None:
            for item in self._by_execution.get(context.execution_id, []):
                if (
                    item.sequence == candidate.sequence
                    and item.evidence_type == candidate.evidence_type
                    and item.content_hash
                    and content_hash
                    and item.content_hash != content_hash
                ):
                    reason = EvidenceRejectionReason.DUPLICATE_CONFLICTING
                    break

        # Sequence must be monotonic non-decreasing for accepted evidence.
        if reason is None:
            accepted_seqs = [
                item.sequence
                for item in self._by_execution.get(context.execution_id, [])
                if item.trust is EvidenceTrustClassification.ACCEPTED
            ]
            if accepted_seqs and candidate.sequence < max(accepted_seqs):
                reason = EvidenceRejectionReason.INVALID_SEQUENCE

        trust = (
            EvidenceTrustClassification.REJECTED
            if reason is not None
            else EvidenceTrustClassification.ACCEPTED
        )
        envelope = EvidenceEnvelope(
            evidence_id=candidate.evidence_id,
            execution_id=context.execution_id,
            attempt_id=context.attempt_id,
            mission_id=context.mission_id,
            project_id=context.project_id,
            organisation_id=context.organisation_id,
            evidence_type=candidate.evidence_type,
            source_backend=source_backend,
            source_harness=source_harness,
            content_ref=candidate.content_ref,
            content_hash=content_hash,
            size_bytes=candidate.size_bytes,
            sequence=candidate.sequence,
            trust=trust,
            producer_identity=source_backend,
            provenance=dict(candidate.provenance),
            payload=_redact_payload(dict(candidate.payload)),
            rejection_reason=reason,
        )
        if reason is None:
            existing.add(fingerprint)
            return envelope, True, True
        return envelope, False, True


def _hash_payload(payload: Mapping[str, Any]) -> str:
    blob = repr(sorted(payload.items())).encode()
    return hashlib.sha256(blob).hexdigest()


def _redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            out[key] = redact_secrets(value)
        elif isinstance(value, Mapping):
            out[key] = _redact_payload(value)
        else:
            out[key] = value
    return out
