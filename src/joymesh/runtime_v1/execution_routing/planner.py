"""ExecutionPlanner — produces intent only; never selects a backend."""

from __future__ import annotations

from uuid import uuid4

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.capability_routing.policies import RoutingPolicy
from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import TaskAnalyzer
from joymesh.runtime_v1.execution_routing.models import (
    ExecutionIntent,
    ExecutionRequest,
    MissionSpec,
)


class ExecutionPlanner:
    """Derive provider-neutral execution requirements from a mission.

    Performs task analysis and merges derived capabilities into the intent.
    Never selects backend, connector, or model — that remains the router's job.
    """

    def __init__(self, analyzer: TaskAnalyzer | None = None) -> None:
        self.analyzer = analyzer or TaskAnalyzer()

    def plan(self, mission: MissionSpec) -> ExecutionIntent:
        analysis = self.analyzer.analyse(mission.prompt, metadata=dict(mission.metadata))
        required: set[ExecutionCapability] = set(mission.required_capabilities)
        required |= set(analysis.derived_execution_capabilities)
        if mission.requires_internet:
            required.add(ExecutionCapability.INTERNET)
        if mission.requires_gpu:
            required.add(ExecutionCapability.GPU)
        if mission.requires_local_filesystem:
            required.add(ExecutionCapability.FILESYSTEM)
        if mission.requires_ephemeral_workspace:
            required.add(ExecutionCapability.EPHEMERAL_WORKSPACE)
        else:
            required.add(ExecutionCapability.PERSISTENT_WORKSPACE)
        if mission.requires_provider_route:
            required.add(ExecutionCapability.PROVIDER_ROUTING)
        if mission.requires_remote_worker:
            required.add(ExecutionCapability.REMOTE_WORKER)
        # Optional capabilities are preferences, not hard requirements.
        _ = mission.optional_capabilities

        routing_prefs = dict(mission.routing_preferences)
        if (
            mission.cost_preference
            and "preset" not in routing_prefs
            and "policy" not in routing_prefs
        ):
            routing_prefs.setdefault("preset", mission.cost_preference)
        if mission.locality_preference == "local":
            routing_prefs.setdefault("prefer_local", True)
        if analysis.prefers_local or analysis.privacy_required:
            routing_prefs.setdefault("prefer_local", True)
        # Validate/normalise policy early so intent carries a consistent shape.
        policy = RoutingPolicy.from_mapping(routing_prefs)

        execution_id = f"execution_{uuid4().hex}"
        return ExecutionIntent(
            mission_id=mission.mission_id,
            execution_id=execution_id,
            prompt=mission.prompt,
            workspace_path=mission.workspace_path,
            required_capabilities=frozenset(required),
            preferred_model=mission.preferred_model,
            preferred_harness=mission.preferred_harness,
            requires_provider_route=mission.requires_provider_route,
            requires_ephemeral_workspace=mission.requires_ephemeral_workspace,
            estimated_runtime_seconds=mission.estimated_runtime_seconds,
            estimated_token_usage=mission.estimated_token_usage,
            cost_preference=mission.cost_preference,
            project_id=mission.project_id,
            organisation_id=mission.organisation_id,
            workspace_ref=mission.workspace_ref or mission.workspace_path,
            execution_authorisation_id=mission.execution_authorisation_id,
            timeout_seconds=mission.timeout_seconds,
            locality_preference=mission.locality_preference,
            organisation_policy=dict(mission.organisation_policy),
            subscription_constraints=dict(mission.subscription_constraints),
            routing_preferences=policy.as_dict(),
            metadata=dict(mission.metadata),
            correlation_id=mission.correlation_id,
            task_class=analysis.task_class.value,
            required_semantic_capabilities=frozenset(
                item.value for item in analysis.required_semantic
            ),
            task_analysis=analysis.as_dict(),
        )

    def request_for(self, intent: ExecutionIntent) -> ExecutionRequest:
        return ExecutionRequest(
            execution_id=intent.execution_id,
            mission_id=intent.mission_id,
            intent=intent,
        )
