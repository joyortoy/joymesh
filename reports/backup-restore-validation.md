# Backup/restore validation

Command: `scripts/production/verify_backup_restore_e2e.sh`

Result: `backup_restore_e2e: ok`

JoyCLI: SQLite backup + registry checksum → destroy → restore.
JoyMesh: outbox backup → destroy → restore.
