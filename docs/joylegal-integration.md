# JoyLegal integration

JoyMesh emits producer-side evidence and submitted claims for JoyLegal admission and evaluation.
JoyMesh never emits ALLOW/DENY legitimacy verdicts — JoyLegal owns those decisions.

## Producer boundary

| Artifact | Schema | Producer may claim |
|----------|--------|--------------------|
| Claim | `joylegal.claim/v2` | `status=submitted` only |
| Certification observation | `joylegal.certification/v2` | `decision=AWAITING_JOYLEGAL`, `report_status=PRODUCER_OBSERVATION` |
| Bundle | `joylegal.bundle/v2` | File hashes and manifest only |
| Soak evidence | `joymesh.producer-soak-evidence/v1` | Observed qualification metrics |
| Connector evidence | `joymesh.producer-connector-evidence/v1` | Catalogue maturity observations |

Certification observations use `evaluator_version=joymesh.producer-observation/v1`,
`evidence_admitted=()`, and list submitted evidence under `evidence_missing` as
`submitted_pending_admission:<id>` until JoyLegal admits them.

## CLI commands

```bash
# Export producer certification observation + submitted claim
joymesh legal certification export [--output PATH] [--repo ROOT]

# Export soak + connector evidence pack
joymesh legal evidence export --output DIR [--repo ROOT]

# Materialize joylegal.bundle/v2 with claim, certification, and evidence
joymesh legal bundle create --output DIR [--evidence-dir DIR] [--repo ROOT]

# Validate sample emissions against schemas/joylegal/*
joymesh legal compatibility check [--repo ROOT]
```

All commands bind to the current git commit/branch/tag/dirty state via `source-identity.json`.

## Soak evidence

When `reports/data/production/qualification-1h.json` is present, JoyMesh maps it into structured
soak evidence (`mode`, requested/actual duration, gates, operations). The observation is labeled
`PRODUCER_OBSERVATION` and does not imply JoyLegal admission.

If the 8h qualification artifact is absent or incomplete (`qualification-8h.json` missing,
prior run lost), limitations explicitly disclose that the 8h soak gate is not satisfied from
producer evidence alone.

## Connector evidence

JoyMesh inspects the packaged connector catalogue and records whether each connector satisfies
live-provider gates by maturity and `remote_execution_supported`. Test-only harness ids (`fake`,
`joy`) are listed separately as not satisfying live-provider gates.

## JoyLegal ingest (downstream)

After export, submit bundle file hashes to JoyLegal:

```bash
joylegal integration joymesh evidence-ingest --evidence-id EVID --content-hash HASH ...
joylegal integration joymesh certify --report-id REPORT_ID
```

See JoyLegal's [joymesh-integration.md](https://github.com/joyuniverse/joylegal/blob/main/docs/joymesh-integration.md)
for M9 contract details. JoyLegal evaluates admitted evidence; JoyMesh does not mutate JoyLegal state.

## Schemas

Vendored JSON Schemas live under `schemas/joylegal/` (`claim-v2`, `bundle-v2`, `certification-v2`,
`authority-v2`). Run `joymesh legal compatibility check` after schema or emitter changes.
