"""Node connector lifecycle, task execution, and evidence contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from joymesh.models import utc_now


class NodeConnectorState(StrEnum):
    UNKNOWN = "unknown"
    NOT_AVAILABLE = "not_available"
    AVAILABLE_TO_INSTALL = "available_to_install"
    INSTALL_PLAN_REQUIRED = "install_plan_required"
    INSTALL_APPROVAL_REQUIRED = "install_approval_required"
    INSTALLING = "installing"
    INSTALLED = "installed"
    EXECUTABLE_BROKEN = "executable_broken"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_IN_PROGRESS = "authentication_in_progress"
    AUTHENTICATED = "authenticated"
    AUTHENTICATION_FAILED = "authentication_failed"
    VERIFICATION_REQUIRED = "verification_required"
    VERIFICATION_IN_PROGRESS = "verification_in_progress"
    VERIFIED = "verified"
    CERTIFICATION_REQUIRED = "certification_required"
    CERTIFICATION_IN_PROGRESS = "certification_in_progress"
    CERTIFIED = "certified"
    CERTIFICATION_FAILED = "certification_failed"
    IDE_ONLY = "ide_only"
    BLOCKED = "blocked"
    NEEDS_REPAIR = "needs_repair"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    ROUTING_DISABLED = "routing_disabled"
    READY = "ready"


class ConnectorTaskStatus(StrEnum):
    PLANNED = "planned"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    QUEUED = "queued"
    OFFERED_TO_NODE = "offered_to_node"
    ACCEPTED_BY_NODE = "accepted_by_node"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_AUTH_CALLBACK = "waiting_for_auth_callback"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    INTERRUPTED = "interrupted"


class ConnectorEvidenceType(StrEnum):
    DISCOVERY = "discovery"
    VERSION = "version"
    INSTALLATION = "installation"
    AUTHENTICATION = "authentication"
    ADAPTER_CONFORMANCE = "adapter_conformance"
    REAL_BINARY_TEST = "real_binary_test"
    CERTIFICATION = "certification"
    ROUTING = "routing"
    FAILURE = "failure"


class RecommendedConnectorAction(StrEnum):
    INSTALL = "install"
    REPAIR = "repair"
    AUTHENTICATE = "authenticate"
    VERIFY_AUTHENTICATION = "verify_authentication"
    VERIFY_ADAPTER = "verify_adapter"
    CERTIFY = "certify"
    ENABLE_ROUTING = "enable_routing"
    DISABLE_ROUTING = "disable_routing"
    RETRY = "retry"
    NONE = "none"


TERMINAL_TASK_STATUSES = frozenset(
    {
        ConnectorTaskStatus.SUCCEEDED,
        ConnectorTaskStatus.FAILED,
        ConnectorTaskStatus.CANCELLED,
        ConnectorTaskStatus.EXPIRED,
        ConnectorTaskStatus.INTERRUPTED,
    }
)

ACTIVE_TASK_STATUSES = frozenset(
    status
    for status in ConnectorTaskStatus
    if status not in TERMINAL_TASK_STATUSES
    and status
    not in {
        ConnectorTaskStatus.PLANNED,
        ConnectorTaskStatus.APPROVAL_REQUIRED,
        ConnectorTaskStatus.APPROVED,
    }
)


@dataclass(frozen=True)
class ConnectorEvidence:
    evidence_id: str
    node_id: str
    connector_id: str
    connector_revision: str
    task_id: str
    evidence_type: ConnectorEvidenceType
    status: str
    executable_path: str | None
    executable_fingerprint: str | None
    harness_version: str | None
    provider_mode: str | None
    details: Mapping[str, Any]
    created_at: datetime
    expires_at: datetime | None


class ConnectorReadiness(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    connector_id: str
    state: NodeConnectorState
    recommended_action: RecommendedConnectorAction | None = None
    blocking_reason: str | None = None
    active_task_id: str | None = None
    latest_evidence_id: str | None = None
    routing_eligible: bool = False
    catalogue_maturity: str
    installed_version: str | None = None
    executable_path: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


@dataclass(frozen=True)
class CertificationProgress:
    discovery_passed: bool
    authentication_passed: bool
    adapter_passed: bool
    read_test_passed: bool
    write_test_passed: bool | None
    command_test_passed: bool | None
    cancellation_test_passed: bool | None
    session_test_passed: bool | None


class ConnectorTaskEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    node_id: str
    connector_id: str
    event_type: str
    sequence: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ConnectorTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    plan_id: str
    node_id: str
    connector_id: str
    connector_revision: str
    action: str
    plan_hash: str
    status: ConnectorTaskStatus
    idempotency_key: str
    previous_task_id: str | None = None
    version: int = 1
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None


class ConnectorLifecyclePlanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: dict[str, Any]
    task_id: str | None = None
    approval_required: bool = True
    next_action: str = "approve"
