"""Connector protocol conformance, registry validation, and node_runner neutrality."""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from joymesh.connectors.lifecycle_models import ConnectorTaskStatus
from joymesh.connectors.node_runner import ConnectorNodeRunner
from joymesh.connectors.planning import ConnectorAction, ConnectorTaskPlan
from joymesh.models import utc_now
from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES
from joymesh.runtime_v1.connector_protocol import (
    AdapterVerificationResult,
    AuthenticationEvidence,
    AuthenticationResult,
    CancellationResult,
    CertificationProfileDefinition,
    ConnectorExecutionContext,
    ConnectorPlan,
    ConnectorRunRequest,
    ConnectorRuntimeNotice,
    DiscoveryResult,
    HarnessEvent,
)
from joymesh.runtime_v1.connectors import (
    ConnectorRegistryError,
    assert_connector_conforms,
    builtin_connectors,
    get_connector,
    reset_builtin_connectors_for_tests,
    validate_builtin_connectors,
)
from joymesh.runtime_v1.connectors.codex import CodexConnectorRuntime
from joymesh.runtime_v1.connectors.cursor import CursorConnectorRuntime
from joymesh.runtime_v1.cursor import CursorConnectorRuntime as CompatCursor
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskStatus
from joymesh.runtime_v1.service import (
    RuntimeService,
    build_ready_codex_node,
    build_ready_cursor_node,
)


def test_builtin_connectors_conform() -> None:
    connectors = builtin_connectors()
    assert set(connectors) == {"cursor", "codex", "opencode", "claude", "grok"}
    for connector in connectors.values():
        assert_connector_conforms(connector)


def test_cursor_and_codex_cert_argv_require_workspace_path() -> None:
    workspace = Path("/tmp/cert-ws")
    cursor_argv = CursorConnectorRuntime().build_read_only_cert_argv(
        executable="/bin/cursor-agent", prompt="p", workspace=workspace
    )
    codex_argv = CodexConnectorRuntime().build_read_only_cert_argv(
        executable="/bin/codex", prompt="p", workspace=workspace
    )
    assert "--trust" in cursor_argv
    assert str(workspace) in codex_argv
    cursor_sig = inspect.signature(CursorConnectorRuntime.build_read_only_cert_argv)
    codex_sig = inspect.signature(CodexConnectorRuntime.build_read_only_cert_argv)
    assert "workspace" in cursor_sig.parameters
    assert "workspace" in codex_sig.parameters
    assert cursor_sig.parameters["workspace"].default is inspect.Parameter.empty
    assert codex_sig.parameters["workspace"].default is inspect.Parameter.empty


def test_old_cert_signature_rejected() -> None:
    class BadCert:
        connector_id = "bad"
        display_name = "Bad"
        connector_revision = "1"

        def declared_capabilities(self) -> frozenset[str]:
            return READ_ONLY_CAPABILITIES

        def certification_profiles(self) -> tuple[CertificationProfileDefinition, ...]:
            return ()

        async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
            return DiscoveryResult(None, None, None)

        def classify_auth_status(self, output: str, *, returncode: int) -> str:
            return "unauthenticated"

        async def inspect_authentication(
            self, context: ConnectorExecutionContext
        ) -> AuthenticationResult:
            return AuthenticationResult(False, "x", "")

        async def build_authentication_plan(
            self, context: ConnectorExecutionContext
        ) -> ConnectorPlan:
            return ConnectorPlan("p", "bad", "1", "authenticate", "x", (), "h", utc_now())

        async def verify_authentication(
            self, context: ConnectorExecutionContext
        ) -> AuthenticationEvidence:
            return AuthenticationEvidence("unauthenticated", "x", None, None, None, {})

        async def verify_adapter(
            self, context: ConnectorExecutionContext
        ) -> AdapterVerificationResult:
            return AdapterVerificationResult(False, None, None, None, {})

        def adapter_verification_notice(self) -> ConnectorRuntimeNotice | None:
            return None

        def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
            del read_only
            return {}

        def build_exec_argv(
            self, *, executable: str, prompt: str, workspace_path: str, read_only: bool = True
        ) -> Sequence[str]:
            return (executable, prompt)

        def build_read_only_cert_argv(self, *, executable: str, prompt: str) -> Sequence[str]:
            return (executable, prompt)

        def parse_events(self, output: str) -> Sequence[Mapping[str, Any]]:
            return ()

        async def execute(self, request, context):  # type: ignore[no-untyped-def]
            if False:
                yield HarnessEvent("x", 1, {})

        async def cancel(self, execution_id, context) -> CancellationResult:  # type: ignore[no-untyped-def]
            return CancellationResult(True, False, None)

    with pytest.raises(ConnectorRegistryError, match="workspace"):
        validate_builtin_connectors({"bad": BadCert()})  # type: ignore[arg-type]


def test_duplicate_connector_id_rejected() -> None:
    a = CursorConnectorRuntime()
    with pytest.raises(ConnectorRegistryError, match=r"duplicate|does not match"):
        validate_builtin_connectors({"cursor": a, "other": a})


def test_invalid_capability_declaration_rejected() -> None:
    class BadCaps(CursorConnectorRuntime):
        def declared_capabilities(self) -> frozenset[str]:
            return frozenset({"not.a.real.capability"})

    with pytest.raises(ConnectorRegistryError, match="unknown capabilities"):
        validate_builtin_connectors({"cursor": BadCaps()})


def test_missing_required_method_rejected() -> None:
    class Missing(CursorConnectorRuntime):
        parse_events = None  # type: ignore[assignment]

    with pytest.raises(
        ConnectorRegistryError, match=r"missing required attribute|must be callable"
    ):
        validate_builtin_connectors({"cursor": Missing()})


def test_compat_cursor_reexport_has_no_logic() -> None:
    assert CompatCursor is CursorConnectorRuntime
    source = Path("src/joymesh/runtime_v1/cursor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)
    assert all(isinstance(node, allowed) for node in tree.body)
    assert "class " not in source


def test_cursor_notice_is_connector_neutral() -> None:
    notice = CursorConnectorRuntime().adapter_verification_notice()
    assert notice is not None
    payload = notice.as_payload()
    assert payload["event_type"] == "connector_plan_restriction"
    assert payload["connector_id"] == "cursor"
    assert "Cursor plan allowance" not in str(payload["message"])
    assert "plan allowance" in str(payload["message"]).lower()
    assert CodexConnectorRuntime().adapter_verification_notice() is None


def test_shared_live_test_and_cli_have_no_connector_id_branches() -> None:
    roots = [
        Path("src/joymesh/runtime_v1/connectors/live_test.py"),
        Path("src/joymesh/connectors/node_runner.py"),
        Path("src/joymesh/connectors/live_test.py"),
    ]
    forbidden = re.compile(
        r"""connector_id\s*(==|!=)\s*['\"](cursor|codex|opencode|claude|grok)['\"]"""
    )
    for path in roots:
        source = path.read_text(encoding="utf-8")
        assert forbidden.search(source) is None, path
    cli = Path("src/joymesh/cli.py").read_text(encoding="utf-8")
    assert 'if connector_id != "cursor"' not in cli
    assert "supports only cursor" not in cli


@pytest.mark.asyncio
async def test_node_runner_uses_fake_connector_discover_and_cert_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict[str, object]] = []
    evidence: list[object] = []

    class FakeConnector:
        connector_id = "fake"
        display_name = "Fake Connector"
        connector_revision = "test"

        def declared_capabilities(self) -> frozenset[str]:
            return READ_ONLY_CAPABILITIES

        def certification_profiles(self) -> tuple[CertificationProfileDefinition, ...]:
            return (
                CertificationProfileDefinition(
                    "read_only_repository", "1", READ_ONLY_CAPABILITIES, "fake"
                ),
            )

        async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
            return DiscoveryResult(
                executable_path=str(tmp_path / "fake-bin"),
                version="1.0",
                fingerprint="abc",
                installed=True,
                usable=True,
                reason_code=None,
                details={"node_id": context.node_id},
            )

        def classify_auth_status(self, output: str, *, returncode: int) -> str:
            return "authenticated" if returncode == 0 else "unauthenticated"

        async def inspect_authentication(
            self, context: ConnectorExecutionContext
        ) -> AuthenticationResult:
            return AuthenticationResult(True, "fake", "ok")

        async def build_authentication_plan(
            self, context: ConnectorExecutionContext
        ) -> ConnectorPlan:
            return ConnectorPlan("p", "fake", "test", "authenticate", "x", (), "h", utc_now())

        async def verify_authentication(
            self, context: ConnectorExecutionContext
        ) -> AuthenticationEvidence:
            return AuthenticationEvidence("authenticated", "fake", "x", "fp", "1", {})

        async def verify_adapter(
            self, context: ConnectorExecutionContext
        ) -> AdapterVerificationResult:
            return AdapterVerificationResult(True, "x", "fp", "1", {})

        def adapter_verification_notice(self) -> ConnectorRuntimeNotice | None:
            return ConnectorRuntimeNotice(
                event_type="connector_limit_detected",
                connector_id=self.connector_id,
                display_name=self.display_name,
                reason_code="test_limit",
                message="Neutral limit notice",
                recoverable=True,
                recommended_action="retry",
            )

        def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
            del read_only
            return {}

        def build_exec_argv(
            self, *, executable: str, prompt: str, workspace_path: str, read_only: bool = True
        ) -> Sequence[str]:
            return (executable, "--workspace", workspace_path, prompt)

        def build_read_only_cert_argv(
            self, *, executable: str, prompt: str, workspace: Path
        ) -> Sequence[str]:
            return (executable, "--cert", str(workspace), prompt)

        def parse_events(self, output: str) -> Sequence[Mapping[str, Any]]:
            return [{"type": "fake", "raw": output[:10]}]

        async def execute(self, request: ConnectorRunRequest, context: ConnectorExecutionContext):
            if False:
                yield HarnessEvent("noop", 1, {})

        async def cancel(
            self, execution_id: str, context: ConnectorExecutionContext
        ) -> CancellationResult:
            return CancellationResult(True, False, None)

    fake = FakeConnector()
    monkeypatch.setattr(
        "joymesh.connectors.node_runner.get_connector",
        lambda connector_id: fake if connector_id == "fake" else get_connector(connector_id),
    )

    runner = ConnectorNodeRunner(node_id="n1")
    plan = ConnectorTaskPlan(
        plan_id=str(uuid4()),
        node_id="n1",
        connector_id="fake",
        connector_revision="test",
        action=ConnectorAction.DISCOVER,
        method_id="discover",
        executable="fake",
        arguments=(),
        package_source="test",
        expected_executables=("fake",),
        risk_level="low",
        expires_at=utc_now(),
        plan_hash="hash",
    )

    async def emit(event):  # type: ignore[no-untyped-def]
        events.append({"type": event.event_type, "payload": dict(event.payload)})

    async def record(item):  # type: ignore[no-untyped-def]
        evidence.append(item)

    status = await runner.execute(task_id="t1", plan=plan, emit_event=emit, record_evidence=record)
    assert status is ConnectorTaskStatus.SUCCEEDED
    assert any(item.evidence_type.value == "discovery" for item in evidence)

    adapter_plan = plan.model_copy(update={"action": ConnectorAction.VERIFY_ADAPTER})
    events.clear()
    status = await runner.execute(
        task_id="t2", plan=adapter_plan, emit_event=emit, record_evidence=record
    )
    assert status is ConnectorTaskStatus.SUCCEEDED
    progress = next(item for item in events if item["type"] == "task.progress")
    assert progress["payload"]["notice"]["event_type"] == "connector_limit_detected"
    assert progress["payload"]["display_name"] == "Fake Connector"


def test_metrics_increment_per_connector_without_hardcoded_keys() -> None:
    import asyncio

    async def _run() -> None:
        runtime = RuntimeService()
        runtime.register_node(build_ready_cursor_node(node_id="c", workspace_id="ws"))
        runtime.register_node(build_ready_codex_node(node_id="x", workspace_id="ws"))
        cursor_task = await runtime.create_task(
            CreateRuntimeTaskBody(
                workspace_id="ws",
                prompt="a",
                policy_profile="read_only",
                requested_capabilities=("repository.read", "structured_output"),
                preferred_connectors=("cursor",),
            ),
            user_id="u",
        )
        codex_task = await runtime.create_task(
            CreateRuntimeTaskBody(
                workspace_id="ws",
                prompt="b",
                policy_profile="read_only",
                requested_capabilities=("repository.read", "structured_output"),
                preferred_connectors=("codex",),
            ),
            user_id="u",
        )
        assert cursor_task.selected_connector_id == "cursor"
        assert codex_task.selected_connector_id == "codex"
        snapshot = runtime.metrics.snapshot()
        by_connector = snapshot["tasks_by_connector"]
        assert isinstance(by_connector, dict)
        assert by_connector.get("cursor") == 1
        assert by_connector.get("codex") == 1
        # Unknown connectors require no schema change — map accepts arbitrary keys.
        runtime.metrics.tasks_by_connector["future"] = 3
        assert "future" in runtime.metrics.snapshot()["tasks_by_connector"]  # type: ignore[index]

    asyncio.run(_run())
    source = Path("src/joymesh/runtime_v1/service.py").read_text(encoding="utf-8")
    assert 'tasks_by_connector = {"cursor"' not in source
    assert 'tasks_by_connector = {"codex"' not in source


def test_empty_preferred_connectors_still_routes() -> None:
    import asyncio

    async def _run() -> None:
        runtime = RuntimeService()
        runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws"))
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
        assert task.status is RuntimeTaskStatus.LEASED
        assert task.selected_connector_id == "cursor"

    asyncio.run(_run())


def test_package_includes_both_connector_modules() -> None:
    root = Path("src/joymesh/runtime_v1/connectors")
    assert (root / "cursor.py").is_file()
    assert (root / "codex.py").is_file()
    assert (root / "__init__.py").is_file()


def test_reset_registry_helper() -> None:
    first = builtin_connectors()
    reset_builtin_connectors_for_tests()
    second = builtin_connectors()
    assert set(first) == set(second)
