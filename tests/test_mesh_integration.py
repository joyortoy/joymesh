from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from joymesh.adapters.codex import CodexAdapter
from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.adapters.opencode import OpenCodeAdapter
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import (
    BillingRoute,
    Capability,
    EventType,
    RunRequest,
    RunStatus,
    SubscriptionCreate,
)
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition
from tests.quota_test_utils import install_ready_quota


def adapters(
    fake_executable_factory: Callable[[str], Path],
) -> tuple[FakeHarnessAdapter, CodexAdapter, OpenCodeAdapter]:
    return (
        FakeHarnessAdapter(),
        CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True),
        OpenCodeAdapter(str(fake_executable_factory("opencode")), conformance_passed=True),
    )


def _registry(adapter_list) -> AdapterRegistry:
    return AdapterRegistry(
        adapters=adapter_list,
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )


def _mesh(database_url: str, registry: AdapterRegistry) -> JoyMesh:
    mesh = JoyMesh(database_url=database_url, registry=registry)
    install_ready_quota(mesh)
    return mesh


async def register_profiles(mesh: JoyMesh) -> None:
    for harness_id in ("fake", "codex", "opencode"):
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id=harness_id,
                name=f"{harness_id} test profile",
                quota_known=True,
                monthly_limit=100,
                max_concurrency=2,
                billing_route=(
                    BillingRoute.LOCAL if harness_id == "fake" else BillingRoute.SUBSCRIPTION
                ),
                cost_weight=0 if harness_id == "fake" else 1,
            )
        )


async def test_same_run_request_works_across_adapters(
    fake_executable_factory, tmp_path: Path
) -> None:
    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cross.db'}",
        registry=_registry(adapters(fake_executable_factory)),
    )
    await mesh.initialize()
    await register_profiles(mesh)
    try:
        request = RunRequest(task="NORMAL", workspace=str(tmp_path))
        categories = []
        for harness_id in ("fake", "codex", "opencode"):
            route = await mesh.resolve_route(request=request, preferred_harness=harness_id)
            run = await mesh.start_run(request=request, route=route)
            result = await mesh.wait_for_run(run.id)
            events = await mesh.events(run.id)
            assert result.status is RunStatus.COMPLETED
            assert result.harness_id == harness_id
            categories.append(
                {event.type for event in events}
                & {EventType.HARNESS_OUTPUT, EventType.RUN_COMPLETED}
            )
        assert categories == [{EventType.HARNESS_OUTPUT, EventType.RUN_COMPLETED}] * 3
    finally:
        await mesh.close()


async def test_routing_rejections_penalties_and_alternative(
    fake_executable_factory, tmp_path: Path
) -> None:
    registry = _registry(adapters(fake_executable_factory))
    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}",
        registry=registry,
    )
    await mesh.initialize()
    try:
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="codex",
                name="Exhausted",
                quota_known=True,
                monthly_limit=10,
                used_amount=9,
                quota_reserve=1,
            )
        )
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="opencode",
                name="Unknown quota",
                quota_known=False,
                cost_weight=0,
            )
        )
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="fake",
                name="test fake",
                billing_route=BillingRoute.LOCAL,
                quota_known=True,
                cost_weight=0,
            )
        )
        request = RunRequest(
            task="route",
            workspace=str(tmp_path),
            required_capabilities=frozenset({Capability.TOOL_USE}),
        )
        first = await mesh.preview_routes(
            task=request.task,
            workspace=request.workspace,
            required_capabilities=request.required_capabilities,
        )
        second = await mesh.preview_routes(
            task=request.task,
            workspace=request.workspace,
            required_capabilities=request.required_capabilities,
        )
        assert first == second
        fake_candidate = next(
            candidate for candidate in first.candidates if candidate.harness_id == "fake"
        )
        assert any("missing capabilities" in reason for reason in fake_candidate.reasons)
        codex_reasons = next(
            candidate.reasons for candidate in first.candidates if candidate.harness_id == "codex"
        )
        assert "configured quota reserve reached" in codex_reasons
        assert any(
            "unknown quota uncertainty penalty" in reason
            for candidate in first.candidates
            if candidate.harness_id == "opencode"
            for reason in candidate.reasons
        )
        assert first.selected and first.selected.harness_id == "opencode"
    finally:
        await mesh.close()


async def test_unavailable_and_concurrent_harnesses_are_rejected(
    fake_executable_factory, tmp_path: Path
) -> None:
    codex = CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True)
    opencode = OpenCodeAdapter(str(fake_executable_factory("opencode")), conformance_passed=True)
    unavailable = OpenCodeAdapter(str(tmp_path / "missing-opencode"), conformance_passed=True)
    unavailable.executable_name = str(tmp_path / "missing-opencode")
    unavailable_registry = _registry([FakeHarnessAdapter(), unavailable])
    unavailable_mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unavailable.db'}",
        registry=unavailable_registry,
    )
    await unavailable_mesh.initialize()
    preview = await unavailable_mesh.preview_routes(task="x", workspace=tmp_path)
    rejected = next(
        candidate for candidate in preview.candidates if candidate.harness_id == "opencode"
    )
    assert not rejected.eligible
    assert "harness unavailable" in rejected.reasons
    await unavailable_mesh.close()

    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'limits.db'}",
        registry=_registry([codex, opencode]),
    )
    await mesh.initialize()
    try:
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="codex",
                name="single Codex slot",
                quota_known=True,
                max_concurrency=1,
            )
        )
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="opencode",
                name="OpenCode alternative",
                quota_known=True,
            )
        )
        request = RunRequest(task="CONCURRENT", workspace=str(tmp_path))
        codex_route = await mesh.resolve_route(request=request, preferred_harness="codex")
        active = await mesh.start_run(request=request, route=codex_route)
        for _ in range(100):
            state = await mesh.inspect_run(active.id)
            if state and state.status is RunStatus.RUNNING:
                break
            await asyncio.sleep(0.01)
        limited = await mesh.preview_routes(
            task=request.task,
            workspace=request.workspace,
            preferred_harness="codex",
        )
        codex_candidate = next(
            candidate for candidate in limited.candidates if candidate.harness_id == "codex"
        )
        assert not codex_candidate.eligible
        assert "concurrency limit reached (1/1)" in codex_candidate.reasons
        assert limited.selected and limited.selected.harness_id == "opencode"
        await mesh.cancel(active.id)
    finally:
        await mesh.close()


async def test_rate_limit_requires_approved_linked_fallback(
    fake_executable_factory, tmp_path: Path
) -> None:
    registry = _registry(
        [
            CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True),
            OpenCodeAdapter(str(fake_executable_factory("opencode")), conformance_passed=True),
        ]
    )
    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fallback.db'}",
        registry=registry,
    )
    await mesh.initialize()
    try:
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="codex",
                name="Codex subscription",
                billing_route=BillingRoute.SUBSCRIPTION,
                quota_known=True,
                monthly_limit=100,
            )
        )
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="opencode",
                name="Paid OpenCode API",
                billing_route=BillingRoute.API,
                quota_known=True,
                monthly_limit=100,
                requires_paid_approval=True,
            )
        )
        request = RunRequest(task="RATE_LIMIT", workspace=str(tmp_path))
        route = await mesh.resolve_route(request=request, preferred_harness="codex")
        original = await mesh.start_run(request=request, route=route)
        failed = await mesh.wait_for_run(original.id)
        events = await mesh.events(original.id)
        proposal = await mesh.fallback_for_run(original.id)

        assert failed.status is RunStatus.FAILED
        assert EventType.RATE_LIMIT_ENCOUNTERED in {event.type for event in events}
        assert EventType.APPROVAL_REQUESTED in {event.type for event in events}
        assert proposal and proposal.route.harness_id == "opencode"
        assert proposal.requires_approval and not proposal.approved
        assert proposal.continuation_run_id is None

        continuation = await mesh.approve_fallback(proposal.id)
        result = await mesh.wait_for_run(continuation.id)
        refreshed = await mesh.fallback_for_run(original.id)
        assert result.status is RunStatus.COMPLETED
        assert continuation.task_context_id == original.task_context_id
        assert continuation.continuation_of_run_id == original.id
        assert result.native_session_id != failed.native_session_id
        assert refreshed and refreshed.approved
    finally:
        await mesh.close()


async def test_concurrent_runs_are_isolated(fake_executable_factory, tmp_path: Path) -> None:
    registry = _registry(
        [
            CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True),
            OpenCodeAdapter(str(fake_executable_factory("opencode")), conformance_passed=True),
        ]
    )
    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}",
        registry=registry,
    )
    await mesh.initialize()
    await register_profiles(mesh)
    try:
        request = RunRequest(task="CONCURRENT", workspace=str(tmp_path))
        codex_route = await mesh.resolve_route(request=request, preferred_harness="codex")
        opencode_route = await mesh.resolve_route(request=request, preferred_harness="opencode")
        codex_run, opencode_run = await asyncio.gather(
            mesh.start_run(request=request, route=codex_route),
            mesh.start_run(request=request, route=opencode_route),
        )
        for _ in range(100):
            left = await mesh.inspect_run(codex_run.id)
            right = await mesh.inspect_run(opencode_run.id)
            if (
                left
                and right
                and left.native_session_id
                and right.native_session_id
                and await mesh.usage(run_id=left.id)
                and await mesh.usage(run_id=right.id)
            ):
                break
            await asyncio.sleep(0.01)
        assert left and right
        assert left.process_id != right.process_id
        assert left.native_session_id != right.native_session_id
        assert all(event.run_id == left.id for event in await mesh.events(left.id))
        assert all(event.run_id == right.id for event in await mesh.events(right.id))

        await mesh.cancel(left.id)
        assert (await mesh.inspect_run(right.id)).status is RunStatus.RUNNING
        await mesh.cancel(right.id)
        assert (await mesh.wait_for_run(left.id)).status is RunStatus.CANCELLED
        assert (await mesh.wait_for_run(right.id)).status is RunStatus.CANCELLED
        assert await mesh.runtime.active_run_ids() == ()
    finally:
        await mesh.close()


async def test_sdk_first_acceptance(fake_executable_factory, tmp_path: Path) -> None:
    """Primary acceptance test: no CLI or API helpers are used."""

    registry = _registry(
        [
            CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True),
            OpenCodeAdapter(str(fake_executable_factory("opencode")), conformance_passed=True),
        ]
    )
    mesh = _mesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}",
        registry=registry,
    )
    await mesh.initialize()
    try:
        detected = await mesh.detect_harnesses()
        assert {item.manifest.harness_id for item in detected} == {
            "codex",
            "opencode",
        }
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="codex",
                name="Codex",
                quota_known=True,
                monthly_limit=100,
            )
        )
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="opencode",
                name="Approved paid fallback",
                billing_route=BillingRoute.API,
                quota_known=True,
                monthly_limit=100,
                requires_paid_approval=True,
            )
        )
        request = RunRequest(task="NORMAL", workspace=str(tmp_path))
        preview = await mesh.preview_routes(task=request.task, workspace=request.workspace)
        assert preview.selected
        route = await mesh.resolve_route(request=request, preferred_harness="codex")
        run = await mesh.start_run(request=request, route=route)
        streamed = [event async for event in mesh.stream_events(run.id)]
        assert (await mesh.wait_for_run(run.id)).status is RunStatus.COMPLETED
        assert streamed and await mesh.usage(run_id=run.id)

        failing_request = request.model_copy(update={"task": "RATE_LIMIT"})
        failing_route = await mesh.resolve_route(request=failing_request, preferred_harness="codex")
        failed = await mesh.start_run(request=failing_request, route=failing_route)
        assert (await mesh.wait_for_run(failed.id)).status is RunStatus.FAILED
        proposal = await mesh.fallback_for_run(failed.id)
        assert proposal and proposal.requires_approval
        continuation = await mesh.approve_fallback(proposal.id)
        assert (await mesh.wait_for_run(continuation.id)).status is RunStatus.COMPLETED
    finally:
        await mesh.close()
