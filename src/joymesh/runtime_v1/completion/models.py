"""Provider-neutral completion, evidence, and verification models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.completion.states import (
    CompletionFailureClass,
    CompletionLifecycleState,
    EvidenceRejectionReason,
    EvidenceTrustClassification,
    VerificationOutcome,
)


@dataclass(frozen=True)
class CandidateEvidence:
    """Backend-produced candidate evidence — never trusted or verified by itself."""

    evidence_type: str
    content_ref: str | None = None
    content_hash: str | None = None
    size_bytes: int = 0
    sequence: int = 0
    provenance: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    evidence_id: str = field(default_factory=lambda: f"evidence_{uuid4().hex}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "content_ref": self.content_ref,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "sequence": self.sequence,
            "provenance": dict(self.provenance),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class EvidenceEnvelope:
    """Normalised provider-neutral evidence record."""

    evidence_id: str
    execution_id: str
    attempt_id: str
    mission_id: str
    project_id: str | None
    organisation_id: str | None
    evidence_type: str
    source_backend: str
    source_harness: str | None
    content_ref: str | None
    content_hash: str | None
    size_bytes: int
    sequence: int
    trust: EvidenceTrustClassification
    producer_identity: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    rejection_reason: EvidenceRejectionReason | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "mission_id": self.mission_id,
            "project_id": self.project_id,
            "organisation_id": self.organisation_id,
            "evidence_type": self.evidence_type,
            "source_backend": self.source_backend,
            "source_harness": self.source_harness,
            "content_ref": self.content_ref,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "sequence": self.sequence,
            "trust": self.trust.value,
            "producer_identity": self.producer_identity,
            "provenance": dict(self.provenance),
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
            "rejection_reason": self.rejection_reason.value if self.rejection_reason else None,
        }


@dataclass(frozen=True)
class VerificationRequest:
    mission_id: str
    execution_id: str
    attempt_id: str
    strategy: str = "backend_success_with_evidence"
    required_evidence_types: tuple[str, ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)
    accepted_evidence: tuple[EvidenceEnvelope, ...] = ()
    backend_ok: bool = False
    restore_ok: bool = True


@dataclass(frozen=True)
class VerificationResult:
    outcome: VerificationOutcome
    strategy: str
    detail: str
    failure_class: CompletionFailureClass | None = None
    checks: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "strategy": self.strategy,
            "detail": self.detail,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class UsageFact:
    organisation_id: str | None
    project_id: str | None
    mission_id: str
    execution_id: str
    attempt_id: str
    backend_id: str
    harness_id: str | None
    facts: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "organisation_id": self.organisation_id,
            "project_id": self.project_id,
            "mission_id": self.mission_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "backend_id": self.backend_id,
            "harness_id": self.harness_id,
            "facts": dict(self.facts),
        }


@dataclass(frozen=True)
class CompletionContext:
    """Identity and policy context for completion orchestration."""

    organisation_id: str | None
    project_id: str | None
    mission_id: str
    execution_id: str
    attempt_id: str
    authoritative_attempt_id: str
    backend_id: str
    harness_id: str | None
    correlation_id: str | None = None
    user_id: str | None = None
    cancelled: bool = False
    require_evidence: bool = False
    verification_strategy: str = "backend_success_with_evidence"
    required_evidence_types: tuple[str, ...] = ()
    max_evidence_bytes: int = 2_000_000
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class CompletionRecord:
    """Resumable completion state machine record."""

    execution_id: str
    mission_id: str
    state: CompletionLifecycleState
    attempt_id: str
    authoritative_attempt_id: str
    backend_id: str
    harness_id: str | None = None
    organisation_id: str | None = None
    project_id: str | None = None
    backend_ok: bool = False
    restore_ok: bool = True
    evidence_ids: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    usage_finalised: bool = False
    usage_aggregate: dict[str, Any] = field(default_factory=dict)
    graph_nodes: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    failure_class: str | None = None
    detail: str | None = None
    sequence: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    terminal_emitted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "mission_id": self.mission_id,
            "state": self.state.value,
            "attempt_id": self.attempt_id,
            "authoritative_attempt_id": self.authoritative_attempt_id,
            "backend_id": self.backend_id,
            "harness_id": self.harness_id,
            "organisation_id": self.organisation_id,
            "project_id": self.project_id,
            "backend_ok": self.backend_ok,
            "restore_ok": self.restore_ok,
            "evidence_ids": list(self.evidence_ids),
            "verification": dict(self.verification),
            "usage_finalised": self.usage_finalised,
            "usage_aggregate": dict(self.usage_aggregate),
            "graph_nodes": list(self.graph_nodes),
            "events": list(self.events),
            "failure_class": self.failure_class,
            "detail": self.detail,
            "sequence": self.sequence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "terminal_emitted": self.terminal_emitted,
        }


@dataclass(frozen=True)
class CompletionOutcome:
    """Authoritative terminal decision — backends never produce this."""

    ok: bool
    state: CompletionLifecycleState
    execution_id: str
    mission_id: str
    attempt_id: str
    detail: str
    failure_class: str | None = None
    verification: Mapping[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    events: tuple[Mapping[str, Any], ...] = ()
    graph_nodes: tuple[Mapping[str, Any], ...] = ()
    record: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state.value,
            "execution_id": self.execution_id,
            "mission_id": self.mission_id,
            "attempt_id": self.attempt_id,
            "detail": self.detail,
            "failure_class": self.failure_class,
            "verification": dict(self.verification),
            "evidence_ids": list(self.evidence_ids),
            "usage": dict(self.usage),
            "events": [dict(item) for item in self.events],
            "graph_nodes": [dict(item) for item in self.graph_nodes],
            "record": dict(self.record),
        }
