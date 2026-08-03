# Service lifecycle validation

Last updated: 2026-08-03T14:28:59Z (production qualification pass)

## Environment
- macOS arm64 host: verify_* scripts + pytest
- Linux x86_64: Lima `prod-qual` (Ubuntu 24.04, systemd available)
- Wheels: `/Users/joytan/Documents/joymesh-rc1-verify/artifacts/{rc1,candidate}/`
- Editable venv (macOS scripts): `joymesh-rc1-verify/venv-joymesh-src`


## Linux x86-64 (Lima prod-qual)

* Unit files installed from `deploy/systemd/` (JoyCLI intake + JoyMesh delivery companion)
* `systemctl start` **failed** for both units (status 203/EXEC). JoyCLI still references `/usr/bin/joyctl` in ExecStartPre; JoyMesh override pointed at host-mounted venv unsuitable for `User=joymesh`.
* Evidence: `reports/data/production/linux-systemd-validation.json`

## macOS

* No systemd; not a substitute for Linux gate.

Status: **blocked / failed** for Linux systemd lifecycle until units are installed to a root-owned venv under `/opt/joymux` and exercised start → stop → reboot.
