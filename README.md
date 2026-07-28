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

The CLI and API commands will be documented when their vertical slice lands.
