"""Normalized provider diagnostic codes (no provider-specific CLI text)."""

from __future__ import annotations

from enum import StrEnum

from joymesh.models import FailureKind
from joymesh.quota.contracts import HarnessAvailability
from joymesh.runtime_snapshot.contracts import RuntimeValidationCode


class ProviderDiagnosticCode(StrEnum):
    CREDENTIAL_MISSING = "credential_missing"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    CONFIGURATION_REQUIRED = "configuration_required"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_FAILURE = "unknown_failure"
    READY = "ready"
    OFFLINE = "offline"
    CAPABILITY_MISMATCH = "capability_mismatch"
    DIRECTIVE_EXPIRED = "directive_expired"
    FALLBACK_NOT_AUTHORIZED = "fallback_not_authorized"
    RUNTIME_CHANGED = "runtime_changed"


def from_availability(availability: HarnessAvailability) -> ProviderDiagnosticCode:
    mapping = {
        HarnessAvailability.READY: ProviderDiagnosticCode.READY,
        HarnessAvailability.AUTHENTICATION_REQUIRED: (
            ProviderDiagnosticCode.AUTHENTICATION_REQUIRED
        ),
        HarnessAvailability.CONFIGURATION_REQUIRED: (
            ProviderDiagnosticCode.CONFIGURATION_REQUIRED
        ),
        HarnessAvailability.QUOTA_EXHAUSTED: ProviderDiagnosticCode.QUOTA_EXHAUSTED,
        HarnessAvailability.RATE_LIMITED: ProviderDiagnosticCode.RATE_LIMITED,
        HarnessAvailability.OFFLINE: ProviderDiagnosticCode.OFFLINE,
        HarnessAvailability.PROVIDER_UNAVAILABLE: (
            ProviderDiagnosticCode.PROVIDER_UNAVAILABLE
        ),
        HarnessAvailability.UNKNOWN: ProviderDiagnosticCode.UNKNOWN_FAILURE,
    }
    return mapping.get(availability, ProviderDiagnosticCode.UNKNOWN_FAILURE)


def from_failure_kind(kind: FailureKind) -> ProviderDiagnosticCode:
    mapping = {
        FailureKind.AUTHENTICATION: ProviderDiagnosticCode.AUTHENTICATION_REQUIRED,
        FailureKind.QUOTA_EXHAUSTED: ProviderDiagnosticCode.QUOTA_EXHAUSTED,
        FailureKind.RATE_LIMIT: ProviderDiagnosticCode.RATE_LIMITED,
        FailureKind.UNSUPPORTED: ProviderDiagnosticCode.UNSUPPORTED_VERSION,
        FailureKind.INVALID_REQUEST: ProviderDiagnosticCode.CONFIGURATION_REQUIRED,
        FailureKind.TIMEOUT: ProviderDiagnosticCode.PROVIDER_UNAVAILABLE,
        FailureKind.PROCESS: ProviderDiagnosticCode.UNKNOWN_FAILURE,
        FailureKind.TURN_LIMIT: ProviderDiagnosticCode.QUOTA_EXHAUSTED,
        FailureKind.UNKNOWN: ProviderDiagnosticCode.UNKNOWN_FAILURE,
    }
    return mapping.get(kind, ProviderDiagnosticCode.UNKNOWN_FAILURE)


def from_runtime_validation(code: RuntimeValidationCode) -> ProviderDiagnosticCode:
    mapping = {
        RuntimeValidationCode.QUOTA_EXHAUSTED: ProviderDiagnosticCode.QUOTA_EXHAUSTED,
        RuntimeValidationCode.AUTHENTICATION_REQUIRED: (
            ProviderDiagnosticCode.AUTHENTICATION_REQUIRED
        ),
        RuntimeValidationCode.CONFIGURATION_REQUIRED: (
            ProviderDiagnosticCode.CONFIGURATION_REQUIRED
        ),
        RuntimeValidationCode.CAPABILITY_MISMATCH: (
            ProviderDiagnosticCode.CAPABILITY_MISMATCH
        ),
        RuntimeValidationCode.PROVIDER_UNAVAILABLE: (
            ProviderDiagnosticCode.PROVIDER_UNAVAILABLE
        ),
        RuntimeValidationCode.RUNTIME_CHANGED: ProviderDiagnosticCode.RUNTIME_CHANGED,
        RuntimeValidationCode.RATE_LIMITED: ProviderDiagnosticCode.RATE_LIMITED,
        RuntimeValidationCode.OFFLINE: ProviderDiagnosticCode.OFFLINE,
    }
    return mapping.get(code, ProviderDiagnosticCode.UNKNOWN_FAILURE)


def classify_detail(detail: str | None) -> ProviderDiagnosticCode:
    """Map factual observation text to a normalized code (never return raw text)."""

    lowered = (detail or "").lower()
    if "credential" in lowered and ("missing" in lowered or "not found" in lowered):
        return ProviderDiagnosticCode.CREDENTIAL_MISSING
    if "expired" in lowered and ("auth" in lowered or "token" in lowered or "login" in lowered):
        return ProviderDiagnosticCode.AUTHENTICATION_EXPIRED
    if "api key" in lowered or "configuration" in lowered:
        return ProviderDiagnosticCode.CONFIGURATION_REQUIRED
    if "not logged" in lowered or "authentication" in lowered or "unauthorized" in lowered:
        return ProviderDiagnosticCode.AUTHENTICATION_REQUIRED
    if "out of credits" in lowered or "quota" in lowered:
        return ProviderDiagnosticCode.QUOTA_EXHAUSTED
    if "rate limit" in lowered or "429" in lowered:
        return ProviderDiagnosticCode.RATE_LIMITED
    if "unsupported version" in lowered or "upgrade required" in lowered:
        return ProviderDiagnosticCode.UNSUPPORTED_VERSION
    if "unavailable" in lowered or "offline" in lowered:
        return ProviderDiagnosticCode.PROVIDER_UNAVAILABLE
    return ProviderDiagnosticCode.UNKNOWN_FAILURE
