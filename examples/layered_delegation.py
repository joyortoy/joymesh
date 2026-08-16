"""Credential-free example of layered, parallel agent delegation."""

from __future__ import annotations

import asyncio

from joymesh import AgentFeedback, DelegatedTask, ParallelDelegator


async def dispatch(task: DelegatedTask) -> AgentFeedback:
    """Stand in for a JoyMesh route to the requested harness and model."""

    await asyncio.sleep(0.1)
    return AgentFeedback(
        task_id=task.id,
        summary=f"Completed: {task.task}",
        evidence=(f"demo://{task.id}",),
        harness_id=task.preferred_harness or "auto",
        model_id=task.preferred_model or "auto",
    )


async def main() -> None:
    tasks = [
        DelegatedTask(
            id="opencode",
            task="Find the relevant OpenCode integration points",
            preferred_harness="opencode",
        ),
        DelegatedTask(
            id="deepseek",
            task="Compare suitable DeepSeek models for this task",
            preferred_model="deepseek-chat",
        ),
    ]
    report = await ParallelDelegator(dispatch, max_parallel=2).delegate(tasks)
    for result in report.results:
        print(f"[{result.task_id}] {result.summary}")
        print(f"  route: {result.harness_id} / {result.model_id}")


if __name__ == "__main__":
    asyncio.run(main())
