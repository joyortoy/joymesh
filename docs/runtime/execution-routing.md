# Execution routing (provider-neutral)

JoyMesh separates mission planning from where work runs. FireConnect is **one
execution backend**, not the execution architecture itself.

The execution unit is not a model. It is a **Route**:

```text
Route = Harness + Connector + Model
```

hosted on an execution backend (local, FireConnect, JoyMesh, …).

```text
Mission scheduler
    ↓
RuntimeService
    ↓
ExecutionPlanner          (task analysis + intent; never picks a backend)
    ↓
ExecutionRouter           (capability-aware Route scoring + fallback)
    ↓
ExecutionBackend
    ├── LocalBackend
    ├── FireConnectBackend
    └── JoyMeshBackend
          ↓
HarnessAdapter
          ↓
Connector + Model (provider or local)
```

**Invariant:** All live mission execution is selected through `ExecutionRouter`.
No production runtime component above `ExecutionBackend` may select, configure,
or invoke FireConnect, a provider, or a harness directly.

| Concept | Answers |
| --- | --- |
| Backend | Where execution occurs |
| Harness | Which execution tool performs the task |
| Connector | How models/providers are accessed |
| Model | Which model strengths apply |
| Provider route | Temporary provider exposure on a compatible backend |

Package: `joymesh.runtime_v1.execution_routing`.
Capability routing: `…/capability_routing`.

## Capability-aware selection

```text
Task
  → Task Analysis (task class + semantic capabilities)
  → Capability Matching (harness / connector / model profiles)
  → Route Scoring (subscription, quota, quality, health, latency, −cost, policy)
  → Highest eligible Route wins
```

User policies (`prefer_local`, `prefer_cheapest`, `prefer_fastest`,
`prefer_strongest_reasoning`, `prefer_open_models`, `avoid_paid_apis`,
`maximize_quality`, `balanced`) adjust scores but **do not** waive capability
requirements.

`ExecutionDecision` records `selected_harness_id`, `selected_backend_id`,
`selected_connector_id`, `selected_model_id`, `route_score`, and `task_analysis`.

When `preferred_harness` is set, backend priority and fallback order are preserved
for compatibility; connector/model are still scored for that harness+backend.

## Live path

`RuntimeService.route_task`:

1. Builds `MissionSpec` (capabilities, workspace ref, harness preference)
2. `ExecutionPlanner.plan` → `ExecutionIntent` / `ExecutionRequest`
   (includes task analysis; still backend-free)
3. `ExecutionRouter.select` → `ExecutionDecision`
4. Persists decision on the runtime task (`execution_id`, backend, harness, fallback)
5. `ExecutionRouter.execute_with_fallback` → backend attempt(s)
6. Backend `ExecutionResult` → `ExecutionCompletionOrchestrator`
   (evidence → verification → usage → graph → terminal state)
7. Projects events / audits / mission status

**Invariant:** Backend process success is not mission completion. See
[Execution completion](execution-completion.md).

Remote node leases are performed inside `JoyMeshBackend` via the existing
scheduler/lease lifecycle — not a second job protocol. Remote terminal events
also enter the completion orchestrator.

## Failure taxonomy and fallback

Typed failures in `failures.py`. Fallback is allowed only for retryable classes
(e.g. backend unavailable/unhealthy, preparation failure, rate limit). It is
**forbidden** for policy denial, workspace/tenant violations, evidence/verification
failures, unresolved FireConnect restore failure, and connector-blocked.

## Cancellation

`CancellationRegistry` tracks the active backend/attempt. Cancellation flows:

```text
RuntimeService.cancel_task → ExecutionRouter.cancel → backend/harness cleanup
→ ExecutionCompletionOrchestrator.finalise_cancelled → mission CANCELLED
```

Idempotent; late events after cancel are ignored.

## Health

Default `BackendRegistry` construction does **not** probe live FireConnect.
`FireConnectBackend(skip_live_probe=True)` returns cached/unknown health unless
an explicit `health_probe` is injected. `HostedBackend` is a disabled stub and
never eligible.

## Related

* [Execution completion](execution-completion.md)
* [Worker runtime](worker-runtime.md)
* [Provider routes](provider-routes.md)
* [ADR 0003](../adr/0003-provider-neutral-execution-routing.md)
* [ADR 0004](../adr/0004-execution-completion-orchestrator.md)
* [ADR 0005](../adr/0005-distributed-fleet-scheduler.md)
