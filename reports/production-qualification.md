# Production qualification

Last updated: 2026-08-03T16:37:36Z

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
| gates | {'duration_met': True, 'min_ticks': True, 'zero_failures': True} |
| ok | **True** |

## 8-hour Linux soak

**IN PROGRESS** on `prod-qual`.

| Field | Value |
|-------|--------|
| PID | 10991 (verify with `limactl shell prod-qual -- ps -p 10991`) |
| output (VM) | `/tmp/qualification-8h.json` |
| QUAL_DURATION_SECONDS | 28800 |
| started (approx.) | 2026-08-03T16:35:00Z (after 1h run completed) |

Copy when complete: `limactl cp prod-qual:/tmp/qualification-8h.json reports/data/production/qualification-8h.json`

## Systemd lifecycle (Linux)

* **JoyCLI** `joycli-runtime-intake.service`: start/stop/restart **pass** (`/opt/joymux/venv`, state `/var/lib/joycli/state`)
* **JoyMesh** `joymesh-delivery.service`: oneshot validate **active** after CLI lazy-import fix (`/opt/joymux/venv/bin/joymesh production validate-config`)

Evidence: `service-lifecycle-live.json`, `service-lifecycle-live-validation.md`.

Verdict: **production candidate** until 8h completes with all gates green.
