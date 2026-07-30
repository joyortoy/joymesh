"""Bridge RuntimeService tasks ↔ provider-neutral execution routing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.models import MissionSpec
from joymesh.runtime_v1.models import RuntimeTaskRecord, RuntimeTaskRequest


def mission_spec_from_task(
    task: RuntimeTaskRecord,
    *,
    prompt: str,
    workspace_path: str,
    workspace_ref: str | None = None,
    locality_preference: str = "any",
    requires_remote_worker: bool = False,
) -> MissionSpec:
    """Build planner input from a runtime task — never names a backend."""

    preferred_harness = task.required_connector
    if preferred_harness is None and task.preferred_connectors:
        preferred_harness = task.preferred_connectors[0]

    requires_provider_route = False
    if task.required_provider and task.required_provider not in {"native", "local"}:
        requires_provider_route = True
    if any(item not in {"native", "local"} for item in task.preferred_providers):
        # Preference alone does not force provider routing; only explicit required_provider does.
        pass

    required_caps: set[ExecutionCapability] = set()
    # Map a few runtime capability strings when present.
    for item in task.expanded_capabilities or task.requested_capabilities:
        mapped = _map_runtime_capability(item)
        if mapped is not None:
            required_caps.add(mapped)

    return MissionSpec(
        prompt=prompt,
        workspace_path=workspace_path,
        mission_id=task.task_id,
        project_id=task.workspace_id,
        workspace_ref=workspace_ref or task.workspace_id,
        preferred_harness=preferred_harness,
        preferred_model=task.selected_model_id,
        required_capabilities=frozenset(required_caps),
        requires_provider_route=requires_provider_route,
        requires_remote_worker=requires_remote_worker,
        requires_local_filesystem=not requires_remote_worker,
        locality_preference=locality_preference,
        timeout_seconds=task.timeout_seconds,
        routing_preferences={
            "preferred_connectors": list(task.preferred_connectors),
            "preferred_nodes": list(task.preferred_nodes),
            "required_node": task.required_node,
            "preferred_providers": list(task.preferred_providers),
            "required_provider": task.required_provider,
        },
        organisation_policy={"policy_profile": task.policy_profile},
        correlation_id=task.task_id,
        metadata={
            "user_id": task.user_id,
            "max_attempts": task.max_attempts,
        },
    )


def mission_spec_from_request(
    request: RuntimeTaskRequest,
    *,
    workspace_path: str,
    locality_preference: str = "any",
    requires_remote_worker: bool = False,
) -> MissionSpec:
    preferred_harness = request.required_connector
    if preferred_harness is None and request.preferred_connectors:
        preferred_harness = request.preferred_connectors[0]
    requires_provider_route = bool(
        request.required_provider and request.required_provider not in {"native", "local"}
    )
    return MissionSpec(
        prompt=request.prompt,
        workspace_path=workspace_path,
        mission_id=request.task_id,
        project_id=request.workspace_id,
        workspace_ref=request.workspace_id,
        preferred_harness=preferred_harness,
        required_capabilities=frozenset(),
        requires_provider_route=requires_provider_route,
        requires_remote_worker=requires_remote_worker,
        requires_local_filesystem=not requires_remote_worker,
        locality_preference=locality_preference,
        timeout_seconds=request.timeout_seconds,
        routing_preferences={
            "preferred_connectors": list(request.preferred_connectors),
            "preferred_nodes": list(request.preferred_nodes),
            "required_node": request.required_node,
            "preferred_providers": list(request.preferred_providers),
            "required_provider": request.required_provider,
        },
        organisation_policy={"policy_profile": request.policy_profile},
        correlation_id=request.task_id,
        metadata={"user_id": request.user_id, "max_attempts": request.max_attempts},
    )


def _map_runtime_capability(value: str) -> ExecutionCapability | None:
    mapping: Mapping[str, ExecutionCapability] = {
        "internet": ExecutionCapability.INTERNET,
        "gpu": ExecutionCapability.GPU,
        "filesystem": ExecutionCapability.FILESYSTEM,
        "browser": ExecutionCapability.BROWSER,
        "vision": ExecutionCapability.VISION,
        "voice": ExecutionCapability.VOICE,
        "multi_agent": ExecutionCapability.MULTI_AGENT,
    }
    return mapping.get(value)


def decision_fields(decision: Any) -> dict[str, Any]:
    return {
        "execution_id": decision.execution_id,
        "selected_backend_id": decision.selected_backend_id,
        "selected_harness_id": decision.selected_harness_id,
        "selected_connector_id": decision.selected_harness_id,
        "execution_decision_reason": decision.reason,
        "execution_fallback_order": tuple(decision.fallback_order),
        "provider_routing_required": decision.provider_routing_required,
    }
