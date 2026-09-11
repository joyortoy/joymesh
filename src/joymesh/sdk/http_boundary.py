"""Minimal JoyMesh SDK HTTP boundary for runtime snapshot/execute.

Does not import runtime_v1 service internals or JoyCTL.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from joymesh.sdk.runtime_contracts import ExecutionDirective, ExecutionResult, RuntimeSnapshot


class ExecuteBody(BaseModel):
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


def create_sdk_app() -> FastAPI:
    app = FastAPI(title="JoyMesh SDK Boundary", version="1")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "joymesh-sdk-boundary", "production": True}

    @app.get("/v1/runtime/snapshot")
    def snapshot() -> dict[str, Any]:
        return RuntimeSnapshot(
            snapshot_id=f"rs_{uuid4().hex}",
            organisation_id="org_boundary",
            harnesses=({"harness_id": "boundary-harness", "availability": "ready"},),
            reason_codes=("runtime_ok",),
        ).to_dict()

    @app.post("/v1/runtime/execute")
    def execute(body: ExecuteBody) -> dict[str, Any]:
        try:
            directive = ExecutionDirective.from_dict(body.model_dump())
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not directive.placement_id:
            return ExecutionResult(
                execution_id=directive.execution_id,
                organisation_id=directive.organisation_id,
                ok=False,
                status="rejected",
                harness_id=directive.selected_harness,
                message="placement_required",
                reason_codes=("placement_required",),
                attempt_id=directive.attempt_id,
                correlation_id=directive.correlation_id,
            ).to_dict()
        return ExecutionResult(
            execution_id=directive.execution_id,
            organisation_id=directive.organisation_id,
            ok=True,
            status="completed",
            harness_id=directive.selected_harness,
            backend_id="sdk-boundary",
            message="ok",
            reason_codes=("execution_ok",),
            attempt_id=directive.attempt_id,
            correlation_id=directive.correlation_id,
        ).to_dict()

    return app
