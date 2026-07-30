"""Claude Code connector + connector-neutral live-test coverage."""

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
from joymesh.runtime_v1.connectors.claude import (
    ClaudeConnectorRuntime,
    classify_claude_auth_mode,
    classify_claude_auth_status,
    parse_claude_stream_json,
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
    build_ready_opencode_node,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_claude.py"


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "claude"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "success")
    # Prevent accidental real provider override detection during unit tests.
    for key in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_BASE",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_builtin_connectors_for_tests()
    return target


def test_builtin_registry_includes_claude() -> None:
    reset_builtin_connectors_for_tests()
    connectors = builtin_connectors()
    assert set(connectors) >= {"cursor", "codex", "opencode", "claude", "grok"}
    assert_connector_conforms(connectors["claude"])


def test_claude_declares_read_only_only() -> None:
    assert ClaudeConnectorRuntime().declared_capabilities() == READ_ONLY_CAPABILITIES
    assert "repository.write" not in ClaudeConnectorRuntime().declared_capabilities()


def test_claude_auth_classification_subscription_and_api_key() -> None:
    assert (
        classify_claude_auth_status(
            json.dumps({"loggedIn": True, "authMethod": "oauth", "apiProvider": "firstParty"}),
            returncode=0,
        )
        == "authenticated"
    )
    assert (
        classify_claude_auth_mode(
            json.dumps({"loggedIn": True, "authMethod": "oauth", "apiProvider": "firstParty"}),
            returncode=0,
        )
        == "authenticated_subscription"
    )
    assert (
        classify_claude_auth_mode(
            json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}),
            returncode=0,
        )
        == "authenticated_api_key"
    )
    assert (
        classify_claude_auth_mode(
            json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "bedrock"}),
            returncode=0,
        )
        == "authenticated_provider_override"
    )
    assert (
        classify_claude_auth_status(
            json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}),
            returncode=0,
        )
        == "unauthenticated"
    )
    # Credential-file style false positive: loggedIn false must not authenticate.
    assert (
        classify_claude_auth_status("Credentials present in ~/.claude", returncode=0)
        == "unauthenticated"
    )
    assert classify_claude_auth_status("rate limit exceeded", returncode=1) == "plan_restricted"
    assert classify_claude_auth_status("quota exhausted", returncode=1) == "quota_exhausted"


def test_claude_event_parser_normalizes_stream_json() -> None:
    output = "\n".join(
        [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "ses_1",
                    "tools": ["Read"],
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": "ses_1",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "ses_1",
                    "is_error": False,
                    "usage": {"input_tokens": 1},
                }
            ),
        ]
    )
    events = parse_claude_stream_json(output)
    assert [item["event_type"] for item in events] == [
        "run.started",
        "message.output",
        "run.completed",
    ]
    assert events[0]["session_id"] == "ses_1"


def test_claude_event_parser_maps_permission_denials() -> None:
    output = json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "error": "Permission denied",
            "permission_denials": [
                {"tool_name": "Edit", "tool_use_id": "1"},
                {"tool_name": "Bash", "tool_use_id": "2"},
                {"tool_name": "WebFetch", "tool_use_id": "3"},
                {"tool_name": "Agent", "tool_use_id": "4"},
            ],
        }
    )
    events = parse_claude_stream_json(output)
    denied = [item for item in events if item["event_type"] == "permission.denied"]
    assert [item["reason_code"] for item in denied] == [
        "permission_denied_edit",
        "permission_denied_shell",
        "permission_denied_network",
        "permission_denied_subprocess",
    ]


def test_claude_event_parser_maps_external_path_text() -> None:
    output = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "I cannot read files outside the workspace; external path denied.",
                    }
                ]
            },
        }
    )
    events = parse_claude_stream_json(output)
    assert any(
        item.get("reason_code") == "permission_denied_external_path"
        for item in events
        if item["event_type"] == "permission.denied"
    )


def test_claude_read_only_argv_and_env(fake_claude: Path) -> None:
    connector = ClaudeConnectorRuntime()
    argv = connector.build_read_only_cert_argv(
        executable=str(fake_claude),
        prompt="summarise",
        workspace=Path("/tmp/ws"),
    )
    assert argv[0] == str(fake_claude)
    assert "--print" in argv
    assert "stream-json" in argv
    assert "--permission-mode" in argv and "plan" in argv
    assert "--tools" in argv
    assert "Read,Glob,Grep" in argv
    assert "--disallowedTools" in argv
    assert "Edit" in argv[argv.index("--disallowedTools") + 1]
    assert connector.execution_environment(read_only=True) == {}
    assert "tool_filtering" in connector.permission_enforcement_method()


@pytest.mark.asyncio
async def test_claude_discovery_with_fake(fake_claude: Path) -> None:
    connector = ClaudeConnectorRuntime()
    result = await connector.discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is True
    assert result.version and "2.1.220" in result.version
    assert result.executable_path == str(fake_claude)
    assert result.fingerprint


@pytest.mark.asyncio
async def test_claude_discovery_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    reset_builtin_connectors_for_tests()
    result = await ClaudeConnectorRuntime().discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is False
    assert result.usable is False
    assert result.reason_code == "executable_not_found"


@pytest.mark.asyncio
async def test_claude_discovery_broken(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "broken")
    result = await ClaudeConnectorRuntime().discover(ConnectorExecutionContext(node_id="n1"))
    assert result.installed is True
    assert result.usable is False
    assert result.reason_code == "broken_executable"


@pytest.mark.asyncio
async def test_claude_auth_modes(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connector = ClaudeConnectorRuntime()
    ctx = ConnectorExecutionContext(node_id="n1")

    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "success")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_subscription"

    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "api_key")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_api_key"

    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "provider_override")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "authenticated"
    assert evidence.details.get("auth_mode") == "authenticated_provider_override"

    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "unauthenticated")
    evidence = await connector.verify_authentication(ctx)
    assert evidence.status == "unauthenticated"


@pytest.mark.asyncio
async def test_claude_provider_override_env_notice(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    evidence = await ClaudeConnectorRuntime().verify_authentication(
        ConnectorExecutionContext(node_id="n1")
    )
    assert evidence.details.get("provider_notice") == "connector_provider_override_active"
    assert "ANTHROPIC_BASE_URL" in str(evidence.details.get("provider_override"))
    # Never leak the URL value into details.
    assert "example.invalid" not in json.dumps(evidence.details)


@pytest.mark.asyncio
async def test_shared_live_test_with_fake_claude(fake_claude: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# live\n", encoding="utf-8")
    result = await run_connector_live_test(
        connector=get_connector("claude"),
        workspace=workspace,
        prompt="Read README.md without modifying files.",
        timeout_seconds=10,
    )
    assert result.connector_id == "claude"
    assert result.authenticated is True
    assert result.certification_passed is True
    assert result.exit_code == 0
    assert any(event.event_type == "run.completed" for event in result.events)
    rendered = render_live_test_result(result)
    assert "claude" in rendered.lower()


@pytest.mark.asyncio
async def test_shared_live_test_unauthenticated_claude(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", "unauthenticated")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_connector_live_test(
        connector=get_connector("claude"),
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
        ("deny_subprocess", "permission_denied_subprocess"),
    ],
)
async def test_live_test_denial_modes(
    fake_claude: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    reason: str,
) -> None:
    monkeypatch.setenv("JOYMESH_FAKE_CLAUDE_MODE", mode)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "protected.txt").write_text("ORIGINAL\n", encoding="utf-8")
    result = await run_connector_live_test(
        connector=get_connector("claude"),
        workspace=workspace,
        prompt="attempt forbidden action",
        timeout_seconds=10,
    )
    assert any(
        event.event_type == "permission.denied" and (event.payload.get("reason_code") == reason)
        for event in result.events
    )
    assert (workspace / "protected.txt").read_text() == "ORIGINAL\n"
    assert not (workspace / "shell-created.txt").exists()
    # Exit 1 after denial can still be a successful security outcome.
    if mode == "deny_edit":
        assert result.exit_code == 1
        assert any(n.reason_code == reason for n in result.notices)


@pytest.mark.asyncio
async def test_cross_connector_protocol_loop_includes_claude(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    prompt = "Read README.md without modifying files"
    for connector_id in ("cursor", "codex", "opencode", "claude", "grok"):
        connector = get_connector(connector_id)
        assert_connector_conforms(connector)
        assert connector.connector_id == connector_id
        env = connector.execution_environment(read_only=True)
        assert isinstance(env, dict)
        argv = connector.build_read_only_cert_argv(
            executable="/usr/bin/false",
            prompt=prompt,
            workspace=workspace,
        )
        assert all(isinstance(item, str) for item in argv)


@pytest.mark.asyncio
async def test_claude_only_routing() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_claude_node(node_id="mac", workspace_id="ws"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("claude",),
        ),
        user_id="u",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.selected_connector_id == "claude"


@pytest.mark.asyncio
async def test_preference_orders_claude_first() -> None:
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
            },
        )
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
            preferred_connectors=("claude", "opencode", "codex", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "claude"


@pytest.mark.asyncio
async def test_unusable_claude_does_not_block_cursor() -> None:
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
                "claude": SchedulerConnectorSnapshot(
                    connector_id="claude",
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
            preferred_connectors=("claude", "cursor"),
        ),
        user_id="u",
    )
    assert task.selected_connector_id == "cursor"


@pytest.mark.asyncio
async def test_empty_preferred_still_routes_ready_claude() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_claude_node(node_id="mac", workspace_id="ws"))
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
    assert task.selected_connector_id == "claude"


@pytest.mark.asyncio
async def test_cursor_only_still_works() -> None:
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
    os.environ.get("JOYMESH_LIVE_CLAUDE") != "1" or shutil.which("claude") is None,
    reason="Set JOYMESH_LIVE_CLAUDE=1 with claude installed and authenticated",
)
async def test_live_claude_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# live\n", encoding="utf-8")
    (workspace / "protected.txt").write_text("ORIGINAL_PROTECTED_CONTENT\n", encoding="utf-8")
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "example.py").write_text('print("read-only")\n', encoding="utf-8")
    (workspace / "pyproject.toml").write_text('[project]\nname = "cert"\n', encoding="utf-8")
    result = await run_connector_live_test(
        connector=ClaudeConnectorRuntime(),
        workspace=workspace,
        prompt="Read README.md and return the title without modifying files.",
        timeout_seconds=180,
    )
    assert result.connector_id == "claude"
    assert result.installed is True
    assert result.authenticated is True
    assert result.certification_passed is True
    assert (workspace / "protected.txt").read_text() == "ORIGINAL_PROTECTED_CONTENT\n"
