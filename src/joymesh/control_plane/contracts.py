"""Versioned contracts shared by the cloud control plane and JoyMesh Node."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from joymesh.models import utc_now


class OnboardingState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    ACCOUNT_READY = "ACCOUNT_READY"
    NODE_PAIRING_REQUIRED = "NODE_PAIRING_REQUIRED"
    NODE_OFFLINE = "NODE_OFFLINE"
    ENVIRONMENT_CHECK = "ENVIRONMENT_CHECK"
    HARNESS_SELECTION = "HARNESS_SELECTION"
    INSTALLATION_REVIEW = "INSTALLATION_REVIEW"
    INSTALLING = "INSTALLING"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    VERIFYING_ACCOUNTS = "VERIFYING_ACCOUNTS"
    CERTIFICATION_REQUIRED = "CERTIFICATION_REQUIRED"
    CERTIFYING = "CERTIFYING"
    ROUTING_SETUP = "ROUTING_SETUP"
    FIRECONNECT_SETUP = "FIRECONNECT_SETUP"
    FINAL_CHECK = "FINAL_CHECK"
    COMPLETE = "COMPLETE"
    LIMITED_MODE = "LIMITED_MODE"
    BLOCKED = "BLOCKED"


class HarnessReadiness(StrEnum):
    NOT_DETECTED = "not_detected"
    DETECTED = "detected"
    INSTALL_PLANNED = "install_planned"
    INSTALLING = "installing"
    INSTALLED = "installed"
    AUTH_REQUIRED = "auth_required"
    AUTHENTICATED = "authenticated"
    FUNDING_UNKNOWN = "funding_unknown"
    CERTIFICATION_REQUIRED = "certification_required"
    CERTIFIED = "certified"
    READY = "ready"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PaidRoutePolicy(StrEnum):
    NEVER = "never"
    ASK = "ask"
    ALLOW_WITH_LIMITS = "allow_with_limits"


class AccountConnectionStatus(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    MISCONFIGURED = "misconfigured"


class NodeProtocolMessageType(StrEnum):
    HELLO = "hello"
    WELCOME = "welcome"
    HEARTBEAT = "heartbeat"
    PRESENCE = "presence"
    TASK_OFFER = "task.offer"
    TASK_ACCEPTED = "task.accepted"
    TASK_REJECTED = "task.rejected"
    TASK_EVENT = "task.event"
    TASK_CANCEL = "task.cancel"
    TASK_RESUME = "task.resume"
    TASK_COMPLETE = "task.complete"
    APPROVAL_REQUEST = "approval.request"
    APPROVAL_DECISION = "approval.decision"
    ERROR = "error"
    GOODBYE = "goodbye"


class ProtocolMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_version: Literal["1"] = "1"
    type: NodeProtocolMessageType
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    node_id: str
    sequence: int = Field(ge=0)
    timestamp: datetime = Field(default_factory=utc_now)
    reply_to: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PairingSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    organisation_id: str
    workspace_id: str
    user_code: str
    device_code_hash: str
    code_challenge: str
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=10))
    interval_seconds: int = Field(default=5, ge=1, le=30)
    approved_by_user_id: str | None = None


class NodeRegistration(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    organisation_id: str
    workspace_id: str
    name: str
    public_key: str
    key_id: str
    platform: str
    version: str
    created_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class OnboardingProgress(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    organisation_id: str
    workspace_id: str
    node_id: str | None = None
    state: OnboardingState = OnboardingState.NOT_STARTED
    selected_harnesses: tuple[str, ...] = ()
    completed_steps: tuple[OnboardingState, ...] = ()
    limited_mode_reason: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class PlanCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    executable: str
    args: tuple[str, ...] = ()
    working_directory: str
    environment: dict[str, str] = Field(default_factory=dict)


class ActionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    browser_session_id: str
    node_id: str
    harness_id: str
    action: str
    command: PlanCommand
    risk: RiskLevel
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=10))
    plan_hash: str = ""


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    plan_hash: str
    user_id: str
    browser_session_id: str
    node_id: str
    approved: bool
    decided_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=5))


class WorkspaceGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    node_id: str
    root_path: str
    allow_read: bool = True
    allow_write: bool = False
    allow_shell: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class RemoteTaskEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_version: Literal["1"] = "1"
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_context_id: str = Field(default_factory=lambda: str(uuid4()))
    organisation_id: str
    workspace_id: str
    node_id: str
    user_id: str
    browser_session_id: str
    harness_id: str
    task: str = Field(min_length=1, max_length=100_000)
    required_capabilities: tuple[str, ...] = ()
    approval_id: str | None = None
    nonce: str = Field(default_factory=lambda: str(uuid4()))
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=5))
    key_id: str
    signature: str = ""


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    organisation_id: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    outcome: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
