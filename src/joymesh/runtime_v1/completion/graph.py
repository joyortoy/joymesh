"""Provider-neutral mission graph projection for completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.completion.models import CompletionRecord, EvidenceEnvelope
from joymesh.runtime_v1.completion.states import CompletionLifecycleState


class MissionGraphProjector:
    def __init__(self) -> None:
        self._nodes: dict[str, list[dict[str, Any]]] = {}
        self._node_keys: dict[str, set[str]] = {}

    def nodes_for(self, execution_id: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._nodes.get(execution_id, []))

    def project_request(
        self, *, execution_id: str, mission_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._add(
            execution_id,
            kind="ExecutionRequest",
            key=f"request:{execution_id}",
            payload={"mission_id": mission_id, **dict(payload)},
        )

    def project_decision(
        self, *, execution_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._add(
            execution_id,
            kind="ExecutionDecision",
            key=f"decision:{execution_id}:{payload.get('selected_backend_id')}",
            payload=dict(payload),
        )

    def project_attempt(
        self, *, execution_id: str, attempt_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._add(
            execution_id,
            kind="ExecutionAttempt",
            key=f"attempt:{attempt_id}",
            payload={"attempt_id": attempt_id, **dict(payload)},
        )

    def project_evidence(
        self,
        *,
        execution_id: str,
        evidence: EvidenceEnvelope,
        accepted: bool,
    ) -> Mapping[str, Any]:
        kind = "AcceptedEvidence" if accepted else "CandidateEvidence"
        return self._add(
            execution_id,
            kind=kind,
            key=f"evidence:{evidence.evidence_id}:{kind}",
            payload=evidence.as_dict(),
        )

    def project_verification(
        self, *, execution_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._add(
            execution_id,
            kind="Verification",
            key=f"verification:{execution_id}:{payload.get('outcome')}",
            payload=dict(payload),
        )

    def project_result(self, record: CompletionRecord) -> Mapping[str, Any]:
        return self._add(
            record.execution_id,
            kind="ExecutionResult",
            key=f"result:{record.execution_id}:{record.state.value}",
            payload={
                "state": record.state.value,
                "ok": record.state is CompletionLifecycleState.COMPLETED,
                "failure_class": record.failure_class,
                "detail": record.detail,
                "verification": dict(record.verification),
                "evidence_ids": list(record.evidence_ids),
            },
        )

    def _add(
        self,
        execution_id: str,
        *,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        keys = self._node_keys.setdefault(execution_id, set())
        if key in keys:
            for node in self._nodes.get(execution_id, []):
                if node.get("dedupe_key") == key:
                    return dict(node)
        node = {
            "node_id": f"graph_{uuid4().hex}",
            "kind": kind,
            "dedupe_key": key,
            "execution_id": execution_id,
            "payload": dict(payload),
            "created_at": utc_now().isoformat(),
        }
        keys.add(key)
        self._nodes.setdefault(execution_id, []).append(node)
        return dict(node)
