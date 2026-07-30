"""Typed failure taxonomy for execution routing fallback decisions."""

from __future__ import annotations

from enum import StrEnum


class ExecutionFailureClass(StrEnum):
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_UNHEALTHY = "backend_unhealthy"
    CAPABILITY_CHANGED = "capability_changed"
    PREPARATION_FAILURE = "preparation_failure"
    RETRYABLE_LAUNCH_FAILURE = "retryable_launch_failure"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ENTITLEMENT_REQUIRED = "entitlement_required"
    BACKEND_NOT_ENTITLED = "backend_not_entitled"
    HARNESS_NOT_ENTITLED = "harness_not_entitled"
    INVALID_AUTHORISATION = "invalid_authorisation"
    POLICY_DENIED = "policy_denied"
    TENANT_VIOLATION = "tenant_violation"
    WORKSPACE_VIOLATION = "workspace_violation"
    CREDENTIAL_VALIDATION_FAILURE = "credential_validation_failure"
    NON_RETRYABLE_USER_CODE_FAILURE = "non_retryable_user_code_failure"
    EVIDENCE_INTEGRITY_FAILURE = "evidence_integrity_failure"
    VERIFICATION_FAILURE = "verification_failure"
    PROVIDER_RESTORE_FAILURE = "provider_restore_failure"
    CONNECTOR_BLOCKED = "connector_blocked"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PROCESS_FAILURE = "process_failure"
    UNKNOWN = "unknown"


# Failures that may advance to the next backend in the fallback chain.
FALLBACK_ELIGIBLE: frozenset[ExecutionFailureClass] = frozenset(
    {
        ExecutionFailureClass.BACKEND_UNAVAILABLE,
        ExecutionFailureClass.BACKEND_UNHEALTHY,
        ExecutionFailureClass.CAPABILITY_CHANGED,
        ExecutionFailureClass.PREPARATION_FAILURE,
        ExecutionFailureClass.RETRYABLE_LAUNCH_FAILURE,
        ExecutionFailureClass.RATE_LIMITED,
        ExecutionFailureClass.QUOTA_EXHAUSTED,
    }
)

# Failures that must never fall back.
FALLBACK_FORBIDDEN: frozenset[ExecutionFailureClass] = frozenset(
    {
        ExecutionFailureClass.INVALID_AUTHORISATION,
        ExecutionFailureClass.POLICY_DENIED,
        ExecutionFailureClass.TENANT_VIOLATION,
        ExecutionFailureClass.WORKSPACE_VIOLATION,
        ExecutionFailureClass.CREDENTIAL_VALIDATION_FAILURE,
        ExecutionFailureClass.NON_RETRYABLE_USER_CODE_FAILURE,
        ExecutionFailureClass.EVIDENCE_INTEGRITY_FAILURE,
        ExecutionFailureClass.VERIFICATION_FAILURE,
        ExecutionFailureClass.PROVIDER_RESTORE_FAILURE,
        ExecutionFailureClass.CONNECTOR_BLOCKED,
        ExecutionFailureClass.CANCELLED,
        ExecutionFailureClass.ENTITLEMENT_REQUIRED,
        ExecutionFailureClass.BACKEND_NOT_ENTITLED,
        ExecutionFailureClass.HARNESS_NOT_ENTITLED,
    }
)


def may_fallback(failure: ExecutionFailureClass) -> bool:
    if failure in FALLBACK_FORBIDDEN:
        return False
    return failure in FALLBACK_ELIGIBLE
