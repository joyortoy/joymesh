# JoyCLI Upgrade

## Schema migrations

Runtime intake uses `joycli_schema_migrations`.

* Current version: **1**
* Upgrades are applied atomically at store open
* Checksum mismatch fails closed
* Unsupported future versions fail closed
* Failed migrations do **not** delete the database

## Rollback

1. Stop intake and JoyMesh publishers.
2. Restore state directory backup taken before upgrade.
3. Reinstall previous wheel pair.
4. Restart intake, then JoyMesh.

Downgrade of applied migration SQL is not supported. Restore from backup.

## Compatibility

* Wire protocol / schema version must match between JoyMesh and JoyCLI packages released together.
* Ed25519 key IDs must remain registered across upgrades.
