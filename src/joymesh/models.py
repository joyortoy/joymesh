"""Public protocol models shared by the SDK, CLI, and API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_serializer


def utc_now() -> datetime:
    return datetime.now(UTC)


class Capability(StrEnum):
    NON_INTERACTIVE = "execution.non_interactive"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    SHELL = "shell"
    STREAMING = "streaming"
    STRUCTURED_EVENTS = "events.structured"
    SESSION_CREATE = "session.create"
    SESSION_RESUME = "session.resume"
    MODEL_SELECTION = "model.selection"
    PROVIDER_SELECTION = "provider.selection"
    TOOL_USE = "tool.use"
    TOOL_PERMISSIONS = "tool.permissions"
    FILESYSTEM_SANDBOX = "sandbox.filesystem"
    NETWORK_SANDBOX = "sandbox.network"
    APPROVAL_MODES = "approval.modes"
    WORKING_DIRECTORY = "workspace.selection"
    ADDITIONAL_WRITABLE_DIRECTORIES = "workspace.additional_writable"
    IMAGE_INPUT = "input.image"
    MCP = "protocol.mcp"
    ACP = "protocol.acp"
    USAGE_REPORTING = "usage.reporting"
    CONTEXT_WINDOW_REPORTING = "usage.context_window"
    RATE_LIMIT_REPORTING = "limit.rate"
    COST_REPORTING = "usage.cost"
    CANCELLATION = "runtime.cancellation"
    TIMEOUT_ENFORCEMENT = "runtime.timeout"
    PROCESS_TREE_CLEANUP = "runtime.process_tree_cleanup"


class HarnessAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PermissionMode(StrEnum):
    DEFAULT = "default"
    READ_ONLY = "read_only"
    AUTO_APPROVE = "auto_approve"


class EventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    HARNESS_OUTPUT = "harness.output"
    HARNESS_PROGRESS = "harness.progress"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"
    SESSION_IDENTIFIED = "session.identified"
    USAGE_RECORDED = "usage.recorded"
    RATE_LIMIT_ENCOUNTERED = "rate_limit.encountered"
    FALLBACK_PROPOSED = "fallback.proposed"
    APPROVAL_REQUESTED = "approval.requested"


class BillingRoute(StrEnum):
    SUBSCRIPTION = "subscription"
    API = "api"
    LOCAL = "local"
    UNKNOWN = "unknown"


class SupportStatus(StrEnum):
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    UNAVAILABLE = "unavailable"


class SubscriptionState(StrEnum):
    HEALTHY = "healthy"
    RATE_LIMITED = "rate_limited"
    EXHAUSTED = "exhausted"
    DISABLED = "disabled"


class FailureKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTHENTICATION = "authentication"
    UNSUPPORTED = "unsupported"
    PROCESS = "process"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    harness_id: str
    display_name: str
    adapter_version: str = "0.1.0"
    capabilities: frozenset[Capability]
    supports_interactive: bool = False
    supports_resume: bool = False
    max_concurrency: int = Field(default=1, ge=1)

    @field_serializer("capabilities")
    def serialize_capabilities(self, capabilities: frozenset[Capability]) -> list[Capability]:
        return sorted(capabilities, key=lambda capability: capability.value)


class HarnessDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: CapabilityManifest
    availability: HarnessAvailability
    executable: str | None = None
    version: str | None = None
    support_status: SupportStatus = SupportStatus.EXPERIMENTAL
    detail: str | None = None


class SubscriptionProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    harness_id: str
    name: str
    billing_route: BillingRoute = BillingRoute.UNKNOWN
    enabled: bool = True
    monthly_limit: float | None = Field(default=None, ge=0)
    used_amount: float = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1)
    cost_weight: float = Field(default=1, ge=0)
    quota_reserve: float = Field(default=0, ge=0)
    quota_known: bool = False
    state: SubscriptionState = SubscriptionState.HEALTHY
    requires_paid_approval: bool = False

    @property
    def remaining_fraction(self) -> float | None:
        if self.monthly_limit is None:
            return None
        if self.monthly_limit == 0:
            return 0
        return max(0.0, (self.monthly_limit - self.used_amount) / self.monthly_limit)


class RouteCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    harness_id: str
    subscription_id: str | None = None
    score: float
    eligible: bool
    reasons: tuple[str, ...]


class RoutePreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    selected: RouteCandidate | None
    candidates: tuple[RouteCandidate, ...]


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    run_id: str
    sequence: int
    type: EventType
    timestamp: datetime = Field(default_factory=utc_now)
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    workspace: str
    harness_id: str
    subscription_id: str | None = None
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str | None = None
    task_context_id: str
    continuation_of_run_id: str | None = None
    native_session_id: str | None = None
    process_id: int | None = None


class RoutePreviewRequest(BaseModel):
    task: str = Field(
        min_length=1,
        validation_alias=AliasChoices("task", "prompt"),
    )
    workspace: str = Field(
        default=".",
        validation_alias=AliasChoices("workspace", "cwd"),
    )
    required_capabilities: frozenset[Capability] = Field(default_factory=frozenset)
    preferred_harness: str | None = None
    allowed_harnesses: frozenset[str] = Field(default_factory=frozenset)
    denied_harnesses: frozenset[str] = Field(default_factory=frozenset)
    paid_routes_approved: bool = False


class RunRequest(BaseModel):
    task: str = Field(
        min_length=1,
        validation_alias=AliasChoices("task", "prompt"),
    )
    workspace: str = Field(validation_alias=AliasChoices("workspace", "cwd"))
    route: RouteCandidate | None = None
    required_capabilities: frozenset[Capability] = Field(default_factory=frozenset)
    timeout_seconds: float | None = Field(default=300, gt=0)
    resume_session_id: str | None = None
    allowed_harnesses: frozenset[str] = Field(default_factory=frozenset)
    denied_harnesses: frozenset[str] = Field(default_factory=frozenset)
    paid_routes_approved: bool = False
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    model: str | None = None
    provider: str | None = None
    additional_writable_directories: tuple[str, ...] = ()


class SubscriptionCreate(BaseModel):
    harness_id: str
    name: str
    billing_route: BillingRoute = BillingRoute.UNKNOWN
    monthly_limit: float | None = Field(default=None, ge=0)
    used_amount: float = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1)
    cost_weight: float = Field(default=1, ge=0)
    quota_reserve: float = Field(default=0, ge=0)
    quota_known: bool = False
    requires_paid_approval: bool = False


class LaunchSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    timeout_seconds: float | None = Field(default=300, gt=0)


class UsageDelta(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost: float | None = Field(default=None, ge=0)


class AdapterObservation(BaseModel):
    event: NormalizedEvent
    native_session_id: str | None = None
    usage: UsageDelta | None = None


class HarnessFailure(BaseModel):
    kind: FailureKind
    message: str
    retryable: bool = False


class UsageRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_id: str
    run_id: str | None
    input_tokens: int
    output_tokens: int
    amount: float
    source: str
    recorded_at: datetime


class FallbackProposal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_run_id: str
    route: RouteCandidate
    requires_approval: bool
    approved: bool = False
    continuation_run_id: str | None = None
    reason: str
    created_at: datetime


class FireConnectTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    harness_id: str | None = None
    enabled: bool = False
    model: str | None = None
    reads_from: str | None = None
    storage: str | None = None
    joymesh_runnable: bool = False


class FireConnectStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    signed_in: bool = False
    version: str | None = None
    backend: str | None = None
    detail: str | None = None
    targets: tuple[FireConnectTarget, ...] = ()


class FireConnectConfigureRequest(BaseModel):
    model: str = Field(
        default="accounts/fireworks/models/kimi-k3",
        min_length=1,
        max_length=300,
    )
