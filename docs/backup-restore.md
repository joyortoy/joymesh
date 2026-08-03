# Backup and Restore

## JoyCLI

```bash
joyctl --state /var/lib/joycli runtime backup --destination /var/backups/joycli/$(date -u +%Y%m%dT%H%M%SZ)
joyctl --state /var/lib/joycli runtime restore --source /var/backups/joycli/<stamp> --force
```

Uses SQLite online backup API, SHA-256 manifests, schema version checks, and refuses overwrite without `--force`.

## JoyMesh delivery

```bash
joymesh delivery backup --destination /var/backups/joymesh/<stamp> --outbox /var/lib/joymesh/delivery_outbox.sqlite3
joymesh delivery restore --source /var/backups/joymesh/<stamp> --outbox /var/lib/joymesh/delivery_outbox.sqlite3 --force
```

Private keys are excluded unless `--include-private-key` is explicitly set.

## Validated flow

publish → durable commit → backup → destroy → restore → restart → projection/routing/outbox resume.
