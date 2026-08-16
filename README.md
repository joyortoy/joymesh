# JoyMesh

**Give one planning agent a team of specialized workers—without changing your harness.**

JoyMesh is an open-source delegation and routing layer for coding agents. Your
favorite agent remains the planner. JoyMesh sends bounded subtasks to the most
suitable harness and model, runs independent work in parallel, then returns
compact results and evidence to the original agent.

That separation keeps specialist workers from inheriting the planner's full
conversation. It can reduce repeated context and token use while letting one
workflow use local, open-source, subscription, or hosted agents together.

> [!IMPORTANT]
> JoyMesh is alpha software under active development. Its connector catalogue
> describes 21 harnesses, but an installed harness is trusted for execution only
> after local, version-specific certification evidence is recorded.

## Why JoyMesh?

One agent should not spend its entire context researching, coding, testing, and
reviewing every detail serially. JoyMesh creates explicit layers between
planning and execution:

```text
Your existing planning agent
        │ bounded subtasks
        ▼
JoyMesh delegation and routing layer
        ├── OpenCode + DeepSeek: focused research
        ├── local agent: quick repository inspection
        └── preferred coding harness: implementation
        │ compact feedback + evidence + usage
        ▼
Original planning agent decides what comes next
```

JoyMesh gives you:

- **Layered delegation** — give each worker only the task context it needs.
- **Parallel execution** — run independent subtasks concurrently with bounded
  parallelism and failure isolation.
- **Compact feedback** — return summaries, evidence, route identity, and token
  usage instead of copying entire worker conversations into the planner.
- **One integration surface** — use the Python SDK, CLI, or local REST API.
- **Capability-aware discovery** — inspect what an installed harness can
  actually do before selecting it.
- **Deterministic routing** — preview and apply routes using capabilities,
  subscriptions, quotas, and policy.
- **Normalized execution** — launch runs and consume a shared event model
  instead of parsing every harness independently.
- **Approval-gated changes** — installation, authentication, and routing
  mutations require explicit approval.
- **Workflow independence** — keep using your preferred planner and harness;
  JoyMesh handles delegation underneath it.
- **Local-first operation** — use SQLite and local processes, with an optional
  outbound-only node for remote access.

## Quick start

JoyMesh currently installs from source and requires Python 3.12 plus
[uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/joyortoy/joymesh.git
cd joymesh
uv sync --extra dev

# See the available commands and connector catalogue.
uv run joymesh --help
uv run joymesh harness list

# Detect locally installed harnesses and inspect one.
uv run joymesh harness detect
uv run joymesh harness inspect codex
```

No lifecycle-changing command runs silently. JoyMesh generates a plan first and
requires approval before applying installation, authentication, or routing
changes.

### 60-second discovery demo

Run the credential-free example to see the catalogue size and the harnesses
available on your machine:

```sh
uv run python examples/discovery.py
```

The example uses a temporary SQLite database and does not read provider
credentials or launch an agent.

### Layered delegation demo

Run the credential-free example to see two focused tasks delegated in parallel
and returned as compact feedback:

```sh
uv run python examples/layered_delegation.py
```

The example uses a simulated dispatcher so it is safe to run anywhere. Replace
that callback with your JoyMesh route and worker execution. See
[Layered delegation](docs/layered-delegation.md) for the integration pattern.

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

await mesh.close()
```

## CLI workflow

```sh
# Discover and inspect
uv run joymesh harness detect
uv run joymesh harness list
uv run joymesh harness inspect codex

# Preview before execution
uv run joymesh route preview --task "Implement authentication tests"

# Run and supervise
uv run joymesh run --workspace . --task "Implement authentication tests"
uv run joymesh run inspect <run-id>
uv run joymesh run cancel <run-id>
```

Manual subscription and quota profiles can be added with
`joymesh subscription add`.

## Local API

Start the service with:

```sh
uv run joymesh api
```

The API supports harness discovery and certification, route previews, run
lifecycle management, normalized event streams, usage data, and approval-gated
fallbacks. See the interactive OpenAPI documentation at
`http://127.0.0.1:8000/docs` after startup.

## Architecture

The core uses Python 3.12, Pydantic v2, FastAPI, SQLAlchemy 2, SQLite, Alembic,
Typer, and asyncio.

```text
Planning agent / existing workflow
          |
  bounded task contracts
          |
  JoyMesh SDK and API
          |
 delegate -> discover -> preview route -> execute in parallel -> compact feedback
          |
 Codex / Claude Code / Gemini CLI / OpenCode / local or hosted models
```

Start with these design documents:

- [Initial architecture](docs/adr/0001-initial-architecture.md)
- [Layered delegation](docs/layered-delegation.md)
- [Harness architecture](docs/harness-architecture.md)
- [Harness catalogue](docs/harness-catalogue.md)
- [Adapter conformance](docs/adapter-conformance.md)
- [Security threat model](docs/threat-model.md)
- [Self-hosting](docs/self-hosting.md)

## Project status

JoyMesh is suitable for exploration, connector development, and local
prototyping. It is not yet recommended as an unattended production control
plane.

Current limitations:

- Real harness support remains uncertified until evidence is recorded for the
  installed executable version.
- Quotas are manually configured rather than observed directly from providers.
- SQLite targets one local JoyMesh service.
- Lifecycle and routing-transform mutations require explicit approval.

Near-term priorities are a clean continuous-integration baseline, a reproducible
end-to-end demo, connector certification evidence, packaged releases, and
newcomer-friendly issues.

See the public [roadmap](ROADMAP.md) for current engineering priorities and the
[30-day traction plan](docs/traction-plan.md) for the adoption work around them.

## Contributing

Contributions are welcome, especially for connector definitions, conformance
tests, documentation, and small end-to-end examples. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull-request guidance, or open a
[GitHub issue](https://github.com/joyortoy/joymesh/issues) to discuss an idea.

## License

JoyMesh is licensed under the [Apache License 2.0](LICENSE).
