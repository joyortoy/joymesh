"""JoyMesh local REST API and realtime event stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from joymesh.models import (
    FallbackProposal,
    HarnessDescriptor,
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


def create_app(mesh: JoyMesh | None = None) -> FastAPI:
    owns_mesh = mesh is None
    service = mesh or JoyMesh()

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
        return await service.detect_harnesses()

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
