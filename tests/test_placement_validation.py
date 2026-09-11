"""JoyMesh placement validation — facts only, no strategic reroute."""

from __future__ import annotations

from joymesh.placement_validation import validate_placement


def test_validate_ok() -> None:
    result = validate_placement(
        requirements={"requirements_id": "r1", "required_capabilities": ["test_execution"]},
        placement={
            "placement_id": "p1",
            "requirements_id": "r1",
            "selected_harness": "h1",
            "selected_runtime": "rt1",
            "executable": True,
        },
        runtime_facts={
            "worker_alive": True,
            "runtime_alive": True,
            "available_harnesses": ["h1"],
            "available_capabilities": ["test_execution"],
            "quota_available": True,
            "compatible_runtimes": ["rt1"],
        },
    )
    assert result.ok is True
    assert "runtime_validated" in result.reason_codes


def test_reject_quota_and_harness() -> None:
    result = validate_placement(
        requirements={"requirements_id": "r1", "required_capabilities": ["test_execution"]},
        placement={
            "placement_id": "p1",
            "requirements_id": "r1",
            "selected_harness": "missing",
            "selected_runtime": "rt1",
            "executable": True,
        },
        runtime_facts={
            "worker_alive": True,
            "runtime_alive": True,
            "available_harnesses": ["h1"],
            "available_capabilities": ["test_execution"],
            "quota_available": False,
            "compatible_runtimes": ["rt1"],
        },
    )
    assert result.ok is False
    assert "harness_unavailable" in result.reason_codes
    assert "quota_unavailable" in result.reason_codes


def test_non_executable_placement_is_valid_fail_closed() -> None:
    result = validate_placement(
        requirements={"requirements_id": "r1", "required_capabilities": []},
        placement={"placement_id": "p1", "requirements_id": "r1", "executable": False},
        runtime_facts={},
    )
    assert result.ok is True
    assert result.valid is True
    assert "non_executable_placement" in result.reason_codes


def test_reject_expired_and_runtime_changed() -> None:
    result = validate_placement(
        requirements={"requirements_id": "r1", "required_capabilities": ["test_execution"]},
        placement={
            "placement_id": "p1",
            "requirements_id": "r1",
            "selected_harness": "h1",
            "selected_runtime": "rt1",
            "executable": True,
            "expires_at": "2000-01-01T00:00:00+00:00",
            "runtime_snapshot_revision": "old",
        },
        runtime_facts={
            "worker_alive": True,
            "runtime_alive": True,
            "available_harnesses": ["h1"],
            "available_capabilities": ["test_execution"],
            "quota_available": True,
            "compatible_runtimes": ["rt1"],
            "runtime_snapshot_revision": "new",
        },
    )
    assert result.valid is False
    assert "placement_expired" in result.reason_codes
    assert "runtime_changed" in result.reason_codes
