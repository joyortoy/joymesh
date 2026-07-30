# ADR 0001: Initial JoyMesh architecture

- Status: Accepted
- Date: 2026-07-28

## Context

JoyMesh needs to expose a stable harness-neutral interface through a Python SDK,
CLI, and local API. Native harness processes differ in installation detection,
capabilities, command syntax, output, sessions, billing, and failure behavior.
The first slice must prove the boundary without coupling the project to JoyCLI
or prematurely implementing every target harness.

## Decision

Use Python 3.12 for the core runtime and public SDK.

- Pydantic v2 defines the public protocol and validates API boundaries.
- Harnesses implement a small `HarnessAdapter` interface and register by stable
  harness identifier.
- `asyncio` owns subprocess supervision and streaming I/O.
- SQLAlchemy 2 with SQLite persists runs, normalized events, subscriptions, and
  usage data; Alembic owns schema evolution.
- FastAPI exposes versioned REST endpoints and server-sent events.
- Typer exposes the same service layer as a local developer CLI.
- Routing is a pure, deterministic function over capabilities, availability,
  subscription policy, quota, concurrency, cost weight, and user preference.
- Codex and OpenCode adapters implement one launch/normalization contract.
  A deterministic fake adapter exists for **tests only** and is not part of the
  production registry or default configuration (historical note: early prototypes
  bundled a fake harness; that path has been removed).
- A reusable conformance suite gates the `supported` status for every adapter.
- JoyMesh has no frontend; the Python package is the only build artifact.

The SDK, CLI, and API call one application service. They do not parse native
harness output or duplicate native command construction.

## Alternatives considered

### TypeScript core or bundled frontend

Python is a better fit for the requested SDK contract and agent ecosystem.
JoyMesh remains backend infrastructure; consuming applications may provide
their own user experience through the SDK or API.

### One process per API request

This would simplify implementation but lose supervision, cancellation, and
streaming. A long-lived local service owns active child processes instead.

### Native output as the public protocol

Passing through terminal output would leak harness-specific semantics to every
consumer. Adapters normalize output into versioned events instead.

### PTY-first execution

PTYs are necessary for interactive harnesses. Early prototypes used a
non-interactive test-only fake adapter; production adapters (Codex, OpenCode)
drive the shared pipe-based runtime, keeping the adapter/runtime boundary
suitable for a PTY transport where needed.

## Consequences

- Consumers can integrate through the SDK, CLI, or API without native harness
  knowledge.
- SQLite supports a single local service well; multi-host operation is outside
  the initial scope.
- Active process handles are in memory, while run and event history survives
  restarts.
- A service restart marks no process as active; reconciliation is a follow-up.
- Non-interactive Codex and OpenCode sessions support native identity, resume,
  normalized usage, cancellation, timeout, and process-tree cleanup.
- Interactive PTY sessions and observed provider quota APIs remain later
  increments.
