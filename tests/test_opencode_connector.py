"""OpenCode connector + connector-neutral live-test coverage."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES
from joymesh.runtime_v1.connector_protocol import ConnectorExecutionContext
from joymesh.runtime_v1.connectors import (
    assert_connector_conforms,
    builtin_connectors,
    get_connector,
    reset_builtin_connectors_for_tests,
)
from joymesh.runtime_v1.connectors.live_test import (
    render_live_test_result,
    run_connector_live_test,
)
from joymesh.runtime_v1.connectors.opencode import (
    OpenCodeConnectorRuntime,
    classify_opencode_auth_status,
    parse_opencode_jsonl,
)
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskStatus
from joymesh.runtime_v1.service import (
    RuntimeService,
    build_ready_codex_node,
    build_ready_connector_node,
    build_ready_opencode_node,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_opencode.py"


@pytest.fixture
def fake_opencode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "opencode"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JOYMESH_FAKE_OPENCODE_MODE", "success")
    reset_builtin_connectors_for_tests()
    return target


def test_builtin_registry_includes_opencode() -> None:
    reset_builtin_connectors_for_tests()
    connectors = builtin_connectors()
    assert set(connectors) >= {"cursor", "codex", "opencode"}
    assert_connector_conforms(connectors["opencode"])


def test_opencode_declares_read_only_only() -> None:
    assert OpenCodeConnectorRuntime().declared_capabilities() == READ_ONLY_CAPABILITIES
    assert "repository.write" not in OpenCodeConnectorRuntime().declared_capabilities()


def test_opencode_auth_classification() -> None:
    assert (
        classify_opencode_auth_status("anthropic  configured\nopenai  configured\n", returncode=0)
        == "authenticated"
    )
    assert (
        classify_opencode_auth_status("No credentials configured", returncode=0)
        == "unauthenticated"
    )
    assert (
        classify_opencode_auth_status(
            "Credentials ~/.local/share/opencode/auth.json\n0 credentials\n",
            returncode=0,
        )
        == "unauthenticated"
    )
    assert classify_opencode_auth_status("quota exhausted", returncode=1) == "quota_exhausted"
    assert classify_opencode_auth_status("rate limit exceeded", returncode=1) == "plan_restricted"


def test_opencode_event_parser_normalizes_native_types() -> None:
    output = "\n".join(
        [
            '{"type":"step_start","sessionID":"ses_1"}',
            '{"type":"tool_use","part":{"tool":"read","state":{"status":"completed"}}}',
            '{"type":"text","part":{"text":"hello"}}',
            '{"type":"step_finish","part":{"tokens":{"input":1}}}',
        ]
    )
    events = parse_opencode_jsonl(output)
    assert [item["event_type"] for item in events] == [
        "run.started",
        "tool.call",
        "message.output",
        "run.completed",
    ]


def test_opencode_event_parser_maps_unavailable_tool_denials() -> None:
    output = "\n".join(
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "invalid",
                    "state": {
                        "status": "completed",
                        "output": (
                            f"Model tried to call unavailable tool '{tool}'. Available tools: read"
                        ),
                    },
                },
            }
        )
        for tool in ("bash", "edit", "webfetch")
    )
    events = parse_opencode_jsonl(output)
    assert [item["event_type"] for item in events] == [
        "permission.denied",
        "permission.denied",
        "permission.denied",
    ]
    assert [item["reason_code"] for item in events] == [
        "permission_denied_shell",
        "permission_denied_edit",
        "permission_denied_network",
    ]
    assert [item["tool"] for item in events] == ["bash", "edit", "webfetch"]


def test_opencode_event_parser_maps_network_tool_withheld_text() -> None:
    output = json.dumps(
        {
            "type": "text",
            "part": {
                "text": (
                    "I don't have access to a webfetch or websearch tool — "
                    "neither is listed among my available tools."
                )
            },
        }
    )
    events = parse_opencode_jsonl(output)
    assert any(
        item["event_type"] == "permission.denied"
        and item.get("reason_code") == "permission_denied_network"
        for item in events
    )


def test_opencode_read_only_argv_and_env(fake_opencode: Path) -> None:
    connector = OpenCodeConnectorRuntime()
    argv = connector.build_read_only_cert_argv(
        executable=str(fake_opencode),
        prompt="summarise",
        workspace=Path("/tmp/ws"),
    )
    assert argv[:4] == (str(fake_opencode), "run", "--format", "json")
    assert "--dir" in argv and "/tmp/ws" in argv
    env = connector.execution_environment(read_only=True)
    assert "OPENCODE_PERMISSION" in env
    assert "deny" in env["OPENCODE_PERMISSION"]


@pytest.mark.asyncio
async def test_opencode_discovery_with_fake(fake_opencode: Path) -> None:
    connector = OpenCodeConnectorRuntime()
    result = await connector.discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is True
    assert result.version
    assert result.executable_path == str(fake_opencode)


@pytest.mark.asyncio
async def test_opencode_broken_launcher(
    fake_opencode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_OPENCODE_MODE", "broken")
    connector = OpenCodeConnectorRuntime()
    result = await connector.discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is False
    assert result.reason_code == "broken_executable"


@pytest.mark.asyncio
async def test_opencode_missing_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    connector = OpenCodeConnectorRuntime()
    result = await connector.discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is False
    assert result.reason_code == "executable_not_found"


@pytest.mark.asyncio
async def test_shared_live_test_with_fake_opencode(
    fake_opencode: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_OPENCODE_MODE", "success")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    connector = get_connector("opencode")
    result = await run_connector_live_test(
        connector=connector,
        workspace=workspace,
        prompt="Read README.md and summarise without edits",
        timeout_seconds=10,
    )
    assert result.connector_id == "opencode"
    assert result.installed is True
    assert result.usable is True
    assert result.authenticated is True
    assert result.certification_passed is True
    assert result.exit_code == 0
    assert any(event.event_type == "run.completed" for event in result.events)
    rendered = render_live_test_result(result)
    assert "opencode" in rendered.lower()


@pytest.mark.asyncio
async def test_shared_live_test_unauthenticated(
    fake_opencode: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_OPENCODE_MODE", "unauthenticated")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_connector_live_test(
        connector=get_connector("opencode"),
        workspace=workspace,
        prompt="x",
        timeout_seconds=5,
    )
    assert result.authenticated is False
    assert result.certification_passed is False
    assert any(item.reason_code == "connector_auth_required" for item in result.notices)


@pytest.mark.asyncio
async def test_cross_connector_protocol_loop(tmp_path: Path) -> None:
    """Same public protocol surface for all built-in connectors (no ID branches)."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    prompt = "Read README.md without modifying files"
    for connector_id in ("cursor", "codex", "opencode", "claude", "grok"):
        connector = get_connector(connector_id)
        assert_connector_conforms(connector)
        assert connector.connector_id == connector_id
        assert connector.display_name
        env = connector.execution_environment(read_only=True)
        assert isinstance(env, dict)
        argv = connector.build_read_only_cert_argv(
            executable="/usr/bin/false",
            prompt=prompt,
            workspace=workspace,
        )
        assert all(isinstance(item, str) for item in argv)
        assert connector.parse_events("") == [] or isinstance(
            connector.parse_events(""), (list, tuple)
        )


@pytest.mark.asyncio
async def test_opencode_only_routing() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_opencode_node(node_id="mac", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("opencode",),
        ),
        user_id="u",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "opencode"


@pytest.mark.asyncio
async def test_preference_orders_opencode_first() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_connector_node(
            node_id="mac",
            workspace_id="ws",
            connector_id="cursor",
            extra_connectors={
                **build_ready_codex_node(node_id="mac", workspace_id="ws").connectors,
                **build_ready_opencode_node(node_id="mac", workspace_id="ws").connectors,
            },
        )
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("opencode", "codex", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "opencode"


@pytest.mark.asyncio
async def test_unusable_opencode_does_not_block_cursor() -> None:
    from joymesh.connectors.lifecycle_models import (
        ConnectorExecutionOrigin,
        EvidenceTrustLevel,
        NodeConnectorState,
    )
    from joymesh.runtime_v1.scheduler import SchedulerConnectorSnapshot

    runtime = RuntimeService()
    runtime.register_node(
        build_ready_connector_node(
            node_id="mac",
            workspace_id="ws",
            connector_id="cursor",
            extra_connectors={
                "opencode": SchedulerConnectorSnapshot(
                    connector_id="opencode",
                    installed=True,
                    readiness=NodeConnectorState.NEEDS_REPAIR,
                    authenticated=False,
                    routing_enabled=False,
                    certified_capabilities=frozenset(),
                    trust_level=EvidenceTrustLevel.NODE_ATTESTED,
                    execution_origin=ConnectorExecutionOrigin.REMOTE_NODE,
                )
            },
        )
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("opencode", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "cursor"


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("JOYMESH_LIVE_OPENCODE") != "1" or shutil.which("opencode") is None,
    reason="Set JOYMESH_LIVE_OPENCODE=1 with opencode installed",
)
async def test_live_opencode_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# live\n", encoding="utf-8")
    result = await run_connector_live_test(
        connector=OpenCodeConnectorRuntime(),
        workspace=workspace,
        prompt="Read README.md and return the title without modifying files.",
        timeout_seconds=180,
    )
    assert result.connector_id == "opencode"
    assert result.installed is True
