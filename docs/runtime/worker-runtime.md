# JoyMesh worker runtime

JoyMesh is a neutral harness execution and worker runtime. It does **not** own
distributed fleet scheduling, organisation fairness, or mission completion.

```text
External planning agent or control plane
        │
        │ execution request + externally issued lease
        ▼
JoyMesh worker runtime
  ├── Capability reporting
  ├── Heartbeat emission
  ├── Worker-side lease validation
  ├── Harness execution
  ├── Streaming / sessions / cancellation
  ├── Remote transport
  ├── Usage observation
  └── Factual execution results
```

## Contracts

Package: `joymesh.runtime_v1.contracts`

* `WorkerReport` — observed worker identity, capacity, harnesses
* `WorkerCapacityReport` — CPU/RAM/GPU/disk/parallelism observations
* `WorkerHeartbeat` — sequenced heartbeat facts (no healthy/degraded/lost policy)
* `ExecutionLeaseToken` — externally issued lease payload
* `ExecutionOffer` / `ExecutionAcknowledgement`
* `FactualExecutionResult` — process facts only

A successful process result does **not** mean the original task is complete.

## Worker helpers

Package: `joymesh.runtime_v1.workers`

* `build_worker_report(snapshot)` — project node facts for an external CP
* `build_worker_heartbeat(report, sequence=…)`
* `WorkerLeaseValidator` — validate lease id/worker/execution/generation/fencing/expiry/signature/replay

JoyMesh does not decide which worker receives a lease.

## RuntimeService

`register_node` stores a local snapshot for connector eligibility ranking and
publishes a neutral `WorkerReport`. It does **not** register workers into a
JoyMesh-owned fleet scheduler.

## Non-goals

JoyMesh does not provide:

* `DistributedScheduler` / placement scoring
* organisation fairness or quotas
* scheduler HA / leadership
* fleet worker lease granting
* mission verification or completion authority

See [ADR 0005](../adr/0005-distributed-fleet-scheduler.md).
