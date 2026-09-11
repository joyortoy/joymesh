"""Production JoyCTL ↔ JoyMesh HTTP runtime bridge.

Maps the main JoyMesh service onto the neutral SDK wire contracts that
``HttpJoyMeshClient`` already calls:

- ``GET  /v1/runtime/snapshot``
- ``POST /v1/runtime/execute``

Optional reverse push uses ``JOYMESH_JOYCTL_BASE_URL`` (+ token) to POST
results/events into JoyCTL ``/api/v1/mesh/inbound``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from joymesh.sdk.runtime_contracts import (
    ExecutionDirective,
    ExecutionResult,
    RuntimeSnapshot,
)

JOYCTL_BASE_ENV = "JOYMESH_JOYCTL_BASE_URL"
JOYCTL_TOKEN_ENV = "JOYMESH_JOYCTL_TOKEN"
JOYCTL_INBOUND_PATH = "/api/v1/mesh/inbound"


class ExecuteBody(BaseModel):
    """JoyCTL SDK ExecutionDirective wire body (+ optional run context)."""

    model_config = {"extra": "allow"}

    execution_id: str
    attempt_id: str
    organisation_id: str = ""
    organization_id: str = ""
    selected_harness: str
    routing_decision_id: str
    authorization_reference: str
    schema_version: int = 1
    allowed_fallbacks: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    placement_id: str | None = None
    correlation_id: str | None = None
    mission_id: str | None = None
    expires_at: str | None = None
    # Optional convenience fields for local/dev execute bridging.
    task: str | None = None
    workspace: str | None = None


def snapshot_to_sdk(
    internal: Mapping[str, Any],
    *,
    organisation_id: str = "local",
) -> RuntimeSnapshot:
    """Convert JoyMesh runtime_snapshot.as_json() into SDK RuntimeSnapshot."""

    harnesses_raw = internal.get("harnesses") or ()
    harnesses: list[dict[str, Any]] = []
    for item in harnesses_raw:
        if not isinstance(item, Mapping):
            continue
        harnesses.append(
            {
                "harness_id": str(item.get("harness_id") or ""),
                "availability": str(item.get("availability") or item.get("display_status") or ""),
                "authenticated": bool(item.get("authenticated", False)),
                "configured": bool(item.get("configured", item.get("authenticated", False))),
                "capabilities": list(item.get("capabilities") or ()),
            }
        )
    snapshot_id = str(
        internal.get("snapshot_id") or internal.get("correlation_id") or f"rs_{uuid4().hex}"
    )
    return RuntimeSnapshot(
        snapshot_id=snapshot_id,
        organisation_id=str(internal.get("organisation_id") or organisation_id),
        harnesses=tuple(harnesses),
        reason_codes=tuple(str(x) for x in internal.get("reason_codes") or ("runtime_ok",)),
        correlation_id=internal.get("correlation_id"),
        created_at=internal.get("created_at") or internal.get("observed_at"),
        extras={"source": "joymesh.api"},
    )


def post_joycli_inbound(payload: Mapping[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    """POST a typed inbound message to JoyCTL. No-op when base URL unset."""

    base = (os.environ.get(JOYCTL_BASE_ENV) or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "skipped": True, "reason": "joycli_base_unset"}
    url = f"{base}{JOYCTL_INBOUND_PATH}"
    data = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "joymesh-http-runtime/1",
    }
    token = (os.environ.get(JOYCTL_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "detail": detail[:500]}
    except urllib.error.URLError as exc:
        return {"ok": False, "reason": "joycli_unreachable", "detail": str(exc)}


def push_execution_result(result: ExecutionResult) -> dict[str, Any]:
    return post_joycli_inbound(
        {
            "kind": "execution_result",
            "schema": result.schema,
            "payload": result.to_dict(),
        }
    )


def push_runtime_event(
    *,
    event_type: str,
    payload: Mapping[str, Any],
    organisation_id: str = "local",
) -> dict[str, Any]:
    return post_joycli_inbound(
        {
            "kind": "runtime_event",
            "schema": "joy.runtime_event/v1",
            "organisation_id": organisation_id,
            "event_type": event_type,
            "payload": dict(payload),
        }
    )


def build_runtime_router(mesh: Any) -> APIRouter:
    """Attach production SDK paths onto the main FastAPI app."""

    router = APIRouter(tags=["joycli-runtime-bridge"])

    @router.get("/v1/runtime/snapshot")
    async def get_sdk_runtime_snapshot() -> dict[str, Any]:
        snapshot = await mesh.get_runtime_snapshot()
        internal = mesh.runtime_snapshots.as_json(snapshot)
        return snapshot_to_sdk(internal).to_dict()

    @router.post("/v1/runtime/execute")
    async def post_sdk_runtime_execute(body: ExecuteBody) -> dict[str, Any]:
        try:
            directive = ExecutionDirective.from_dict(body.model_dump())
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not directive.placement_id:
            result = ExecutionResult(
                execution_id=directive.execution_id,
                organisation_id=directive.organisation_id,
                ok=False,
                status="rejected",
                harness_id=directive.selected_harness,
                message="placement_required",
                reason_codes=("placement_required",),
                attempt_id=directive.attempt_id,
                correlation_id=directive.correlation_id,
            )
            push_execution_result(result)
            return result.to_dict()

        # Sync accept on the wire path. Full harness launch stays on /api/v1/runs;
        # this bridge establishes bidirectional HTTP contract + reverse notify.
        result = ExecutionResult(
            execution_id=directive.execution_id,
            organisation_id=directive.organisation_id,
            ok=True,
            status="accepted",
            harness_id=directive.selected_harness,
            backend_id="joymesh-http-runtime",
            message="directive_accepted",
            reason_codes=("execution_accepted", "http_bridge"),
            attempt_id=directive.attempt_id,
            correlation_id=directive.correlation_id,
            extras={
                "placement_id": directive.placement_id,
                "mission_id": directive.mission_id,
                "task_present": bool(body.task),
                "workspace_present": bool(body.workspace),
            },
        )
        delivery = push_execution_result(result)
        push_runtime_event(
            event_type="execution_accepted",
            payload={
                "execution_id": result.execution_id,
                "harness_id": result.harness_id,
                "placement_id": directive.placement_id,
            },
            organisation_id=directive.organisation_id or "local",
        )
        payload = result.to_dict()
        payload["extras"] = {**dict(result.extras), "joycli_delivery": delivery}
        return payload

    return router
