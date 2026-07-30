# JoyMesh

JoyMesh is a standalone open-source interoperability layer for coding-agent
harnesses. It provides one stable SDK, CLI, and local API for discovering
harnesses, inspecting capabilities, launching runs, normalizing events,
tracking subscriptions and quota, and selecting deterministic routes.

JoyMesh is independent of JoyCLI and contains no application-specific planning,
mission decomposition, private workflows, or proprietary data.

The browser-first platform adds a cloud control plane and outbound-only JoyMesh
Node without changing the core: the Python SDK remains the orchestration
implementation, while REST, CLI, WebSocket, and browser surfaces are adapters.

> **Status:** active development. The bundled fake adapter is certified.
> Real-binary support is version-aware and remains uncertified until evidence is
> recorded for the installed executable.

## Architecture

The core is Python 3.12 with Pydantic v2, FastAPI, SQLAlchemy 2, SQLite, Alembic,
Typer, and asyncio. The separately packaged Sites application provides secure
onboarding and remote access; it is not a second orchestration implementation.

See [ADR 0001](docs/adr/0001-initial-architecture.md) for the decision record,
tradeoffs, and current limitations. See [Harness architecture](docs/harness-architecture.md),
[Harness catalogue](docs/harness-catalogue.md), and
[Adapter conformance](docs/adapter-conformance.md) for the support gate.
The platform boundaries are documented in
[Browser-first architecture](docs/browser-first-architecture.md),
[JoyMesh Node](docs/joymesh-node.md),
[Remote task security](docs/remote-task-security.md), and the
[Threat model](docs/threat-model.md).

## Development

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy src
```

## Python SDK

```python
from joymesh import JoyMesh, RunRequest

mesh = JoyMesh()

request = RunRequest(
    task="Implement authentication tests",
    workspace="/path/to/repository",
)

route = await mesh.resolve_route(request=request, preferred_harness="codex")
run = await mesh.start_run(request=request, route=route)
result = await mesh.wait_for_run(run.id)
```

Call `await mesh.close()` during application shutdown.

## CLI

```sh
uv run joymesh harness detect
uv run joymesh harness list
uv run joymesh harness discover
uv run joymesh harness inspect codex
uv run joymesh harness install gemini-cli
uv run joymesh harness certify codex
uv run joymesh subscription list
uv run joymesh route preview --task "Implement authentication tests"
uv run joymesh run --workspace . --task "Implement authentication tests"
uv run joymesh run inspect <run-id>
uv run joymesh run cancel <run-id>
```

Manual subscription profiles can be added with `joymesh subscription add`.

## Local API

Start the service:

```sh
uv run joymesh api
```

```text
GET  /api/v1/health
GET  /api/v1/harnesses
GET  /api/v1/harnesses/catalogue
POST /api/v1/harnesses/discovery
GET  /api/v1/harnesses/{id}
GET  /api/v1/harnesses/{id}/capabilities
POST /api/v1/harnesses/{id}/install/plan
POST /api/v1/harnesses/{id}/install
POST /api/v1/harnesses/{id}/login/plan
POST /api/v1/harnesses/{id}/certify
GET  /api/v1/fireconnect
POST /api/v1/fireconnect/{harness}/connect/plan
POST /api/v1/fireconnect/{harness}/disconnect/plan
POST /api/v1/fireconnect/{harness}/execute
GET  /api/v1/subscriptions
POST /api/v1/routes/preview
POST /api/v1/runs
GET  /api/v1/runs
GET  /api/v1/runs/{id}
GET  /api/v1/runs/{id}/events
GET  /api/v1/runs/{id}/event-log
POST /api/v1/runs/{id}/cancel
GET  /api/v1/runs/{id}/usage
GET  /api/v1/runs/{id}/fallback
POST /api/v1/fallbacks/{id}/approve
```

The events endpoint uses server-sent events and emits only normalized JoyMesh
protocol objects.

## Current limitations

- Quotas are manually configured, not observed from providers.
- SQLite targets one local JoyMesh service; distributed supervision is out of
  scope for the initial slice.
- Lifecycle and routing-transform mutation always requires explicit approval.
