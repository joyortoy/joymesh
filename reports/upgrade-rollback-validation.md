# Upgrade/rollback validation

Last updated: 2026-08-03T14:28:59Z (production qualification pass)

## Environment
- macOS arm64 host: verify_* scripts + pytest
- Linux x86_64: Lima `prod-qual` (Ubuntu 24.04, systemd available)
- Wheels: `/Users/joytan/Documents/joymesh-rc1-verify/artifacts/{rc1,candidate}/`
- Editable venv (macOS scripts): `joymesh-rc1-verify/venv-joymesh-src`


Automated: `scripts/production/verify_upgrade_rollback.py` → **FAIL** (`upgrade-rollback.json`).

Findings:
* Candidate wheel upgrade path verifies (`joymesh.production.config` import OK).
* RC1 joymesh wheel in `artifacts/rc1/` **missing** `joymesh.production` module — RC1 install/rollback verification fails.
* Script honors `RC1_WHEEL_DIR` / `CANDIDATE_WHEEL_DIR` for joymesh wheels.

Action: rebuild/publish RC1 joymesh wheel with production package before production-ready rollback claim.
