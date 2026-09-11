"""Retry and failover policy for execution attempts."""

from __future__ import annotations

from dataclasses import dataclass

from joymesh.runtime_v1.capabilities import MUTATING_CAPABILITIES
from joymesh.runtime_v1.models import FailureClass, RuntimeTaskRecord


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    reason: str
    failure_class: FailureClass


_SAFE_BEFORE_START = frozenset(
    {
        FailureClass.NODE_UNAVAILABLE,
        FailureClass.OFFER_TIMEOUT,
        FailureClass.CONNECTOR_UNAVAILABLE,
        FailureClass.RATE_LIMITED,
        FailureClass.WORKSPACE_UNAVAILABLE,
    }
)


def decide_retry(
    *,
    task: RuntimeTaskRecord,
    failure_class: FailureClass,
    attempt_number: int,
    execution_started: bool,
    uncertain: bool = False,
) -> RetryDecision:
    if attempt_number >= task.max_attempts:
        return RetryDecision(False, "max attempts reached", failure_class)
    if failure_class is FailureClass.USER_CANCELLED:
        return RetryDecision(False, "user cancellation is not retried", failure_class)
    if failure_class is FailureClass.POLICY_REJECTED:
        return RetryDecision(False, "policy rejection is not retried", failure_class)
    if failure_class is FailureClass.AUTHENTICATION_INVALID:
        return RetryDecision(
            False, "authentication failure is not automatically retried", failure_class
        )
    if uncertain or failure_class is FailureClass.UNCERTAIN_EXECUTION:
        return RetryDecision(
            False, "uncertain execution requires reconciliation first", failure_class
        )
    mutating = bool(set(task.expanded_capabilities) & MUTATING_CAPABILITIES)
    if mutating and execution_started:
        return RetryDecision(
            False, "side-effecting attempt already started; not retry-safe", failure_class
        )
    if failure_class in _SAFE_BEFORE_START and not execution_started:
        return RetryDecision(True, "pre-start failure is retry-safe", failure_class)
    if (
        not mutating
        and not execution_started
        and failure_class
        in {
            FailureClass.NODE_DISCONNECTED,
            FailureClass.PROCESS_FAILURE,
            FailureClass.CONNECTOR_UNAVAILABLE,
        }
    ):
        return RetryDecision(True, "read-only pre-start failure may fail over", failure_class)
    return RetryDecision(False, "failure class is not automatically retryable", failure_class)
