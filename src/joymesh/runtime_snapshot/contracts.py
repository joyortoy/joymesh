"""Canonical runtime snapshot contracts for JoyCLI consumption.

JoyMesh observes and reports facts only. JoyCLI owns all routing policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from joymesh.quota.contracts import HarnessAvailability, QuotaSnapshot

SCHEMA_VERSION = 1


class ExecutionState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    UNKNOWN = "unknown"


class QualityLevel(StrEnum):
    GOOD = "good"
    BAD = "bad"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    execution_count: int = 0
    last_execution: datetime | None = None
    average_duration_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "execution_count": self.execution_count,
            "last_execution": (
                self.last_execution.isoformat() if self.last_execution else None
            ),
            "average_duration_ms": self.average_duration_ms,
        }


@dataclass(frozen=True)
class QualitySnapshot:
    level: QualityLevel = QualityLevel.UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level.value}


@dataclass(frozen=True)
class LatencySnapshot:
    average_ms: float | None = None
    last_ms: float | None = None
    p95_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "average_ms": self.average_ms,
            "last_ms": self.last_ms,
            "p95_ms": self.p95_ms,
        }


@dataclass(frozen=True)
class HarnessRuntimeSnapshot:
    harness_id: str
    availability: HarnessAvailability
    authenticated: bool
    configured: bool
    quota: QuotaSnapshot
    capabilities: frozenset[str]
    execution_state: ExecutionState
    recent_usage: UsageSnapshot
    recent_quality: QualitySnapshot
    latency: LatencySnapshot
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "availability": self.availability.value,
            "authenticated": self.authenticated,
            "configured": self.configured,
            "quota": self.quota.as_dict(),
            "capabilities": sorted(self.capabilities),
            "execution_state": self.execution_state.value,
            "recent_usage": self.recent_usage.as_dict(),
            "recent_quality": self.recent_quality.as_dict(),
            "latency": self.latency.as_dict(),
            "provider_metadata": dict(self.provider_metadata),
        }

    @property
    def display_status(self) -> str:
        mapping = {
            HarnessAvailability.READY: "Ready",
            HarnessAvailability.AUTHENTICATION_REQUIRED: "Authentication Required",
            HarnessAvailability.CONFIGURATION_REQUIRED: "Configuration Required",
            HarnessAvailability.QUOTA_EXHAUSTED: "Quota Exhausted",
            HarnessAvailability.RATE_LIMITED: "Rate Limited",
            HarnessAvailability.OFFLINE: "Offline",
            HarnessAvailability.PROVIDER_UNAVAILABLE: "Provider Unavailable",
            HarnessAvailability.UNKNOWN: "Unknown",
        }
        return mapping.get(self.availability, self.availability.value)


@dataclass(frozen=True)
class RuntimeSnapshot:
    snapshot_id: str
    observed_at: datetime
    harnesses: tuple[HarnessRuntimeSnapshot, ...]
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at.isoformat(),
            "schema_version": self.schema_version,
            "harnesses": [item.as_dict() for item in self.harnesses],
        }

    def harness(self, harness_id: str) -> HarnessRuntimeSnapshot | None:
        for item in self.harnesses:
            if item.harness_id == harness_id:
                return item
        return None


# Structured launch-time rejection codes (JoyMesh validates; JoyCLI decides next steps).
class RuntimeValidationCode(StrEnum):
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CONFIGURATION_REQUIRED = "configuration_required"
    CAPABILITY_MISMATCH = "capability_mismatch"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RUNTIME_CHANGED = "runtime_changed"
    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"
