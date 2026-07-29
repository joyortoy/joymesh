"""JoyMesh Runtime v1 acceptance and unit coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.runtime_v1.capabilities import expand_capabilities
from joymesh.runtime_v1.certification import ReadOnlyRepositoryProfile
from joymesh.runtime_v1.cursor import CursorConnectorRuntime
from joymesh.runtime_v1.leases import LeaseService
from joymesh.runtime_v1.models import (
    CreateRuntimeTaskBody,
    FailureClass,
    RuntimeTaskRequest,
    RuntimeTaskStatus,
)
from joymesh.runtime_v1.policy import PolicyEngine
from joymesh.runtime_v1.retry import decide_retry
from joymesh.runtime_v1.scheduler import RuntimeScheduler
from joymesh.runtime_v1.service import RuntimeService, build_ready_cursor_node
from joymesh.service import JoyMesh


def test_capability_expansion_and_unknown() -> None:
    expanded = expand_capabilities(frozenset({"repository.summarise"}))
    assert "repository.read" in expanded
    assert "filesystem.read" in expanded
    with pytest.raises(ValueError, match="unknown"):
        expand_capabilities(frozenset({"not.a.capability"}))
    with pytest.raises(ValueError, match="prohibited"):
        expand_capabilities(
            frozenset({"repository.read"}),
            prohibited=frozenset({"repository.read"}),
        )


def test_read_only_policy_blocks_shell_and_writes() -> None:
    engine = PolicyEngine()
    allowed = engine.evaluate(
        RuntimeTaskRequest(
            workspace_id="ws",
            prompt="summarise",
            requested_capabilities=frozenset(
                {"repository.read", "repository.summarise", "structured_output"}
            ),
            policy_profile="read_only",
        )
    )
    assert allowed.allowed is True
    blocked = engine.evaluate(
        RuntimeTaskRequest(
            workspace_id="ws",
            prompt="run shell",
            requested_capabilities=frozenset({"shell.execute"}),
            policy_profile="read_only",
        )
    )
    assert blocked.allowed is False
    write = engine.evaluate(
        RuntimeTaskRequest(
            workspace_id="ws",
            prompt="edit",
            requested_capabilities=frozenset({"repository.write"}),
            policy_profile="read_only",
        )
    )
    assert write.allowed is False


def test_autonomous_policy_disabled() -> None:
    engine = PolicyEngine()
    decision = engine.evaluate(
        RuntimeTaskRequest(
            workspace_id="ws",
            prompt="go",
            requested_capabilities=frozenset({"repository.read"}),
            policy_profile="autonomous",
        )
    )
    assert decision.allowed is False
    assert "disabled" in decision.reasons[0]


def test_scheduler_rejects_offline_and_uncertified_write() -> None:
    scheduler = RuntimeScheduler()
    request = RuntimeTaskRequest(
        workspace_id="ws",
        prompt="read",
        requested_capabilities=frozenset({"repository.read", "structured_output"}),
        policy_profile="read_only",
        preferred_connectors=("cursor",),
    )
    offline = build_ready_cursor_node(node_id="n1", workspace_id="ws", online=False)
    ranked = scheduler.rank_candidates(request, [offline])
    assert ranked[0].eligible is False
    assert any("offline" in reason for reason in ranked[0].rejection_reasons)

    write_request = RuntimeTaskRequest(
        workspace_id="ws",
        prompt="write",
        requested_capabilities=frozenset({"repository.write"}),
        policy_profile="read_only",
    )
    online = build_ready_cursor_node(node_id="n1", workspace_id="ws", online=True)
    ranked_write = scheduler.rank_candidates(write_request, [online])
    assert ranked_write[0].eligible is False


def test_lease_fencing_rejects_stale_token() -> None:
    leases = LeaseService(ttl_seconds=30)
    first = leases.acquire(
        task_id="t1", node_id="n1", connector_id="cursor", attempt_id="a1"
    )
    leases.release("t1", first.fencing_token)
    second = leases.acquire(
        task_id="t1", node_id="n1", connector_id="cursor", attempt_id="a2"
    )
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(PermissionError, match="stale"):
        leases.validate_event(
            task_id="t1",
            lease_id=second.lease_id,
            fencing_token=first.fencing_token,
            attempt_id="a2",
        )


def test_retry_policy_side_effects() -> None:
    from joymesh.runtime_v1.models import RuntimeTaskRecord

    task = RuntimeTaskRecord(
        task_id="t1",
        workspace_id="ws",
        user_id="u",
        prompt_digest="x",
        prompt_size=1,
        requested_capabilities=("repository.write",),
        prohibited_capabilities=(),
        expanded_capabilities=("repository.write", "filesystem.write"),
        policy_profile="developer",
        max_attempts=3,
        status=RuntimeTaskStatus.FAILED,
    )
    decision = decide_retry(
        task=task,
        failure_class=FailureClass.PROCESS_FAILURE,
        attempt_number=1,
        execution_started=True,
    )
    assert decision.retry is False
    read_task = task.model_copy(
        update={
            "expanded_capabilities": ("repository.read",),
            "requested_capabilities": ("repository.read",),
        }
    )
    safe = decide_retry(
        task=read_task,
        failure_class=FailureClass.OFFER_TIMEOUT,
        attempt_number=1,
        execution_started=False,
    )
    assert safe.retry is True


def test_generic_certification_detects_changes(tmp_path: Path) -> None:
    profile = ReadOnlyRepositoryProfile()
    workspace = profile.build_workspace(task_id="cert-1", root=tmp_path)
    ok = profile.verify_result(
        workspace,
        output=f"The name is {workspace.project_name}",
        returncode=0,
    )
    assert ok.passed is True
    (workspace.path / "README.md").write_text("# mutated\n", encoding="utf-8")
    bad = profile.verify_result(workspace, output=workspace.project_name, returncode=0)
    assert bad.passed is False
    assert any("hash" in reason or "file" in reason for reason in bad.reasons)
    profile.cleanup(workspace)


def test_cursor_connector_owns_trust_argv() -> None:
    cursor = CursorConnectorRuntime()
    argv = cursor.build_read_only_cert_argv(executable="/bin/cursor-agent", prompt="hi")
    assert argv == (
        "/bin/cursor-agent",
        "--print",
        "--output-format",
        "stream-json",
        "--trust",
        "hi",
    )
    assert "repository.read" in cursor.declared_capabilities()


async def test_scenario_a_successful_read_only_route() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws-1"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="Summarise the architecture of this repository.",
            policy_profile="read_only",
            requested_capabilities=(
                "repository.read",
                "repository.summarise",
                "structured_output",
                "streaming_output",
            ),
            preferred_connectors=("cursor",),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "cursor"
    assert task.selected_node_id == "mac"
    lease = runtime.leases.active_lease(task.task_id)
    assert lease is not None
    await runtime.mark_offered(task.task_id, lease.fencing_token)
    await runtime.ingest_node_event(
        task_id=task.task_id,
        attempt_id=lease.attempt_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        event_type="task.accepted",
        payload={},
    )
    await runtime.ingest_node_event(
        task_id=task.task_id,
        attempt_id=lease.attempt_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        event_type="task.started",
        payload={},
    )
    final = await runtime.ingest_node_event(
        task_id=task.task_id,
        attempt_id=lease.attempt_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        event_type="task.succeeded",
        payload={},
    )
    assert final.status is RuntimeTaskStatus.SUCCEEDED
    assert runtime.leases.active_lease(task.task_id).status.value == "released"


async def test_scenario_b_unsupported_write() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws-1"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="edit files",
            policy_profile="read_only",
            requested_capabilities=("repository.write",),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.REJECTED
    assert "repository.write" in (task.detail or "")


async def test_scenario_c_offline_queues() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_cursor_node(node_id="mac", workspace_id="ws-1", online=False)
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.QUEUED
    runtime.register_node(
        build_ready_cursor_node(node_id="mac", workspace_id="ws-1", online=True)
    )
    routed = await runtime.route_task(task.task_id)
    assert routed.status is RuntimeTaskStatus.LEASED


async def test_scenario_f_policy_rejection_before_schedule() -> None:
    runtime = RuntimeService()
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="shell",
            policy_profile="read_only",
            requested_capabilities=("shell.execute",),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.REJECTED
    assert runtime.store.candidates.get(task.task_id) in (None, [])


async def test_scenario_g_stale_fencing_token() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws-1"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="read",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
        ),
        user_id="user",
    )
    lease = runtime.leases.active_lease(task.task_id)
    assert lease is not None
    with pytest.raises(PermissionError, match="stale"):
        await runtime.ingest_node_event(
            task_id=task.task_id,
            attempt_id=lease.attempt_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token - 1,
            event_type="task.succeeded",
            payload={},
        )
    assert runtime.metrics.stale_event_rejections >= 1
    current = await runtime.store.get_task(task.task_id)
    assert current.status is RuntimeTaskStatus.LEASED


async def test_runtime_api_endpoints(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    await mesh.initialize()
    mesh.runtime_service.register_node(
        build_ready_cursor_node(node_id="mac", workspace_id="ws-api")
    )
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/runtime/tasks",
                json={
                    "workspace_id": "ws-api",
                    "prompt": "Summarise architecture",
                    "policy_profile": "read_only",
                    "requested_capabilities": [
                        "repository.read",
                        "repository.summarise",
                        "structured_output",
                    ],
                    "preferred_connectors": ["cursor"],
                },
            )
            assert created.status_code == 200, created.text
            task_id = created.json()["task_id"]
            assert created.json()["status"] == "leased"
            caps = await client.get("/runtime/capabilities")
            assert caps.status_code == 200
            assert any(item["capability_id"] == "repository.read" for item in caps.json())
            policies = await client.get("/runtime/policies")
            assert any(item["profile_id"] == "read_only" for item in policies.json())
            candidates = await client.get(f"/runtime/tasks/{task_id}/candidates")
            assert candidates.status_code == 200
            assert candidates.json()[0]["eligible"] is True
            health = await client.get("/runtime/health")
            assert health.json()["scheduler"] == "ok"
    await mesh.close()


def test_cursor_golden_reference_doc_exists() -> None:
    path = Path(__file__).resolve().parents[1] / "docs/runtime/cursor-golden-reference.md"
    text = path.read_text(encoding="utf-8")
    assert "node_attested" in text
    assert "remote_node" in text
    assert "--trust" in text
    assert "Ready for read-only routed tasks" in text
