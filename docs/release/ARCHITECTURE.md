# JoyCLI / JoyMesh Architecture

```text
JoyMesh
  observe → execute → publish → sign (Ed25519)
        ↓ Unix socket (NDJSON)
JoyCLI
  verify → authenticate → persist → project → route
        ↓
ExecutionDirective (revision-pinned)
        ↓
JoyMesh launch-time revalidation
```

## Ownership

| Component | Owner |
|-----------|--------|
| Runtime observation, execution, outbox, signing client | JoyMesh |
| Unix listener, intake, projection, routing, readiness | JoyCLI |
| Presentation | JoyClaw (out of scope for RC1 packaging) |

JoyMesh deprecated intake (`joymesh.delivery.intake`) is reference/test only and is not a production default.
