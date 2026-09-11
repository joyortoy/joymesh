# Resource bounds validation

Last updated: 2026-08-03T14:28:59Z (production qualification pass)

## Environment
- macOS arm64 host: verify_* scripts + pytest
- Linux x86_64: Lima `prod-qual` (Ubuntu 24.04, systemd available)
- Wheels: `/Users/joytan/Documents/joymesh-rc1-verify/artifacts/{rc1,candidate}/`
- Editable venv (macOS scripts): `joymesh-rc1-verify/venv-joymesh-src`


Automated: `scripts/production/verify_resource_bounds.py` → **PASS** (`resource-bounds.json`).

Config model bounds present; full runtime enforcement on long soak **pending** 1h/8h qualification.
