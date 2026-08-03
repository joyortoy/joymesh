# Production qualification

Last updated: 2026-08-03T14:28:59Z (production qualification pass)

## Environment
- macOS arm64 host: verify_* scripts + pytest
- Linux x86_64: Lima `prod-qual` (Ubuntu 24.04, systemd available)
- Wheels: `/Users/joytan/Documents/joymesh-rc1-verify/artifacts/{rc1,candidate}/`
- Editable venv (macOS scripts): `joymesh-rc1-verify/venv-joymesh-src`


## Completed

* macOS verify scripts: fault injection, multitenancy negatives, resource bounds, incident exercises (all `ok: true` in `reports/data/production/*.json`)
* Upgrade simulation: partial — see upgrade-rollback.json
* Linux candidate wheels: installed in `/tmp/joymux-qual` on prod-qual; validate-config OK

## In progress

* **1-hour** sampler on Linux x86_64 (`QUAL_DURATION_SECONDS=3600` → `reports/data/production/qualification-1h.json`). Started with `/tmp/joymux-qual/bin/python`; **not complete at report time**.

## Not done

* **8-hour** qualification — not started; do not treat macOS sampler as Linux gate
* Prior 30s sampler remains under `qualification-30s-*.json`

Verdict: **production candidate**, not production ready.
