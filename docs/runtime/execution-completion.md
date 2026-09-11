# Execution completion and verification

**No backend, harness or worker may directly complete a mission.**

```text
ExecutionRouter
  ↓
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

## Core invariant

| Signal | Meaning |
| --- | --- |
| Backend success | Execution finished |
| Verified completion | Mission may complete |

Package: `joymesh.runtime_v1.completion`.

## Lifecycle states

```text
queued → routing → preparing → running
  → backend_completed → evidence_pending → evidence_accepted
  → verification_pending → verifying → verified → finalising → completed
```

Terminal non-success: `failed`, `blocked`, `cancelled`, `timed_out`.

`completed` means verified mission completion — not process exit.

## Backend result contract

`ExecutionResult` carries provider-neutral execution facts only:

* identity (`execution_id`, `attempt_id`)
* backend / harness / status / timestamps
* exit classification, structured output refs, candidate evidence
* usage facts, diagnostics, cleanup / restore status
* remote execution reference where applicable

Backends must not assert final mission verification. `candidate_verification`
(deprecated alias: `verification`) is untrusted observation only.

## Evidence intake and trust

All backends share `EvidenceBoundary`.

Trust classifications: `untrusted_backend_output`, `declared`, `accepted`,
`verified`, `rejected`.

Deterministic rejection reasons include identity/tenant/project/mission/
execution/attempt mismatch, invalid sequence, conflicting duplicates,
unsupported type, oversized payload, invalid hash, missing provenance,
stale attempt, late-after-terminal, cancelled execution.

Secrets in payloads are redacted. Credentials and lease tokens must not be
stored.

## Verification

`VerificationService` accepts mission requirements, accepted evidence, and
policy. Outcomes are typed: `verified`, `failed`, `inconclusive`, `blocked`,
`cancelled`, `verification_error`, `pending_human_approval`.

Strategies include test-command, artifact existence, content-hash match,
schema validation, remote verifier event, composite, and
backend-success-with-evidence.

Verification failure is not backend unavailability and does not auto-fallback
unless an explicit retry policy authorises it.

## Local, FireConnect, and remote

The same orchestrator handles Local, FireConnect, JoyMesh, and future hosted
backends. Remote node events (`accepted`, `started`, `output`, evidence,
verification, completed/failed/cancelled) project into this model.

Remote process completion ≠ mission completion.

## Attempt authority

Only the authoritative attempt may finalise. Late / superseded attempts are
rejected (`stale_attempt`, `superseded_attempt`, `late_event`, `lease_lost`)
without changing authoritative terminal state.

Fallback occurs before authoritative verification finalisation unless policy
explicitly permits verification-driven retry.

## FireConnect restoration

Unresolved route restoration → no unsafe fallback → no successful
finalisation. The orchestrator blocks completion, preserves audit-safe
evidence, and does not mark the mission verified.

## Cancellation and timeout

```text
cancel requested → backend/harness cancel → cleanup → restore if required
  → orchestrator finalises cancelled → late output rejected
```

Timeout classes (launch, execution, stream inactivity, verification, cleanup,
remote lease) are typed. Verification timeout is not backend failure.

## Usage finalisation

Attempt usage is aggregated once per execution inside the completion path.
Duplicate completion must not duplicate usage. Commercial charging remains
outside JoyMesh.

## Mission graph projection

Provider-neutral nodes: ExecutionRequest, ExecutionDecision, ExecutionAttempt,
CandidateEvidence, AcceptedEvidence, Verification, ExecutionResult.
Only authoritative verification produces the terminal mission projection.

## Idempotency and recovery

Every stage is idempotent. After restart, resumable states
(`backend_completed`, `evidence_pending`, `verification_pending`, `verifying`,
`finalising`) resume without re-running successful backends, duplicating usage,
accepting stale attempts, or completing without verification.

## Tenant isolation

Organisation / project / mission / execution joins are validated on evidence,
verification, usage, and completion. Frontend filtering is not a security
boundary.

## Related

* [Execution routing](execution-routing.md)
* [ADR 0004](../adr/0004-execution-completion-orchestrator.md)
* [FireConnect integration](../fireconnect-integration.md)
