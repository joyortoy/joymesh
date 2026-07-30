"""Facade: Mission → Planner → Router → Backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from joymesh.runtime_v1.execution_routing.models import (
    ExecutionDecision,
    ExecutionIntent,
    ExecutionRequest,
    ExecutionResult,
    MissionSpec,
)
from joymesh.runtime_v1.execution_routing.planner import ExecutionPlanner
from joymesh.runtime_v1.execution_routing.registry import BackendRegistry
from joymesh.runtime_v1.execution_routing.router import ExecutionRouter


class ExecutionRoutingService:
    """Provider-neutral orchestration entry point."""

    def __init__(
        self,
        *,
        registry: BackendRegistry | None = None,
        planner: ExecutionPlanner | None = None,
        router: ExecutionRouter | None = None,
        available_harnesses: Sequence[str] | None = None,
        subscription_allows: Mapping[str, bool] | None = None,
    ) -> None:
        self.registry = registry or BackendRegistry()
        self.planner = planner or ExecutionPlanner()
        self.router = router or ExecutionRouter(
            self.registry,
            available_harnesses=available_harnesses,
            subscription_allows=subscription_allows,
        )

    def plan(self, mission: MissionSpec) -> ExecutionIntent:
        return self.planner.plan(mission)

    def request(self, mission: MissionSpec) -> ExecutionRequest:
        intent = self.plan(mission)
        return self.planner.request_for(intent)

    def decide(self, mission: MissionSpec) -> ExecutionDecision:
        intent = self.plan(mission)
        return self.router.select(intent)

    async def execute(self, mission: MissionSpec) -> ExecutionResult:
        intent = self.plan(mission)
        return await self.router.execute_with_fallback(intent)

    async def execute_intent(self, intent: ExecutionIntent) -> ExecutionResult:
        return await self.router.execute_with_fallback(intent)
