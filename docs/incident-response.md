# Incident Response

## Runbooks (summary)

Each incident: detect → contain → preserve evidence → recover → verify → escalate → post-incident.

### Signature failures
Disable traffic if compromise suspected; inspect key registry; rotate keys; preserve rejected audit counters.

### Suspected key compromise
Revoke compromised key on JoyCLI; generate replacement on JoyMesh; register new public key; resume; keep failed-attempt audit.

### Outbox not draining
Check JoyCLI listener readiness, socket perms, signature/key ids, disk space; do not delete undelivered authoritative outbox rows.

### Database corruption
Stop services; restore from last good backup; verify checksums; migration status; replay outbox.

### Routing selects no harness
Inspect projections, freshness, reconciliation flags, organisation scope; do not force unsigned mode.

See also `docs/key-management.md` and `docs/backup-restore.md`.
