"""Production placement enforcement for JoyMesh submit paths.

OWNER: JoyMesh (validation/enforcement only).
JoyMesh never selects a replacement placement. Missing placement fails closed
unless an explicit test opt-in is set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from joymesh.placement_validation.validate import (
    PlacementValidationResult,
    require_valid_placement,
)

TEST_BYPASS_ENV = "JOYMESH_ALLOW_TEST_WITHOUT_PLACEMENT"
# Compatibility sunset: remove test bypass and optional placement typing after
# JoyCTL/hosted callers always attach ContextPlacementDecision (target: next
# ownership audit milestone after Phase 3.5).
COMPAT_SUNSET = "phase3.5-placement-required-v1"


def test_without_placement_allowed() -> bool:
    return os.environ.get(TEST_BYPASS_ENV) == "1"


def extract_placement_payloads(
    *,
    directive: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    context_placement: Mapping[str, Any] | None = None,
    strategic_requirements: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    placement = dict(context_placement) if context_placement else None
    requirements = dict(strategic_requirements) if strategic_requirements else None
    if metadata:
        placement = placement or _as_dict(
            metadata.get("context_placement") or metadata.get("placement")
        )
        requirements = requirements or _as_dict(
            metadata.get("strategic_requirements") or metadata.get("requirements")
        )
    if directive:
        placement = placement or _as_dict(
            directive.get("context_placement") or directive.get("placement")
        )
        requirements = requirements or _as_dict(
            directive.get("strategic_requirements") or directive.get("requirements")
        )
    return placement, requirements


def _as_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


def enforce_placement(
    *,
    placement: Mapping[str, Any] | None,
    requirements: Mapping[str, Any] | None = None,
    runtime_facts: Mapping[str, Any] | None = None,
    require: bool | None = None,
) -> PlacementValidationResult | None:
    """Validate placement. Production requires placement; tests may opt out."""

    must_require = (not test_without_placement_allowed()) if require is None else require
    if placement is None:
        if must_require:
            return require_valid_placement(
                requirements=requirements,
                placement=None,
                runtime_facts=runtime_facts,
                require_placement=True,
            )
        return None
    return require_valid_placement(
        requirements=requirements or {"requirements_id": placement.get("requirements_id")},
        placement=placement,
        runtime_facts=runtime_facts,
        require_placement=True,
    )


def decision_from_placement(
    *,
    execution_id: str,
    placement: Mapping[str, Any],
    default_backend_id: str = "local",
) -> dict[str, Any]:
    """Resolve already-selected IDs from JoyMux placement (no ranking)."""

    harness = str(placement.get("selected_harness") or "")
    runtime = placement.get("selected_runtime")
    backend = str(
        placement.get("selected_backend_id")
        or placement.get("extras", {}).get("backend_id")
        or default_backend_id
    )
    return {
        "execution_id": execution_id,
        "selected_backend_id": backend,
        "selected_harness_id": harness,
        "selected_connector_id": placement.get("selected_harness"),
        "selected_model_id": placement.get("model"),
        "reason": "validated_joymux_placement",
        "fallback_order": (),
        "provider_routing_required": False,
        "placement_id": placement.get("placement_id"),
        "selected_runtime": runtime,
        "selected_session": placement.get("selected_session"),
        "selected_checkpoint": placement.get("selected_checkpoint"),
        "selection_reason_codes": list(placement.get("selection_reason_codes") or ()),
    }
