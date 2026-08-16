"""Launch-time validation of JoyCLI execution directives."""

from __future__ import annotations

from joymesh.config import load_user_config
from joymesh.diagnostics.codes import ProviderDiagnosticCode, from_availability
from joymesh.execution.directive import ExecutionDirective
from joymesh.models import utc_now
from joymesh.quota.contracts import AUTO_BLOCKED_AVAILABILITIES, HarnessAvailability
from joymesh.registry import AdapterRegistry
from joymesh.runtime_snapshot.contracts import RuntimeValidationCode
from joymesh.runtime_snapshot.service import RuntimeLaunchError, RuntimeSnapshotService


class DirectiveValidationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: ProviderDiagnosticCode,
        remediation: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.details = details or {}


async def validate_directive(
    directive: ExecutionDirective,
    *,
    registry: AdapterRegistry,
    runtime_snapshots: RuntimeSnapshotService,
    harness_enabled: bool | None = None,
    is_fallback: bool = False,
) -> None:
    """Validate a JoyCLI directive. Never recalculates routing policy."""

    if directive.expires_at <= utc_now():
        raise DirectiveValidationError(
            "execution directive has expired",
            code=ProviderDiagnosticCode.DIRECTIVE_EXPIRED,
            remediation="Request a fresh routing decision from JoyCLI.",
            details={"expires_at": directive.expires_at.isoformat()},
        )

    if not directive.authorization_reference.strip():
        raise DirectiveValidationError(
            "authorization reference is required",
            code=ProviderDiagnosticCode.FALLBACK_NOT_AUTHORIZED
            if is_fallback
            else ProviderDiagnosticCode.UNKNOWN_FAILURE,
            details={"execution_id": directive.execution_id},
        )

    if is_fallback:
        if directive.selected_harness not in directive.allowed_fallbacks:
            raise DirectiveValidationError(
                "selected harness is not an authorized fallback",
                code=ProviderDiagnosticCode.FALLBACK_NOT_AUTHORIZED,
                details={
                    "selected_harness": directive.selected_harness,
                    "allowed_fallbacks": list(directive.allowed_fallbacks),
                },
            )
        if (
            directive.fallback_authorization_references
            and directive.authorization_reference
            not in directive.fallback_authorization_references
        ):
            raise DirectiveValidationError(
                "fallback authorization reference is not approved",
                code=ProviderDiagnosticCode.FALLBACK_NOT_AUTHORIZED,
                details={"authorization_reference": directive.authorization_reference},
            )

    enabled = harness_enabled
    if enabled is None:
        prefs = load_user_config().harnesses
        if prefs.enabled:
            enabled = directive.selected_harness in prefs.enabled
        else:
            enabled = True
    if not enabled:
        raise DirectiveValidationError(
            f"harness disabled: {directive.selected_harness}",
            code=ProviderDiagnosticCode.PROVIDER_UNAVAILABLE,
            remediation="Enable the harness before launching.",
            details={"harness_id": directive.selected_harness},
        )

    try:
        registry.get(directive.selected_harness)
    except KeyError as exc:
        raise DirectiveValidationError(
            f"unknown harness: {directive.selected_harness}",
            code=ProviderDiagnosticCode.PROVIDER_UNAVAILABLE,
            details={"harness_id": directive.selected_harness},
        ) from exc

    try:
        await runtime_snapshots.revalidate_for_launch(
            directive.selected_harness,
            required_capabilities=directive.required_capabilities,
        )
    except RuntimeLaunchError as exc:
        mapped = {
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
        }.get(exc.code, ProviderDiagnosticCode.UNKNOWN_FAILURE)
        raise DirectiveValidationError(
            str(exc),
            code=mapped,
            remediation=exc.remediation,
            details=exc.details,
        ) from exc

    entry = await runtime_snapshots.harness_snapshot(
        directive.selected_harness, refresh=False
    )
    if entry.availability in AUTO_BLOCKED_AVAILABILITIES:
        raise DirectiveValidationError(
            f"harness unavailable: {entry.availability.value}",
            code=from_availability(entry.availability),
            details={"availability": entry.availability.value},
        )
    if entry.availability is HarnessAvailability.UNKNOWN:
        # Unknown is allowed only when JoyCLI explicitly authorized the harness.
        return
