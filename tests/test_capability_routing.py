"""Capability-aware routing: task analysis, policies, and Route scoring."""

from __future__ import annotations

from joymesh.runtime_v1.execution_routing import (
    BackendRegistry,
    BackendRegistryConfig,
    ExecutionPlanner,
    ExecutionRouter,
    FireConnectBackend,
    HostedBackend,
    JoyMeshBackend,
    LocalBackend,
    MissionSpec,
    builtin_harness_adapters,
)
from joymesh.runtime_v1.execution_routing.capability_routing import (
    CapabilityAwareRouteSelector,
    RoutingPolicy,
    RoutingPolicyPreset,
    TaskAnalyzer,
    TaskClass,
)
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService


def _registry() -> BackendRegistry:
    harnesses = builtin_harness_adapters()
    return BackendRegistry(
        {
            "fireconnect": FireConnectBackend(
                harnesses=harnesses,
                healthy=True,
                provider_routes=ProviderRouteService(store=ProviderRouteLeaseStore()),
            ),
            "local": LocalBackend(harnesses=harnesses, healthy=True),
            "hosted": HostedBackend(healthy=True),
            "joymesh": JoyMeshBackend(healthy=True),
        },
        config=BackendRegistryConfig(
            enabled_backends=("fireconnect", "local", "hosted", "joymesh"),
            priority=("fireconnect", "local", "hosted", "joymesh"),
            fallback_order=("fireconnect", "local", "hosted"),
        ),
    )


def test_task_analyzer_classifies_repository_refactor() -> None:
    analysis = TaskAnalyzer().analyse("Large repository refactor across packages")
    assert analysis.task_class is TaskClass.REPOSITORY_REFACTOR
    assert "repository_editing" in analysis.required_semantic_values()
    assert "autonomous_coding" in analysis.required_semantic_values()


def test_task_analyzer_classifies_interactive_and_private() -> None:
    interactive = TaskAnalyzer().analyse("Interactive IDE pair programming edit")
    assert interactive.task_class is TaskClass.INTERACTIVE_EDIT
    private = TaskAnalyzer().analyse("Private codebase local-only air-gap work")
    assert private.task_class is TaskClass.PRIVATE_CODEBASE
    assert private.privacy_required is True


def test_planner_embeds_task_analysis_without_backend() -> None:
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="Cheap autonomous coding with open models",
            workspace_path="/tmp/ws",
            cost_preference="cheapest",
        )
    )
    assert intent.task_class is not None
    assert intent.task_analysis
    assert "backend" not in intent.as_dict()
    assert intent.routing_preferences.get("prefer_cheapest") or intent.routing_preferences.get(
        "preset"
    ) in {"cheapest", "prefer_cheapest", "balanced"}


def test_router_selects_codex_for_large_refactor_without_preferred_harness() -> None:
    router = ExecutionRouter(_registry(), available_harnesses=("codex", "cursor", "opencode"))
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="Large repository refactor with terminal tests",
            workspace_path="/tmp/ws",
        )
    )
    decision = router.select(intent)
    assert decision.selected_harness_id == "codex"
    assert decision.task_analysis.get("task_class") == "repository_refactor"
    assert decision.route_score is not None
    assert decision.selected_connector_id is not None
    assert decision.selected_model_id is not None


def test_router_selects_cursor_for_interactive_edit() -> None:
    router = ExecutionRouter(_registry(), available_harnesses=("codex", "cursor", "opencode"))
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="Interactive editing in the IDE for pair programming",
            workspace_path="/tmp/ws",
        )
    )
    decision = router.select(intent)
    assert decision.selected_harness_id == "cursor"


def test_router_prefers_local_connector_for_private_codebase() -> None:
    router = ExecutionRouter(
        _registry(),
        available_harnesses=("opencode", "codex", "cursor"),
        available_connectors=("lmstudio", "ollama", "openai", "fireconnect"),
    )
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="Private codebase local-only, no cloud",
            workspace_path="/tmp/ws",
            locality_preference="local",
            routing_preferences={"preset": "prefer_local"},
        )
    )
    decision = router.select(intent)
    assert decision.selected_harness_id == "opencode"
    assert decision.selected_backend_id == "local"
    assert decision.selected_connector_id in {"lmstudio", "ollama"}
    assert decision.selected_model_id in {"local-default", "qwen-local"}


def test_router_cheap_policy_favours_open_model_route() -> None:
    router = ExecutionRouter(
        _registry(),
        available_harnesses=("opencode", "codex"),
        available_connectors=("fireconnect", "openai"),
    )
    intent = ExecutionPlanner().plan(
        MissionSpec(
            prompt="Cheap autonomous coding with open model qwen",
            workspace_path="/tmp/ws",
            requires_provider_route=True,
            cost_preference="cheapest",
            routing_preferences={"preset": "prefer_cheapest", "prefer_open_models": True},
        )
    )
    decision = router.select(intent)
    assert decision.selected_backend_id == "fireconnect"
    assert decision.selected_harness_id == "opencode"
    assert decision.selected_connector_id == "fireconnect"
    assert decision.selected_model_id in {"qwen", "deepseek", "gpt-4.1"}


def test_policy_does_not_bypass_capability_requirements() -> None:
    selector = CapabilityAwareRouteSelector()
    analysis = TaskAnalyzer().analyse("Interactive editing in the IDE")
    policy = RoutingPolicy.from_mapping(
        {
            "preset": RoutingPolicyPreset.PREFER_CHEAPEST.value,
            "preferred_harnesses": ["opencode"],
        }
    )
    order = selector.order_harnesses(
        available_harnesses=("opencode", "cursor", "codex"),
        analysis=analysis,
        policy=policy,
        preferred_harness=None,
    )
    # Cursor remains the capability match; cheapest preference does not promote opencode above it.
    assert order[0] == "cursor"


def test_routing_policy_from_mission_cost_preference() -> None:
    policy = RoutingPolicy.from_mapping({"preset": "cheapest"})
    assert policy.prefer_cheapest is True
    assert policy.preset is RoutingPolicyPreset.PREFER_CHEAPEST
