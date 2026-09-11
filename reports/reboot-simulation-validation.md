# Reboot simulation validation

Updated: 2026-08-03T19:17:23Z

## Result: partial pass

Cold-start after `/run` wipe on Lima `prod-qual` (full guest reboot deferred to protect 8h soak).

| Check | Result |
|-------|--------|
| Stop joycli-runtime-intake + joymesh-delivery | done |
| Wipe `/run` sockets / recreate `/run/joymux`, `/run/joymesh` | done |
| Restart intake | active, socket bound |
| Restart joymesh-delivery | active (needs `/run/joymesh` for ProtectSystem) |
| Units enabled for boot | **no** (remain disabled) |
| Full VM reboot | **not performed** |

Evidence: `reports/data/production/reboot-sim-result.json`.
