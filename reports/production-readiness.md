# Production Readiness Report

## Verdict

```text
Production candidate with remaining gates
```

Last updated: 2026-08-03T14:28:59Z (production qualification pass)

## Environment
- macOS arm64 host: verify_* scripts + pytest
- Linux x86_64: Lima `prod-qual` (Ubuntu 24.04, systemd available)
- Wheels: `/Users/joytan/Documents/joymesh-rc1-verify/artifacts/{rc1,candidate}/`
- Editable venv (macOS scripts): `joymesh-rc1-verify/venv-joymesh-src`


## Tested deployment profile

* macOS arm64: packaging scripts, pytest, verify_* harness
* Linux x86_64 (Lima prod-qual): candidate wheel install + `production validate-config` OK
* Versions: JoyCLI 0.26.0 (`production/readiness-v0.26`); JoyMesh 0.1.0 (`production/readiness-v0.1`)
* Transport: Unix-socket JoyMesh → JoyCLI
* Database: SQLite intake + outbox
* Install: candidate/RC1 wheels under `joymesh-rc1-verify/artifacts`

## Gates

| Gate | Status |
|------|--------|
| verify_fault_injection.py | PASS (`reports/data/production/fault-injection.json`) |
| verify_multitenancy_negatives.py | PASS |
| verify_resource_bounds.py | PASS |
| verify_incident_exercises.py | PASS |
| verify_upgrade_rollback.py | **FAIL** (RC1 joymesh wheel lacks `joymesh.production`; candidate OK; rollback verify fails) |
| Linux systemd lifecycle | **FAIL** (203/EXEC; see `linux-systemd-validation.json`) |
| 1h Linux qualification | **IN PROGRESS** (`qualification-1h.json` on prod-qual) |
| 8h Linux qualification | **NOT STARTED** |

## Pytest (this pass)

* JoyMesh production + new tests: 40 passed (incl. `test_service`, `test_gemini_adapter` subset run with production suite)
* JoyCLI: `test_joycli_production_readiness` + `test_verification_verdict`: 14 passed

## Residual

* Rebuild RC1-tagged joymesh wheel with `joymesh.production` before claiming rollback gate
* Wire systemd units to packaged `/opt/joymux/venv` paths and fix ExecStartPre binary paths
* Complete 1h soak; run 8h only after 1h passes
