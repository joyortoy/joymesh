# JoyMesh agent instructions

## Repository identity

- Repository: JoyMesh (`joymesh`)
- Remote: `https://github.com/joyortoy/joymesh`
- Package identity: `joymesh` (`pyproject.toml`)
- Primary system: JoyMesh — harness interoperability, delivery, connectors, and signed routing

## Primary system

JoyMesh is a standalone interoperability layer for coding-agent harnesses. It owns discovery, capability inspection, run lifecycle, event normalization, subscription/quota tracking, deterministic routing, delivery/outbox, and connector catalogue maturity observations.

JoyMesh is independent of JoyCLI application planning. It emits producer-side certification and soak evidence; it does not emit JoyLegal ALLOW/DENY legitimacy verdicts.

## Subsystem boundaries

| Subsystem | Ownership | Authoritative paths |
|---|---|---|
| Transport / delivery | JoyMesh | `src/joymesh/delivery/` |
| Connectors | JoyMesh | `src/joymesh/connectors/` (catalogue, lifecycle, live_test) |
| Adapters / harness | JoyMesh | `src/joymesh/adapters/` (`fake`, `codex`, `opencode`, …) |
| Signed routing | JoyMesh | routing via SDK/CLI; FireConnect transforms are not coding harnesses |
| Soak / qualification | Producer evidence only | `reports/`, `reports/data/production/` |

## Authoritative source paths

- `AGENTS.md`, `README.md`
- `docs/harness-architecture.md`, `docs/deployment.md`, `docs/joylegal-integration.md`
- `docs/harness-certification.md`, `docs/adapter-conformance.md`
- `pyproject.toml`
- `src/joymesh/adapters/`, `src/joymesh/connectors/`, `src/joymesh/delivery/`
- `tests/`

## Generated files

- `reports/` — producer observations and qualification artifacts (evidence, not source)
- `schemas/joylegal/` — vendored JoyLegal schemas for compatibility checks
- Python `__pycache__/`, build/wheel outputs

## Active versus RC branches

- Active production-readiness work observed on branch `production/readiness-v0.1`
- RC / release-hardening identities (for example `release/joymesh-rc1-hardening`) are distinct from the active tip
- An RC tag does **not** prove the current active branch is release-green

## Soak and live-connector limitations

- Bundled fake adapter is certified for fake/mock paths only
- Real-binary / live-provider support remains uncertified until admitted JoyLegal evidence exists
- Producer soak evidence (for example 1h qualification JSON) is observational; missing or incomplete 8h soak does not satisfy live qualification
- Test-only harness ids (`fake`, `joy`) do not satisfy live-provider gates

## Certification evidence ownership

- JoyMesh may emit `decision=AWAITING_JOYLEGAL` / `report_status=PRODUCER_OBSERVATION` only
- Claims are `status=submitted` only
- JoyLegal owns admission, evaluation, and final certification verdicts

## Current maturity

- Core delivery / SDK / CLI: production candidate with remaining gates
- Live connectors and 8h soak: limited; limitations must stay explicit in reports
- Dirty qualification/report edits in the working tree are not release readiness

## Required startup commands

```bash
joylegal context verify --repo .
joylegal context bootstrap --repo .
```

Do not begin implementation work until the context decision is `VALID`, or limited work is explicitly approved with recorded obligations.

## Required tests

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy src
```

Run focused tests only for the changed surface; focused green tests do not imply full regression.

## Commit and push rules

- Create focused commits; do not overwrite unrelated dirty report/test/catalogue changes
- Do not push unless explicitly requested
- Do not rewrite producer reports to imply JoyLegal certification

## Forbidden assumptions

- Focused tests imply full regression
- Fake connector evidence satisfies live qualification
- Producer report equals JoyLegal certification
- RC tag implies current active branch is release-green
- Chat history is authoritative repository context

## JoyLegal ownership boundary

JoyLegal owns certification verdicts, legitimacy decisions, obligations, and context-readiness. JoyMesh produces evidence and submitted claims only.
