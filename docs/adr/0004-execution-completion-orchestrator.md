# ADR 0004: Execution completion orchestrator

## Status

Accepted

## Context

Live execution correctly flows through planner → router → backend → harness.
Backends previously had ambiguous authority over terminal mission state: a
successful process exit could be treated as mission success.

Remote node events had a parallel terminal path. FireConnect restore failures,
stale attempts, and verification needed a single control-plane owner.

## Decision

Backends report execution facts and candidate evidence.

The control-plane completion orchestrator owns evidence acceptance,
verification, usage finalisation, mission graph projection and terminal state.

```text
ExecutionBackend
  ↓
ExecutionResult + candidate evidence
  ↓
ExecutionCompletionOrchestrator
  ├── EvidenceBoundary
  ├── VerificationService
  ├── UsageFinaliser
  └── MissionGraphProjector
        ↓
authoritative terminal execution and mission state
```

**Invariant:** Backend success means execution finished. Verified completion
means the mission may complete.

**Invariant:** No backend, harness or worker may directly complete a mission.

Package: `joymesh.runtime_v1.completion`.

## Consequences

* `RuntimeService` hands backend results and remote terminal events to the
  orchestrator; it does not independently mark missions complete from backend
  `ok`.
* Scheduler / node placement remain lease and routing concerns only.
* Harness adapters may emit structured candidate evidence; they must not mark
  evidence trusted or decide mission verification policy.
* FireConnect unresolved restoration blocks successful finalisation and
  fallback on the affected connector.
* Completion stages are idempotent and resumable after restart.
