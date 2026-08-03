# Production qualification

Last updated: 2026-08-03T16:35:45Z

## 1-hour Linux soak (prod-qual)

**PASS** — `qualification-1h.json` (copied from VM `/tmp/qualification-1h.json`).

| Gate | Result |
|------|--------|
| duration_met | True (elapsed 3605.1s / 3600s) |
| zero_failures | True (failures=0) |
| min_ticks | True (ticks=714) |
| overall ok | **True** |

## 8-hour Linux soak

**In progress** on prod-qual → `/tmp/qualification-8h.json` (`QUAL_DURATION_SECONDS=28800`). Copy to host when complete.

## macOS verify scripts

Prior pass: fault injection (24+1 skip), upgrade RC1→candidate, multitenancy, resource bounds, incident exercises.

Verdict: **production candidate** until 8h completes and remaining live gates close.
