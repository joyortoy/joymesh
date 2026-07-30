# Provider routes (FireConnect / Fireworks)

FireConnect is a **provider-route manager**, not a JoyMesh connector.

```text
Connector (executes): opencode | codex | claude | cursor | grok
Provider (serves models): native | fireworks
Manager (configures route): fireconnect
```

See ADR: `docs/adr/0002-provider-route-managers.md`.

For provider-neutral execution (Mission → Planner → Router → Backend), see
[`docs/runtime/execution-routing.md`](execution-routing.md) and ADR 0003.
FireConnect is one `ExecutionBackend` implementation; only that backend owns
`ProviderRouteService`.

## CLI

```bash
joymesh provider-route list --json
joymesh provider-route status --json
joymesh provider-route status opencode --json
joymesh provider-route enable fireconnect opencode --model accounts/fireworks/models/deepseek-v4-flash --approve
joymesh provider-route verify fireconnect opencode --json
joymesh connector live-test opencode --json --workspace <isolated>
joymesh provider-route disable fireconnect opencode --approve
```

Mutations require `--approve`. JoyMesh never silently switches native ↔ Fireworks.

## Concurrency

All provider mutations are lease-coordinated.

```text
All provider mutations are lease-coordinated.
Direct provider-manager mutation is an internal primitive.
Temporary routes restore exact prior state.
Permanent changes are serialised but intentionally retained.
```

Public entry point: ``ProviderRouteService``

* permanent enable/disable → ``run_serialised_mutation``
* temporary execution routes → ``run_lifecycle`` (capture → enable → verify → execute → restore)

Raw ``manager.enable_route`` / ``disable_route`` require an active coordinator
mutation-authority ContextVar and fail closed otherwise.

Protection is **database-backed lease** (unique active lease per manager+connector)
plus a process-local ``asyncio.Lock``. Multiple async jobs in one process, multiple
worker processes on one machine sharing the JoyMesh database, and multiple service
instances sharing that database are covered. Separate machines with **separate**
databases are not coordinated — deploy a shared database for that topology.

Expired lifecycle leases are recovered on startup/before acquire by restoring the
**saved** original route state (never assuming “off”). Recovery claims the expired
lease transactionally (status ``recovering``) without acquiring a second lease, so
it cannot deadlock. Failed recovery blocks new mutations for that connector until
cleared.

## API

* `GET /api/v1/provider-routes/managers`
* `GET /api/v1/provider-routes/status?connector_id=opencode`
* `POST /api/v1/provider-routes/{manager}/{connector}/enable?approve=true`
* `POST /api/v1/provider-routes/{manager}/{connector}/disable?approve=true`
* `GET /api/v1/provider-routes/{manager}/{connector}/verify`

Legacy `/api/v1/fireconnect*` plan/execute endpoints are **deprecated**. They still
work temporarily but route mutations through ``ProviderRouteService`` (same leases).
Prefer `/api/v1/provider-routes/...`.
