# Service lifecycle live validation (Linux x86-64)

Updated: 2026-08-03T15:40:48.515831+00:00

## Summary

**Pass** for packaged paths on Lima `prod-qual` after:

- `/opt/joymux/venv` candidate wheels
- Shared socket directory `/run/joymux` (`joymux` group)
- `JOYMESH_DATA_DIR=/var/lib/joymesh/data`, signing key under `/etc/joymesh/keys/`
- JoyMesh CLI fix: `production validate-config` no longer imports the FastAPI app at module load

## JoyCLI intake

Prior validation: start/stop/restart **active**. Runtime env should use `/run/joymux/joymesh-delivery.sock` (aligned with JoyMesh).

## JoyMesh delivery companion (oneshot validate)

`systemctl start joymesh-delivery.service` → **active** (lightweight oneshot validate; JoyMesh runtime remains API/library driven).

Evidence: `service-lifecycle-live.json`.
