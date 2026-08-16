from __future__ import annotations

import asyncio

import pytest

from joymesh import (
    AgentFeedback,
    DelegatedTask,
    DelegationStatus,
    ParallelDelegator,
)


@pytest.mark.asyncio
async def test_delegates_in_parallel_and_preserves_order() -> None:
    active = 0
    peak_active = 0

    async def dispatch(task: DelegatedTask) -> AgentFeedback:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return AgentFeedback(task_id=task.id, summary=f"finished {task.id}")

    report = await ParallelDelegator(dispatch, max_parallel=2).delegate(
        [
            DelegatedTask(id="harness", task="Inspect the harness"),
            DelegatedTask(id="model", task="Inspect the model"),
            DelegatedTask(id="docs", task="Summarize the documentation"),
        ]
    )

    assert peak_active == 2
    assert [result.task_id for result in report.results] == ["harness", "model", "docs"]
    assert report.succeeded


@pytest.mark.asyncio
async def test_worker_failure_does_not_cancel_siblings() -> None:
    async def dispatch(task: DelegatedTask) -> AgentFeedback:
        if task.id == "broken":
            raise RuntimeError("worker unavailable")
        return AgentFeedback(task_id=task.id, summary="useful result", input_tokens=10)

    report = await ParallelDelegator(dispatch).delegate(
        [
            DelegatedTask(id="broken", task="Fail"),
            DelegatedTask(id="healthy", task="Succeed"),
        ]
    )

    assert report.results[0].status is DelegationStatus.FAILED
    assert report.results[1].status is DelegationStatus.SUCCEEDED
    assert report.total_tokens == 10


@pytest.mark.asyncio
async def test_rejects_duplicate_task_ids() -> None:
    async def dispatch(task: DelegatedTask) -> AgentFeedback:
        return AgentFeedback(task_id=task.id, summary="done")

    with pytest.raises(ValueError, match="unique"):
        await ParallelDelegator(dispatch).delegate(
            [DelegatedTask(id="same", task="One"), DelegatedTask(id="same", task="Two")]
        )
