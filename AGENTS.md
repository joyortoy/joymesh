# JoyMesh Agent Guide

## Mission

JoyMesh is a mission-oriented orchestration runtime for coordinating AI
harnesses, agents, tools, and human participants through a persistent mission
graph with verification-driven execution.

## Repository State

The runtime is not implemented yet. Keep foundational decisions explicit and
avoid introducing a framework, language, or service dependency unless the task
requires it.

## Working Agreements

- Read `README.md` and inspect the repository before proposing architecture.
- Keep changes small, reviewable, and aligned with the mission above.
- Prefer deterministic behavior and explicit state transitions.
- Treat mission state, verification evidence, and audit history as durable data.
- Never commit credentials, tokens, private keys, or local environment files.
- Document new setup, build, test, and run commands in `README.md`.
- Add or update tests whenever behavior changes.
- Run `./scripts/check.sh` before committing.

## Architecture Expectations

- Separate orchestration policy from harness- and tool-specific adapters.
- Make side effects visible and idempotent where practical.
- Preserve enough context to explain why a mission changed state.
- Define failure, retry, cancellation, and human-approval behavior explicitly.
- Keep public interfaces typed or schema-validated once an implementation
  language is selected.

## Code Review Rules

- Flag changes that can advance a mission without recorded verification.
- Flag destructive or externally visible actions that lack an approval boundary.
- Flag retry paths that can duplicate non-idempotent side effects.
- Flag persisted state changes that cannot be audited or replayed.
- Flag secrets, personal data, or untrusted content written to logs.
