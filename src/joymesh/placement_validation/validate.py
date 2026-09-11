"""Validate ContextPlacementDecision + StrategicExecutionRequirements against runtime facts.

OWNER: JoyMesh — facts only. Never chooses a replacement placement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

UTC = UTC


@dataclass(frozen=True)
class PlacementValidationResult:
    valid: bool
    reason_codes: tuple[str, ...]
    current_runtime_revision: str
    validated_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Backward-compatible alias."""

        return self.valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "ok": self.valid,
            "reason_codes": list(self.reason_codes),
            "current_runtime_revision": self.current_runtime_revision,
            "validated_at": self.validated_at.isoformat(),
            "details": dict(self.details),
        }


def validate_placement(
    *,
    requirements: Mapping[str, Any],
    placement: Mapping[str, Any],
    runtime_facts: Mapping[str, Any] | None = None,
) -> PlacementValidationResult:
    """Return structured validation facts. Does not choose another runtime."""

    facts = dict(runtime_facts or {})
    codes: list[str] = []
    now = datetime.now(UTC)
    revision = str(
        facts.get("runtime_snapshot_revision")
        or placement.get("runtime_snapshot_revision")
        or "unknown"
    )

    schema_version = placement.get("schema_version", 1)
    try:
        if int(schema_version) != 1:
            codes.append("unsupported_placement_version")
    except (TypeError, ValueError):
        codes.append("unsupported_placement_version")

    if not placement.get("placement_id"):
        codes.append("missing_placement_id")
    if requirements.get("requirements_id") and placement.get("requirements_id") != requirements.get(
        "requirements_id"
    ):
        codes.append("requirements_mismatch")

    expires_at = placement.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= now:
                codes.append("placement_expired")
        except ValueError:
            codes.append("placement_expired")

    expected_revision = placement.get("runtime_snapshot_revision")
    current_revision = facts.get("runtime_snapshot_revision")
    if expected_revision and current_revision and str(expected_revision) != str(current_revision):
        codes.append("runtime_changed")

    worker_alive = facts.get("worker_alive")
    if worker_alive is False:
        codes.append("worker_not_alive")
        codes.append("runtime_unavailable")
        codes.append("runtime_changed")
    runtime_alive = facts.get("runtime_alive")
    if runtime_alive is False:
        codes.append("runtime_not_alive")
        codes.append("runtime_unavailable")
        codes.append("runtime_changed")

    session_id = placement.get("selected_session")
    if session_id and facts.get("session_available") is False:
        codes.append("session_unavailable")

    harness = placement.get("selected_harness")
    available_harnesses = set(facts.get("available_harnesses") or ())
    if harness and available_harnesses and harness not in available_harnesses:
        codes.append("harness_unavailable")

    auth_ok = facts.get("authentication_valid")
    if auth_ok is False:
        codes.append("authentication_invalid")
        codes.append("authentication_required")

    required_caps = set(requirements.get("required_capabilities") or ())
    available_caps = set(facts.get("available_capabilities") or required_caps)
    missing = sorted(required_caps - available_caps)
    if missing:
        codes.append("capability_unavailable")
        codes.append("capability_mismatch")

    quota_ok = facts.get("quota_available")
    if quota_ok is False:
        codes.append("quota_unavailable")
        codes.append("quota_exhausted")

    if placement.get("selected_runtime") and facts.get("compatible_runtimes"):
        if placement["selected_runtime"] not in set(facts["compatible_runtimes"]):
            codes.append("runtime_incompatible")
            codes.append("runtime_changed")

    checkpoint = placement.get("selected_checkpoint")
    if checkpoint:
        valid_checkpoints = facts.get("valid_checkpoints")
        if valid_checkpoints is not None and checkpoint not in set(valid_checkpoints):
            codes.append("checkpoint_unavailable")
        if facts.get("checkpoint_valid") is False:
            codes.append("checkpoint_unavailable")

    fallback_authorized = facts.get("fallback_authorized")
    if facts.get("is_fallback") and fallback_authorized is False:
        codes.append("fallback_not_authorized")

    # Non-executable placements are valid as fail-closed decisions.
    if placement.get("executable") is False and not codes:
        return PlacementValidationResult(
            valid=True,
            reason_codes=("non_executable_placement",),
            current_runtime_revision=revision,
            validated_at=now,
            details={"executable": False},
        )

    if codes:
        return PlacementValidationResult(
            valid=False,
            reason_codes=tuple(dict.fromkeys(codes)),
            current_runtime_revision=revision,
            validated_at=now,
            details={"missing_capabilities": missing},
        )
    return PlacementValidationResult(
        valid=True,
        reason_codes=("runtime_validated",),
        current_runtime_revision=revision,
        validated_at=now,
        details={},
    )


def require_valid_placement(
    *,
    requirements: Mapping[str, Any] | None,
    placement: Mapping[str, Any] | None,
    runtime_facts: Mapping[str, Any] | None = None,
    require_placement: bool = False,
) -> PlacementValidationResult | None:
    """Validate when placement is present; optionally fail closed when missing."""

    if placement is None:
        if require_placement:
            return PlacementValidationResult(
                valid=False,
                reason_codes=("placement_required",),
                current_runtime_revision=str(
                    (runtime_facts or {}).get("runtime_snapshot_revision") or "unknown"
                ),
                validated_at=datetime.now(UTC),
            )
        return None
    return validate_placement(
        requirements=requirements or {},
        placement=placement,
        runtime_facts=runtime_facts,
    )
