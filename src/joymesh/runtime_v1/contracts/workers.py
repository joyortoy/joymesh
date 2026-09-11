"""Neutral worker and capacity contracts — facts only, no fleet policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now


@dataclass(frozen=True)
class WorkerCapacityReport:
    """Observed worker capacity — not organisation quotas or placement scores."""

    cpu: float = 0.0
    ram_mb: float = 0.0
    gpu: float = 0.0
    disk_mb: float = 0.0
    network_mbps: float = 0.0
    parallel_execution_limit: int = 0
    active_execution_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu,
            "ram_mb": self.ram_mb,
            "gpu": self.gpu,
            "disk_mb": self.disk_mb,
            "network_mbps": self.network_mbps,
            "parallel_execution_limit": self.parallel_execution_limit,
            "active_execution_count": self.active_execution_count,
        }


@dataclass(frozen=True)
class HarnessCapabilityReport:
    harness_id: str
    installed: bool
    version: str | None = None
    capabilities: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "installed": self.installed,
            "version": self.version,
            "capabilities": sorted(self.capabilities),
            "providers": sorted(self.providers),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ActiveExecutionReport:
    execution_id: str
    attempt_id: str | None = None
    harness_id: str | None = None
    started_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "harness_id": self.harness_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


@dataclass(frozen=True)
class WorkerReport:
    """Neutral worker identity and observed state for an external control plane."""

    worker_id: str
    node_id: str | None = None
    platform: str | None = None
    runtime_version: str | None = None
    region: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    capacity: WorkerCapacityReport = field(default_factory=WorkerCapacityReport)
    harnesses: tuple[HarnessCapabilityReport, ...] = ()
    online: bool = False
    session_authenticated: bool = False
    revoked: bool = False
    generation: int = 1
    observed_at: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "node_id": self.node_id,
            "platform": self.platform,
            "runtime_version": self.runtime_version,
            "region": self.region,
            "labels": dict(self.labels),
            "capacity": self.capacity.as_dict(),
            "harnesses": [item.as_dict() for item in self.harnesses],
            "online": self.online,
            "session_authenticated": self.session_authenticated,
            "revoked": self.revoked,
            "generation": self.generation,
            "observed_at": self.observed_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WorkerHeartbeat:
    """Worker-emitted heartbeat facts — classification belongs to the control plane."""

    worker_id: str
    generation: int
    sequence: int
    observed_at: datetime
    capacity: WorkerCapacityReport
    active_executions: tuple[ActiveExecutionReport, ...] = ()
    harnesses: tuple[HarnessCapabilityReport, ...] = ()
    fencing_token: int | None = None
    runtime_faults: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "capacity": self.capacity.as_dict(),
            "active_executions": [item.as_dict() for item in self.active_executions],
            "harnesses": [item.as_dict() for item in self.harnesses],
            "fencing_token": self.fencing_token,
            "runtime_faults": list(self.runtime_faults),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionLeaseToken:
    """Externally issued lease — JoyMesh validates; it does not grant or place."""

    lease_id: str
    worker_id: str
    execution_id: str
    attempt_id: str
    generation: int
    fencing_token: int
    expires_at: datetime
    issued_at: datetime | None = None
    signature: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "worker_id": self.worker_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "generation": self.generation,
            "fencing_token": self.fencing_token,
            "expires_at": self.expires_at.isoformat(),
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "signature": self.signature,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionOffer:
    execution_id: str
    attempt_id: str
    worker_id: str
    harness_id: str
    prompt: str
    workspace_path: str
    lease: ExecutionLeaseToken
    timeout_seconds: int = 300
    offer_id: str = field(default_factory=lambda: f"offer_{uuid4().hex}")
    sequence: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "harness_id": self.harness_id,
            "prompt": self.prompt,
            "workspace_path": self.workspace_path,
            "lease": self.lease.as_dict(),
            "timeout_seconds": self.timeout_seconds,
            "sequence": self.sequence,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionAcknowledgement:
    offer_id: str
    execution_id: str
    attempt_id: str
    worker_id: str
    accepted: bool
    detail: str = ""
    sequence: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "accepted": self.accepted,
            "detail": self.detail,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class UsageObservation:
    metric: str
    value: float
    unit: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class FactualExecutionResult:
    """Process/runtime facts only — not mission verification or completion."""

    execution_id: str
    attempt_id: str
    worker_id: str
    harness: str
    started_at: datetime
    finished_at: datetime
    exit_code: int | None
    process_status: str
    stdout_reference: str | None = None
    stderr_reference: str | None = None
    artifact_references: tuple[str, ...] = ()
    usage_observations: tuple[UsageObservation, ...] = ()
    session_reference: str | None = None
    cleanup_status: str = "unknown"
    failure_class: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "harness": self.harness,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "exit_code": self.exit_code,
            "process_status": self.process_status,
            "stdout_reference": self.stdout_reference,
            "stderr_reference": self.stderr_reference,
            "artifact_references": list(self.artifact_references),
            "usage_observations": [item.as_dict() for item in self.usage_observations],
            "session_reference": self.session_reference,
            "cleanup_status": self.cleanup_status,
            "failure_class": self.failure_class,
            "detail": self.detail,
            # Explicit non-claims:
            "mission_completed": None,
            "verification_passed": None,
        }
