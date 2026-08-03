# Service lifecycle live validation (Linux x86-64)

Last updated: 2026-08-03T14:39:17.177711+00:00

Environment: Lima `prod-qual`, candidate wheels in `/opt/joymux/venv`.

## JoyCLI runtime intake

- Unit paths: `/opt/joymux/venv/bin/joyctl`
- State directory: `/var/lib/joycli/state` (durable mode cannot use repository root)
- Lifecycle: **start → stop → restart succeeded** (`systemctl is-active` = `active`)
- Prerequisites exercised: publisher key registered, `/var/lib/joycli` owned by `joycli`, `/etc/joycli/runtime.env`

## JoyMesh delivery companion

- Unit paths: `/opt/joymux/venv/bin/joymesh production validate-config`
- Lifecycle: **failed to start** — packaged CLI imports full API during validate; hit permission errors on `/run/joycli/joymesh-delivery.sock` and default data dir under `/home/joymesh`
- Evidence: `reports/data/production/service-lifecycle-live.json`, VM `journalctl -u joymesh-delivery.service`

## Verdict

**Partial pass** — JoyCLI systemd lifecycle validated on Linux x86-64; JoyMesh companion unit still blocked (follow-up: slim validate entrypoint, `JOYMESH_DATA_DIR` enforcement before API import, group/read ACL on `/run/joycli`).

Overall production verdict remains **candidate**.
