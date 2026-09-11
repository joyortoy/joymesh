# Production qualification

Last updated: 2026-08-03T19:17:23Z

Branch: `production/readiness-v0.1`

## 1-hour Linux soak (Lima `prod-qual`, x86_64)

**PASS** — host artifact `reports/data/production/qualification-1h.json` (from VM `/tmp/qualification-1h.json`).

| Measurement | Value |
|-------------|--------|
| started_at | 2026-08-03T15:34:52.898818+00:00 |
| ended_at | 2026-08-03T16:34:58.016682+00:00 |
| elapsed_seconds | 3605.12 |
| ticks | 714 |
| failures | 0 |
| gates | duration_met, min_ticks, zero_failures |
| ok | **True** |

## 8-hour Linux soak

### Prior attempt (PID 10991) — DEAD / incomplete

VM `prod-qual` was **Stopped** when qualification resumed (2026-08-04 ~03:04 +08). PID 10991 gone; `/tmp/qualification-8h.json` absent (tmpfs cleared). Approx. ~2.5h elapsed of 8h before stop. Evidence: `prior-8h-dead.json`.

### Restart (in progress)

| Field | Value |
|-------|--------|
| PID | **2915** |
| started_at_utc | 2026-08-03T19:15:01Z |
| QUAL_DURATION_SECONDS | 28800 |
| durable output (VM) | `/home/joytan.guest/prod-qual-evidence/qualification-8h/qualification-8h.json` |
| also | copy to `/tmp/qualification-8h.json` on completion |
| host poller | `.tmp/poll-qualification-8h.sh` |

Copy when complete:

`limactl cp prod-qual:/home/joytan.guest/prod-qual-evidence/qualification-8h/qualification-8h.json reports/data/production/qualification-8h.json`

## Systemd lifecycle (Linux)

* **JoyCLI** `joycli-runtime-intake.service`: start/stop/restart **pass**; FI-25 SIGKILL recovery **pass**
* **JoyMesh** `joymesh-delivery.service`: oneshot validate **active** (requires `/run/joymesh` present for ProtectSystem namespacing)

## Reboot simulation

**Partial / cold-start pass** without full VM reboot (deferred to protect 8h soak): stop units → wipe `/run` sockets → recreate `/run/joymux`+`/run/joymesh` → start intake+delivery → both **active**, socket bound. Units remain **disabled** (not enabled for boot). Evidence: `reboot-sim-result.json`.

## FI-25

**PASS** (brief): SIGKILL MainPID 2702 → respawn 2739; sqlite integrity ok. Evidence: `fi25-result.json`.

Verdict: **production candidate** until restarted 8h completes with all gates green.
