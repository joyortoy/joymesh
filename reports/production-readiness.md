# Production Readiness Report

## Verdict

```text
Production candidate with remaining gates
```

## Tested deployment profile

* OS/arch exercised for packaging/ops scripts: macOS arm64 (Darwin); **qualified profile target remains Linux x86-64**
* Versions: JoyCLI 0.26.0 branch `production/readiness-v0.26` from `joycli-v0.26.0-rc1`; JoyMesh 0.1.0 branch `production/readiness-v0.1` from `joymesh-v0.1.0-rc1`
* Transport: Unix-socket JoyMesh → JoyCLI
* Database: SQLite intake + SQLite outbox
* Harness: OpenCode (live path unchanged from RC1; not re-run in this commit set)
* Install method: wheels / editable for unit proofs
* Security mode: Ed25519 signed; unsigned forbidden in production

## Production controls

| Control | Status |
|---------|--------|
| Configuration validate-config | Implemented (`joyctl` / `joymesh`) |
| Unsigned production intake | Fail-closed |
| Ephemeral production signing keys | Fail-closed on JoyMesh |
| Key generate/inspect/rotate | Implemented |
| Backup/restore JoyCLI + outbox | Implemented + E2E ok |
| Migrations CLI | status/dry-run/apply with backup requirement |
| systemd units | Added under `deploy/systemd/` |
| Metrics export | JoyCLI `runtime metrics` JSON/Prometheus text |
| Bounds | Config model fields present; full enforcement incomplete |
| Retention jobs | Documented; not fully automated |
| 1h / 8h qualification | **Not completed** (30s sampler only) |
| Full fault-injection suite | Partial (unit-level production fails-closed) |
| Upgrade simulation from RC1 wheels | Documented; not fully automated in this pass |

## Observability

* Health: `joyctl runtime intake-status`, `joymesh delivery health`
* Readiness: existing assess_readiness
* Metrics: `joyctl runtime metrics`
* Alerts: documented defaults in `docs/alerts.md`

## Security

* Tenant org allowlist in production config
* Key registry public-only on JoyCLI
* Private key permissions 0600
* Residual: no KMS; operator key distribution; Linux profile not yet soak-tested for 8h

## Qualification

* 30s sampler recorded under `reports/data/production/qualification-30s-*.json`
* 1-hour and 8-hour gates **open**

## Tests

* JoyCLI production unit: 6 passed
* JoyMesh production unit: 4 passed
* Key rotation E2E: ok
* Backup/restore E2E: ok

## Remaining limitations

1. Eight-hour and one-hour packaged qualifications not completed
2. Linux x86-64 soak / systemd live verification not completed on a Linux host
3. Full fault-injection matrix incomplete
4. Resource bound enforcement and retention jobs incomplete
5. Multi-tenant negative suite incomplete beyond existing cross-tenant intake test
6. No KMS; operator-managed keys
7. RC1 tags unchanged; this is a candidate branch, not a stable release

## Git state

* Branches: `production/readiness-v0.26`, `production/readiness-v0.1`
* RC1 tags immutable
* Nothing pushed

```text
Nothing was pushed.
```
