# Key rotation validation

Command: `scripts/production/verify_key_rotation_e2e.sh`

Result: `key_rotation_e2e: ok`

Observed sequence: generate A/B → register A → rotate add B overlapping → disable A → revoke A → list shows B active, A revoked with audit events.
