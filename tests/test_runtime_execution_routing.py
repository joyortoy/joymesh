"""Live RuntimeService integration with provider-neutral execution routing."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from joymesh.runtime_v1.execution_routing import (
    BackendRegistry,
    BackendRegistryConfig,
    ExecutionPlanner,
    FireConnectBackend,
    HostedBackend,
    JoyMeshBackend,
    LocalBackend,
    MissionSpec,
    builtin_harness_adapters,
)
from joymesh.runtime_v1.execution_routing.failures import (
    ExecutionFailureClass,
    may_fallback,
)
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskStatus
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService
from joymesh.runtime_v1.service import RuntimeService, build_ready_cursor_node

ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "src/joymesh/runtime_v1/service.py"
PLANNER_PATH = ROOT / "src/joymesh/runtime_v1/execution_routing/planner.py"
REGISTRY_PATH = ROOT / "src/joymesh/runtime_v1/execution_routing/registry.py"


@pytest.mark.asyncio
async def test_runtime_routes_through_execution_planner_and_router() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_cursor_node(node_id="n1", workspace_id="ws1", local_path="/tmp/ws1")
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws1",
            prompt="hello",
            requested_capabilities=("repository.read",),
            preferred_connectors=("cursor",),
        ),
        user_id="u1",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert task.execution_id and task.execution_id.startswith("execution_")
    assert task.selected_backend_id == "joymesh"
    assert task.selected_harness_id == "cursor"
    assert task.selected_connector_id == "cursor"
    assert task.selected_node_id == "n1"
    audits = [a.event_type for a in runtime.store.audits if a.task_id == task.task_id]
    assert "execution.planned" in audits
    assert "backend.selected" in audits


@pytest.mark.asyncio
async def test_runtime_preserves_execution_id_across_remote_lease() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_cursor_node(node_id="n1", workspace_id="ws1", local_path="/tmp/ws1")
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws1",
            prompt="hello",
            requested_capabilities=("repository.read",),
        ),
        user_id="u1",
    )
    execution_id = task.execution_id
    assert execution_id
    attempts = runtime.store.attempts[task.task_id]
    assert attempts
    assert attempts[0].attempt_id.startswith("execution_attempt_")
    assert task.execution_id == execution_id


@pytest.mark.asyncio
async def test_local_mission_selects_local_backend(tmp_path: Path) -> None:
    harnesses = builtin_harness_adapters(use_real_adapters=False)
    registry = BackendRegistry(
        {
            "local": LocalBackend(harnesses=harnesses, healthy=True),
            "fireconnect": FireConnectBackend(
                harnesses=harnesses, healthy=False, skip_live_probe=True
            ),
            "joymesh": JoyMeshBackend(healthy=False),
            "hosted": HostedBackend(enabled=False),
        },
        config=BackendRegistryConfig(
            enabled_backends=("local", "fireconnect", "joymesh"),
            priority=("local", "fireconnect", "joymesh"),
            fallback_order=("local", "fireconnect"),
        ),
    )
    from joymesh.runtime_v1.execution_routing import ExecutionRoutingService

    service = ExecutionRoutingService(registry=registry)
    result = await service.execute(
        MissionSpec(
            prompt="local",
            workspace_path=str(tmp_path),
            preferred_harness="codex",
            locality_preference="local",
        )
    )
    assert result.ok
    assert result.backend_id == "local"
    assert result.execution_id.startswith("execution_")


@pytest.mark.asyncio
async def test_provider_routing_selects_fireconnect_backend() -> None:
    harnesses = builtin_harness_adapters(use_real_adapters=False)
    routes = ProviderRouteService(store=ProviderRouteLeaseStore())
    seen: list[str] = []

    async def fake_temporary(manager_id, connector_id, *, execute, **kwargs):
        seen.append(f"{manager_id}:{connector_id}")
        payload = await execute()

        class _Life:
            ok = True
            message = "ok"
            restoration_verified = True
            execution_result = payload
            reason_code = None

        return _Life()

    routes.run_temporary = fake_temporary  # type: ignore[method-assign]
    backend = FireConnectBackend(
        harnesses=harnesses, healthy=True, provider_routes=routes, skip_live_probe=True
    )
    registry = BackendRegistry(
        {
            "fireconnect": backend,
            "local": LocalBackend(harnesses=harnesses),
            "joymesh": JoyMeshBackend(healthy=True),
        },
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect", "local", "joymesh"),
            priority=("fireconnect", "local", "joymesh"),
            fallback_order=("fireconnect", "local"),
        ),
    )
    from joymesh.runtime_v1.execution_routing import ExecutionRoutingService

    result = await ExecutionRoutingService(registry=registry).execute(
        MissionSpec(
            prompt="x",
            workspace_path="/tmp",
            preferred_harness="opencode",
            requires_provider_route=True,
        )
    )
    assert result.ok
    assert result.backend_id == "fireconnect"
    assert seen == ["fireconnect:opencode"]


def test_hosted_stub_never_selected() -> None:
    registry = BackendRegistry()
    hosted = registry.get("hosted")
    assert isinstance(hosted, HostedBackend)
    intent = ExecutionPlanner().plan(
        MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="codex")
    )
    assert hosted.supports(intent, harness_id="codex") is False


def test_default_registry_skips_live_fireconnect_probe() -> None:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    assert "skip_live_probe=True" in text
    assert "class BackendRegistry" in text


def test_planner_has_no_fireconnect_imports() -> None:
    tree = ast.parse(PLANNER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = " ".join(alias.name for alias in getattr(node, "names", []))
            blob = f"{module} {names}".lower()
            assert "fireconnect" not in blob


def test_runtime_service_does_not_call_provider_route_service() -> None:
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "provider_routes" in module and "ProviderRouteService" in {
                alias.name for alias in node.names
            }:
                raise AssertionError("RuntimeService must not import ProviderRouteService")
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {"run_temporary", "enable_permanently", "disable_permanently"}:
                raise AssertionError(f"RuntimeService must not call {name}")
    text = SERVICE_PATH.read_text(encoding="utf-8")
    assert "create_subprocess" not in text
    assert "subprocess.Popen" not in text
    assert "shell=True" not in text


def test_fallback_taxonomy() -> None:
    assert may_fallback(ExecutionFailureClass.BACKEND_UNAVAILABLE)
    assert not may_fallback(ExecutionFailureClass.PROVIDER_RESTORE_FAILURE)
    assert not may_fallback(ExecutionFailureClass.POLICY_DENIED)
    assert not may_fallback(ExecutionFailureClass.WORKSPACE_VIOLATION)


@pytest.mark.asyncio
async def test_cancellation_emits_backend_cancelled() -> None:
    runtime = RuntimeService()
    runtime.register_node(
        build_ready_cursor_node(node_id="n1", workspace_id="ws1", local_path="/tmp/ws1")
    )
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws1",
            prompt="hello",
            requested_capabilities=("repository.read",),
        ),
        user_id="u1",
    )
    cancelled = await runtime.cancel_task(task.task_id)
    assert cancelled.status is RuntimeTaskStatus.CANCELLED
    again = await runtime.cancel_task(task.task_id)
    assert again.status is RuntimeTaskStatus.CANCELLED
    assert any(
        a.event_type == "backend.cancelled"
        for a in runtime.store.audits
        if a.task_id == task.task_id
    )


@pytest.mark.asyncio
async def test_subscription_blocks_before_execution() -> None:
    from joymesh.runtime_v1.execution_routing import ExecutionRouter, ExecutionRouterError

    registry = BackendRegistry(
        {
            "local": LocalBackend(harnesses=builtin_harness_adapters(), healthy=True),
        },
        config=BackendRegistryConfig(
            enabled_backends=("local",),
            priority=("local",),
            fallback_order=("local",),
        ),
    )
    router = ExecutionRouter(registry, subscription_allows={"local": False})
    intent = ExecutionPlanner().plan(
        MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="codex")
    )
    with pytest.raises(ExecutionRouterError) as exc:
        router.select(intent)
    assert exc.value.reason_code in {"no_compatible_backend", "backend_not_entitled"}


@pytest.mark.asyncio
async def test_process_runner_filters_environment_and_rejects_traversal(tmp_path: Path) -> None:
    from joymesh.runtime_v1.execution_routing.process_runner import (
        ProcessRunnerError,
        ProcessRunRequest,
        SafeProcessRunner,
    )

    runner = SafeProcessRunner()
    with pytest.raises(ProcessRunnerError) as exc:
        runner.validate_cwd(str(tmp_path / ".." / "etc"), allowed_roots=(str(tmp_path),))
    assert exc.value.reason_code in {
        "workspace_escape",
        "workspace_unavailable",
        "workspace_traversal",
    }

    script = tmp_path / "echo_env.py"
    script.write_text("import os; print(os.environ.get('SECRET_TOKEN', 'missing'))\n")
    result = await runner.run(
        ProcessRunRequest(
            argv=["python3", str(script)],
            cwd=str(tmp_path),
            timeout_seconds=5,
        )
    )
    assert result.ok
    assert "missing" in result.stdout
