"""Context-efficient parallel delegation for planning agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DelegationStatus(StrEnum):
    """Outcome of one delegated task."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class DelegatedTask:
    """A bounded unit of work with only the context its worker needs."""

    id: str
    task: str
    workspace: str = "."
    preferred_harness: str | None = None
    preferred_model: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentFeedback:
    """Compact evidence returned from a worker to the planning agent."""

    task_id: str
    summary: str
    status: DelegationStatus = DelegationStatus.SUCCEEDED
    evidence: tuple[str, ...] = ()
    harness_id: str | None = None
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DelegationReport:
    """Ordered, compact feedback for the original planning agent."""

    results: tuple[AgentFeedback, ...]

    @property
    def succeeded(self) -> bool:
        return all(result.status is DelegationStatus.SUCCEEDED for result in self.results)

    @property
    def total_tokens(self) -> int | None:
        counts = [
            count
            for result in self.results
            for count in (result.input_tokens, result.output_tokens)
            if count is not None
        ]
        return sum(counts) if counts else None


Dispatch = Callable[[DelegatedTask], Awaitable[AgentFeedback]]


class ParallelDelegator:
    """Run isolated tasks concurrently and collect planner-ready feedback.

    The dispatcher is deliberately injected. It can route through JoyMesh to a
    local harness, an open-source model, a hosted model, or another worker
    without requiring the planning agent to change its own harness.
    """

    def __init__(self, dispatch: Dispatch, *, max_parallel: int = 4) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self._dispatch = dispatch
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def delegate(self, tasks: Sequence[DelegatedTask]) -> DelegationReport:
        """Dispatch tasks concurrently while preserving their input order."""

        task_ids = [task.id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("delegated task ids must be unique")

        async def run_one(task: DelegatedTask) -> AgentFeedback:
            async with self._semaphore:
                try:
                    feedback = await self._dispatch(task)
                except Exception as exc:  # A failed worker must not cancel its siblings.
                    return AgentFeedback(
                        task_id=task.id,
                        summary=str(exc),
                        status=DelegationStatus.FAILED,
                    )
                if feedback.task_id != task.id:
                    raise ValueError(
                        f"dispatcher returned task_id {feedback.task_id!r} for task {task.id!r}"
                    )
                return feedback

        return DelegationReport(results=tuple(await asyncio.gather(*(run_one(t) for t in tasks))))
