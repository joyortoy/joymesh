"""Deterministic harness resolution for runs — no silent fallbacks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from joymesh.adapters.fake import REMOVED_PRODUCTION_HARNESS_IDS
from joymesh.config import HarnessPreferences
from joymesh.models import Capability


class HarnessSelectionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        remediation: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation
        self.details = details or {}


@dataclass(frozen=True)
class HarnessResolution:
    harness_id: str
    reason: str


@dataclass(frozen=True)
class CapabilityMismatch:
    """Structured capability incompatibility — separate from readiness."""

    harness_id: str
    required_capabilities: tuple[str, ...]
    supported_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "harness_id": self.harness_id,
            "required_capabilities": list(self.required_capabilities),
            "supported_capabilities": list(self.supported_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
        }


def find_capability_mismatch(
    *,
    harness_id: str,
    supported: frozenset[Capability],
    required: frozenset[Capability],
) -> CapabilityMismatch | None:
    """Return a mismatch when required capabilities are not a subset of supported."""

    missing = required - supported
    if not missing:
        return None
    return CapabilityMismatch(
        harness_id=harness_id,
        required_capabilities=tuple(sorted(item.value for item in required)),
        supported_capabilities=tuple(sorted(item.value for item in supported)),
        missing_capabilities=tuple(sorted(item.value for item in missing)),
    )


def resolve_harness(
    *,
    prefs: HarnessPreferences,
    ready_enabled: Sequence[str],
    override: str | None = None,
    preferred: str | None = None,
    interactive: bool = False,
    prompt_fn: Callable[[Sequence[str]], str] | None = None,
    allow_disabled_override: bool = False,
    allow_test_harnesses: bool = False,
    known_ids: Sequence[str] | None = None,
) -> HarnessResolution:
    """Resolve harness with explicit precedence.

    explicit override → preferred/mission → configured default →
    single ready enabled → selection required / interactive prompt
    """

    ready = list(ready_enabled)
    if not allow_test_harnesses:
        ready = [
            item
            for item in ready
            if item not in REMOVED_PRODUCTION_HARNESS_IDS
        ]
    known = set(known_ids or ()) | set(ready) | set(prefs.enabled) | set(prefs.custom)

    def _validate(harness_id: str, *, require_enabled: bool) -> str:
        if harness_id in REMOVED_PRODUCTION_HARNESS_IDS and not allow_test_harnesses:
            raise HarnessSelectionError(
                "harness_removed",
                f"harness removed from production: {harness_id}",
                remediation="Run `joymesh harness select` or add a custom harness.",
            )
        if known and harness_id not in known and harness_id not in prefs.custom:
            raise HarnessSelectionError(
                "unknown_harness",
                f"unknown harness: {harness_id}",
            )
        if require_enabled and prefs.enabled and harness_id not in prefs.enabled:
            if not allow_disabled_override:
                raise HarnessSelectionError(
                    "harness_disabled",
                    f"harness is not enabled: {harness_id}",
                    remediation="Enable it with `joymesh harness enable`.",
                )
        if harness_id not in ready:
            raise HarnessSelectionError(
                "harness_not_ready",
                f"harness is not ready: {harness_id}",
                remediation="Run `joymesh harness doctor` or validate a custom harness.",
            )
        return harness_id

    if override:
        chosen = _validate(override, require_enabled=not allow_disabled_override)
        return HarnessResolution(chosen, "per_run_override")

    if preferred:
        chosen = _validate(preferred, require_enabled=True)
        return HarnessResolution(chosen, "preferred_harness")

    if prefs.selection_required and prefs.default is None and not interactive:
        raise HarnessSelectionError(
            "harness_selection_required",
            "harness selection required after legacy configuration migration",
            remediation="Run `joymesh harness select`.",
        )

    if prefs.default:
        chosen = _validate(prefs.default, require_enabled=True)
        return HarnessResolution(chosen, "configured_default")

    enabled_ready = [item for item in ready if not prefs.enabled or item in prefs.enabled]
    if not enabled_ready:
        raise HarnessSelectionError(
            "no_ready_harness",
            "no ready harness available",
            remediation="Run `joymesh harness select` or add a custom harness.",
        )
    if len(enabled_ready) == 1:
        return HarnessResolution(enabled_ready[0], "single_enabled_ready")

    if interactive and prompt_fn is not None:
        chosen = prompt_fn(enabled_ready)
        return HarnessResolution(
            _validate(chosen, require_enabled=True),
            "interactive_selection",
        )

    raise HarnessSelectionError(
        "harness_selection_required",
        "multiple ready harnesses enabled and no default configured",
        remediation="Set a default with `joymesh harness default <id>` or pass `--harness`.",
    )
