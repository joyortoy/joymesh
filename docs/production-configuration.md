# Production Configuration

See also JoyCLI `docs/production-configuration.md` and `joyctl production validate-config`.

## JoyMesh variables

| Variable | Production rule |
|----------|-----------------|
| `JOYMESH_ENV` | Must be `production` |
| `JOYMESH_DELIVERY_SOCKET` | Absolute Unix socket path |
| `JOYMESH_RUNTIME_SIGNING_KEY_PATH` or `JOYMESH_RUNTIME_SIGNING_KEY` | Required; no ephemeral keys |
| `JOYMESH_RUNTIME_SIGNING_KEY_ID` | Stable key id |
| `JOYMESH_ALLOW_UNSIGNED` | Must be unset/false |
| `JOYMESH_OUTBOX_PATH` | Absolute durable outbox path |
| `JOYMESH_BACKUP_PATH` | Absolute backup root |

Validation fails closed on missing keys, insecure key permissions, relative sockets, and unsigned mode.
