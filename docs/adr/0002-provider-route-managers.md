# ADR 0002: Provider-route managers vs coding-harness connectors

## Status

Accepted (2026-07-29)

## Context

JoyMesh Runtime v1 registers coding harnesses through `builtin_connectors()`:

```text
cursor, codex, opencode, claude, grok
```

FireConnect (`fireconnect` CLI v0.9.0) was inspected in this environment. Its help
surface is:

```text
login | logout | status | model list | configure | upgrade
<harness> on | off | status
```

Observed behaviour:

* FireConnect does **not** expose a headless repository execution command.
* `on` / `off` mutate harness provider configuration (OpenCode, Codex, Claude,
  Cursor, Pi, VS Code, Deep Agents).
* `status --json` reports sign-in, key storage, and per-harness enablement.
* Inference still runs inside the underlying harness process.

Therefore FireConnect is a **provider configuration / routing layer**, not an
autonomous coding harness. Registering it in `builtin_connectors()` would
collapse two distinct identities and break connector-neutral execution.

## Decision

### Architecture classification: Outcome A

```text
ConnectorRuntime (execution)
  ├── cursor
  ├── codex
  ├── opencode
  ├── claude
  └── grok

ProviderRouteManager (configuration)
  └── fireconnect
```

Provider routes are separate:

```text
connector_id = opencode
provider_id  = fireworks | native
manager_id   = fireconnect | null
model_id     = exact configured model when known
```

### Abstractions

* `ProviderRoute` — connector-neutral route snapshot
* `ProviderRouteManager` — discover / inspect / enable / disable / verify / redact
* `builtin_provider_route_managers()` — sole manager registry (not mixed into
  `builtin_connectors()`)
* Two-stage selection: (1) eligible connector, (2) eligible provider route

### Approval and rollback

Enabling, disabling, or changing models mutates user-level harness config.
Mutations require explicit approval. Previous configuration is captured before
change; `off` restores when FireConnect supports it. Automatic silent switching
between native and Fireworks is forbidden.

### Credential safety

Never log, persist, or return Fireworks API keys, bearer tokens, or keychain
contents. Diagnostics are redacted and bounded.

### Metrics / observability

Record `connector_id`, `provider_id`, `provider_route_id`,
`provider_route_manager_id`, and `model_id` as generic dimensions. Do not treat
FireConnect as a second worker in the execution timeline.

## Consequences

* Future provider managers (non-FireConnect) register beside FireConnect.
* Connector #N remains reserved for autonomous harnesses with their own
  execution lifecycle.
* Existing FireConnect HTTP plan/execute endpoints remain as a compatibility
  façade over the manager protocol.
* If a future FireConnect release gains independent headless execution, a new
  ADR must reclassify it before any `builtin_connectors()` registration.

## Known FireConnect limitations (v0.9.0)

* Quota / usage is Claude-session oriented (`usage`); not a general quota API.
* Cursor / VS Code transforms may require the IDE to be quit.
* Codex Fireworks routing uses a separate API-key path from ChatGPT subscription.
* OpenCode may store a literal API key in config when enabled — JoyMesh must
  never print that value.
* Concurrent mutations on the same harness are serialised via database-backed
  provider-route leases (`ProviderRouteMutationCoordinator`). Cross-host
  isolation requires a shared JoyMesh database; process-local locks alone are
  not sufficient for multi-worker deployments.
