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
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from joymesh.connectors import ConnectorDefinition
from joymesh.connectors.lifecycle_models import (
    ConnectorLifecyclePlanResponse,
    ConnectorReadiness,
    ConnectorTaskEvent,
    ConnectorTaskRecord,
    ConnectorTaskStatus,
)
from joymesh.connectors.planning import (
    ConnectorAction,
    ConnectorPlanError,
    ConnectorTaskPlan,
)
from joymesh.control_plane.contracts import (
    NodeProtocolMessageType,
    NodeSession,
    OnboardingProgress,
    OnboardingState,
    PaidRoutePolicy,
    ProtocolMessage,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.gateway import ConnectorTaskEventIngestor, NodeGateway
from joymesh.control_plane.onboarding_flow import derive_wizard_state
from joymesh.control_plane.onboarding_store import OnboardingConflictError
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
    utc_now,
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


class ConnectorPlanRequest(BaseModel):
    method_id: str | None = None
    platform: str | None = None
    download_digest: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class ConnectorTaskExecutionRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool


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
    pairing_id: str | None = None
    selected_harnesses: tuple[str, ...] | None = None
    limited_mode_reason: str | None = None
    paid_route_policy: PaidRoutePolicy | None = None
    fireconnect_enabled: bool | None = None
    last_error: str | None = None
    expected_revision: int | None = None
    clear_error: bool = False


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


def _onboarding_actions(
    state: OnboardingState,
    progress: OnboardingProgress,
    readiness: list[ConnectorReadiness],
) -> list[str]:
    actions: list[str] = []
    if state is OnboardingState.NODE_PAIRING_REQUIRED:
        actions.extend(["start_pairing", "refresh_pairing"])
    if state is OnboardingState.ENVIRONMENT_CHECK:
        actions.append("run_environment_diagnostics")
    if state is OnboardingState.HARNESS_SELECTION:
        actions.append("save_selection")
    if state is OnboardingState.INSTALLATION_REVIEW:
        actions.extend(["request_install_plans", "approve_install", "reject_install"])
    if state is OnboardingState.INSTALLING:
        actions.append("poll_tasks")
    if state is OnboardingState.AUTHENTICATION_REQUIRED:
        actions.extend(["start_authentication", "verify_authentication"])
    if state is OnboardingState.CERTIFICATION_REQUIRED:
        actions.append("start_certification")
    if state in {
        OnboardingState.FINAL_CHECK,
        OnboardingState.ROUTING_SETUP,
        OnboardingState.FIRECONNECT_SETUP,
    }:
        actions.append("complete")
        if not all(
            item.routing_eligible
            for item in readiness
            if item.connector_id in progress.selected_harnesses
        ):
            actions.append("enter_limited_mode")
    if state in {OnboardingState.FAILED, OnboardingState.BLOCKED}:
        actions.append("retry")
    return actions


def create_app(
    mesh: JoyMesh | None = None,
    fireconnect: FireConnectClient | None = None,
) -> FastAPI:
    owns_mesh = mesh is None
    service = mesh or JoyMesh()
    fireconnect_client = fireconnect or FireConnectClient()
    signing_private_key, signing_public_key = generate_node_keypair()
    gateway = NodeGateway(
        signing_private_key=signing_private_key,
        signing_public_key=signing_public_key,
    )
    event_ingestor: ConnectorTaskEventIngestor | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal event_ingestor
        await service.initialize()
        event_ingestor = ConnectorTaskEventIngestor(store=service.connector_lifecycle.store)

        async def offer(task: object, plan: ConnectorTaskPlan) -> bool:
            assert isinstance(task, ConnectorTaskRecord)
            return await gateway.offer_connector_task(task, plan)

        service.connector_lifecycle.register_offer_callback(offer)
        app.state.mesh = service
        app.state.gateway = gateway
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

    @app.get("/connector-catalogue", response_model=list[ConnectorDefinition])
    @app.get("/api/v1/connector-catalogue", response_model=list[ConnectorDefinition])
    async def connectors() -> tuple[ConnectorDefinition, ...]:
        """Return the versioned backend catalogue; provider routes are intentionally absent."""

        return service.list_connectors()

    @app.get("/connector-catalogue/{connector_id}", response_model=ConnectorDefinition)
    @app.get(
        "/api/v1/connector-catalogue/{connector_id}",
        response_model=ConnectorDefinition,
    )
    async def connector(connector_id: str) -> ConnectorDefinition:
        try:
            return service.connector(connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/nodes/{node_id}/connectors", response_model=list[DiscoveryResult])
    @app.get("/api/v1/nodes/{node_id}/connectors", response_model=list[DiscoveryResult])
    async def node_connectors(node_id: str) -> tuple[DiscoveryResult, ...]:
        del node_id
        return await service.discover_harnesses()

    @app.post("/nodes/{node_id}/connectors/discover", response_model=list[DiscoveryResult])
    @app.post(
        "/api/v1/nodes/{node_id}/connectors/discover",
        response_model=list[DiscoveryResult],
    )
    async def discover_node_connectors(node_id: str) -> tuple[DiscoveryResult, ...]:
        del node_id
        return await service.discover_harnesses(probe_versions=True)

    async def build_connector_plan(
        node_id: str,
        connector_id: str,
        action: ConnectorAction,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        try:
            plan = await service.plan_and_persist_connector_task(
                node_id=node_id,
                connector_id=connector_id,
                action=action,
                method_id=request.method_id,
                platform=request.platform,
                download_digest=request.download_digest,
            )
        except (KeyError, ConnectorPlanError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.connector_lifecycle.plan_response(plan)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/install/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    @app.post(
        "/api/v1/nodes/{node_id}/connectors/{connector_id}/install/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_install_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.INSTALL, request)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/upgrade/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_upgrade_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.UPGRADE, request)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/uninstall/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_uninstall_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.UNINSTALL, request)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/authenticate/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_authentication_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(
            node_id, connector_id, ConnectorAction.AUTHENTICATE, request
        )

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/verify-authentication/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_verify_authentication_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(
            node_id, connector_id, ConnectorAction.VERIFY_AUTHENTICATION, request
        )

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/verify-adapter/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_verify_adapter_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(
            node_id, connector_id, ConnectorAction.VERIFY_ADAPTER, request
        )

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/repair/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_repair_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.REPAIR, request)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/discover/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_discover_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.DISCOVER, request)

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/certify/plan",
        response_model=ConnectorLifecyclePlanResponse,
    )
    async def connector_certification_task_plan(
        node_id: str,
        connector_id: str,
        request: ConnectorPlanRequest,
    ) -> ConnectorLifecyclePlanResponse:
        return await build_connector_plan(node_id, connector_id, ConnectorAction.CERTIFY, request)

    @app.get(
        "/nodes/{node_id}/connectors/{connector_id}/readiness",
        response_model=ConnectorReadiness,
    )
    @app.get(
        "/api/v1/nodes/{node_id}/connectors/{connector_id}/readiness",
        response_model=ConnectorReadiness,
    )
    async def connector_readiness(node_id: str, connector_id: str) -> ConnectorReadiness:
        try:
            return await service.connector_readiness(node_id=node_id, connector_id=connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/nodes/{node_id}/connectors/readiness", response_model=list[ConnectorReadiness])
    @app.get(
        "/api/v1/nodes/{node_id}/connectors/readiness",
        response_model=list[ConnectorReadiness],
    )
    async def connectors_readiness(node_id: str) -> tuple[ConnectorReadiness, ...]:
        return await service.list_connector_readiness(node_id=node_id)

    @app.get(
        "/nodes/{node_id}/active-connector-tasks",
        response_model=list[ConnectorTaskRecord],
    )
    async def active_connector_tasks(node_id: str) -> tuple[ConnectorTaskRecord, ...]:
        return await service.active_connector_tasks(node_id=node_id)

    @app.post("/connector-tasks/{plan_id}/execute", response_model=ConnectorTaskRecord)
    @app.post("/connector-tasks/{plan_id}/approve", response_model=ConnectorTaskRecord)
    async def execute_connector_task(
        plan_id: str,
        request: ConnectorTaskExecutionRequest,
    ) -> ConnectorTaskRecord:
        try:
            return await service.execute_connector_plan(
                plan_id=plan_id,
                plan_hash=request.plan_hash,
                approved=request.approved,
            )
        except (KeyError, ConnectorPlanError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/connector-tasks/{task_id}", response_model=ConnectorTaskRecord)
    async def connector_task(task_id: str) -> ConnectorTaskRecord:
        try:
            return await service.connector_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/connector-tasks/{task_id}/events", response_model=list[ConnectorTaskEvent])
    async def connector_task_events(
        task_id: str,
        after: int = Query(default=0, ge=0),
    ) -> tuple[ConnectorTaskEvent, ...]:
        try:
            await service.connector_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await service.connector_task_events(task_id, after=after)

    @app.post("/connector-tasks/{task_id}/cancel", response_model=ConnectorTaskRecord)
    async def cancel_connector_task(task_id: str) -> ConnectorTaskRecord:
        try:
            return await service.connector_lifecycle.cancel_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/connector-tasks/{task_id}/retry", response_model=ConnectorTaskRecord)
    async def retry_connector_task(task_id: str) -> ConnectorTaskRecord:
        try:
            return await service.connector_lifecycle.retry_task(task_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/connector-tasks/{task_id}/authentication-complete",
        response_model=ConnectorTaskRecord,
    )
    async def connector_authentication_complete(task_id: str) -> ConnectorTaskRecord:
        try:
            return await service.connector_lifecycle.authentication_complete(task_id)
        except (KeyError, ValueError, ConnectorPlanError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/connector-tasks/{task_id}/resume", response_model=ConnectorTaskRecord)
    async def resume_connector_task(task_id: str) -> ConnectorTaskRecord:
        try:
            return await service.connector_lifecycle.retry_task(task_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/harnesses/catalogue", response_model=list[HarnessDefinition])
    async def harness_catalogue() -> tuple[HarnessDefinition, ...]:
        return service.list_harnesses()

    @app.get("/api/v1/harnesses/preferences")
    async def harness_preferences() -> dict[str, object]:
        """Read enabled/default/custom harness preferences (not metrics)."""

        from joymesh.config import load_user_config

        return load_user_config().harnesses.as_dict()

    class HarnessPreferencesUpdate(BaseModel):
        enabled: list[str] | None = None
        default: str | None = None
        clear_default: bool = False

    class CustomHarnessBody(BaseModel):
        harness_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
        display_name: str
        executable: str
        args: list[str] = Field(default_factory=list)
        input_mode: str = "stdin"
        output_mode: str = "jsonl"
        timeout_seconds: int = Field(default=1800, ge=1, le=86_400)
        working_directory: str = "inherit"
        environment_allowlist: list[str] = Field(default_factory=lambda: ["PATH", "HOME"])
        capabilities: list[str] = Field(default_factory=list)

    @app.put("/api/v1/harnesses/preferences")
    async def update_harness_preferences(body: HarnessPreferencesUpdate) -> dict[str, object]:
        from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences
        from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS

        prefs = load_user_config().harnesses
        enabled = tuple(body.enabled) if body.enabled is not None else prefs.enabled
        if any(item in FORBIDDEN_PRODUCTION_HARNESS_IDS for item in enabled):
            raise HTTPException(status_code=400, detail="forbidden harness id in enabled list")
        if body.clear_default:
            default = None
        elif body.default is not None:
            if body.default in FORBIDDEN_PRODUCTION_HARNESS_IDS:
                raise HTTPException(status_code=400, detail="forbidden harness id")
            default = body.default
        else:
            default = prefs.default
        save_harness_preferences(
            HarnessPreferences(
                enabled=enabled,
                default=default,
                custom=dict(prefs.custom),
                selection_required=False,
                migration_message=None,
            )
        )
        return load_user_config().harnesses.as_dict()

    @app.post("/api/v1/harnesses/{harness_id}/enable")
    async def enable_harness(harness_id: str) -> dict[str, object]:
        from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences
        from joymesh.harnesses.nonstandard import validate_custom_harness_config
        from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS

        if harness_id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
            raise HTTPException(status_code=400, detail="harness removed from production")
        prefs = load_user_config().harnesses
        if harness_id in prefs.custom:
            validation = validate_custom_harness_config(prefs.custom[harness_id])
            if not validation.ok:
                raise HTTPException(
                    status_code=400,
                    detail=[issue.message for issue in validation.issues],
                )
        enabled = tuple(dict.fromkeys([*prefs.enabled, harness_id]))
        save_harness_preferences(
            HarnessPreferences(
                enabled=enabled,
                default=prefs.default,
                custom=dict(prefs.custom),
                selection_required=False,
                migration_message=None,
            )
        )
        return load_user_config().harnesses.as_dict()

    @app.post("/api/v1/harnesses/{harness_id}/disable")
    async def disable_harness(harness_id: str) -> dict[str, object]:
        from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences

        prefs = load_user_config().harnesses
        enabled = tuple(item for item in prefs.enabled if item != harness_id)
        default = None if prefs.default == harness_id else prefs.default
        save_harness_preferences(
            HarnessPreferences(
                enabled=enabled,
                default=default,
                custom=dict(prefs.custom),
                selection_required=prefs.selection_required,
                migration_message=prefs.migration_message,
            )
        )
        return load_user_config().harnesses.as_dict()

    @app.post("/api/v1/harnesses/custom/validate")
    async def validate_custom_harness(body: CustomHarnessBody) -> dict[str, object]:
        from joymesh.config import CustomHarnessConfig
        from joymesh.harnesses.nonstandard import validate_custom_harness_config

        config = CustomHarnessConfig(
            harness_id=body.harness_id,
            display_name=body.display_name,
            executable=body.executable,
            args=tuple(body.args),
            input_mode=body.input_mode,
            output_mode=body.output_mode,
            timeout_seconds=body.timeout_seconds,
            working_directory=body.working_directory,
            environment_allowlist=tuple(body.environment_allowlist),
            capabilities=tuple(body.capabilities),
        )
        result = validate_custom_harness_config(config)
        return {
            "ok": result.ok,
            "issues": [{"code": i.code, "message": i.message} for i in result.issues],
        }

    @app.put("/api/v1/harnesses/custom/{harness_id}")
    async def save_custom_harness(harness_id: str, body: CustomHarnessBody) -> dict[str, object]:
        from joymesh.config import (
            CustomHarnessConfig,
            HarnessPreferences,
            load_user_config,
            save_harness_preferences,
        )
        from joymesh.harnesses.nonstandard import validate_custom_harness_config

        if body.harness_id != harness_id:
            raise HTTPException(status_code=400, detail="harness_id mismatch")
        config = CustomHarnessConfig(
            harness_id=body.harness_id,
            display_name=body.display_name,
            executable=body.executable,
            args=tuple(body.args),
            input_mode=body.input_mode,
            output_mode=body.output_mode,
            timeout_seconds=body.timeout_seconds,
            working_directory=body.working_directory,
            environment_allowlist=tuple(body.environment_allowlist),
            capabilities=tuple(body.capabilities),
        )
        result = validate_custom_harness_config(config)
        if not result.ok:
            raise HTTPException(
                status_code=400,
                detail=[issue.message for issue in result.issues],
            )
        prefs = load_user_config().harnesses
        custom = dict(prefs.custom)
        custom[harness_id] = config
        save_harness_preferences(
            HarnessPreferences(
                enabled=prefs.enabled,
                default=prefs.default,
                custom=custom,
                selection_required=prefs.selection_required,
                migration_message=prefs.migration_message,
            )
        )
        return {"saved": True, "enabled": harness_id in prefs.enabled}

    @app.delete("/api/v1/harnesses/custom/{harness_id}")
    async def remove_custom_harness(harness_id: str) -> dict[str, object]:
        from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences

        prefs = load_user_config().harnesses
        if harness_id not in prefs.custom:
            raise HTTPException(status_code=404, detail="unknown custom harness")
        custom = dict(prefs.custom)
        del custom[harness_id]
        enabled = tuple(item for item in prefs.enabled if item != harness_id)
        default = None if prefs.default == harness_id else prefs.default
        save_harness_preferences(
            HarnessPreferences(
                enabled=enabled,
                default=default,
                custom=custom,
                selection_required=prefs.selection_required,
                migration_message=prefs.migration_message,
            )
        )
        return load_user_config().harnesses.as_dict()

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
        try:
            return await service.control_plane.set_onboarding_state(
                user_id=identity.user_id,
                organisation_id=identity.organisation_id,
                workspace_id=identity.workspace_id,
                state=request.state,
                node_id=request.node_id,
                pairing_id=request.pairing_id,
                selected_harnesses=request.selected_harnesses,
                limited_mode_reason=request.limited_mode_reason,
                paid_route_policy=request.paid_route_policy,
                fireconnect_enabled=request.fireconnect_enabled,
                last_error=request.last_error,
                expected_revision=request.expected_revision,
                clear_error=request.clear_error,
            )
        except OnboardingConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/onboarding/snapshot")
    async def onboarding_snapshot(
        identity: BrowserIdentityDependency,
    ) -> dict[str, object]:
        progress = await service.control_plane.onboarding_progress(
            user_id=identity.user_id,
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
        )
        readiness: list[ConnectorReadiness] = []
        active_tasks: list[ConnectorTaskRecord] = []
        if progress.node_id:
            readiness = list(await service.list_connector_readiness(node_id=progress.node_id))
            active_tasks = list(await service.active_connector_tasks(node_id=progress.node_id))
        derived = derive_wizard_state(progress, readiness=readiness, active_tasks=active_tasks)
        pairing = None
        if progress.pairing_id:
            try:
                pairing = await service.control_plane.pairing_status(progress.pairing_id)
            except KeyError:
                pairing = {"pairing_id": progress.pairing_id, "status": "unknown"}
        environment = None
        if progress.node_id:
            try:
                environment = await service.control_plane.environment_diagnostics(
                    node_id=progress.node_id
                )
            except (KeyError, PermissionError) as exc:
                environment = {
                    "node_id": progress.node_id,
                    "node_online": False,
                    "detail": str(exc),
                }
        return {
            "revision": progress.revision,
            "state": derived.value,
            "stored_state": progress.state.value,
            "workspace_id": progress.workspace_id,
            "selected_node_id": progress.node_id,
            "selected_connector_ids": list(progress.selected_harnesses),
            "pairing": pairing,
            "environment": environment,
            "connectors": [item.model_dump(mode="json") for item in readiness],
            "active_tasks": [item.model_dump(mode="json") for item in active_tasks],
            "routing_preferences": {"paid_route_policy": progress.paid_route_policy.value},
            "fireconnect_preferences": {"enabled": progress.fireconnect_enabled},
            "limited_mode": progress.state is OnboardingState.LIMITED_MODE,
            "limited_mode_reason": progress.limited_mode_reason,
            "blocking_reasons": [
                item.blocking_reason
                for item in readiness
                if item.blocking_reason and item.connector_id in progress.selected_harnesses
            ],
            "available_actions": _onboarding_actions(derived, progress, readiness),
            "progress": progress.model_dump(mode="json"),
            "updated_at": progress.updated_at.isoformat(),
            "authority": "python-control-plane",
            "synchronised": not progress.unsynchronised,
        }

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
        await service.control_plane.set_onboarding_state(
            user_id=identity.user_id,
            organisation_id=identity.organisation_id,
            workspace_id=identity.workspace_id,
            state=OnboardingState.NODE_PAIRING_REQUIRED,
            pairing_id=pairing.id,
        )
        return {
            "pairing": pairing.model_dump(mode="json"),
            "device_code": device_code,
            "cli_hint": f"joymesh node pair --code {pairing.user_code}",
        }

    @app.get("/api/v1/nodes/pairing/{pairing_id}")
    async def pairing_status(
        pairing_id: str,
        identity: BrowserIdentityDependency,
    ) -> dict[str, object]:
        try:
            status_payload = await service.control_plane.pairing_status(pairing_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="pairing session not found") from exc
        pairing = service.control_plane.store.pairings.get(pairing_id)
        if pairing is None or pairing.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=403, detail="pairing belongs to another organisation")
        if status_payload.get("status") == "paired" and status_payload.get("node"):
            node = status_payload["node"]
            assert isinstance(node, dict)
            await service.control_plane.set_onboarding_state(
                user_id=identity.user_id,
                organisation_id=identity.organisation_id,
                workspace_id=identity.workspace_id,
                state=OnboardingState.ENVIRONMENT_CHECK,
                node_id=str(node.get("id")),
                pairing_id=pairing_id,
            )
        return status_payload

    @app.post("/api/v1/nodes/pairing/{pairing_id}/cancel")
    async def cancel_pairing(
        pairing_id: str,
        identity: BrowserIdentityDependency,
    ) -> dict[str, str]:
        pairing = service.control_plane.store.pairings.get(pairing_id)
        if pairing is None:
            raise HTTPException(status_code=404, detail="pairing session not found")
        if pairing.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=403, detail="pairing belongs to another organisation")
        await service.control_plane.cancel_pairing(pairing_id)
        return {"status": "cancelled", "pairing_id": pairing_id}

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

    @app.get("/api/v1/nodes/{node_id}/environment")
    async def node_environment(
        node_id: str,
        identity: BrowserIdentityDependency,
    ) -> dict[str, object]:
        node = service.control_plane.store.nodes.get(node_id)
        if node is None or node.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=404, detail="node not found")
        if node.workspace_id != identity.workspace_id:
            raise HTTPException(status_code=403, detail="node belongs to another workspace")
        try:
            return await service.control_plane.environment_diagnostics(node_id=node_id)
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        socket = gateway.connections.get(request.node_id)
        if socket is not None:
            await socket.websocket.send_json(
                ProtocolMessage(
                    type=NodeProtocolMessageType.TASK_OFFER,
                    node_id=request.node_id,
                    sequence=0,
                    payload=signed.model_dump(mode="json"),
                ).model_dump(mode="json")
            )
        return signed

    @app.get("/nodes/{node_id}/session", response_model=NodeSession | None)
    async def node_session(node_id: str) -> NodeSession | None:
        connection = gateway.connections.get(node_id)
        return None if connection is None else connection.session

    @app.post("/nodes/{node_id}/revoke")
    async def revoke_node(node_id: str, identity: BrowserIdentityDependency) -> dict[str, str]:
        node = service.control_plane.store.nodes.get(node_id)
        if node is None or node.organisation_id != identity.organisation_id:
            raise HTTPException(status_code=404, detail="node not found")
        service.control_plane.store.nodes[node_id] = node.model_copy(
            update={"revoked_at": utc_now()}
        )
        await gateway.revoke_node(node_id)
        return {"status": "revoked", "node_id": node_id}

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/routing/enable",
        response_model=ConnectorReadiness,
    )
    async def enable_connector_routing(node_id: str, connector_id: str) -> ConnectorReadiness:
        return await service.connector_lifecycle.enable_routing(
            node_id=node_id, connector_id=connector_id
        )

    @app.post(
        "/nodes/{node_id}/connectors/{connector_id}/routing/disable",
        response_model=ConnectorReadiness,
    )
    async def disable_connector_routing(node_id: str, connector_id: str) -> ConnectorReadiness:
        return await service.connector_lifecycle.disable_routing(
            node_id=node_id, connector_id=connector_id
        )

    @app.get("/connector-tasks/{task_id}/events/stream")
    async def stream_connector_task_events(task_id: str, request: Request) -> StreamingResponse:
        try:
            await service.connector_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def event_source() -> AsyncIterator[str]:
            sequence = 0
            while True:
                if await request.is_disconnected():
                    break
                events = await service.connector_task_events(task_id, after=sequence)
                for event in events:
                    sequence = event.sequence
                    yield (
                        f"id: {sequence}\nevent: {event.event_type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                task = await service.connector_task(task_id)
                if (
                    task.status
                    in {
                        ConnectorTaskStatus.SUCCEEDED,
                        ConnectorTaskStatus.FAILED,
                        ConnectorTaskStatus.CANCELLED,
                        ConnectorTaskStatus.EXPIRED,
                        ConnectorTaskStatus.INTERRUPTED,
                    }
                    and not events
                ):
                    break
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.websocket("/api/v1/node-gateway")
    @app.websocket("/nodes/connect")
    async def node_gateway(websocket: WebSocket) -> None:
        configured_token = os.environ.get("JOYMESH_NODE_GATEWAY_TOKEN")
        supplied = websocket.headers.get("authorization", "").removeprefix("Bearer ")
        # Bearer token is an optional edge gate; cryptographic node auth is still required.
        if configured_token is not None and not hmac.compare_digest(supplied, configured_token):
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
            await gateway.authenticate(
                websocket,
                node_id=node_id,
                organisation_id=node.organisation_id,
                public_key=node.public_key,
                runtime_version=str(hello.payload.get("runtime_version", node.version)),
                remote_address=websocket.client.host if websocket.client else None,
            )
            await service.connector_lifecycle.offer_queued_tasks(node_id=node_id)
            while True:
                message = ProtocolMessage.model_validate_json(await websocket.receive_text())
                if message.node_id != node_id:
                    await websocket.close(code=4403, reason="node identity changed")
                    return
                if message.type is NodeProtocolMessageType.HEARTBEAT:
                    connection = gateway.connections.get(node_id)
                    if connection is not None:
                        gateway.sessions[connection.session.session_id] = (
                            connection.session.model_copy(update={"last_seen_at": utc_now()})
                        )
                        await websocket.send_json(
                            ProtocolMessage(
                                type=NodeProtocolMessageType.HEARTBEAT_ACK,
                                node_id=node_id,
                                sequence=0,
                                reply_to=message.message_id,
                                payload={"status": "ok"},
                            ).model_dump(mode="json")
                        )
                    continue
                if message.type is NodeProtocolMessageType.READY:
                    await service.connector_lifecycle.offer_queued_tasks(node_id=node_id)
                    continue
                if message.type is NodeProtocolMessageType.TASK_RECONCILE_RESPONSE:
                    continue
                if event_ingestor is not None and message.type.value.startswith("task."):
                    try:
                        await event_ingestor.ingest(message)
                    except (ValueError, PermissionError, KeyError) as exc:
                        # Keep the authenticated node session alive; reject only the event.
                        await websocket.send_json(
                            ProtocolMessage(
                                type=NodeProtocolMessageType.TASK_PROGRESS,
                                node_id=node_id,
                                sequence=0,
                                payload={
                                    "detail": f"event rejected: {exc}",
                                    "rejected_type": message.type.value,
                                    "task_id": message.payload.get("task_id"),
                                },
                            ).model_dump(mode="json")
                        )
                    continue
        except WebSocketDisconnect:
            pass
        except (ValueError, PermissionError):
            pass
        finally:
            if node_id is not None:
                gateway.disconnect(node_id, websocket)

    @app.get("/api/v1/fireconnect", response_model=FireConnectStatus)
    async def fireconnect_status() -> FireConnectStatus:
        return await fireconnect_client.status()

    @app.post(
        "/api/v1/fireconnect/{harness_id}/connect/plan",
        response_model=LifecyclePlan,
        deprecated=True,
    )
    async def plan_connect_fireconnect(
        harness_id: str,
        request: FireConnectConfigureRequest,
        response: Response,
    ) -> LifecyclePlan:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = (
            '</api/v1/provider-routes/{manager}/{connector}/enable>; rel="successor-version"'
        )
        try:
            return fireconnect_client.plan_connect(harness_id, request.model)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/fireconnect/{harness_id}/disconnect/plan",
        response_model=LifecyclePlan,
        deprecated=True,
    )
    async def plan_disconnect_fireconnect(
        harness_id: str,
        response: Response,
    ) -> LifecyclePlan:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = (
            '</api/v1/provider-routes/{manager}/{connector}/disable>; rel="successor-version"'
        )
        try:
            return fireconnect_client.plan_disconnect(harness_id)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/fireconnect/{harness_id}/execute",
        response_model=FireConnectStatus,
        deprecated=True,
    )
    async def execute_fireconnect(
        harness_id: str,
        request: LifecycleExecutionRequest,
        response: Response,
    ) -> FireConnectStatus:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = (
            '</api/v1/provider-routes/{manager}/{connector}/enable>; rel="successor-version"'
        )
        if request.plan.harness_id != harness_id:
            raise HTTPException(status_code=422, detail="plan target does not match URL")
        try:
            return await fireconnect_client.execute_plan(request.plan, request.approval)
        except FireConnectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/provider-routes/managers")
    async def list_provider_route_managers() -> list[dict[str, str]]:
        """List provider-route managers (configuration layers, not harnesses)."""

        from joymesh.runtime_v1.provider_routes import builtin_provider_route_managers

        return [
            {"manager_id": item.manager_id, "display_name": item.display_name}
            for item in builtin_provider_route_managers().values()
        ]

    @app.get("/api/v1/provider-routes/status")
    async def provider_route_status(
        connector_id: str | None = None,
        manager_id: str = "fireconnect",
    ) -> dict[str, object]:
        """Inspect provider routes separately from connector readiness."""

        from joymesh.runtime_v1.provider_routes import get_provider_route_manager

        try:
            manager = get_provider_route_manager(manager_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        discovery = await manager.discover()
        auth = await manager.inspect_auth()
        routes = await manager.list_routes(connector_id)
        return {
            "manager": {"manager_id": manager.manager_id, "display_name": manager.display_name},
            "discovery": discovery.as_dict(),
            "authentication": auth.as_dict(),
            "routes": [item.as_dict() for item in routes],
            "display": {
                "connector": connector_id,
                "provider": "fireworks"
                if any(r.enabled for r in routes if r.provider_id == "fireworks")
                else "native",
                "managed_by": manager.display_name,
                "note": "FireConnect configures providers; the harness connector executes tasks.",
            },
        }

    @app.post("/api/v1/provider-routes/{manager_id}/{connector_id}/enable")
    async def enable_provider_route(
        manager_id: str,
        connector_id: str,
        model: str | None = None,
        approve: bool = False,
    ) -> dict[str, object]:
        from joymesh.runtime_v1.provider_routes.fireconnect import FireConnectManagerError
        from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseError
        from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

        if not approve:
            raise HTTPException(
                status_code=403,
                detail="explicit approve=true is required to mutate provider routing",
            )
        try:
            service = ProviderRouteService()
            result = await service.enable_permanently(
                manager_id,
                connector_id,
                model_id=model,
            )
            return result.as_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProviderRouteLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FireConnectManagerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/provider-routes/{manager_id}/{connector_id}/disable")
    async def disable_provider_route(
        manager_id: str,
        connector_id: str,
        approve: bool = False,
    ) -> dict[str, object]:
        from joymesh.runtime_v1.provider_routes.fireconnect import FireConnectManagerError
        from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseError
        from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

        if not approve:
            raise HTTPException(
                status_code=403,
                detail="explicit approve=true is required to mutate provider routing",
            )
        try:
            service = ProviderRouteService()
            result = await service.disable_permanently(manager_id, connector_id)
            return result.as_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProviderRouteLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FireConnectManagerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/provider-routes/{manager_id}/{connector_id}/verify")
    async def verify_provider_route(manager_id: str, connector_id: str) -> dict[str, object]:
        from joymesh.runtime_v1.provider_routes import get_provider_route_manager

        try:
            manager = get_provider_route_manager(manager_id)
            route = await manager.verify_route(connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return route.as_dict()

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

    # --- JoyMesh Runtime v1 (capability-first) ---

    @app.post("/runtime/tasks")
    async def create_runtime_task(body: dict[str, object]) -> dict[str, object]:
        from joymesh.runtime_v1.models import CreateRuntimeTaskBody

        try:
            request = CreateRuntimeTaskBody.model_validate(body)
            task = await service.runtime_service.create_task(request, user_id="browser")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.get("/runtime/tasks/{task_id}")
    async def get_runtime_task(task_id: str) -> dict[str, object]:
        try:
            task = await service.runtime_service.store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="runtime task not found") from exc
        return task.model_dump(mode="json")

    @app.get("/runtime/tasks/{task_id}/events")
    async def runtime_task_events(task_id: str) -> list[dict[str, object]]:
        return list(service.runtime_service.store.events.get(task_id, []))

    @app.post("/runtime/tasks/{task_id}/approve")
    async def approve_runtime_task(task_id: str) -> dict[str, object]:
        try:
            task = await service.runtime_service.approve_task(task_id)
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.post("/runtime/tasks/{task_id}/cancel")
    async def cancel_runtime_task(task_id: str) -> dict[str, object]:
        try:
            task = await service.runtime_service.cancel_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.post("/runtime/tasks/{task_id}/retry")
    async def retry_runtime_task(task_id: str) -> dict[str, object]:
        from joymesh.runtime_v1.models import FailureClass

        try:
            task = await service.runtime_service.retry_task(
                task_id, failure_class=FailureClass.OFFER_TIMEOUT
            )
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.get("/runtime/tasks/{task_id}/candidates")
    async def runtime_task_candidates(task_id: str) -> list[dict[str, object]]:
        candidates = service.runtime_service.store.candidates.get(task_id, [])
        return [
            {
                "node_id": item.node_id,
                "connector_id": item.connector_id,
                "policy_profile": item.policy_profile,
                "certified_capabilities": sorted(item.certified_capabilities),
                "score": item.score,
                "eligible": item.eligible,
                "rejection_reasons": list(item.rejection_reasons),
                "scoring_factors": dict(item.scoring_factors),
            }
            for item in candidates
        ]

    @app.get("/runtime/tasks/{task_id}/attempts")
    async def runtime_task_attempts(task_id: str) -> list[dict[str, object]]:
        attempts = service.runtime_service.store.attempts.get(task_id, [])
        return [
            {
                "attempt_id": item.attempt_id,
                "attempt_number": item.attempt_number,
                "node_id": item.node_id,
                "connector_id": item.connector_id,
                "lease_id": item.lease_id,
                "execution_origin": item.execution_origin.value,
                "status": item.status,
                "failure_class": item.failure_class.value if item.failure_class else None,
                "retry_safe": item.retry_safe,
            }
            for item in attempts
        ]

    @app.get("/runtime/tasks/{task_id}/lease")
    async def runtime_task_lease(task_id: str) -> dict[str, object] | None:
        lease = service.runtime_service.leases.active_lease(task_id)
        if lease is None:
            return None
        return {
            "lease_id": lease.lease_id,
            "task_id": lease.task_id,
            "node_id": lease.node_id,
            "connector_id": lease.connector_id,
            "attempt_id": lease.attempt_id,
            "fencing_token": lease.fencing_token,
            "status": lease.status.value,
            "expires_at": lease.expires_at.isoformat(),
        }

    @app.get("/runtime/tasks/{task_id}/audit")
    async def runtime_task_audit(task_id: str) -> list[dict[str, object]]:
        return [
            {
                "event_id": item.event_id,
                "event_type": item.event_type,
                "payload": dict(item.payload),
                "created_at": item.created_at.isoformat(),
            }
            for item in service.runtime_service.store.audits
            if item.task_id == task_id
        ]

    @app.get("/runtime/capabilities")
    async def runtime_capabilities() -> list[dict[str, object]]:
        return service.runtime_service.list_capabilities()

    @app.get("/runtime/policies")
    async def runtime_policies() -> list[dict[str, object]]:
        return service.runtime_service.list_policies()

    @app.get("/runtime/policies/{policy_id}")
    async def runtime_policy(policy_id: str) -> dict[str, object]:
        try:
            profile = service.runtime_service.policy.get(policy_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "profile_id": profile.profile_id,
            "description": profile.description,
            "allowed": sorted(profile.allowed),
            "denied": sorted(profile.denied),
            "require_node_attested": profile.require_node_attested,
            "enabled": profile.enabled,
        }

    @app.get("/runtime/health")
    async def runtime_health() -> dict[str, object]:
        return service.runtime_service.health()

    @app.get("/runtime/metrics")
    async def runtime_metrics() -> dict[str, object]:
        return service.runtime_service.metrics.snapshot()

    @app.get("/workspaces/{workspace_id}/placements")
    async def workspace_placements(workspace_id: str) -> list[dict[str, object]]:
        placements = service.runtime_service.store.placements.get(workspace_id, [])
        return [
            {
                "workspace_id": item.workspace_id,
                "node_id": item.node_id,
                "local_path": item.local_path if item.expose_path else None,
                "fingerprint": item.fingerprint,
                "writable": item.writable,
                "last_verified_at": item.last_verified_at.isoformat(),
            }
            for item in placements
        ]

    return app


def _problem(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


app = create_app()
