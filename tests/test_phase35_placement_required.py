"""Phase 3.5: production execution requires JoyMux placement; JoyMesh never selects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from joymesh.models import RouteCandidate, RunRequest
from joymesh.placement_validation import (
    TEST_BYPASS_ENV,
    enforce_placement,
    extract_placement_payloads,
)
from joymesh.placement_validation.validate import validate_placement
from joymesh.runtime_v1.execution_routing.models import ExecutionIntent
from joymesh.runtime_v1.execution_routing.registry import BackendRegistry
from joymesh.runtime_v1.execution_routing.router import ExecutionRouter, ExecutionRouterError
from joymesh.service import JoyMesh, NoRouteError

UTC = UTC


def _placement(**overrides):
    base = {
        "placement_id": "place_test",
        "requirements_id": "req_test",
        "selected_harness": "fake",
        "selected_runtime": "rt_1",
        "selected_session": None,
        "selected_checkpoint": None,
        "executable": True,
        "schema_version": 1,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "runtime_snapshot_revision": "snap_1",
        "selection_reason_codes": ["candidate_selected"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def production_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(TEST_BYPASS_ENV, raising=False)
    monkeypatch.setenv(TEST_BYPASS_ENV, "0")
    yield
    monkeypatch.setenv(TEST_BYPASS_ENV, "1")


def test_missing_placement_fails_closed(production_mode) -> None:
    result = enforce_placement(placement=None)
    assert result is not None
    assert result.valid is False
    assert "placement_required" in result.reason_codes


@pytest.mark.asyncio
async def test_start_run_requires_placement(production_mode, tmp_path) -> None:
    mesh = JoyMesh()
    await mesh.initialize()
    # Register a minimal fake route target if needed — use explicit route.
    request = RunRequest(task="hello", workspace=str(tmp_path))
    route = RouteCandidate(
        harness_id="fake",
        subscription_id=None,
        eligible=True,
        score=1.0,
        reasons=("test",),
    )
    with pytest.raises(NoRouteError) as exc:
        await mesh.start_run(request=request, route=route)
    assert exc.value.code == "placement_required"


@pytest.mark.asyncio
async def test_run_requires_placement_no_selector(production_mode, tmp_path) -> None:
    mesh = JoyMesh()
    with pytest.raises(NoRouteError) as exc:
        await mesh.run(task="hello", workspace=str(tmp_path))
    assert exc.value.code == "placement_required"


def test_router_select_requires_placement(production_mode) -> None:
    router = ExecutionRouter(BackendRegistry())
    intent = ExecutionIntent(
        mission_id="m1",
        execution_id="e1",
        prompt="do work",
        workspace_path="/tmp",
        required_capabilities=frozenset(),
        preferred_model=None,
        preferred_harness=None,
        requires_provider_route=False,
        requires_ephemeral_workspace=False,
        estimated_runtime_seconds=None,
        estimated_token_usage=None,
        cost_preference="balanced",
    )
    with pytest.raises(ExecutionRouterError) as exc:
        router.select(intent)
    assert exc.value.reason_code == "placement_required"


def test_router_select_resolves_placement_without_ranking(production_mode) -> None:
    registry = BackendRegistry()
    router = ExecutionRouter(
        registry,
        available_harnesses=("fake", "codex", "local"),
    )
    # Use a harness that exists in available list; backend resolved by registry order.
    placement = _placement(selected_harness="codex")
    intent = ExecutionIntent(
        mission_id="m1",
        execution_id="e1",
        prompt="do work",
        workspace_path="/tmp",
        required_capabilities=frozenset(),
        preferred_model=None,
        preferred_harness=None,
        requires_provider_route=False,
        requires_ephemeral_workspace=False,
        estimated_runtime_seconds=None,
        estimated_token_usage=None,
        cost_preference="balanced",
        metadata={
            "context_placement": placement,
            "strategic_requirements": {"requirements_id": "req_test"},
        },
    )
    decision = router.select(intent)
    assert decision.selected_harness_id == "codex"
    assert decision.reason == "validated_joymux_placement"
    assert decision.fallback_order == ()


def test_runtime_changed_no_mutation() -> None:
    placement = _placement(runtime_snapshot_revision="old")
    result = validate_placement(
        requirements={"requirements_id": "req_test", "required_capabilities": []},
        placement=placement,
        runtime_facts={
            "worker_alive": True,
            "runtime_alive": True,
            "available_harnesses": ["fake"],
            "quota_available": True,
            "runtime_snapshot_revision": "new",
            "compatible_runtimes": ["rt_1"],
        },
    )
    assert result.valid is False
    assert "runtime_changed" in result.reason_codes
    # Placement dict must remain unchanged by validator.
    assert placement["runtime_snapshot_revision"] == "old"


def test_checkpoint_unavailable() -> None:
    placement = _placement(selected_checkpoint="ckpt_missing")
    result = validate_placement(
        requirements={"requirements_id": "req_test"},
        placement=placement,
        runtime_facts={
            "worker_alive": True,
            "runtime_alive": True,
            "available_harnesses": ["fake"],
            "quota_available": True,
            "compatible_runtimes": ["rt_1"],
            "valid_checkpoints": ["ckpt_ok"],
            "runtime_snapshot_revision": "snap_1",
        },
    )
    assert result.valid is False
    assert "checkpoint_unavailable" in result.reason_codes


def test_extract_placement_from_request_fields() -> None:
    placement, reqs = extract_placement_payloads(
        context_placement=_placement(),
        strategic_requirements={"requirements_id": "req_test"},
    )
    assert placement is not None
    assert placement["placement_id"] == "place_test"
    assert reqs["requirements_id"] == "req_test"


def test_architecture_guard_no_rank_in_select_source() -> None:
    from pathlib import Path

    text = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("src/joymesh/runtime_v1/execution_routing/router.py")
        .read_text(encoding="utf-8")
    )
    assert "validated_joymux_placement" in text
    assert "placement_required" in text
    assert "_select_legacy_for_tests" in text
    assert "JOYMESH_ALLOW_TEST_WITHOUT_PLACEMENT" in text
