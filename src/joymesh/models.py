"""Public protocol models shared by the SDK, CLI, and API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def utc_now() -> datetime:
    return datetime.now(UTC)


class Capability(StrEnum):
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    SHELL = "shell"
    STREAMING = "streaming"
    SESSION_RESUME = "session.resume"
    TOOL_USE = "tool.use"


class HarnessAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    HARNESS_OUTPUT = "harness.output"
    HARNESS_PROGRESS = "harness.progress"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


class BillingRoute(StrEnum):
    SUBSCRIPTION = "subscription"
    API = "api"
    LOCAL = "local"
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


class RoutePreviewRequest(BaseModel):
    task: str = Field(min_length=1)
    workspace: str = "."
    required_capabilities: frozenset[Capability] = Field(default_factory=frozenset)
    preferred_harness: str | None = None


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    workspace: str
    route: RouteCandidate | None = None


class SubscriptionCreate(BaseModel):
    harness_id: str
    name: str
    billing_route: BillingRoute = BillingRoute.UNKNOWN
    monthly_limit: float | None = Field(default=None, ge=0)
    used_amount: float = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1)
    cost_weight: float = Field(default=1, ge=0)
