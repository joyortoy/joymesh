# JoyCTL ↔ JoyMesh HTTP two-way traffic

Symmetric HTTP path alongside the existing Unix delivery channel.

## Direction

```text
JoyCTL  --GET  /v1/runtime/snapshot-->  JoyMesh
JoyCTL  --POST /v1/runtime/execute -->  JoyMesh
JoyMesh --POST /api/v1/mesh/inbound-->  JoyCTL
```

Ownership is unchanged: JoyCTL verifies/routes and supplies
`ExecutionDirective` (+ JoyMux `placement_id`). JoyMesh observes/executes and
pushes `ExecutionResult` / runtime events / delivery envelopes.

## JoyCTL → JoyMesh

| Env | Value |
|-----|-------|
| `JOYCTL_MESH_TRANSPORT` | `http` |
| `JOYCTL_MESH_ENDPOINT` | e.g. `http://127.0.0.1:8787` |

Client: `HttpJoyMeshClient` (`GET /v1/runtime/snapshot`, `POST /v1/runtime/execute`).

`POST /v1/runtime/execute` requires `placement_id`. Without it the response is
`status=rejected` / `placement_required` (and a reverse notify is still attempted).

## JoyMesh → JoyCTL

| Env | Value |
|-----|-------|
| `JOYMESH_JOYCTL_BASE_URL` | e.g. `http://127.0.0.1:8765` |
| `JOYMESH_JOYCTL_TOKEN` | optional bearer (must match JoyCTL) |
| `JOYMESH_DELIVERY_TRANSPORT` | set `http` to route durable outbox via webhook |
| `JOYCTL_MESH_INBOUND_TOKEN` | optional shared secret on JoyCTL |

Inbound kinds on `POST /api/v1/mesh/inbound`:

- `execution_result`
- `runtime_event`
- `delivery_envelope`

Inspect: `GET /api/v1/mesh/inbound`.

## Local smoke

```bash
# Terminal A — JoyCTL hosted
joyctl hosted --host 127.0.0.1 --port 8765

# Terminal B — JoyMesh API (example)
export JOYMESH_JOYCTL_BASE_URL=http://127.0.0.1:8765
uvicorn joymesh.api:app --host 127.0.0.1 --port 8787

# Terminal C — JoyCTL client env
export JOYCTL_MESH_TRANSPORT=http
export JOYCTL_MESH_ENDPOINT=http://127.0.0.1:8787
```

Unix socket delivery remains the default production push path when
`JOYMESH_DELIVERY_TRANSPORT` is unset on POSIX.
