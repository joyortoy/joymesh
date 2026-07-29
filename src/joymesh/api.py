"""JoyMesh local REST API and realtime event stream."""

from __future__ import annotations

import asyncio
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from joymesh.control_plane.contracts import (
    NodeProtocolMessageType,
    OnboardingProgress,
    OnboardingState,
    ProtocolMessage,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.security import generate_node_keypair
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


class BrowserIdentity(BaseModel):
    user_id: str
    organisation_id: str
    workspace_id: str
    browser_session_id: str


class OnboardingUpdateRequest(BaseModel):
    state: OnboardingState
    node_id: str | None = None
    selected_harnesses: tuple[str, ...] | None = None
    limited_mode_reason: str | None = None


class PairingStartRequest(BaseModel):
    code_challenge: str = Field(min_length=43, max_length=128)


class PairingApprovalRequest(BaseModel):
    user_code: str


class NodeRegistrationRequest(BaseModel):
    pairing_id: str
    device_code: str
    name: str
    public_key: str
    key_id: str
    platform: str
    version: str


class RemoteTaskRequest(BaseModel):
    node_id: str
    harness_id: str
    task: str = Field(min_length=1, max_length=100_000)
    required_capabilities: tuple[str, ...] = ()
    key_id: str


async def browser_identity(
    x_joymesh_user_id: str = Header(...),
    x_joymesh_organisation_id: str = Header(...),
    x_joymesh_workspace_id: str = Header(...),
    x_joymesh_browser_session_id: str = Header(...),
) -> BrowserIdentity:
    """Resolve identity injected by the deployment's OIDC/session middleware."""

    return BrowserIdentity(
        user_id=x_joymesh_user_id,
        organisation_id=x_joymesh_organisation_id,
        workspace_id=x_joymesh_workspace_id,
        browser_session_id=x_joymesh_browser_session_id,
    )


BrowserIdentityDependency = Annotated[BrowserIdentity, Depends(browser_identity)]


def create_app(
    mesh: JoyMesh | None = None,
    fireconnect: FireConnectClient | None = None,
) -> FastAPI:
    owns_mesh = mesh is None
    service = mesh or JoyMesh()
    fireconnect_client = fireconnect or FireConnectClient()
    signing_private_key, signing_public_key = generate_node_keypair()
    node_connections: dict[str, WebSocket] = {}

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

    @app.get("/api/v1/control-plane/public-key")
    async def control_plane_public_key() -> dict[str, str]:
        return {
            "algorithm": "Ed25519",
            "key_id": "ephemeral-reference",
            "public_key": signing_public_key,
        }

    @app.get("/api/v1/onboarding", response_model=OnboardingProgress)
    async def onboarding(
        identity: BrowserIdentityDependency,
    ) -> OnboardingProgress:
        return await service.control_plane.onboarding_progress(
            user_id=identity.user_id,
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
        )

    @app.put("/api/v1/onboarding", response_model=OnboardingProgress)
    async def update_onboarding(
        request: OnboardingUpdateRequest,
        identity: BrowserIdentityDependency,
    ) -> OnboardingProgress:
        return await service.control_plane.set_onboarding_state(
            user_id=identity.user_id,
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
            state=request.state,
            node_id=request.node_id,
            selected_harnesses=request.selected_harnesses,
            limited_mode_reason=request.limited_mode_reason,
        )

    @app.post("/api/v1/nodes/pairing/start")
    async def start_pairing(
        request: PairingStartRequest,
        identity: BrowserIdentityDependency,
    ) -> dict[str, object]:
        pairing, device_code = await service.control_plane.begin_pairing(
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
            code_challenge=request.code_challenge,
        )
        return {"pairing": pairing.model_dump(mode="json"), "device_code": device_code}

    @app.post("/api/v1/nodes/pairing/{pairing_id}/approve")
    async def approve_pairing(
        pairing_id: str,
        request: PairingApprovalRequest,
        identity: BrowserIdentityDependency,
    ) -> dict[str, object]:
        pairing = service.control_plane.store.pairings.get(pairing_id)
        if pairing is None or not hmac.compare_digest(pairing.user_code, request.user_code):
            raise HTTPException(status_code=404, detail="pairing session not found")
        if pairing.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=403, detail="pairing belongs to another organisation")
        result = await service.control_plane.approve_pairing(pairing_id, user_id=identity.user_id)
        return result.model_dump(mode="json")

    @app.post("/api/v1/nodes/register", status_code=status.HTTP_201_CREATED)
    async def register_node(request: NodeRegistrationRequest) -> dict[str, object]:
        try:
            node = await service.control_plane.register_node(
                request.pairing_id,
                device_code=request.device_code,
                name=request.name,
                public_key=request.public_key,
                key_id=request.key_id,
                platform=request.platform,
                version=request.version,
            )
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return node.model_dump(mode="json")

    @app.post("/api/v1/workspaces/grants", status_code=status.HTTP_201_CREATED)
    async def create_workspace_grant(
        grant: WorkspaceGrant,
        identity: BrowserIdentityDependency,
    ) -> WorkspaceGrant:
        node = service.control_plane.store.nodes.get(grant.node_id)
        if node is None or node.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=404, detail="node not found")
        if grant.workspace_id != identity.workspace_id:
            raise HTTPException(status_code=403, detail="cross-workspace grant rejected")
        return await service.control_plane.grant_workspace(grant, actor_id=identity.user_id)

    @app.post("/api/v1/remote-tasks", status_code=status.HTTP_202_ACCEPTED)
    async def create_remote_task(
        request: RemoteTaskRequest,
        identity: BrowserIdentityDependency,
    ) -> RemoteTaskEnvelope:
        envelope = RemoteTaskEnvelope(
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
            node_id=request.node_id,
            user_id=identity.user_id,
            browser_session_id=identity.browser_session_id,
            harness_id=request.harness_id,
            task=request.task,
            required_capabilities=request.required_capabilities,
            key_id=request.key_id,
        )
        try:
            signed = await service.control_plane.create_remote_task(
                envelope,
                signing_private_key=signing_private_key,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        socket = node_connections.get(request.node_id)
        if socket is not None:
            await socket.send_json(
                ProtocolMessage(
                    type=NodeProtocolMessageType.TASK_OFFER,
                    node_id=request.node_id,
                    sequence=0,
                    payload=signed.model_dump(mode="json"),
                ).model_dump(mode="json")
            )
        return signed

    @app.websocket("/api/v1/node-gateway")
    async def node_gateway(websocket: WebSocket) -> None:
        configured_token = os.environ.get("JOYMESH_NODE_GATEWAY_TOKEN")
        supplied = websocket.headers.get("authorization", "").removeprefix("Bearer ")
        if configured_token is None or not hmac.compare_digest(supplied, configured_token):
            await websocket.close(code=4401, reason="unauthorized")
            return
        await websocket.accept()
        node_id: str | None = None
        try:
            raw = await websocket.receive_text()
            hello = ProtocolMessage.model_validate_json(raw)
            if hello.type is not NodeProtocolMessageType.HELLO:
                raise ValueError("first message must be hello")
            node = service.control_plane.store.nodes.get(hello.node_id)
            if node is None or node.revoked_at is not None:
                await websocket.close(code=4403, reason="node revoked or unknown")
                return
            node_id = hello.node_id
            node_connections[node_id] = websocket
            await websocket.send_json(
                ProtocolMessage(
                    type=NodeProtocolMessageType.WELCOME,
                    node_id=node_id,
                    sequence=0,
                    reply_to=hello.message_id,
                    payload={"heartbeat_seconds": 20},
                ).model_dump(mode="json")
            )
            while True:
                message = ProtocolMessage.model_validate_json(await websocket.receive_text())
                if message.node_id != node_id:
                    await websocket.close(code=4403, reason="node identity changed")
                    return
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            if node_id is not None and node_connections.get(node_id) is websocket:
                node_connections.pop(node_id, None)

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
