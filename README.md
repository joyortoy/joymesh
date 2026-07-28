# JoyMesh

JoyMesh is a standalone open-source interoperability layer for coding-agent
harnesses. It provides one stable SDK, CLI, and local API for discovering
harnesses, inspecting capabilities, launching runs, normalizing events,
tracking subscriptions and quota, and selecting deterministic routes.

JoyMesh is independent of JoyCLI and contains no application-specific planning,
mission decomposition, private workflows, or proprietary data.

> **Status:** initial vertical slice under active development. The fake harness
> is the only adapter in the first release.

## Architecture

The core is Python 3.12 with Pydantic v2, FastAPI, SQLAlchemy 2, SQLite, Alembic,
Typer, and asyncio. A static reference dashboard is served by the API and adds
no frontend build requirement.

See [ADR 0001](docs/adr/0001-initial-architecture.md) for the decision record,
tradeoffs, and current limitations.

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
from joymesh import JoyMesh

mesh = JoyMesh()

routes = await mesh.preview_routes(
    task="Implement authentication tests",
    workspace="/path/to/repository",
)

run = await mesh.run(
    task="Implement authentication tests",
    workspace="/path/to/repository",
    route=routes.selected,
)
```

Call `await mesh.close()` during application shutdown.

## CLI

```sh
uv run joymesh harness detect
uv run joymesh harness list
uv run joymesh subscription list
uv run joymesh route preview --task "Implement authentication tests"
uv run joymesh run --workspace . --task "Implement authentication tests"
uv run joymesh run inspect <run-id>
uv run joymesh run cancel <run-id>
```

Manual subscription profiles can be added with `joymesh subscription add`.

## Local API and dashboard

Start the service:

```sh
uv run joymesh api
```

Open `http://127.0.0.1:8787` for the reference dashboard or use:

```text
GET  /api/v1/harnesses
GET  /api/v1/subscriptions
POST /api/v1/routes/preview
POST /api/v1/runs
GET  /api/v1/runs/{id}
GET  /api/v1/runs/{id}/events
POST /api/v1/runs/{id}/cancel
```

The events endpoint uses server-sent events and emits only normalized JoyMesh
protocol objects.

## Current limitations

- Only the deterministic fake adapter is implemented.
- Subprocess streaming uses stdout/stderr pipes; interactive PTY sessions and
  session resume are deferred to the first real harness adapter.
- Quotas are manually configured, not observed from providers.
- SQLite targets one local JoyMesh service; distributed supervision is out of
  scope for the initial slice.
