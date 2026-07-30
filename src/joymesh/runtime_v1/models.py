"""Runtime task, lease, attempt, placement, and candidate models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from joymesh.connectors.lifecycle_models import (
    ConnectorExecutionOrigin,
    EvidenceTrustLevel,
)
from joymesh.models import utc_now


class RuntimeTaskStatus(StrEnum):
    PENDING = "pending"
    APPROVAL_REQUIRED = "approval_required"
    ROUTING = "routing"
    QUEUED = "queued"
    LEASED = "leased"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    RUNNING = "running"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class FailureClass(StrEnum):
    NODE_UNAVAILABLE = "node_unavailable"
    NODE_DISCONNECTED = "node_disconnected"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    AUTHENTICATION_INVALID = "authentication_invalid"
    CERTIFICATION_INVALID = "certification_invalid"
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    OFFER_TIMEOUT = "offer_timeout"
    TASK_TIMEOUT = "task_timeout"
    PROCESS_FAILURE = "process_failure"
    POLICY_REJECTED = "policy_rejected"
    USER_CANCELLED = "user_cancelled"
    UNCERTAIN_EXECUTION = "uncertain_execution"
    UNKNOWN = "unknown"


class LeaseStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"
    RECONCILING = "reconciling"


@dataclass(frozen=True)
class RuntimeTaskRequest:
    workspace_id: str
    prompt: str
    requested_capabilities: frozenset[str]
    policy_profile: str = "read_only"
    prohibited_capabilities: frozenset[str] = frozenset()
    preferred_connectors: tuple[str, ...] = ()
    required_connector: str | None = None
    preferred_providers: tuple[str, ...] = ()
    required_provider: str | None = None
    preferred_nodes: tuple[str, ...] = ()
    required_node: str | None = None
    timeout_seconds: int = 300
    max_attempts: int = 2
    user_id: str = "anonymous"
    task_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utc_now)

    @property
    def prompt_digest(self) -> str:
        return sha256(self.prompt.encode()).hexdigest()


class RuntimeTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    workspace_id: str
    user_id: str
    prompt_digest: str
    prompt_size: int
    requested_capabilities: tuple[str, ...]
    prohibited_capabilities: tuple[str, ...]
    expanded_capabilities: tuple[str, ...]
    policy_profile: str
    preferred_connectors: tuple[str, ...] = ()
    required_connector: str | None = None
    preferred_providers: tuple[str, ...] = ()
    required_provider: str | None = None
    preferred_nodes: tuple[str, ...] = ()
    required_node: str | None = None
    timeout_seconds: int = 300
    max_attempts: int = 2
    status: RuntimeTaskStatus = RuntimeTaskStatus.PENDING
    selected_node_id: str | None = None
    selected_connector_id: str | None = None
    selected_backend_id: str | None = None
    selected_harness_id: str | None = None
    execution_id: str | None = None
    execution_decision_reason: str | None = None
    execution_fallback_order: tuple[str, ...] = ()
    provider_routing_required: bool = False
    selected_provider_id: str | None = None
    selected_provider_route_id: str | None = None
    selected_provider_route_manager_id: str | None = None
    selected_model_id: str | None = None
    provider_selection_reason: str | None = None
    approval_required: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    detail: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    granted_capabilities: frozenset[str]
    denied_capabilities: frozenset[str]
    approval_requirements: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WorkspacePlacement:
    workspace_id: str
    node_id: str
    local_path: str
    fingerprint: str
    writable: bool
    last_verified_at: datetime
    expose_path: bool = False


@dataclass(frozen=True)
class RouteCandidate:
    node_id: str
    connector_id: str
    policy_profile: str
    certified_capabilities: frozenset[str]
    score: float
    eligible: bool
    rejection_reasons: tuple[str, ...]
    scoring_factors: Mapping[str, float]


@dataclass(frozen=True)
class TaskLease:
    lease_id: str
    task_id: str
    node_id: str
    connector_id: str
    attempt_id: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime
    status: LeaseStatus


@dataclass(frozen=True)
class ExecutionAttempt:
    attempt_id: str
    task_id: str
    attempt_number: int
    node_id: str
    connector_id: str
    lease_id: str
    execution_origin: ConnectorExecutionOrigin
    status: str
    started_at: datetime | None
    terminal_at: datetime | None
    failure_class: FailureClass | None
    retry_safe: bool


@dataclass(frozen=True)
class CertifiedCapability:
    capability_id: str
    connector_id: str
    node_id: str
    certification_profile: str
    certification_profile_revision: str
    evidence_id: str
    execution_origin: ConnectorExecutionOrigin
    trust_level: EvidenceTrustLevel
    executable_fingerprint: str
    connector_revision: str
    connector_version: str
    capability_definition_revision: str
    certified_at: datetime
    expires_at: datetime | None
    constraints: Mapping[str, Any]
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class RuntimeAuditEvent:
    event_id: str
    task_id: str | None
    event_type: str
    payload: Mapping[str, Any]
    created_at: datetime = field(default_factory=utc_now)


class CreateRuntimeTaskBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: str
    prompt: str
    policy_profile: str = "read_only"
    requested_capabilities: tuple[str, ...]
    prohibited_capabilities: tuple[str, ...] = ()
    preferred_connectors: tuple[str, ...] = ()
    required_connector: str | None = None
    preferred_providers: tuple[str, ...] = ()
    required_provider: str | None = None
    preferred_nodes: tuple[str, ...] = ()
    required_node: str | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_attempts: int = Field(default=2, ge=1, le=10)
