# JoyCLI Compatibility Layer

This document describes the JoyCLI compatibility routes that enable JoyCLI (durable-local mode) to submit work to a running JoyMesh instance.

## Overview

JoyMesh now provides a compatibility layer that implements the legacy JoyCLI execution API. This allows JoyCLI running in durable-local mode with `JOYCLI_ALLOW_LEGACY_JOYMESH=1` to submit missions and steps to JoyMesh for execution.

## Compatibility Routes

### GET `/ready`

Check if JoyMesh is ready to accept executions.

**Response:**
```json
{
  "ready": true,
  "status": "ok",
  "detail": "JoyMesh runtime ready",
  "routes": {
    "executions": "/executions",
    "health": "/runtime/health"
  },
  "connected_nodes": 0,
  "queued_tasks": 0
}
```

### POST `/executions`

Submit an execution request to JoyMesh.

**Request Body:**
```json
{
  "mission_id": "mission_abc123",
  "step_id": "step_001",
  "repository_path": "/path/to/repository",
  "instruction": "Write tests for the authentication module",
  "policy_grant": "read_only",
  "capabilities": ["repository.read", "filesystem.read"],
  "timeout_seconds": 300,
  "constraints": {},
  "context": {}
}
```

**Note:** `policy_grant` can be either a string or a dict object:
- **String:** Direct policy profile name (e.g., `"read_only"`)
- **Dict:** Object containing policy metadata. The policy profile is extracted from keys in this order:
  1. `profile`
  2. `mode`
  3. `policy_profile`
  4. If none found, defaults to `"read_only"`

Example with dict policy_grant:
```json
{
  "mission_id": "mission_abc123",
  "step_id": "step_001",
  "repository_path": "/path/to/repository",
  "instruction": "Write tests for the authentication module",
  "policy_grant": {"profile": "read_only", "metadata": "example"},
  "capabilities": ["repository.read"],
  "timeout_seconds": 300
}
```

**Response:**
```json
{
  "execution_id": "task-uuid-here"
}
```

**Field Mapping:**
- `repository_path` → `workspace_id` in runtime tasks
- `instruction` → `prompt` in runtime tasks
- `policy_grant` → `policy_profile` in runtime tasks
- `capabilities` → `requested_capabilities` in runtime tasks

### GET `/executions/{execution_id}/events`

Retrieve normalized events for an execution.

**Response:**
```json
{
  "events": [
    {
      "event_type": "accepted",
      "timestamp": "2026-08-16T18:00:00Z",
      "sequence": 1,
      "payload": {},
      "original_type": "task.created"
    },
    {
      "event_type": "started",
      "timestamp": "2026-08-16T18:00:01Z",
      "sequence": 2,
      "payload": {"status": "running"},
      "original_type": "task.started"
    }
  ]
}
```

**Event Type Mapping:**

JoyMesh internal events are mapped to JoyCLI event types:

- `task.created`, `backend.selected` → `accepted`
- `task.queued`, `task.offered` → `queued`
- `task.started`, `route.selected` → `started`
- `task.succeeded`, `execution.completed` → `completed`
- `task.failed`, `execution.failed` → `failed`
- `task.cancelled`, `execution.cancelled` → `cancelled`

### POST `/executions/{execution_id}/cancel`

Cancel a running execution.

**Response:**
```json
{
  "status": "cancelled",
  "execution_id": "task-uuid-here"
}
```

## Configuration

To use JoyCLI with JoyMesh:

1. Start JoyMesh API server:
   ```bash
   uv run joymesh api --host 127.0.0.1 --port 8787
   ```

2. Configure JoyCLI environment variables:
   ```bash
   export JOYCLI_ALLOW_LEGACY_JOYMESH=1
   export JOYCLI_DEV_LEGACY_JOYMESH_URL=http://127.0.0.1:8787
   ```

3. Run JoyCLI mission:
   ```bash
   joyctl mission run-step <mission-id> <step-id>
   ```

## Implementation Notes

- The compatibility layer wraps the existing `/runtime/tasks` API with `skip_routing=True`
- All executions are created with `user_id="joycli"`
- Capabilities must be valid JoyMesh capability IDs (see `src/joymesh/runtime_v1/capabilities.py`)
- **POST /executions always returns immediately** (typically < 100ms)
  - Tasks are created and saved in QUEUED state
  - Routing to workers happens later when workers become available
  - Does NOT block waiting for `execute_with_fallback` or backend selection
- **GET /executions/{id}/events** returns a synthetic "accepted" event immediately
- The layer does not require authentication (suitable for local development only)
- Normal `/runtime/tasks` API behavior unchanged (still performs full routing synchronously)

## Testing

Run the compatibility layer tests:

```bash
uv run pytest tests/test_joycli_compat.py -v
```

## Future Work

- Add authentication support for production use
- Support for streaming event updates via SSE
- Enhanced error reporting with retry guidance
- Metrics and observability integration
