# Upgrade/rollback validation

Last updated: 2026-08-03T14:39:25Z

Primary path: **RC1 baseline import → candidate `joymesh.production` import** — **PASS**.

Code rollback to RC1 verifies baseline import only (RC1 wheel intentionally lacks `joymesh.production`).

Unsafe schema downgrade: **refused by policy** (see `test_restore_rejects_future_schema_version`).

Artifact: `upgrade-rollback.json`.
