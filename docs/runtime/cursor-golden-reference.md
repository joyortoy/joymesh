# Cursor as JoyMesh Runtime golden reference

This document freezes the production guarantees proven by the live Cursor
acceptance path. Refactors of the JoyMesh Runtime must preserve every item
below. Regression coverage lives in `tests/test_live_cursor_e2e.py` and the
connector lifecycle / evidence-trust suites.

## Production environment

```bash
export JOYMESH_ENV=production
export JOYMESH_INLINE_CONNECTOR_NODE=0
unset JOYMESH_MOCK_CERTIFY
```

Inline connector execution and mock certification are refused for production
routing. `assert_live_production_config()` fails clearly when misconfigured.

## Proven end-to-end path

```text
Browser / API
→ persisted connector task
→ signed task envelope
→ authenticated outbound WebSocket
→ JoyMesh Node
→ real Cursor Agent
→ node-attested evidence
→ restricted cursor_read_only routing
→ READY
```

## Node authentication

- Node initiates outbound WebSocket to the control plane.
- Control plane issues a challenge; node signs with Ed25519 private key.
- Session is bound to the registered public key.
- Revoked nodes cannot authenticate or receive offers.

## Task signing

- Connector plans are exact, expiring, hash-bound argument vectors.
- Offers are signed as `ConnectorTaskEnvelope` with the control-plane key.
- Nodes verify the envelope before accepting work.

## Task persistence and journal

- Control-plane tasks persist through SQL with monotonic versioning.
- Nodes keep a durable journal keyed by `task_id + plan_hash`.
- Terminal journal entries are replayed; non-terminal entries resume without
  relaunching Cursor.

## Reconnect reconciliation

- Duplicate offers after reconnect do not duplicate Cursor execution.
- Waiting authentication tasks resume as `WAITING_FOR_USER`.
- Browser SSE resumes from the latest sequence number; polling is the fallback.

## Execution origin and evidence trust

```text
execution_origin ∈ {remote_node, inline_development, mock_test}
trust_level ∈ {node_attested, development, mock}
```

Production certification and routing require:

```text
execution_origin = remote_node
trust_level = node_attested
```

Mock and inline evidence may exercise lifecycle behaviour in CI but cannot make
a production connector routing-eligible.

## Certification

Real Cursor read-only certification:

1. Creates `~/.joymesh/certification/cursor/<task-id>/` with restrictive mode.
2. Initialises a Git repository with a controlled `README.md`.
3. Runs:

   ```text
   cursor-agent --print --output-format stream-json --trust <PROMPT>
   ```

4. Verifies exact project name in output, unchanged files/hashes, clean Git,
   and no symlink escape.
5. Persists `REAL_BINARY_TEST` and `CERTIFICATION` evidence before readiness
   advances.

`--trust` is Cursor-specific and must remain connector-owned.

## Readiness lifecycle

```text
DISCOVERED
→ AUTHENTICATION_REQUIRED
→ AUTHENTICATED
→ VERIFICATION_REQUIRED
→ CERTIFICATION_REQUIRED
→ ROUTING_DISABLED
→ READY
```

## Task lifecycle

```text
PLANNED
→ APPROVAL_REQUIRED
→ APPROVED
→ QUEUED
→ OFFERED_TO_NODE
→ ACCEPTED_BY_NODE
→ RUNNING
→ SUCCEEDED
```

Authentication login tasks may enter `WAITING_FOR_USER` and must not claim
success from the login command exit code.

## Restricted routing

After valid certification, readiness is `ROUTING_DISABLED` until the user
explicitly enables routing. Eligible profile:

```text
cursor_read_only  (compatibility alias of generic read_only)
```

Allowed: codebase reading, explanation, architecture analysis, summarisation,
non-mutating review, search, dependency-file inspection, documentation analysis.

Blocked: file edits, applied patches, shell, dependency install, git commit /
push, test/build execution, session resume, unrestricted network, autonomous
work.

UI copy for ready state:

```text
Ready for read-only routed tasks
```

## Cancellation

```text
Browser → control plane → task.cancel → authenticated WebSocket
→ JoyMesh Node → SIGINT → SIGTERM → SIGKILL process group
→ task.cancelled (exactly one terminal event)
```

Previously valid evidence is not erased by a cancelled retry.

## SSE

`/connector-tasks/{task_id}/events/stream` streams redacted progress. Secrets,
tokens, cookies, and private keys never enter SQL or browser events.

## Revocation

Node revocation immediately disconnects the session and disables routing
eligibility.

## Production refusal of inline fallback

When no authenticated node is connected:

- tasks remain `QUEUED`
- the API process must not execute Cursor
- `JOYMESH_INLINE_CONNECTOR_NODE=1` is refused in production

## Compatibility note for Runtime v1

The generic JoyMesh Runtime extracts scheduling, policy, leases, capability
expansion, and evidence validation from connector-specific code. Cursor becomes
one `ConnectorRuntime` implementation. Existing connector lifecycle APIs remain
as a compatibility layer and must continue to satisfy this golden reference.
