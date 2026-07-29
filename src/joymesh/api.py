"""JoyMesh local REST API and realtime event stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from joymesh.fireconnect import FireConnectClient, FireConnectError
from joymesh.harnesses.contracts import (
    ApprovalToken,
    DiscoveryResult,
    HarnessDefinition,
    HarnessInspection,
    LifecyclePlan,
    LifecycleResult,
)
from joymesh.harnesses.lifecycle import LifecyclePlanError
from joymesh.models import (
    FallbackProposal,
    FireConnectConfigureRequest,
    FireConnectStatus,
    HarnessDescriptor,
    NormalizedEvent,
    RoutePreview,
    RoutePreviewRequest,
    Run,
    RunRequest,
    RunStatus,
    SubscriptionCreate,
    SubscriptionProfile,
    UsageRecord,
)
from joymesh.service import JoyMesh, NoRouteError
from joymesh.workspace import InvalidWorkspaceError

TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


class DiscoveryRequest(BaseModel):
    harness_id: str | None = None
    probe_versions: bool = False
    overrides: dict[str, str] = Field(default_factory=dict)


class LifecycleExecutionRequest(BaseModel):
    plan: LifecyclePlan
    approval: ApprovalToken


def create_app(
    mesh: JoyMesh | None = None,
    fireconnect: FireConnectClient | None = None,
) -> FastAPI:
    owns_mesh = mesh is None
    service = mesh or JoyMesh()
    fireconnect_client = fireconnect or FireConnectClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.initialize()
        app.state.mesh = service
        yield
        if owns_mesh:
            await service.close()

    app = FastAPI(title="JoyMesh API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(InvalidWorkspaceError)
    async def invalid_workspace(_request: Request, exc: InvalidWorkspaceError) -> JSONResponse:
        return _problem(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    @app.get("/api/v1/harnesses", response_model=list[HarnessDescriptor])
    async def harnesses() -> tuple[HarnessDescriptor, ...]:
        """Compatibility adapter view."""

        return await service.detect_harnesses()

    @app.get("/api/v1/harnesses/catalogue", response_model=list[HarnessDefinition])
    async def harness_catalogue() -> tuple[HarnessDefinition, ...]:
        return service.list_harnesses()

    @app.get("/api/v1/harnesses/detected", response_model=list[HarnessDescriptor])
    async def detected_harnesses() -> tuple[HarnessDescriptor, ...]:
        """Compatibility view for the original adapter descriptors."""

        return await service.detect_harnesses()

    @app.post("/api/v1/harnesses/discovery", response_model=list[DiscoveryResult])
    async def discover_harnesses(request: DiscoveryRequest) -> tuple[DiscoveryResult, ...]:
        try:
            return await service.discover_harnesses(
                request.harness_id,
                probe_versions=request.probe_versions,
                overrides=request.overrides,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/harnesses/discovery", response_model=list[DiscoveryResult])
    async def discover_harnesses_read_only() -> tuple[DiscoveryResult, ...]:
        return await service.discover_harnesses()

    @app.get("/api/v1/harnesses/{harness_id}", response_model=HarnessInspection)
    async def inspect_harness(harness_id: str) -> HarnessInspection:
        try:
            return await service.inspect_harness(harness_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/harnesses/{harness_id}/capabilities")
    async def harness_capabilities(harness_id: str) -> dict[str, str]:
        try:
            definition = service.registry.definition(harness_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {key.value: value.value for key, value in definition.capabilities.items()}

    @app.post("/api/v1/harnesses/{harness_id}/install/plan", response_model=LifecyclePlan)
    async def install_plan(harness_id: str) -> LifecyclePlan:
        try:
            return service.plan_install(harness_id)
        except (KeyError, LifecyclePlanError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/harnesses/{harness_id}/install-plan", response_model=LifecyclePlan)
    async def install_plan_compatibility(harness_id: str) -> LifecyclePlan:
        return await install_plan(harness_id)

    @app.post("/api/v1/harnesses/{harness_id}/install", response_model=LifecycleResult)
    async def install_harness(
        harness_id: str,
        request: LifecycleExecutionRequest,
    ) -> LifecycleResult:
        if request.plan.harness_id != service.registry.resolve_id(harness_id):
            raise HTTPException(status_code=422, detail="plan target does not match URL")
        try:
            return await service.execute_lifecycle_plan(
                request.plan,
                approval=request.approval,
            )
        except (PermissionError, LifecyclePlanError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/harnesses/{harness_id}/login/plan", response_model=LifecyclePlan)
    async def login_plan(harness_id: str) -> LifecyclePlan:
        try:
            return service.plan_login(harness_id)
        except (KeyError, LifecyclePlanError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/harnesses/{harness_id}/login-plan", response_model=LifecyclePlan)
    async def login_plan_compatibility(harness_id: str) -> LifecyclePlan:
        return await login_plan(harness_id)

    @app.post("/api/v1/harnesses/{harness_id}/certify", response_model=LifecyclePlan)
    async def certification_plan(harness_id: str) -> LifecyclePlan:
        try:
            return service.plan_certification(harness_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "joymesh", "version": "0.1.0"}

    @app.get("/api/v1/fireconnect", response_model=FireConnectStatus)
    async def fireconnect_status() -> FireConnectStatus:
        return await fireconnect_client.status()

    @app.post("/api/v1/fireconnect/{harness_id}/connect/plan", response_model=LifecyclePlan)
    async def plan_connect_fireconnect(
        harness_id: str,
        request: FireConnectConfigureRequest,
    ) -> LifecyclePlan:
        try:
            return fireconnect_client.plan_connect(harness_id, request.model)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/fireconnect/{harness_id}/disconnect/plan", response_model=LifecyclePlan)
    async def plan_disconnect_fireconnect(harness_id: str) -> LifecyclePlan:
        try:
            return fireconnect_client.plan_disconnect(harness_id)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/fireconnect/{harness_id}/execute", response_model=FireConnectStatus)
    async def execute_fireconnect(
        harness_id: str,
        request: LifecycleExecutionRequest,
    ) -> FireConnectStatus:
        if request.plan.harness_id != harness_id:
            raise HTTPException(status_code=422, detail="plan target does not match URL")
        try:
            return await fireconnect_client.execute_plan(request.plan, request.approval)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/subscriptions", response_model=list[SubscriptionProfile])
    async def subscriptions() -> tuple[SubscriptionProfile, ...]:
        return await service.list_subscriptions()

    @app.post(
        "/api/v1/subscriptions",
        response_model=SubscriptionProfile,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_subscription(data: SubscriptionCreate) -> SubscriptionProfile:
        try:
            return await service.create_subscription(data)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/routes/preview", response_model=RoutePreview)
    async def preview_routes(request: RoutePreviewRequest) -> RoutePreview:
        return await service.preview_routes(
            task=request.task,
            workspace=request.workspace,
            required_capabilities=request.required_capabilities,
            preferred_harness=request.preferred_harness,
            allowed_harnesses=request.allowed_harnesses,
            denied_harnesses=request.denied_harnesses,
            paid_routes_approved=request.paid_routes_approved,
        )

    @app.post("/api/v1/runs", response_model=Run, status_code=status.HTTP_202_ACCEPTED)
    async def create_run(request: RunRequest) -> Run:
        try:
            route = request.route or await service.resolve_route(request=request)
            return await service.start_run(request=request, route=route)
        except NoRouteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}", response_model=Run)
    async def get_run(run_id: str) -> Run:
        run = await service.inspect_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.get("/api/v1/runs", response_model=list[Run])
    async def list_runs(limit: int = Query(default=25, ge=1, le=100)) -> tuple[Run, ...]:
        return await service.list_runs(limit=limit)

    @app.get("/api/v1/runs/{run_id}/events")
    async def stream_events(run_id: str, request: Request) -> StreamingResponse:
        if await service.inspect_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")

        async def event_source() -> AsyncIterator[str]:
            sequence = 0
            while True:
                if await request.is_disconnected():
                    break
                events = await service.events(run_id, after=sequence)
                for event in events:
                    sequence = event.sequence
                    data = event.model_dump_json()
                    yield f"id: {sequence}\nevent: {event.type.value}\ndata: {data}\n\n"
                run = await service.inspect_run(run_id)
                if run is None or (run.status in TERMINAL_STATUSES and not events):
                    break
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/event-log")
    async def event_log(
        run_id: str,
        after: int = Query(default=0, ge=0),
    ) -> tuple[NormalizedEvent, ...]:
        if await service.inspect_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return await service.events(run_id, after=after)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=Run)
    async def cancel_run(run_id: str) -> Run:
        try:
            return await service.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @app.get("/api/v1/runs/{run_id}/usage", response_model=list[UsageRecord])
    async def run_usage(run_id: str) -> tuple[UsageRecord, ...]:
        if await service.inspect_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return await service.usage(run_id=run_id)

    @app.get(
        "/api/v1/runs/{run_id}/fallback",
        response_model=FallbackProposal | None,
    )
    async def run_fallback(run_id: str) -> FallbackProposal | None:
        return await service.fallback_for_run(run_id)

    @app.post("/api/v1/fallbacks/{proposal_id}/approve", response_model=Run)
    async def approve_fallback(proposal_id: str) -> Run:
        try:
            return await service.approve_fallback(proposal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Fallback not found") from exc

    return app


def _problem(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


app = create_app()
