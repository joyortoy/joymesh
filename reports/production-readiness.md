# Production Readiness Report

## Verdict

```text
Production candidate with remaining gates
```

Last updated: 2026-08-03T14:39:25Z

## Gate summary

| Gate | Status |
|------|--------|
| Fault injection (25 cases) | **24/25 pass**, 1 skip (`fault-injection.json`) |
| Upgrade RC1→candidate | **PASS** (`upgrade-rollback.json`) |
| Linux systemd lifecycle | **Partial** — JoyCLI pass, JoyMesh fail (`service-lifecycle-live.json`) |
| 1h Linux qualification | **in_progress on prod-qual (started ~2026-08-03T14:28Z)** |
| 8h Linux qualification | **Not started** (blocked on 1h gates) |

## Lima prod-qual

* Ubuntu 24.04 x86_64, `/opt/joymux/venv` candidate wheels
* JoyCLI unit uses `/opt/joymux/venv/bin/joyctl` and state dir `/var/lib/joycli/state`

## Pytest evidence (macOS host venv)

* JoyMesh production suite: 40 passed (prior pass)
* JoyCLI production readiness: 14 passed (prior pass)

## Remaining before production-ready

1. Complete 1h soak with all measurement gates green (then 8h)
2. JoyMesh systemd companion unit — validate-config must not require full API import; fix data dir + `/run/joycli` ACLs
3. Live Linux case FI-25 (SIGKILL mid-commit)
4. Full reboot simulation under systemd
