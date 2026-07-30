# ADR 0003: Provider-neutral execution routing

## Status

Accepted (2026-07-29); extended for capability-aware routes (2026-07-30)

## Context

Early Runtime v1 work centred FireConnect in execution stories: temporary
provider enablement, harness configuration, and lease coordination. That made
FireConnect look like *the* execution backend, even though ADR 0002 already
classified it as a **provider-route manager**, not a coding harness.

Missions need to run on multiple surfaces:

* FireConnect-backed local harnesses (with optional temporary provider routing)
* Pure local harnesses (no provider mutation)
* Future JoyMesh remote workers
* Hosted / other providers

Planner and mission-graph code must not import or assume FireConnect.

Separately, selecting a **model** alone is insufficient. Execution quality depends
on the combination of harness capabilities, connector access, and model strengths,
plus subscription, quota, cost, latency, privacy, and user policy.

## Decision

Introduce an explicit routing stack:

```text
Mission
  → ExecutionPlanner      (intent / capabilities / task analysis only)
  → ExecutionRouter       (Route = harness + backend + connector + model)
  → ExecutionBackend      (FireConnect | Local | JoyMesh | Hosted | …)
  → HarnessAdapter        (Codex | Claude | OpenCode | Cursor | …)
  → ProviderRouteService  (FireConnectBackend only, when required)
```

The execution unit is:

```text
Route = Harness + Connector + Model
```

(with a backend that can host that route).

Capability-aware flow:

```text
Task → Task Analysis → Capability Matching → Harness → Connector → Model → Execution
```

Implemented under `execution_routing/capability_routing/`:

* **TaskAnalyzer** — classifies prompts into task classes and semantic requirements
* **Capability profiles** — harness / connector / model registries (routing hints;
  catalogue YAML remains install/readiness authority)
* **RoutingPolicy** — user presets (`prefer_local`, `prefer_cheapest`, …); policies
  influence scoring but **never** bypass hard capability requirements
* **ScoredRoute** — multi-factor score (capability, subscription, quota, quality,
  health, latency, cost, policy)
* **CapabilityAwareRouteSelector** — ranks Route candidates for the router

Rules:

1. **Planner never references FireConnect** or any backend id.
2. **Planner may analyse the task** and derive required capabilities; it never
   selects backend, connector, or model.
3. **Router** owns selection, capability matching, subscription compatibility,
   fallback, route scoring, and provider-neutral backend audits.
4. **Only `FireConnectBackend`** may call `ProviderRouteService`.
5. **Harness ≠ backend ≠ connector ≠ model** — the same harness may run on
   multiple backends with different connectors/models.
6. Mission graph stores `ExecutionRequest` / `ExecutionDecision` /
   `ExecutionResult`, not FireConnect-specific job types.
7. Extension points (historical success rates, A/B evaluation, org policies,
   quota forecasting) plug into scoring without redesigning the Route unit.

## Consequences

* FireConnect becomes an implementation detail behind `ExecutionBackend`.
* Local execution works without provider-route leases or mutations.
* JoyMesh remote workers grow behind `JoyMeshBackend` without rewriting planners.
* Existing provider-route concurrency / bypass hardening remains scoped under
  FireConnectBackend + `ProviderRouteService`.
* **Live `RuntimeService.route_task` is authoritative through `ExecutionRouter`.**
  The control-plane node scheduler remains the placement engine for
  `JoyMeshBackend` only.
* Users can describe the task and policy; JoyMesh selects the optimal Route.

## Related

* ADR 0002 — provider-route managers vs coding-harness connectors
* `docs/runtime/execution-routing.md`
* `joymesh.runtime_v1.execution_routing`
* `joymesh.runtime_v1.execution_routing.capability_routing`
