# JoyCTL / JoyMesh Architecture

```text
JoyMesh
  observe → execute → publish → sign (Ed25519)
        ↓ Unix socket (NDJSON)  OR  HTTP POST /api/v1/mesh/inbound
JoyCTL
  verify → authenticate → persist → project → route
        ↓
    ExecutionDirective (revision-pinned, placement_id)
        ↓  HTTP POST /v1/runtime/execute  (and GET /v1/runtime/snapshot)
JoyMesh launch-time revalidation
```

See also: `docs/runtime/http-two-way.md` for the HTTP symmetric path.

## Ownership

| Component | Owner |
|-----------|--------|
| Runtime observation, execution, outbox, signing client | JoyMesh |
| Unix listener, intake, projection, routing, readiness | JoyCTL |
| HTTP inbound webhook (`/api/v1/mesh/inbound`) | JoyCTL |
| Presentation | JoyClaw (out of scope for RC1 packaging) |

JoyMesh deprecated intake (`joymesh.delivery.intake`) is reference/test only and is not a production default.
