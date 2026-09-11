# Upgrade and Rollback

## Upgrade path (RC1 → production-readiness build)

1. Stop publishers / intake
2. `joyctl runtime backup` and `joymesh delivery backup`
3. `joyctl production validate-config` / `joymesh production validate-config`
4. `joyctl runtime migration dry-run`
5. Install new wheels
6. `joyctl runtime migration apply --backup-dir ...`
7. Start intake, then publishers
8. Verify readiness and delivery health

## Rollback

* Code: reinstall previous wheels
* Config: restore previous env files
* Database: restore from pre-upgrade backup if schema incompatible
* Keys: keep overlapping keys until confirmed
* Protocol: wire v1 remains compatible within RC1/production-readiness

Downgrade that would require future-schema data is refused.
