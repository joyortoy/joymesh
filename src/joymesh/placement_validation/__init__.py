"""JoyMesh runtime validation for JoyMux context placement decisions.

JoyMesh validates facts only. It never strategically reroutes or completes missions.
"""

from joymesh.placement_validation.enforce import (
    COMPAT_SUNSET,
    TEST_BYPASS_ENV,
    decision_from_placement,
    enforce_placement,
    extract_placement_payloads,
    test_without_placement_allowed,
)
from joymesh.placement_validation.validate import (
    PlacementValidationResult,
    require_valid_placement,
    validate_placement,
)

__all__ = [
    "COMPAT_SUNSET",
    "TEST_BYPASS_ENV",
    "PlacementValidationResult",
    "decision_from_placement",
    "enforce_placement",
    "extract_placement_payloads",
    "require_valid_placement",
    "test_without_placement_allowed",
    "validate_placement",
]
