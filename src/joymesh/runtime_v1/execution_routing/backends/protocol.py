"""ExecutionBackend protocol — provider implementations plug in here."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.models import (
    BackendHealth,
    ExecutionDecision,
    ExecutionIntent,
    ExecutionResult,
)


@runtime_checkable
class ExecutionBackend(Protocol):
    backend_id: str
    display_name: str

    def capabilities(self) -> frozenset[ExecutionCapability]: ...

    def supports(
        self,
        intent: ExecutionIntent,
        *,
        harness_id: str,
    ) -> bool: ...

    async def health(self) -> BackendHealth: ...

    async def prepare(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]: ...

    async def validate(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> None: ...

    async def execute(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
        *,
        prepared: Mapping[str, Any] | None = None,
    ) -> ExecutionResult: ...

    async def cancel(self, execution_id: str) -> None: ...

    async def cleanup(self, execution_id: str) -> None: ...
