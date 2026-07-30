"""Grok Build connector + connector-neutral live-test coverage."""

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
from joymesh.runtime_v1.connectors.grok import (
    GrokConnectorRuntime,
    classify_grok_auth_mode,
    classify_grok_auth_status,
    parse_grok_streaming_json,
)
from joymesh.runtime_v1.connectors.live_test import (
    render_live_test_result,
    run_connector_live_test,
)
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskStatus
from joymesh.runtime_v1.service import (
    RuntimeService,
    build_ready_claude_node,
    build_ready_codex_node,
    build_ready_connector_node,
    build_ready_cursor_node,
    build_ready_grok_node,
    build_ready_opencode_node,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_grok.py"


@pytest.fixture
def fake_grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "grok"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "success")
    for key in (
        "XAI_API_KEY",
        "GROK_CLI_CHAT_PROXY_BASE_URL",
        "XAI_API_BASE_URL",
        "GROK_OIDC_ISSUER",
        "GROK_TELEMETRY_ENABLED",
        "GROK_TELEMETRY_TRACE_UPLOAD",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_builtin_connectors_for_tests()
    return target


def test_builtin_registry_includes_grok() -> None:
    reset_builtin_connectors_for_tests()
    connectors = builtin_connectors()
    assert set(connectors) >= {"cursor", "codex", "opencode", "claude", "grok"}
    assert_connector_conforms(connectors["grok"])


def test_grok_declares_read_only_only() -> None:
    assert GrokConnectorRuntime().declared_capabilities() == READ_ONLY_CAPABILITIES
    assert "repository.write" not in GrokConnectorRuntime().declared_capabilities()


def test_grok_auth_classification() -> None:
    assert (
        classify_grok_auth_status(
            "Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5",
            returncode=0,
        )
        == "authenticated"
    )
    assert (
        classify_grok_auth_mode(
            "Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (free trial)",
            returncode=0,
        )
        == "authenticated_free_access"
    )
    assert (
        classify_grok_auth_status(
            "You are not authenticated.\n\nDefault model: grok-4.5",
            returncode=0,
        )
        == "unauthenticated"
    )
    assert classify_grok_auth_status("rate limit exceeded", returncode=1) == "plan_restricted"
    assert classify_grok_auth_status("quota exhausted", returncode=1) == "quota_exhausted"


def test_grok_event_parser_normalizes_streaming_json() -> None:
    output = "\n".join(
        [
            json.dumps({"type": "text", "data": "hello"}),
            json.dumps({"type": "thought", "data": "thinking"}),
            json.dumps(
                {
                    "type": "end",
                    "stopReason": "EndTurn",
                    "sessionId": "ses_1",
                    "usage": {"input_tokens": 1},
                }
            ),
        ]
    )
    events = parse_grok_streaming_json(output)
    types = [item["event_type"] for item in events]
    assert "run.started" in types
    assert "message.output" in types
    assert "run.completed" in types
    assert any(item.get("session_id") == "ses_1" for item in events)


def test_grok_event_parser_maps_permission_denials() -> None:
    output = "\n".join(
        [
            json.dumps(
                {
                    "type": "permission_denied",
                    "tool": "search_replace",
                    "message": "denied",
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "message": "Permission denied",
                    "permission_denials": [
                        {"tool_name": "run_terminal_cmd"},
                        {"tool_name": "web_fetch"},
                        {"tool_name": "Agent"},
                        {"tool_name": "MCPTool"},
                    ],
                }
            ),
        ]
    )
    events = parse_grok_streaming_json(output)
    denied = [item for item in events if item["event_type"] == "permission.denied"]
    reasons = {item["reason_code"] for item in denied}
    assert "permission_denied_edit" in reasons
    assert "permission_denied_shell" in reasons
    assert "permission_denied_network" in reasons
    assert "permission_denied_subagent" in reasons
    assert "permission_denied_mcp" in reasons


def test_grok_read_only_argv_and_env(fake_grok: Path) -> None:
    connector = GrokConnectorRuntime()
    argv = connector.build_read_only_cert_argv(
        executable=str(fake_grok),
        prompt="summarise",
        workspace=Path("/tmp/ws"),
    )
    assert argv[0] == str(fake_grok)
    assert "--no-auto-update" in argv
    assert "-p" in argv and "summarise" in argv
    assert "streaming-json" in argv
    assert "--sandbox" in argv and "strict" in argv
    assert "--permission-mode" in argv and "plan" in argv
    assert "--tools" in argv
    assert "read_file,grep,list_dir" in argv
    assert "--disallowed-tools" in argv
    assert "--disable-web-search" in argv
    assert "--no-subagents" in argv
    assert "--always-approve" not in argv
    assert "--yolo" not in argv
    env = connector.execution_environment(read_only=True)
    assert env["GROK_TELEMETRY_ENABLED"] == "0"
    assert env["GROK_TELEMETRY_TRACE_UPLOAD"] == "0"
    assert "native_sandbox" in connector.permission_enforcement_method()


@pytest.mark.asyncio
async def test_grok_discovery_with_fake(fake_grok: Path) -> None:
    result = await GrokConnectorRuntime().discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is True
    assert result.version and "0.2.114" in result.version
    assert result.executable_path == str(fake_grok)
    assert result.fingerprint
    assert result.details.get("acp_supported") is True


@pytest.mark.asyncio
async def test_grok_discovery_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    reset_builtin_connectors_for_tests()
    result = await GrokConnectorRuntime().discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is False
    assert result.reason_code == "executable_not_found"


@pytest.mark.asyncio
async def test_grok_discovery_broken(fake_grok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "broken")
    result = await GrokConnectorRuntime().discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is False
    assert result.reason_code == "broken_executable"


@pytest.mark.asyncio
async def test_grok_auth_modes(fake_grok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connector = GrokConnectorRuntime()
    ctx = ConnectorExecutionContext(node_id="n1")

    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "success")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_subscription"

    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "api_key")
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_api_key"
    assert evidence.details.get("billing_risk") == "api_key_possibly_billable"
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "free_access")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.details.get("auth_mode") == "authenticated_free_access"

    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "provider_override")
    monkeypatch.setenv("GROK_CLI_CHAT_PROXY_BASE_URL", "https://example.invalid")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_provider_override"
    assert evidence.details.get("provider_notice") == "connector_provider_override_active"
    assert "example.invalid" not in json.dumps(evidence.details)
    monkeypatch.delenv("GROK_CLI_CHAT_PROXY_BASE_URL", raising=False)

    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "unauthenticated")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "unauthenticated"


@pytest.mark.asyncio
async def test_shared_live_test_with_fake_grok(fake_grok: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# live\n", encoding="utf-8")
    result = await run_connector_live_test(
        connector=get_connector("grok"),
        workspace=workspace,
        prompt="Read README.md without modifying files.",
        timeout_seconds=10,
    )
    assert result.connector_id == "grok"
    assert result.authenticated is True
    assert result.certification_passed is True
    assert result.exit_code == 0
    assert any(event.event_type == "run.completed" for event in result.events)
    assert "grok" in render_live_test_result(result).lower()


@pytest.mark.asyncio
async def test_shared_live_test_unauthenticated_grok(
    fake_grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "unauthenticated")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_connector_live_test(
        connector=get_connector("grok"),
        workspace=workspace,
        prompt="x",
        timeout_seconds=5,
    )
    assert result.authenticated is False
    assert result.certification_passed is False
    assert any(item.reason_code == "connector_auth_required" for item in result.notices)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("deny_edit", "permission_denied_edit"),
        ("deny_shell", "permission_denied_shell"),
        ("deny_network", "permission_denied_network"),
        ("deny_external", "permission_denied_external_path"),
        ("deny_subprocess", "permission_denied_subprocess"),
        ("deny_plugin", "permission_denied_plugin"),
        ("deny_mcp", "permission_denied_mcp"),
        ("deny_subagent", "permission_denied_subagent"),
    ],
)
async def test_live_test_denial_modes(
    fake_grok: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    reason: str,
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", mode)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "protected.txt").write_text("ORIGINAL\n", encoding="utf-8")
    result = await run_connector_live_test(
        connector=get_connector("grok"),
        workspace=workspace,
        prompt="attempt forbidden action",
        timeout_seconds=10,
    )
    assert any(
        event.event_type == "permission.denied" and event.payload.get("reason_code") == reason
        for event in result.events
    )
    assert (workspace / "protected.txt").read_text() == "ORIGINAL\n"
    assert not (workspace / "shell-created.txt").exists()
    if mode == "deny_edit":
        assert result.exit_code == 1


@pytest.mark.asyncio
async def test_security_failure_on_unexpected_mutation(
    fake_grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "unexpected_mutation")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_connector_live_test(
        connector=get_connector("grok"),
        workspace=workspace,
        prompt="mutate",
        timeout_seconds=10,
    )
    # Process may report success, but workspace was mutated.
    assert result.exit_code == 0
    assert (workspace / "forbidden-created.txt").exists()


@pytest.mark.asyncio
async def test_shared_live_test_blocks_api_key_billing_without_approval(
    fake_grok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_GROK_MODE", "api_key")
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-a-real-key")
    monkeypatch.delenv("JOYMESH_APPROVE_API_BILLING", raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_connector_live_test(
        connector=get_connector("grok"),
        workspace=workspace,
        prompt="x",
        timeout_seconds=5,
    )
    assert result.authenticated is True
    assert result.certification_passed is False
    assert any(item.reason_code == "connector_billing_approval_required" for item in result.notices)


@pytest.mark.asyncio
async def test_cross_connector_protocol_loop_includes_grok(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    prompt = "Read README.md without modifying files"
    for connector_id in ("cursor", "codex", "opencode", "claude", "grok"):
        connector = get_connector(connector_id)
        assert_connector_conforms(connector)
        assert connector.connector_id == connector_id
        argv = connector.build_read_only_cert_argv(
            executable="/usr/bin/false",
            prompt=prompt,
            workspace=workspace,
        )
        assert all(isinstance(item, str) for item in argv)


@pytest.mark.asyncio
async def test_grok_only_routing() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_grok_node(node_id="mac", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("grok",),
        ),
        user_id="u",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "grok"


@pytest.mark.asyncio
async def test_preference_orders_grok_first() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_connector_node(
            node_id="mac",
            workspace_id="ws",
            connector_id="cursor",
            extra_connectors={
                **build_ready_codex_node(node_id="mac", workspace_id="ws").connectors,
                **build_ready_opencode_node(node_id="mac", workspace_id="ws").connectors,
                **build_ready_claude_node(node_id="mac", workspace_id="ws").connectors,
                **build_ready_grok_node(node_id="mac", workspace_id="ws").connectors,
            },
        )
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("grok", "claude", "opencode", "codex", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "grok"


@pytest.mark.asyncio
async def test_unusable_grok_falls_back_to_cursor() -> None:
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
                "grok": SchedulerConnectorSnapshot(
                    connector_id="grok",
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
            preferred_connectors=("grok", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "cursor"


@pytest.mark.asyncio
async def test_empty_preferred_still_routes_ready_grok() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_grok_node(node_id="mac", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=(),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "grok"


@pytest.mark.asyncio
async def test_cursor_only_still_works_with_grok_present() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("cursor",),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "cursor"


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("JOYMESH_LIVE_GROK") != "1" or shutil.which("grok") is None,
    reason="Set JOYMESH_LIVE_GROK=1 with grok installed and authenticated",
)
async def test_live_grok_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# live\n", encoding="utf-8")
    (workspace / "protected.txt").write_text("ORIGINAL_PROTECTED_CONTENT\n", encoding="utf-8")
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "example.py").write_text('print("read-only")\n', encoding="utf-8")
    (workspace / "pyproject.toml").write_text('[project]\nname = "cert"\n', encoding="utf-8")
    result = await run_connector_live_test(
        connector=GrokConnectorRuntime(),
        workspace=workspace,
        prompt="Read README.md and return the title without modifying files.",
        timeout_seconds=180,
    )
    assert result.connector_id == "grok"
    assert result.installed is True
    assert result.authenticated is True
    assert result.certification_passed is True
    assert (workspace / "protected.txt").read_text() == "ORIGINAL_PROTECTED_CONTENT\n"
