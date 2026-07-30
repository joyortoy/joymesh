"""Deterministic tests for provider-neutral execution routing."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from joymesh.runtime_v1.execution_routing import (
    BackendRegistry,
    BackendRegistryConfig,
    ExecutionCapability,
    ExecutionPlanner,
    ExecutionRouter,
    ExecutionRouterError,
    ExecutionRoutingService,
    ExecutionStatus,
    FireConnectBackend,
    HostedBackend,
    JoyMeshBackend,
    LocalBackend,
    MissionSpec,
    builtin_harness_adapters,
)
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

ROUTING_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "joymesh" / "runtime_v1" / "execution_routing"
)


def _registry(
    *,
    fireconnect_healthy: bool = True,
    local_healthy: bool = True,
    hosted_healthy: bool = True,
    joymesh_healthy: bool = True,
    provider_routes: ProviderRouteService | None = None,
) -> BackendRegistry:
    harnesses = builtin_harness_adapters()
    return BackendRegistry(
        {
            "fireconnect": FireConnectBackend(
                harnesses=harnesses,
                healthy=fireconnect_healthy,
                provider_routes=provider_routes
                or ProviderRouteService(store=ProviderRouteLeaseStore()),
            ),
            "local": LocalBackend(harnesses=harnesses, healthy=local_healthy),
            "hosted": HostedBackend(healthy=hosted_healthy),
            "joymesh": JoyMeshBackend(healthy=joymesh_healthy),
        },
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect", "local", "hosted", "joymesh"),
            priority=("fireconnect", "local", "hosted", "joymesh"),
            fallback_order=("fireconnect", "local", "hosted"),
        ),
    )


def test_planner_never_references_fireconnect() -> None:
    planner_path = ROUTING_ROOT / "planner.py"
    text = planner_path.read_text(encoding="utf-8")
    assert "fireconnect" not in text.lower()
    assert "FireConnect" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "fireconnect" not in node.value.lower()


def test_planner_produces_intent_only() -> None:
    planner = ExecutionPlanner()
    mission = MissionSpec(
        prompt="summarise repo",
        workspace_path="/tmp/ws",
        requires_internet=True,
        requires_provider_route=True,
        preferred_harness="opencode",
        preferred_model="accounts/fireworks/models/x",
    )
    intent = planner.plan(mission)
    assert intent.requires_provider_route is True
    assert ExecutionCapability.PROVIDER_ROUTING in intent.required_capabilities
    assert ExecutionCapability.INTERNET in intent.required_capabilities
    assert "backend" not in intent.as_dict()
    assert intent.preferred_harness == "opencode"


def test_router_selects_fireconnect_when_provider_routing_required() -> None:
    class _HealthyFC(FireConnectBackend):
        async def health(self):  # type: ignore[override]
            from joymesh.runtime_v1.execution_routing.models import BackendHealth

            return BackendHealth(
                healthy=True,
                backend_id=self.backend_id,
                detail="forced healthy",
                capabilities=self.capabilities(),
            )

    harnesses = builtin_harness_adapters()
    registry = BackendRegistry(
        {
            "fireconnect": _HealthyFC(
                harnesses=harnesses,
                healthy=True,
                provider_routes=ProviderRouteService(store=ProviderRouteLeaseStore()),
            ),
            "local": LocalBackend(harnesses=harnesses, healthy=True),
            "hosted": HostedBackend(healthy=True),
            "joymesh": JoyMeshBackend(healthy=True),
        }
    )
    router = ExecutionRouter(registry)
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="x",
            workspace_path="/tmp",
            requires_provider_route=True,
            preferred_harness="opencode",
        )
    )
    decision = router.select(intent)
    assert decision.selected_backend_id == "fireconnect"
    assert decision.provider_routing_required is True
    assert decision.selected_harness_id == "opencode"


def test_capability_matching_rejects_mismatch() -> None:
    registry = _registry()
    router = ExecutionRouter(registry)
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="x",
            workspace_path="/tmp",
            required_capabilities=frozenset({ExecutionCapability.GPU}),
            preferred_harness="codex",
        )
    )
    with pytest.raises(ExecutionRouterError) as excinfo:
        router.select(intent)
    assert excinfo.value.reason_code == "no_compatible_backend"


@pytest.mark.asyncio
async def test_fallback_ordering_on_backend_health_failure() -> None:
    harnesses = builtin_harness_adapters()
    registry = BackendRegistry(
        {
            "fireconnect": FireConnectBackend(harnesses=harnesses, healthy=False),
            "local": LocalBackend(harnesses=harnesses, healthy=True),
            "hosted": HostedBackend(enabled=False),
            "joymesh": JoyMeshBackend(healthy=False),
        },
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect", "local"),
            priority=("fireconnect", "local"),
            fallback_order=("fireconnect", "local"),
            default_backend=None,
        ),
    )
    service = ExecutionRoutingService(registry=registry)
    result = await service.execute(
        MissionSpec(
            prompt="local work",
            workspace_path="/tmp",
            preferred_harness="codex",
            requires_provider_route=False,
            requires_internet=False,
        )
    )
    assert result.ok is True
    assert result.backend_id == "local"
    assert "fireconnect" in result.attempted_backends
    assert any(a["event_type"] == "backend.unavailable" for a in result.audits)
    assert any(a["event_type"] == "backend.completed" for a in result.audits)
    assert result.execution_id  # preserved


@pytest.mark.asyncio
async def test_local_backend_performs_no_provider_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def boom(*_a, **_k):
        calls.append("mutate")
        raise AssertionError("provider mutation must not run")

    service = ProviderRouteService(store=ProviderRouteLeaseStore())
    monkeypatch.setattr(service, "run_temporary", boom)
    monkeypatch.setattr(service, "enable_permanently", boom)
    backend = LocalBackend(harnesses=builtin_harness_adapters())
    registry = BackendRegistry(
        {"local": backend},
        config=BackendRegistryConfig(
            enabled_backends=("local",), priority=("local",), fallback_order=("local",)
        ),
    )
    router = ExecutionRouter(registry)
    intent = ExecutionPlanner().plan(
        MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="opencode")
    )
    result = await router.execute_with_fallback(intent)
    assert result.ok
    assert result.backend_id == "local"
    assert calls == []


@pytest.mark.asyncio
async def test_fireconnect_backend_uses_provider_route_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    store = ProviderRouteLeaseStore()
    routes = ProviderRouteService(store=store)

    async def fake_temporary(manager_id, connector_id, *, execute, **kwargs):
        seen.append(f"{manager_id}:{connector_id}")
        payload = await execute()

        class _Life:
            ok = True
            message = "lifecycle"
            restoration_verified = True
            execution_result = payload

            def as_dict(self):
                return {"ok": True}

        return _Life()

    monkeypatch.setattr(routes, "run_temporary", fake_temporary)
    backend = FireConnectBackend(
        harnesses=builtin_harness_adapters(),
        healthy=True,
        provider_routes=routes,
    )

    async def _healthy():
        from joymesh.runtime_v1.execution_routing.models import BackendHealth

        return BackendHealth(True, "fireconnect", "ok", backend.capabilities())

    monkeypatch.setattr(backend, "health", _healthy)
    registry = BackendRegistry(
        {"fireconnect": backend},
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect",),
            priority=("fireconnect",),
            fallback_order=("fireconnect",),
        ),
    )
    result = await ExecutionRouter(registry).execute_with_fallback(
        ExecutionPlanner().plan(
            MissionSpec(
                prompt="x",
                workspace_path="/tmp",
                preferred_harness="opencode",
                requires_provider_route=True,
            )
        )
    )
    assert result.ok
    assert seen == ["fireconnect:opencode"]


def test_mission_graph_references_are_provider_neutral() -> None:
    planner = ExecutionPlanner()
    mission = MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="codex")
    intent = planner.plan(mission)
    request = planner.request_for(intent)
    payload = request.as_dict()
    blob = str(payload).lower()
    assert "fireconnect" not in blob
    assert payload["execution_id"] == intent.execution_id
    assert payload["mission_id"] == intent.mission_id


def test_unsupported_backend_rejected() -> None:
    from joymesh.runtime_v1.execution_routing import BackendRegistryError

    registry = BackendRegistry({})
    with pytest.raises(BackendRegistryError, match="unknown execution backend"):
        registry.get("nope")


def test_subscription_policy_respected() -> None:
    registry = _registry()
    router = ExecutionRouter(registry, subscription_allows={"fireconnect": False, "local": True})
    intent = ExecutionPlanner().plan(
        MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="codex")
    )
    decision = router.select(intent)
    assert decision.selected_backend_id == "local"


@pytest.mark.asyncio
async def test_execution_identifiers_preserved_across_fallback() -> None:
    harnesses = builtin_harness_adapters()
    registry = BackendRegistry(
        {
            "fireconnect": FireConnectBackend(harnesses=harnesses, healthy=False),
            "local": LocalBackend(harnesses=harnesses, healthy=True),
        },
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect", "local"),
            priority=("fireconnect", "local"),
            fallback_order=("fireconnect", "local"),
        ),
    )
    intent = ExecutionPlanner().plan(
        MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="claude")
    )
    result = await ExecutionRouter(registry).execute_with_fallback(intent)
    assert result.ok
    assert result.execution_id == intent.execution_id
    assert result.decision is not None
    assert result.decision.execution_id == intent.execution_id


@pytest.mark.asyncio
async def test_joymesh_backend_foundation_submit_claim() -> None:
    backend = JoyMeshBackend(healthy=True)
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="x",
            workspace_path="/tmp",
            preferred_harness="codex",
            requires_local_filesystem=False,
            requires_ephemeral_workspace=True,
            required_capabilities=frozenset({ExecutionCapability.REMOTE_WORKER}),
        )
    )
    from joymesh.runtime_v1.execution_routing.models import ExecutionDecision

    decision = ExecutionDecision(
        execution_id=intent.execution_id,
        selected_backend_id="joymesh",
        selected_harness_id="codex",
        reason="test",
        fallback_order=(),
        provider_routing_required=False,
    )
    result = await backend.execute(intent, decision)
    assert result.ok
    assert result.output["completion_verified"] is True


@pytest.mark.asyncio
async def test_all_backends_blocked_emits_audits() -> None:
    registry = BackendRegistry(
        {
            "local": LocalBackend(healthy=False),
            "hosted": HostedBackend(healthy=False),
        },
        config=BackendRegistryConfig(
            enabled_backends=("local", "hosted"),
            priority=("local", "hosted"),
            fallback_order=("local", "hosted"),
        ),
    )
    result = await ExecutionRouter(registry).execute_with_fallback(
        ExecutionPlanner().plan(
            MissionSpec(prompt="x", workspace_path="/tmp", preferred_harness="codex")
        )
    )
    assert result.ok is False
    assert result.status is ExecutionStatus.BLOCKED
    assert any(a["event_type"] == "backend.unavailable" for a in result.audits)


def test_static_planner_and_router_separation() -> None:
    router_text = (ROUTING_ROOT / "router.py").read_text(encoding="utf-8")
    # Router may know fireconnect as the only provider-routing backend implementation.
    assert "PROVIDER_ROUTING" in router_text or "provider_routing" in router_text
    planner_text = (ROUTING_ROOT / "planner.py").read_text(encoding="utf-8")
    assert not re.search(r"FireConnect|fireconnect", planner_text)
