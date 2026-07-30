"""Provider-neutral execution routing models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    PLANNED = "planned"
    ROUTED = "routed"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class MissionSpec:
    """High-level mission input — never names a concrete backend."""

    prompt: str
    workspace_path: str
    mission_id: str = field(default_factory=lambda: str(uuid4()))
    project_id: str | None = None
    organisation_id: str | None = None
    workspace_ref: str | None = None
    execution_authorisation_id: str | None = None
    preferred_model: str | None = None
    preferred_harness: str | None = None
    required_capabilities: frozenset[ExecutionCapability] = frozenset()
    optional_capabilities: frozenset[ExecutionCapability] = frozenset()
    requires_provider_route: bool = False
    requires_internet: bool = False
    requires_gpu: bool = False
    requires_local_filesystem: bool = True
    requires_ephemeral_workspace: bool = False
    requires_remote_worker: bool = False
    estimated_runtime_seconds: int | None = None
    estimated_token_usage: int | None = None
    cost_preference: str = "balanced"  # balanced | cheapest | fastest
    locality_preference: str = "any"  # any | local | remote
    timeout_seconds: int = 300
    organisation_policy: Mapping[str, Any] = field(default_factory=dict)
    subscription_constraints: Mapping[str, Any] = field(default_factory=dict)
    routing_preferences: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass(frozen=True)
class ExecutionIntent:
    """Planner output — execution requirements only (no backend selection)."""

    mission_id: str
    execution_id: str
    prompt: str
    workspace_path: str
    required_capabilities: frozenset[ExecutionCapability]
    preferred_model: str | None
    preferred_harness: str | None
    requires_provider_route: bool
    requires_ephemeral_workspace: bool
    estimated_runtime_seconds: int | None
    estimated_token_usage: int | None
    cost_preference: str
    project_id: str | None = None
    organisation_id: str | None = None
    workspace_ref: str | None = None
    execution_authorisation_id: str | None = None
    timeout_seconds: int = 300
    locality_preference: str = "any"
    organisation_policy: Mapping[str, Any] = field(default_factory=dict)
    subscription_constraints: Mapping[str, Any] = field(default_factory=dict)
    routing_preferences: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    task_class: str | None = None
    required_semantic_capabilities: frozenset[str] = frozenset()
    task_analysis: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "execution_id": self.execution_id,
            "prompt": self.prompt,
            "workspace_path": self.workspace_path,
            "workspace_ref": self.workspace_ref,
            "project_id": self.project_id,
            "organisation_id": self.organisation_id,
            "execution_authorisation_id": self.execution_authorisation_id,
            "required_capabilities": sorted(c.value for c in self.required_capabilities),
            "preferred_model": self.preferred_model,
            "preferred_harness": self.preferred_harness,
            "requires_provider_route": self.requires_provider_route,
            "requires_ephemeral_workspace": self.requires_ephemeral_workspace,
            "estimated_runtime_seconds": self.estimated_runtime_seconds,
            "estimated_token_usage": self.estimated_token_usage,
            "cost_preference": self.cost_preference,
            "locality_preference": self.locality_preference,
            "timeout_seconds": self.timeout_seconds,
            "organisation_policy": dict(self.organisation_policy),
            "subscription_constraints": dict(self.subscription_constraints),
            "routing_preferences": dict(self.routing_preferences),
            "metadata": dict(self.metadata),
            "correlation_id": self.correlation_id,
            "task_class": self.task_class,
            "required_semantic_capabilities": sorted(self.required_semantic_capabilities),
            "task_analysis": dict(self.task_analysis),
        }


@dataclass(frozen=True)
class ExecutionRequest:
    """Mission-graph reference for a single execution attempt identity."""

    execution_id: str
    mission_id: str
    intent: ExecutionIntent
    created_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "mission_id": self.mission_id,
            "intent": self.intent.as_dict(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ExecutionDecision:
    """Router output — backend + harness (+ connector/model) + fallback, with reason."""

    execution_id: str
    selected_backend_id: str
    selected_harness_id: str
    reason: str
    fallback_order: tuple[str, ...]
    provider_routing_required: bool
    retry_policy: Mapping[str, Any] = field(default_factory=dict)
    scores: Mapping[str, float] = field(default_factory=dict)
    capability_match: Mapping[str, Any] = field(default_factory=dict)
    policy_result: Mapping[str, Any] = field(default_factory=dict)
    health_snapshot: Mapping[str, Any] = field(default_factory=dict)
    quota_snapshot: Mapping[str, Any] = field(default_factory=dict)
    registry_revision: str = "default"
    decided_at: datetime = field(default_factory=utc_now)
    selected_connector_id: str | None = None
    selected_model_id: str | None = None
    route_score: float | None = None
    route_candidates: tuple[Mapping[str, Any], ...] = ()
    task_analysis: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "selected_backend_id": self.selected_backend_id,
            "selected_harness_id": self.selected_harness_id,
            "selected_connector_id": self.selected_connector_id,
            "selected_model_id": self.selected_model_id,
            "reason": self.reason,
            "fallback_order": list(self.fallback_order),
            "provider_routing_required": self.provider_routing_required,
            "retry_policy": dict(self.retry_policy),
            "scores": dict(self.scores),
            "route_score": self.route_score,
            "route_candidates": [dict(item) for item in self.route_candidates],
            "capability_match": dict(self.capability_match),
            "policy_result": dict(self.policy_result),
            "health_snapshot": dict(self.health_snapshot),
            "quota_snapshot": dict(self.quota_snapshot),
            "registry_revision": self.registry_revision,
            "decided_at": self.decided_at.isoformat(),
            "task_analysis": dict(self.task_analysis),
        }


@dataclass(frozen=True)
class ExecutionAttemptRecord:
    """One backend attempt under a stable execution_id."""

    attempt_id: str
    execution_id: str
    attempt_number: int
    backend_id: str
    harness_id: str
    started_at: datetime
    completed_at: datetime | None = None
    failure_class: str | None = None
    fallback_reason: str | None = None
    backend_execution_ref: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    audit_correlation_id: str | None = None
    status: str = "started"

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "attempt_number": self.attempt_number,
            "backend_id": self.backend_id,
            "harness_id": self.harness_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "failure_class": self.failure_class,
            "fallback_reason": self.fallback_reason,
            "backend_execution_ref": self.backend_execution_ref,
            "usage": dict(self.usage),
            "evidence_refs": list(self.evidence_refs),
            "audit_correlation_id": self.audit_correlation_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """Provider-neutral backend execution facts — never mission verification.

    ``ok`` / ``status`` mean the backend process finished successfully.
    Mission completion requires ``ExecutionCompletionOrchestrator`` verification.
    ``candidate_verification`` holds untrusted remote observations only.
    """

    ok: bool
    execution_id: str
    backend_id: str
    harness_id: str
    status: ExecutionStatus
    message: str
    attempted_backends: tuple[str, ...] = ()
    decision: ExecutionDecision | None = None
    output: Mapping[str, Any] = field(default_factory=dict)
    audits: tuple[Mapping[str, Any], ...] = ()
    attempts: tuple[Mapping[str, Any], ...] = ()
    failure_class: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    # Untrusted candidate observations from backends/workers — not final verification.
    candidate_verification: Mapping[str, Any] = field(default_factory=dict)
    attempt_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cleanup_status: str | None = None
    restore_status: str | None = None
    remote_execution_ref: str | None = None
    # Deprecated alias — treat as candidate_verification only.
    verification: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        candidate = dict(self.candidate_verification or self.verification)
        return {
            "ok": self.ok,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "backend_id": self.backend_id,
            "harness_id": self.harness_id,
            "status": self.status.value,
            "backend_status": self.status.value,
            "message": self.message,
            "attempted_backends": list(self.attempted_backends),
            "decision": self.decision.as_dict() if self.decision else None,
            "output": dict(self.output),
            "audits": [dict(item) for item in self.audits],
            "attempts": [dict(item) for item in self.attempts],
            "failure_class": self.failure_class,
            "usage": dict(self.usage),
            "evidence_refs": list(self.evidence_refs),
            "candidate_verification": candidate,
            "verification": candidate,  # deprecated alias
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "cleanup_status": self.cleanup_status,
            "restore_status": self.restore_status,
            "remote_execution_ref": self.remote_execution_ref,
        }


@dataclass(frozen=True)
class BackendHealth:
    healthy: bool
    backend_id: str
    detail: str
    capabilities: frozenset[ExecutionCapability] = frozenset()
    state: str = "unknown"  # healthy | unhealthy | disabled | unsupported | unknown


@dataclass(frozen=True)
class BackendAuditEvent:
    event_type: str
    execution_id: str
    backend_id: str | None
    harness_id: str | None = None
    reason: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "execution_id": self.execution_id,
            "backend_id": self.backend_id,
            "harness_id": self.harness_id,
            "reason": self.reason,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class BackendRegistryConfig:
    """Configuration for enabled backends, priority, and fallback."""

    enabled_backends: tuple[str, ...] = ("local", "fireconnect", "joymesh")
    priority: tuple[str, ...] = ("local", "fireconnect", "joymesh")
    default_backend: str | None = "local"
    fallback_order: tuple[str, ...] = ("local", "fireconnect", "joymesh")
    capability_overrides: Mapping[str, Sequence[str]] = field(default_factory=dict)
    fireconnect: Mapping[str, Any] = field(default_factory=dict)
    registry_revision: str = "v1"
    # Hosted is a stub — disabled unless explicitly enabled with a real implementation.
    allow_stub_backends: bool = False
