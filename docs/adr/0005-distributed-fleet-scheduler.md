# ADR 0005: Control-plane scheduling boundary

## Status

Accepted (supersedes the earlier JoyMesh-owned fleet scheduler experiment)

## Context

A JoyMesh-local `distributed_scheduler` package briefly owned fleet placement,
queues, fairness, worker leases, and scheduler HA. That control-plane work now
lives in JoyCLI.

JoyMesh must remain an open-source, reusable harness execution and worker
runtime layer usable without JoyCLI.

## Decision

JoyCLI owns distributed fleet scheduling and mission orchestration.

JoyMesh provides a neutral worker runtime and harness execution fabric.

JoyMesh does not select workers, schedule missions, apply organisation fairness,
grant fleet leases, or determine mission completion.

```text
External control plane (JoyCLI)
        │
        │ explicit execution request + externally issued lease
        ▼
JoyMesh worker runtime
  ├── Capability reporting
  ├── Heartbeat emission
  ├── Lease validation
  ├── Harness execution
  ├── Streaming / sessions / cancellation
  ├── Remote transport
  ├── Usage observation
  └── Factual execution result
```

Neutral contracts live under `joymesh.runtime_v1.contracts` and
`joymesh.runtime_v1.workers`.

The package `joymesh.runtime_v1.distributed_scheduler` is a deprecation shim
only and raises `ImportError` for removed control-plane symbols.

Fleet SQL tables (`fleet_*`) are dropped by migration `f6a7b8c9d0e1`.

## Consequences

* Third parties can use JoyMesh standalone for harness execution and remote
  workers without installing JoyCLI.
* JoyCLI (or another control plane) supplies placement, fairness, queues, and
  lease granting.
* JoyMesh may validate an externally issued lease and emit worker/heartbeat
  facts, but must not classify fleet eligibility or grant capacity.
