# Layered agent delegation

JoyMesh lets a planning agent delegate focused work without moving the planner
or its full conversation into every worker. The planner keeps authority over the
original task; JoyMesh owns the neutral mechanics of routing, execution, and
feedback collection.

## The layers

1. **Planning layer** — your existing agent identifies independent subtasks and
   decides what evidence is sufficient.
2. **Delegation layer** — `DelegatedTask` carries only the prompt, workspace,
   route preferences, and context required by one worker.
3. **Routing layer** — JoyMesh matches harness and model preferences against
   installed capabilities, policy, availability, and cost constraints. Routes
   can be previewed before execution.
4. **Execution layer** — bounded workers run concurrently. One failure is
   returned as data and does not cancel unrelated tasks.
5. **Feedback layer** — `AgentFeedback` returns a concise summary, evidence,
   selected harness/model, and token counts to the planner.

This design can reduce context duplication; it does not guarantee lower cost.
Savings depend on the tasks, prompts, models, and amount of feedback returned.

## SDK pattern

```python
from joymesh import AgentFeedback, DelegatedTask, ParallelDelegator


async def dispatch(task: DelegatedTask) -> AgentFeedback:
    # Preview/select a JoyMesh route, execute the worker, and normalize its
    # final output here. The planning agent does not need to change harnesses.
    return AgentFeedback(
        task_id=task.id,
        summary="The focused answer for the planning agent",
        evidence=("path/to/file.py:42",),
        harness_id=task.preferred_harness,
        model_id=task.preferred_model,
    )


delegator = ParallelDelegator(dispatch, max_parallel=3)
report = await delegator.delegate(
    [
        DelegatedTask(
            id="integration",
            task="Find the OpenCode integration points",
            preferred_harness="opencode",
        ),
        DelegatedTask(
            id="model",
            task="Evaluate the suitable DeepSeek model",
            preferred_model="deepseek-chat",
        ),
    ]
)
```

The result order matches the submitted task order, even when workers finish in
a different order. `report.succeeded` describes the batch and
`report.total_tokens` aggregates reported worker usage.

## Context contract

Send a worker only what it needs:

- a concrete deliverable and definition of done;
- the relevant files, URLs, or facts—not the planner's whole transcript;
- allowed tools, harnesses, models, and cost or privacy constraints;
- the expected feedback shape and evidence requirements.

Keep raw worker logs in the execution layer. Return a compact summary and
references to the planner, which can request more detail only when needed.

## Safety boundary

Planning authority remains outside JoyMesh. A successful worker process proves
only that the delegated execution finished. The planning agent still decides
whether the evidence is correct and whether the original task is complete.
