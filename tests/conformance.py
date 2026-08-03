"""Reusable adapter conformance assertions.

Every supported adapter is parametrized through this suite. New adapters must
reuse these tests instead of copying adapter-specific equivalents.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from joymesh.adapters.base import HarnessAdapter
from joymesh.models import (
    EventType,
    FailureKind,
    HarnessAvailability,
    RunRequest,
    RunStatus,
    SubscriptionCreate,
    SupportStatus,
)
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh


async def assert_static_conformance(adapter: HarnessAdapter, workspace: Path) -> None:
    descriptor = await adapter.detect()
    assert descriptor.availability is HarnessAvailability.AVAILABLE
    assert descriptor.version
    assert descriptor.support_status is SupportStatus.SUPPORTED
    assert descriptor.manifest.harness_id
    assert descriptor.manifest.capabilities
    assert descriptor.manifest.max_concurrency >= 1

    os.environ["JOYMESH_CONFORMANCE_SECRET"] = "must-not-leak"
    request = RunRequest(task="conformance", workspace=str(workspace))
    launch = adapter.build_launch_spec(request)
    assert launch.argv
    assert launch.cwd == str(workspace)
    assert "JOYMESH_CONFORMANCE_SECRET" not in launch.env

    if descriptor.manifest.supports_resume:
        resumed = adapter.build_launch_spec(
            request.model_copy(update={"resume_session_id": "native-session-1"})
        )
        assert "native-session-1" in resumed.argv

    for capability in descriptor.manifest.capabilities:
        adapter.require_feature(capability)


async def assert_runtime_conformance(
    adapter: HarnessAdapter, workspace: Path, database_url: str
) -> None:
    from joymesh.harnesses.catalogue import builtin_catalogue
    from tests.fixtures.fake_harness_definition import fake_harness_definition

    definitions = builtin_catalogue()
    if adapter.manifest.harness_id == "fake":
        definitions = (fake_harness_definition(), *definitions)
    mesh = JoyMesh(
        database_url=database_url,
        registry=AdapterRegistry([adapter], definitions=definitions),
    )
    from tests.quota_test_utils import install_ready_quota

    install_ready_quota(mesh)
    await mesh.initialize()
    try:
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id=adapter.manifest.harness_id,
                name="Conformance profile",
                quota_known=True,
            )
        )
        request = RunRequest(task="NORMAL", workspace=str(workspace))
        route = await mesh.resolve_route(
            request=request, preferred_harness=adapter.manifest.harness_id
        )
        run = await mesh.start_run(request=request, route=route)
        events = [event async for event in mesh.stream_events(run.id)]
        completed = await mesh.wait_for_run(run.id)
        usage = await mesh.usage(run_id=run.id)

        assert completed.status is RunStatus.COMPLETED
        assert completed.native_session_id
        assert completed.process_id
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert all(event.run_id == run.id for event in events)
        assert EventType.HARNESS_OUTPUT in {event.type for event in events}
        assert EventType.RUN_COMPLETED in {event.type for event in events}
        assert usage and usage[0].input_tokens > 0
        assert all("supersecretvalue" not in (event.message or "") for event in events)

        if adapter.manifest.supports_resume:
            resumed_request = request.model_copy(
                update={"resume_session_id": completed.native_session_id}
            )
            resumed_route = await mesh.resolve_route(
                request=resumed_request,
                preferred_harness=adapter.manifest.harness_id,
            )
            resumed = await mesh.start_run(request=resumed_request, route=resumed_route)
            resumed_result = await mesh.wait_for_run(resumed.id)
            assert resumed_result.native_session_id == completed.native_session_id

        failed_request = request.model_copy(update={"task": "FAIL"})
        failed = await mesh.start_run(request=failed_request, route=route)
        assert (await mesh.wait_for_run(failed.id)).status is RunStatus.FAILED

        timeout_request = request.model_copy(update={"task": "SLOW", "timeout_seconds": 0.05})
        timed_out = await mesh.start_run(request=timeout_request, route=route)
        assert (await mesh.wait_for_run(timed_out.id)).status is RunStatus.TIMED_OUT

        tree_request = request.model_copy(update={"task": "SPAWN_CHILD"})
        tree_run = await mesh.start_run(request=tree_request, route=route)
        child_pid = None
        for _ in range(100):
            for event in await mesh.events(tree_run.id):
                if event.message and event.message.startswith("child_pid="):
                    child_pid = int(event.message.partition("=")[2])
            if child_pid:
                break
            await asyncio.sleep(0.01)
        assert child_pid is not None
        active = await mesh.inspect_run(tree_run.id)
        assert active and active.process_id
        parent_pid = active.process_id
        await mesh.cancel(tree_run.id)
        assert (await mesh.wait_for_run(tree_run.id)).status is RunStatus.CANCELLED
        for _ in range(100):
            if not _pid_exists(parent_pid) and not _pid_exists(child_pid):
                break
            await asyncio.sleep(0.01)
        assert not _pid_exists(parent_pid)
        assert not _pid_exists(child_pid)

        failure = adapter.classify_failure(exit_code=29, output="429 rate limit")
        assert failure.kind is FailureKind.RATE_LIMIT
        quota = adapter.classify_failure(exit_code=1, output="quota exhausted")
        assert quota.kind is FailureKind.QUOTA_EXHAUSTED
    finally:
        await mesh.close()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
