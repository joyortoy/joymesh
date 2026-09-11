"""Worker-side reporting and lease validation — no fleet ownership."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from joymesh.models import utc_now
from joymesh.runtime_v1.contracts.workers import (
    ActiveExecutionReport,
    ExecutionLeaseToken,
    HarnessCapabilityReport,
    WorkerCapacityReport,
    WorkerHeartbeat,
    WorkerReport,
)
from joymesh.runtime_v1.scheduler import SchedulerNodeSnapshot


class LeaseValidationError(PermissionError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def build_worker_report(
    snapshot: SchedulerNodeSnapshot,
    *,
    region: str | None = None,
    runtime_version: str | None = None,
    platform: str | None = None,
    generation: int = 1,
) -> WorkerReport:
    """Project a node snapshot into a neutral worker report for an external CP."""

    harnesses: list[HarnessCapabilityReport] = []
    for connector_id, connector in sorted(snapshot.connectors.items()):
        harnesses.append(
            HarnessCapabilityReport(
                harness_id=connector_id,
                installed=bool(connector.installed),
                capabilities=frozenset(connector.certified_capabilities),
                details={
                    "readiness": getattr(connector.readiness, "value", str(connector.readiness)),
                    "authenticated": bool(connector.authenticated),
                    "routing_enabled": bool(connector.routing_enabled),
                },
            )
        )
    concurrency = int(getattr(snapshot, "concurrency", 0) or 0)
    limit = max(1, 4)
    capacity = WorkerCapacityReport(
        cpu=4.0,
        ram_mb=8192,
        gpu=0.0,
        disk_mb=102400,
        parallel_execution_limit=limit,
        active_execution_count=concurrency,
    )
    return WorkerReport(
        worker_id=snapshot.node_id,
        node_id=snapshot.node_id,
        platform=platform,
        runtime_version=runtime_version,
        region=region,
        labels={"filesystem": "true"},
        capacity=capacity,
        harnesses=tuple(harnesses),
        online=bool(snapshot.online),
        session_authenticated=bool(snapshot.session_authenticated),
        revoked=bool(snapshot.revoked),
        generation=generation,
        metadata={
            "queue_depth": int(getattr(snapshot, "queue_depth", 0) or 0),
            "recent_failures": int(getattr(snapshot, "recent_failures", 0) or 0),
        },
    )


def build_worker_heartbeat(
    report: WorkerReport,
    *,
    sequence: int,
    active_executions: tuple[ActiveExecutionReport, ...] = (),
    fencing_token: int | None = None,
    runtime_faults: tuple[str, ...] = (),
    observed_at: datetime | None = None,
) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=report.worker_id,
        generation=report.generation,
        sequence=sequence,
        observed_at=observed_at or utc_now(),
        capacity=report.capacity,
        active_executions=active_executions,
        harnesses=report.harnesses,
        fencing_token=fencing_token,
        runtime_faults=runtime_faults,
        metadata=dict(report.metadata),
    )


class WorkerLeaseValidator:
    """Validate an externally issued lease before accepting execution."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._last_sequence: dict[str, int] = {}

    def validate(
        self,
        lease: ExecutionLeaseToken | Mapping[str, Any],
        *,
        worker_id: str,
        execution_id: str | None = None,
        now: datetime | None = None,
        expected_signature: str | None = None,
    ) -> ExecutionLeaseToken:
        token = (
            lease
            if isinstance(lease, ExecutionLeaseToken)
            else ExecutionLeaseToken(
                lease_id=str(lease["lease_id"]),
                worker_id=str(lease["worker_id"]),
                execution_id=str(lease["execution_id"]),
                attempt_id=str(lease["attempt_id"]),
                generation=int(lease["generation"]),
                fencing_token=int(lease["fencing_token"]),
                expires_at=_parse_dt(lease["expires_at"]),
                issued_at=_parse_dt(lease.get("issued_at")) if lease.get("issued_at") else None,
                signature=lease.get("signature")
                if isinstance(lease.get("signature"), str)
                else None,
                metadata=dict(lease.get("metadata") or {}),
            )
        )
        current = now or utc_now()
        if token.worker_id != worker_id:
            raise LeaseValidationError("wrong_worker", "lease worker mismatch")
        if execution_id is not None and token.execution_id != execution_id:
            raise LeaseValidationError("wrong_execution", "lease execution mismatch")
        if token.expires_at <= current:
            raise LeaseValidationError("expired", "lease expired")
        if expected_signature is not None and token.signature != expected_signature:
            raise LeaseValidationError("invalid_signature", "lease signature mismatch")
        replay_key = f"{token.lease_id}:{token.fencing_token}:{token.generation}"
        if replay_key in self._seen:
            raise LeaseValidationError("replay", "duplicate lease presentation")
        self._seen.add(replay_key)
        return token

    def accept_heartbeat_sequence(self, worker_id: str, sequence: int) -> None:
        last = self._last_sequence.get(worker_id, 0)
        if sequence <= last:
            raise LeaseValidationError("stale_sequence", "heartbeat sequence not monotonic")
        self._last_sequence[worker_id] = sequence


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
