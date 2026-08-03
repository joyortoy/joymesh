"""Universal quota contracts for harness availability and remaining capacity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class HarnessAvailability(StrEnum):
    """Normalized harness readiness for routing (quota layer)."""

    READY = "ready"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CONFIGURATION_REQUIRED = "configuration_required"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


class QuotaVisibility(StrEnum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    OBSERVED = "observed"
    UNKNOWN = "unknown"


class QuotaState(StrEnum):
    AVAILABLE = "available"
    LOW = "low"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class QuotaSource(StrEnum):
    OFFICIAL_API = "official_api"
    OFFICIAL_CLI = "official_cli"
    OFFICIAL_HEADER = "official_header"
    DOCUMENTED_RESPONSE = "documented_response"
    EXECUTION_RESULT = "execution_result"
    CACHE = "cache"
    NONE = "none"


# Availabilities that must not be auto-selected unless the user explicitly
# requests that harness for the run.
AUTO_BLOCKED_AVAILABILITIES = frozenset(
    {
        HarnessAvailability.AUTHENTICATION_REQUIRED,
        HarnessAvailability.CONFIGURATION_REQUIRED,
        HarnessAvailability.QUOTA_EXHAUSTED,
        HarnessAvailability.RATE_LIMITED,
        HarnessAvailability.OFFLINE,
        HarnessAvailability.PROVIDER_UNAVAILABLE,
    }
)

# Routing score preference (higher is better).
AVAILABILITY_SCORE: Mapping[HarnessAvailability, float] = {
    HarnessAvailability.READY: 40.0,
    HarnessAvailability.UNKNOWN: 5.0,
    HarnessAvailability.RATE_LIMITED: -20.0,
    HarnessAvailability.QUOTA_EXHAUSTED: -40.0,
    HarnessAvailability.AUTHENTICATION_REQUIRED: -50.0,
    HarnessAvailability.CONFIGURATION_REQUIRED: -50.0,
    HarnessAvailability.OFFLINE: -60.0,
    HarnessAvailability.PROVIDER_UNAVAILABLE: -60.0,
}

QUOTA_STATE_SCORE: Mapping[QuotaState, float] = {
    QuotaState.AVAILABLE: 10.0,
    QuotaState.LOW: 0.0,
    QuotaState.UNKNOWN: -5.0,
    QuotaState.BLOCKED: -25.0,
    QuotaState.EXHAUSTED: -40.0,
}


@dataclass(frozen=True)
class QuotaSnapshot:
    harness_id: str
    availability: HarnessAvailability
    quota_visibility: QuotaVisibility
    state: QuotaState
    authenticated: bool
    configured: bool
    credits_remaining: float | None
    requests_remaining: int | None
    tokens_remaining: int | None
    reset_at: datetime | None
    observed_at: datetime
    source: QuotaSource
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "availability": self.availability.value,
            "quota_visibility": self.quota_visibility.value,
            "state": self.state.value,
            "authenticated": self.authenticated,
            "configured": self.configured,
            "credits_remaining": self.credits_remaining,
            "requests_remaining": self.requests_remaining,
            "tokens_remaining": self.tokens_remaining,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source.value,
            "raw_metadata": dict(self.raw_metadata),
        }

    @property
    def display_status(self) -> str:
        mapping = {
            HarnessAvailability.READY: "Ready",
            HarnessAvailability.AUTHENTICATION_REQUIRED: "Login required",
            HarnessAvailability.CONFIGURATION_REQUIRED: "API key missing",
            HarnessAvailability.QUOTA_EXHAUSTED: "Credits exhausted",
            HarnessAvailability.RATE_LIMITED: "Rate limited",
            HarnessAvailability.OFFLINE: "Offline",
            HarnessAvailability.PROVIDER_UNAVAILABLE: "Provider unavailable",
            HarnessAvailability.UNKNOWN: "Unknown",
        }
        return mapping.get(self.availability, self.availability.value)

    @property
    def display_mark(self) -> str:
        if self.availability is HarnessAvailability.READY:
            return "✓"
        if self.availability is HarnessAvailability.UNKNOWN:
            return "?"
        return "⚠"


class QuotaProvider(Protocol):
    """Optional harness-facing quota probe. Unsupported providers return UNKNOWN."""

    harness_id: str

    def quota_snapshot(self) -> QuotaSnapshot:
        """Return the current quota snapshot (may perform a local CLI/API probe)."""
        ...
