"""Codex connector + cross-connector runtime routing coverage."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from joymesh.connectors.lifecycle_models import NodeConnectorState
from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES
from joymesh.runtime_v1.connector_protocol import ConnectorExecutionContext
from joymesh.runtime_v1.connectors import builtin_connectors, get_connector
from joymesh.runtime_v1.connectors.codex import (
    CodexConnectorRuntime,
    classify_codex_auth_status,
    parse_codex_jsonl,
)
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskRequest, RuntimeTaskStatus
from joymesh.runtime_v1.scheduler import RuntimeScheduler, SchedulerConnectorSnapshot
from joymesh.runtime_v1.service import (
    RuntimeService,
    build_ready_codex_node,
    build_ready_connector_node,
    build_ready_cursor_node,
)

_HAS_CODEX = shutil.which("codex") is not None
_LIVE = os.environ.get("JOYMESH_LIVE_CODEX") == "1"


def test_builtin_registry_includes_codex_and_cursor() -> None:
    connectors = builtin_connectors()
    assert set(connectors) >= {"cursor", "codex"}
    assert get_connector("codex").connector_id == "codex"
    assert get_connector("cursor").connector_id == "cursor"


def test_codex_declares_read_only_capabilities_only() -> None:
    codex = CodexConnectorRuntime()
    declared = codex.declared_capabilities()
    assert declared == READ_ONLY_CAPABILITIES
    assert "repository.write" not in declared
    assert "shell.execute" not in declared
    profiles = codex.certification_profiles()
    assert profiles[0].profile_id == "read_only_repository"


def test_codex_auth_classification() -> None:
    assert classify_codex_auth_status("Logged in using ChatGPT", returncode=0) == "authenticated"
    assert classify_codex_auth_status("Not logged in", returncode=0) == "unauthenticated"
    assert (
        classify_codex_auth_status("Authentication token expired; please login", returncode=1)
        == "expired"
    )
    assert CodexConnectorRuntime().classify_auth_status("logged out", returncode=0) == (
        "unauthenticated"
    )


def test_codex_jsonl_parser() -> None:
    output = '\nnoise\n{"type":"item.completed","item":{}}\n{"bad"\n{"type":"turn.completed"}\n'
    events = parse_codex_jsonl(output)
    assert [item["type"] for item in events] == ["item.completed", "turn.completed"]


def test_codex_exec_argv_is_sandbox_read_only() -> None:
    argv = CodexConnectorRuntime().build_exec_argv(
        executable="/usr/bin/codex",
        prompt="summarise",
        workspace_path="/tmp/ws",
        read_only=True,
    )
    assert argv[:4] == ("/usr/bin/codex", "exec", "--json", "--sandbox")
    assert "read-only" in argv
    assert "-C" in argv
    assert "/tmp/ws" in argv
    assert argv[-1] == "summarise"
    cert = CodexConnectorRuntime().build_read_only_cert_argv(
        executable="/usr/bin/codex",
        prompt="summarise",
        workspace=Path("/tmp/ws"),
    )
    assert "-C" in cert and "/tmp/ws" in cert


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_CODEX, reason="codex executable not installed")
async def test_codex_discovery_finds_executable_and_fingerprint() -> None:
    codex = CodexConnectorRuntime()
    result = await codex.discover(ConnectorExecutionContext(node_id="n1"))
    assert result.executable_path
    assert result.version
    assert result.fingerprint
    assert result.executable_path.endswith("codex") or "codex" in result.executable_path
    assert "codex" in result.version.lower() or result.version


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_CODEX, reason="codex executable not installed")
async def test_codex_authentication_inspection() -> None:
    codex = CodexConnectorRuntime()
    evidence = await codex.verify_authentication(ConnectorExecutionContext(node_id="n1"))
    assert evidence.status in {"authenticated", "unauthenticated", "expired"}
    assert evidence.method_id == "chatgpt"
    assert evidence.fingerprint


async def test_cross_connector_scenario_a_cursor_when_codex_unavailable() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws"))
    # Codex present on another offline/unready node should not win.
    runtime.register_node(build_ready_codex_node(node_id="linux", workspace_id="ws", online=False))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "repository.summarise", "structured_output"),
            preferred_connectors=("codex", "cursor"),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "cursor"
    assert task.selected_node_id == "mac"


async def test_cross_connector_scenario_b_codex_when_cursor_unavailable() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws", online=False))
    runtime.register_node(build_ready_codex_node(node_id="linux", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "repository.summarise", "structured_output"),
            preferred_connectors=("cursor", "codex"),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "codex"
    assert task.selected_node_id == "linux"


async def test_cross_connector_scenario_c_preference_and_score() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_connector_node(
            node_id="mac",
            workspace_id="ws",
            connector_id="cursor",
            extra_connectors={
                "codex": SchedulerConnectorSnapshot(
                    connector_id="codex",
                    installed=True,
                    readiness=NodeConnectorState.READY,
                    authenticated=True,
                    routing_enabled=True,
                    certified_capabilities=READ_ONLY_CAPABILITIES,
                    trust_level=None,
                    execution_origin=None,
                )
            },
        )
    )
    # Prefer Codex explicitly.
    preferred = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("codex", "cursor"),
        ),
        user_id="user",
    )
    assert preferred.selected_connector_id == "codex"

    # Without preference, deterministic tie-break by connector_id among equal scores.
    scheduler = RuntimeScheduler()
    request = RuntimeTaskRequest(
        workspace_id="ws",
        prompt="summarise",
        requested_capabilities=frozenset({"repository.read", "structured_output"}),
        policy_profile="read_only",
    )
    node = build_ready_connector_node(
        node_id="mac",
        workspace_id="ws",
        connector_id="cursor",
        extra_connectors={
            "codex": SchedulerConnectorSnapshot(
                connector_id="codex",
                installed=True,
                readiness=NodeConnectorState.READY,
                authenticated=True,
                routing_enabled=True,
                certified_capabilities=READ_ONLY_CAPABILITIES,
                trust_level=None,
                execution_origin=None,
            )
        },
    )
    ranked = [item for item in scheduler.rank_candidates(request, [node]) if item.eligible]
    assert [item.connector_id for item in ranked] == ["codex", "cursor"] or ranked[0].score >= (
        ranked[1].score if len(ranked) > 1 else 0
    )
    # Equal base scores: sort by connector_id ascending after score.
    assert ranked[0].connector_id == "codex"


async def test_cross_connector_scenario_d_neither_certified() -> None:
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
    )

    runtime = RuntimeService()
    uncertified = build_ready_connector_node(
        node_id="mac",
        workspace_id="ws",
        connector_id="cursor",
        extra_connectors={
            "codex": SchedulerConnectorSnapshot(
                connector_id="codex",
                installed=True,
                readiness=NodeConnectorState.CERTIFICATION_REQUIRED,
                authenticated=True,
                routing_enabled=True,
                certified_capabilities=frozenset(),
                trust_level=EvidenceTrustLevel.NODE_ATTESTED,
                execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
            )
        },
    )
    # Downgrade cursor readiness as well.
    cursor = uncertified.connectors["cursor"]
    uncertified = build_ready_connector_node(
        node_id="mac",
        workspace_id="ws",
        connector_id="cursor",
        extra_connectors={
            "cursor": SchedulerConnectorSnapshot(
                connector_id="cursor",
                installed=True,
                readiness=NodeConnectorState.CERTIFICATION_REQUIRED,
                authenticated=True,
                routing_enabled=True,
                certified_capabilities=frozenset(),
                trust_level=cursor.trust_level,
                execution_origin=cursor.execution_origin,
            ),
            "codex": SchedulerConnectorSnapshot(
                connector_id="codex",
                installed=True,
                readiness=NodeConnectorState.CERTIFICATION_REQUIRED,
                authenticated=True,
                routing_enabled=True,
                certified_capabilities=frozenset(),
                trust_level=EvidenceTrustLevel.NODE_ATTESTED,
                execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
            ),
        },
    )
    runtime.register_node(uncertified)
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.REJECTED
    candidates = runtime.store.candidates.get(task.task_id) or []
    assert candidates
    assert all(not item.eligible for item in candidates)
    assert "capabilities not certified" in (task.detail or "")


async def test_codex_runtime_lifecycle_succeeds() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_codex_node(node_id="mac", workspace_id="ws-1"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="Summarise the architecture of this repository.",
            policy_profile="read_only",
            requested_capabilities=(
                "repository.read",
                "repository.summarise",
                "structured_output",
            ),
            preferred_connectors=("codex",),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "codex"
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
        payload={"verification": {"outcome": "verified", "passed": True}, "sequence": 1},
    )
    assert final.status is RuntimeTaskStatus.SUCCEEDED
    assert runtime.metrics.tasks_by_connector.get("codex") == 1


@pytest.mark.asyncio
@pytest.mark.skipif(not _LIVE or not _HAS_CODEX, reason="Set JOYMESH_LIVE_CODEX=1 for live Codex")
async def test_live_codex_read_only_certification(tmp_path: Path) -> None:
    from joymesh.runtime_v1.certification import ReadOnlyRepositoryProfile

    codex = CodexConnectorRuntime()
    auth = await codex.verify_authentication(ConnectorExecutionContext(node_id="live"))
    if auth.status != "authenticated":
        pytest.skip(f"Codex not authenticated ({auth.status})")
    profile = ReadOnlyRepositoryProfile()
    workspace = profile.build_workspace(task_id=f"live-{uuid4().hex[:8]}", root=tmp_path)
    try:
        executable = shutil.which("codex")
        assert executable
        argv = codex.build_read_only_cert_argv(
            executable=executable,
            prompt=workspace.prompt,
            workspace=workspace.path,
        )
        import asyncio

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        verification = profile.verify_result(
            workspace,
            output=output,
            returncode=process.returncode if process.returncode is not None else 1,
        )
        assert verification.passed, verification.reasons
        assert verification.git_clean
    finally:
        profile.cleanup(workspace)
