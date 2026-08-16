# JoyCLI Operations

## Processes

1. **JoyCLI intake** — `joyctl runtime intake-serve`
2. **JoyMesh runtime** — publisher + execution

## Health vs readiness

* Liveness: process up
* Readiness: listener bound, migrations complete, publisher keys present (unless insecure unsigned mode), projection readable, reconciliation under threshold

```bash
joyctl runtime intake-status
```

## Common failures

| Symptom | Likely cause |
|---------|--------------|
| NACK `missing_signature` | JoyMesh not signing / unsigned mode disabled |
| NACK `invalid_signature` / `unknown_key_id` | Key ID or public key mismatch |
| Ready=false `publisher_keys_missing` | `JOYCLI_RUNTIME_PUBLISHER_*` unset |
| Outbox grows on JoyMesh | JoyCLI intake down or rejecting |

## Socket permissions

Listener creates the socket directory with restricted permissions. Do not chmod the socket world-writable.
