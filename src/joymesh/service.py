"""SDK-first application service shared by the CLI and REST API."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from joymesh.config import HarnessPreferences
from joymesh.connectors import ConnectorCatalogue, ConnectorDefinition
from joymesh.connectors.lifecycle import ConnectorLifecycleCoordinator, build_coordinator
from joymesh.connectors.lifecycle_models import (
    ConnectorReadiness,
    ConnectorTaskEvent,
    ConnectorTaskRecord,
)
from joymesh.connectors.planning import (
    ConnectorAction,
    ConnectorPlanner,
    ConnectorTaskPlan,
)
from joymesh.control_plane.service import ControlPlane
from joymesh.harnesses.certification import CertificationService
from joymesh.harnesses.contracts import (
    ApprovalToken,
    CertificationEvidence,
    DiscoveryResult,
    HarnessDefinition,
    HarnessInspection,
    LifecyclePlan,
    LifecycleResult,
)
from joymesh.harnesses.discovery import DiscoveryPolicy
from joymesh.harnesses.lifecycle import HarnessLifecycleService
from joymesh.models import (
    Capability,
    EventType,
    FailureKind,
    FallbackProposal,
    HarnessDescriptor,
    NormalizedEvent,
    PermissionMode,
    RouteCandidate,
    RoutePreview,
    RoutePreviewRequest,
    Run,
    RunRequest,
    RunStatus,
    SubscriptionCreate,
    SubscriptionProfile,
    SubscriptionState,
    UsageRecord,
    utc_now,
)
from joymesh.persistence import Database
from joymesh.quota.contracts import QuotaSnapshot
from joymesh.registry import AdapterRegistry
from joymesh.routing import Router
from joymesh.runtime import HarnessRuntime, HarnessTimeoutError
from joymesh.runtime_snapshot.contracts import HarnessRuntimeSnapshot, RuntimeSnapshot
from joymesh.runtime_v1.service import RuntimeService
from joymesh.runtime_v1.store import RuntimeStore
from joymesh.workspace import resolve_workspace

TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


def _durable_sidecar_paths(database_url: str | None) -> tuple[Path, Path]:
    from joymesh.delivery import default_outbox_path
    from joymesh.execution import default_checkpoint_path

    if database_url and "sqlite" in database_url:
        marker = ":///"
        if marker in database_url:
            raw = database_url.split(marker, 1)[1]
            db_path = Path(raw)
            if str(db_path):
                return (
                    db_path.with_name(db_path.stem + ".delivery.sqlite3"),
                    db_path.with_name(db_path.stem + ".checkpoints.sqlite3"),
                )
    return default_outbox_path(), default_checkpoint_path()


class NoRouteError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        remediation: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.details = details or {}


class JoyMesh:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        registry: AdapterRegistry | None = None,
        runtime: HarnessRuntime | None = None,
        delivery_settings: object | None = None,
        delivery_transport: object | None = None,
    ) -> None:
        self.database = Database(database_url)
        self.connector_catalogue = ConnectorCatalogue.builtins()
        self.connector_planner = ConnectorPlanner(self.connector_catalogue)
        self._connector_lifecycle: ConnectorLifecycleCoordinator | None = None
        self._runtime_service: RuntimeService | None = None
        self.registry = registry or AdapterRegistry()
        self.runtime = runtime or HarnessRuntime()
        from joymesh.quota import QuotaService

        self.quota = QuotaService(
            harness_ids=[item.manifest.harness_id for item in self.registry.list()]
        )
        self.router = Router(self.registry, self.database, quota=self.quota)
        from joymesh.runtime_snapshot import RuntimeSnapshotService

        self.runtime_snapshots = RuntimeSnapshotService(
            quota=self.quota,
            registry=self.registry,
            harness_ids=[item.manifest.harness_id for item in self.registry.list()],
        )
        self.lifecycle = HarnessLifecycleService(
            self.registry.definitions(),
            self.registry.discovery,
        )
        self.certification = CertificationService(self.registry, self.database)
        from joymesh.control_plane.onboarding_store import SqlOnboardingProgressRepository

        self.control_plane = ControlPlane(
            onboarding_repository=SqlOnboardingProgressRepository(self.database.sessions)
        )
        from joymesh.config import load_user_config
        from joymesh.delivery import (
            DeliveryOutbox,
            DeliveryWorker,
            RuntimeDeliveryPublisher,
        )
        from joymesh.delivery.factory import build_delivery_transport
        from joymesh.delivery.settings import (
            DeliverySettings,
            delivery_settings_from_mapping,
            resolve_delivery_settings,
        )
        from joymesh.execution import (
            ApprovalContinuationService,
            CheckpointStore,
        )

        # Delivery uses an isolated SQLite file so crash recovery does not depend
        # on the primary mesh DB being writable.
        outbox_path, checkpoint_path = _durable_sidecar_paths(database_url)
        self._delivery_outbox = DeliveryOutbox(outbox_path)
        self.delivery_publisher = RuntimeDeliveryPublisher(self._delivery_outbox)
        if isinstance(delivery_settings, DeliverySettings):
            resolved_settings = delivery_settings
        else:
            user_delivery = delivery_settings_from_mapping(
                load_user_config().delivery.as_dict()
            )
            resolved_settings = resolve_delivery_settings(config_delivery=user_delivery)
        self.delivery_settings = resolved_settings
        if delivery_transport is not None:
            self._delivery_transport = delivery_transport
        else:
            self._delivery_transport = build_delivery_transport(resolved_settings)
        self.delivery_worker = DeliveryWorker(
            self._delivery_outbox,
            self._delivery_transport,  # type: ignore[arg-type]
        )
        self.approvals = ApprovalContinuationService()
        self.checkpoints = CheckpointStore(checkpoint_path)
        self.runtime_snapshots.publisher.subscribe(self._on_runtime_snapshot)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, RunRequest] = {}

    @property
    def delivery_transport(self) -> object:
        return self._delivery_transport

    def delivery_health(self) -> dict[str, object]:
        return self.delivery_worker.health()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if not self._initialized:
                await self.database.initialize()
                self._connector_lifecycle = build_coordinator(self.database, self.connector_planner)
                self._runtime_service = RuntimeService(
                    store=RuntimeStore(self.database),
                )
                # Restore durable delivery + interrupted checkpoints after restart.
                self.checkpoints.mark_interrupted()
                await self.delivery_worker.start()
                await self.delivery_worker.flush_once()
                self._initialized = True

    @property
    def connector_lifecycle(self) -> ConnectorLifecycleCoordinator:
        if self._connector_lifecycle is None:
            raise RuntimeError("JoyMesh is not initialized")
        return self._connector_lifecycle

    @property
    def runtime_service(self) -> RuntimeService:
        if self._runtime_service is None:
            raise RuntimeError("JoyMesh is not initialized")
        return self._runtime_service

    def _on_runtime_snapshot(self, snapshot: object) -> None:
        from joymesh.runtime_snapshot.contracts import RuntimeSnapshot

        if isinstance(snapshot, RuntimeSnapshot):
            self.delivery_publisher.publish_snapshot(snapshot)

    async def close(self) -> None:
        for run_id in await self.runtime.active_run_ids():
            await self.cancel(run_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self.delivery_worker.stop()
        self._delivery_outbox.close()
        self.checkpoints.close()
        await self.database.close()
        self._initialized = False

    async def detect_harnesses(self) -> tuple[HarnessDescriptor, ...]:
        await self.initialize()
        return await self.registry.detect()

    async def list_quota(
        self,
        *,
        refresh: bool = False,
        harness_ids: tuple[str, ...] | None = None,
    ) -> tuple[QuotaSnapshot, ...]:
        """Return normalized quota snapshots for routing and status surfaces."""
        await self.initialize()
        ids = harness_ids
        if ids is None:
            ids = tuple(
                sorted(
                    {
                        *self.quota.known_harness_ids(),
                        *(item.manifest.harness_id for item in self.registry.list()),
                    }
                )
            )
        return await self.quota.list_snapshots(harness_ids=ids, refresh=refresh)

    async def get_quota(self, harness_id: str, *, refresh: bool = False) -> QuotaSnapshot:
        await self.initialize()
        resolved = harness_id
        try:
            resolved = self.registry.resolve_id(harness_id)
        except KeyError:
            pass
        return await self.quota.snapshot(resolved, refresh=refresh)

    async def refresh_quota(self, harness_id: str | None = None) -> tuple[QuotaSnapshot, ...]:
        await self.initialize()
        if harness_id:
            resolved = harness_id
            try:
                resolved = self.registry.resolve_id(harness_id)
            except KeyError:
                pass
            snapshots = await self.quota.refresh(resolved)
        else:
            ids = tuple(
                sorted(
                    {
                        *self.quota.known_harness_ids(),
                        *(item.manifest.harness_id for item in self.registry.list()),
                    }
                )
            )
            self.quota.cache.invalidate()
            snapshots = await self.quota.list_snapshots(harness_ids=ids, refresh=True)
        self.runtime_snapshots.cache.invalidate()
        return snapshots

    async def get_runtime_snapshot(self, *, refresh: bool = False) -> RuntimeSnapshot:
        await self.initialize()
        return await self.runtime_snapshots.snapshot(refresh=refresh)

    async def get_harness_runtime_snapshot(
        self, harness_id: str, *, refresh: bool = False
    ) -> HarnessRuntimeSnapshot:
        await self.initialize()
        resolved = harness_id
        try:
            resolved = self.registry.resolve_id(harness_id)
        except KeyError:
            pass
        return await self.runtime_snapshots.harness_snapshot(resolved, refresh=refresh)

    async def refresh_runtime_snapshot(
        self, harness_id: str | None = None
    ) -> RuntimeSnapshot:
        await self.initialize()
        if harness_id:
            resolved = harness_id
            try:
                resolved = self.registry.resolve_id(harness_id)
            except KeyError:
                pass
            return await self.runtime_snapshots.refresh(resolved)
        return await self.runtime_snapshots.refresh()

    def list_harnesses(self) -> tuple[HarnessDefinition, ...]:
        return self.registry.definitions()

    def list_connectors(self) -> tuple[ConnectorDefinition, ...]:
        """Return the same versioned catalogue consumed by API and CLI surfaces."""

        return self.connector_catalogue.all()

    def connector(self, connector_id: str) -> ConnectorDefinition:
        return self.connector_catalogue.get(connector_id)

    def plan_connector_task(
        self,
        *,
        node_id: str,
        connector_id: str,
        action: ConnectorAction,
        method_id: str | None = None,
        platform: str | None = None,
        download_digest: str | None = None,
    ) -> ConnectorTaskPlan:
        return self.connector_planner.plan(
            node_id=node_id,
            connector_id=connector_id,
            action=action,
            method_id=method_id,
            platform=platform,
            download_digest=download_digest,
        )

    async def plan_and_persist_connector_task(
        self,
        *,
        node_id: str,
        connector_id: str,
        action: ConnectorAction,
        method_id: str | None = None,
        platform: str | None = None,
        download_digest: str | None = None,
    ) -> ConnectorTaskPlan:
        await self.initialize()
        plan = self.plan_connector_task(
            node_id=node_id,
            connector_id=connector_id,
            action=action,
            method_id=method_id,
            platform=platform,
            download_digest=download_digest,
        )
        return await self.connector_lifecycle.persist_plan(plan)

    async def execute_connector_plan(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        approved: bool,
    ) -> ConnectorTaskRecord:
        await self.initialize()
        return await self.connector_lifecycle.approve_and_queue(
            plan_id, plan_hash=plan_hash, approved=approved
        )

    async def connector_readiness(self, *, node_id: str, connector_id: str) -> ConnectorReadiness:
        await self.initialize()
        return await self.connector_lifecycle.get_readiness(
            node_id=node_id, connector_id=connector_id
        )

    async def list_connector_readiness(self, *, node_id: str) -> tuple[ConnectorReadiness, ...]:
        await self.initialize()
        return await self.connector_lifecycle.list_readiness(node_id=node_id)

    async def connector_task(self, task_id: str) -> ConnectorTaskRecord:
        await self.initialize()
        return await self.connector_lifecycle.get_task(task_id)

    async def connector_task_events(
        self, task_id: str, *, after: int = 0
    ) -> tuple[ConnectorTaskEvent, ...]:
        await self.initialize()
        return await self.connector_lifecycle.list_task_events(task_id, after=after)

    async def active_connector_tasks(self, *, node_id: str) -> tuple[ConnectorTaskRecord, ...]:
        await self.initialize()
        return await self.connector_lifecycle.list_active_tasks(node_id=node_id)

    async def discover_harnesses(
        self,
        harness_id: str | None = None,
        *,
        probe_versions: bool = False,
        overrides: dict[str, str] | None = None,
    ) -> tuple[DiscoveryResult, ...]:
        await self.initialize()
        return await self.registry.discover(
            harness_id,
            policy=DiscoveryPolicy(execute_version_commands=probe_versions),
            overrides=overrides,
        )

    async def inspect_harness(self, harness_id: str) -> HarnessInspection:
        await self.initialize()
        definition = self.registry.definition(harness_id)
        discovery = (await self.registry.discover(definition.id))[0]
        evidence = await self.database.list_certifications(harness_id=definition.id)
        return HarnessInspection(
            definition=definition,
            discovery=discovery,
            authentication_detail="Credentials are never read; use the harness login/status flow.",
            certifications=evidence,
        )

    def plan_install(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self.lifecycle.plan_install(
            self.registry.resolve_id(harness_id),
            dry_run=dry_run,
        )

    def plan_upgrade(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self.lifecycle.plan_upgrade(
            self.registry.resolve_id(harness_id),
            dry_run=dry_run,
        )

    def plan_uninstall(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self.lifecycle.plan_uninstall(
            self.registry.resolve_id(harness_id),
            dry_run=dry_run,
        )

    def plan_login(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self.lifecycle.plan_login(
            self.registry.resolve_id(harness_id),
            dry_run=dry_run,
        )

    def plan_certification(self, harness_id: str) -> LifecyclePlan:
        return self.certification.plan(harness_id)

    async def execute_lifecycle_plan(
        self,
        plan: LifecyclePlan,
        *,
        approval: ApprovalToken,
    ) -> LifecycleResult:
        await self.initialize()
        return await self.lifecycle.execute(plan, approval=approval)

    async def certifications(
        self, *, harness_id: str | None = None
    ) -> tuple[CertificationEvidence, ...]:
        await self.initialize()
        resolved = self.registry.resolve_id(harness_id) if harness_id else None
        return await self.database.list_certifications(harness_id=resolved)

    async def certify_harness(
        self,
        harness_id: str,
        *,
        approval: ApprovalToken,
    ) -> CertificationEvidence:
        """Run one bounded real-binary smoke certification in an isolated repository."""

        resolved = self.registry.resolve_id(harness_id)
        if (
            not approval.approved
            or approval.action.value != "certify"
            or approval.harness_id != resolved
        ):
            raise PermissionError("explicit certification approval is required")
        discovery = (await self.discover_harnesses(resolved, probe_versions=True))[0]
        if not discovery.installations:
            raise RuntimeError(f"{resolved} is not installed")
        installation = next(
            (item for item in discovery.installations if item.version),
            discovery.installations[0],
        )
        workspace = _create_certification_workspace()
        observations: list[NormalizedEvent] = []
        try:
            await _initialize_git_repository(workspace)
            request = RunRequest(
                task=(
                    "Create joymesh-certification.txt containing exactly "
                    "JOYMESH_CERTIFICATION_OK and then finish."
                ),
                workspace=workspace,
                timeout_seconds=120,
                permission_mode=PermissionMode.AUTO_APPROVE,
            )
            adapter = self.registry.get(resolved)
            launch = adapter.build_launch_spec(request)
            launch = launch.model_copy(update={"argv": (installation.executable, *launch.argv[1:])})

            async def on_line(stream: str, line: str) -> None:
                observation = adapter.normalize_output(
                    run_id="certification",
                    sequence=len(observations) + 1,
                    stream=stream,
                    line=line,
                )
                observations.append(observation.event)

            exit_code = await self.runtime.execute(
                run_id=f"certification-{uuid4()}",
                launch=launch,
                on_line=on_line,
            )
            output_file = Path(workspace) / "joymesh-certification.txt"
            output_valid = _read_certification_output(output_file)
            checks = {
                "installation_detection": True,
                "version_reporting": installation.version is not None,
                "launch_specification": bool(launch.argv),
                "environment_filtering": all(
                    "KEY" not in key and "TOKEN" not in key for key in launch.env
                ),
                "workspace_propagation": launch.cwd == workspace,
                "streaming_output": bool(observations),
                "normalized_events": all(event.run_id == "certification" for event in observations),
                "event_sequence_ordering": [event.sequence for event in observations]
                == list(range(1, len(observations) + 1)),
                "successful_completion": exit_code == 0,
                "deterministic_workspace_result": output_valid,
                "secret_redaction": all(
                    "JOYMESH_CERTIFICATION_SECRET" not in (event.message or "")
                    for event in observations
                ),
            }
            return await self.certification.record(
                harness_id=resolved,
                binary_version=installation.version,
                executable=installation.executable,
                checks=checks,
                detail=None if all(checks.values()) else "smoke certification checks failed",
            )
        finally:
            _remove_certification_workspace(workspace)

    async def list_subscriptions(self) -> tuple[SubscriptionProfile, ...]:
        await self.initialize()
        return await self.database.list_subscriptions()

    async def create_subscription(self, data: SubscriptionCreate) -> SubscriptionProfile:
        await self.initialize()
        definition = self.registry.definition(data.harness_id)
        return await self.database.create_subscription(
            data.model_copy(update={"harness_id": definition.id})
        )

    async def resolve_route(
        self, *, request: RunRequest, preferred_harness: str | None = None
    ) -> RouteCandidate:
        preview = await self.preview_routes(
            task=request.task,
            workspace=request.workspace,
            required_capabilities=request.required_capabilities,
            preferred_harness=preferred_harness,
            allowed_harnesses=request.allowed_harnesses,
            denied_harnesses=request.denied_harnesses,
            paid_routes_approved=request.paid_routes_approved,
        )
        if preview.selected is None:
            raise NoRouteError("no eligible harness route")
        return preview.selected

    async def preview_routes(
        self,
        *,
        task: str,
        workspace: str | Path = ".",
        required_capabilities: frozenset[Capability] | None = None,
        preferred_harness: str | None = None,
        allowed_harnesses: frozenset[str] | None = None,
        denied_harnesses: frozenset[str] | None = None,
        paid_routes_approved: bool = False,
    ) -> RoutePreview:
        await self.initialize()
        resolved = resolve_workspace(workspace)
        return await self.router.preview(
            RoutePreviewRequest(
                task=task,
                workspace=str(resolved),
                required_capabilities=required_capabilities or frozenset(),
                preferred_harness=preferred_harness,
                allowed_harnesses=allowed_harnesses or frozenset(),
                denied_harnesses=denied_harnesses or frozenset(),
                paid_routes_approved=paid_routes_approved,
            )
        )

    def _assert_capabilities(self, *, harness_id: str, required: frozenset[Capability]) -> None:
        from joymesh.harnesses.selection import find_capability_mismatch

        adapter = self.registry.get(harness_id)
        mismatch = find_capability_mismatch(
            harness_id=harness_id,
            supported=adapter.manifest.capabilities,
            required=required,
        )
        if mismatch is None:
            return
        raise NoRouteError(
            f"harness capability mismatch: {harness_id} missing "
            f"{', '.join(mismatch.missing_capabilities)}",
            code="harness_capability_mismatch",
            remediation=(
                "Choose a harness that declares the required capabilities, "
                "or reduce the task requirements."
            ),
            details=mismatch.as_dict(),
        )

    async def start_run(
        self,
        *,
        request: RunRequest,
        route: RouteCandidate,
        task_context_id: str | None = None,
        continuation_of_run_id: str | None = None,
    ) -> Run:
        await self.initialize()
        resolved = resolve_workspace(request.workspace)
        if not route.eligible:
            raise NoRouteError("selected route is not eligible")
        self._assert_capabilities(
            harness_id=route.harness_id,
            required=request.required_capabilities,
        )
        from joymesh.execution import (
            DirectiveValidationError,
            ExecutionDirective,
            validate_directive,
        )
        from joymesh.runtime_snapshot import RuntimeLaunchError

        if request.directive is not None:
            try:
                directive = ExecutionDirective.model_validate(request.directive)
                is_fallback = bool(continuation_of_run_id)
                await validate_directive(
                    directive,
                    registry=self.registry,
                    runtime_snapshots=self.runtime_snapshots,
                    is_fallback=is_fallback,
                    harness_enabled=True,
                )
                if directive.selected_harness != route.harness_id:
                    raise NoRouteError(
                        "directive selected_harness does not match route",
                        code="runtime_changed",
                        details={
                            "directive_harness": directive.selected_harness,
                            "route_harness": route.harness_id,
                        },
                    )
            except DirectiveValidationError as exc:
                raise NoRouteError(
                    str(exc),
                    code=exc.code.value,
                    remediation=exc.remediation,
                    details=exc.details,
                ) from exc
        else:
            try:
                await self.runtime_snapshots.revalidate_for_launch(
                    route.harness_id,
                    required_capabilities=request.required_capabilities,
                )
            except RuntimeLaunchError as exc:
                raise NoRouteError(
                    str(exc),
                    code=exc.code.value,
                    remediation=exc.remediation,
                    details=exc.details,
                ) from exc
        self.registry.get(route.harness_id)
        normalized_request = request.model_copy(update={"workspace": str(resolved), "route": route})
        run = Run(
            id=str(uuid4()),
            task=request.task,
            workspace=str(resolved),
            harness_id=route.harness_id,
            subscription_id=route.subscription_id,
            status=RunStatus.QUEUED,
            created_at=utc_now(),
            task_context_id=task_context_id or str(uuid4()),
            continuation_of_run_id=continuation_of_run_id,
        )
        await self.database.create_run(run)
        import json

        from joymesh.execution import ExecutionCheckpoint

        self.checkpoints.save(
            ExecutionCheckpoint(
                execution_id=request.execution_id or run.id,
                attempt_id=run.id,
                harness_id=run.harness_id,
                native_session_id=None,
                status=RunStatus.QUEUED.value,
                directive_json=(
                    None
                    if request.directive is None
                    else json.dumps(request.directive, sort_keys=True)
                ),
                updated_at=utc_now(),
            )
        )
        self._requests[run.id] = normalized_request
        correlation_metadata = {
            key: value
            for key, value in {
                "correlation_id": request.correlation_id,
                "mission_id": request.mission_id,
                "trace_id": request.trace_id,
                "execution_id": request.execution_id,
            }.items()
            if value is not None
        }
        await self._event(
            run.id,
            EventType.RUN_QUEUED,
            "Run queued",
            {"integration": correlation_metadata} if correlation_metadata else None,
        )
        handle = asyncio.create_task(self._execute(run.id), name=f"joymesh-run-{run.id}")
        self._tasks[run.id] = handle
        handle.add_done_callback(lambda _task: self._tasks.pop(run.id, None))
        return run

    async def run(
        self,
        *,
        task: str | None = None,
        workspace: str | Path | None = None,
        route: RouteCandidate | None = None,
        request: RunRequest | None = None,
        harness: str = "auto",
    ) -> Run:
        selected_request = request
        if selected_request is None:
            if task is None or workspace is None:
                raise TypeError("task and workspace are required when request is omitted")
            selected_request = RunRequest(task=task, workspace=str(workspace))
        if route is not None:
            return await self.start_run(request=selected_request, route=route)

        from joymesh.config import load_user_config
        from joymesh.harnesses.selection import HarnessSelectionError, resolve_harness
        from joymesh.models import HarnessAvailability

        prefs = load_user_config().harnesses
        self._apply_custom_harnesses(prefs)
        detected = await self.registry.detect()
        ready = [
            item.manifest.harness_id
            for item in detected
            if item.availability is HarnessAvailability.AVAILABLE
        ]
        if prefs.enabled:
            ready = [item for item in ready if item in prefs.enabled]
        override = None if harness == "auto" else harness
        if override:
            try:
                override = self.registry.resolve_id(override)
            except KeyError:
                pass
        preferred = selected_request.preferred_harness
        if preferred:
            try:
                preferred = self.registry.resolve_id(preferred)
            except KeyError:
                pass
        try:
            resolution = resolve_harness(
                prefs=prefs,
                ready_enabled=ready,
                override=override,
                preferred=preferred,
                interactive=False,
                known_ids=[item.manifest.harness_id for item in detected],
                allow_disabled_override=bool(override),
                allow_test_harnesses=bool(getattr(self.registry, "_allow_test_harnesses", False)),
            )
        except HarnessSelectionError as exc:
            raise NoRouteError(
                str(exc.message),
                code=exc.code,
                remediation=exc.remediation,
                details=exc.details,
            ) from exc

        # Explicit per-run override: never silently fall back to another harness.
        if override:
            self._assert_capabilities(
                harness_id=resolution.harness_id,
                required=selected_request.required_capabilities,
            )
            locked = selected_request.model_copy(
                update={
                    "allowed_harnesses": frozenset({resolution.harness_id}),
                    "preferred_harness": resolution.harness_id,
                }
            )
            selected = await self.resolve_route(
                request=locked,
                preferred_harness=resolution.harness_id,
            )
            return await self.start_run(request=locked, route=selected)

        selected = await self.resolve_route(
            request=selected_request,
            preferred_harness=resolution.harness_id,
        )
        return await self.start_run(request=selected_request, route=selected)

    def _apply_custom_harnesses(self, prefs: HarnessPreferences) -> None:
        from joymesh.harnesses.nonstandard import (
            CustomHarnessAdapter,
            assess_custom_harness_readiness,
            custom_harness_definition,
            validate_custom_harness_config,
        )

        for config in prefs.custom.values():
            validation = validate_custom_harness_config(config)
            if not validation.ok:
                continue
            definition = custom_harness_definition(config)
            if definition.id not in {item.id for item in self.registry.definitions()}:
                self.registry.register_custom_definition(definition)
            readiness = assess_custom_harness_readiness(config)
            if not readiness.ready:
                continue
            try:
                self.registry.get(config.harness_id)
            except KeyError:
                self.registry.register(CustomHarnessAdapter(config), replace=True)

    async def inspect_run(self, run_id: str) -> Run | None:
        await self.initialize()
        return await self.database.get_run(run_id)

    async def list_runs(self, *, limit: int = 25) -> tuple[Run, ...]:
        await self.initialize()
        return await self.database.list_runs(limit=limit)

    async def events(self, run_id: str, *, after: int = 0) -> tuple[NormalizedEvent, ...]:
        await self.initialize()
        return await self.database.list_events(run_id, after=after)

    async def stream_events(self, run_id: str) -> AsyncIterator[NormalizedEvent]:
        sequence = 0
        while True:
            events = await self.events(run_id, after=sequence)
            for event in events:
                sequence = event.sequence
                yield event
            run = await self.inspect_run(run_id)
            if run is None or (run.status in TERMINAL_STATUSES and not events):
                task = self._tasks.get(run_id)
                if run is not None and task is not None and not task.done():
                    await asyncio.shield(task)
                    final_events = await self.events(run_id, after=sequence)
                    for event in final_events:
                        sequence = event.sequence
                        yield event
                break
            await asyncio.sleep(0.02)

    async def wait_for_run(self, run_id: str) -> Run:
        if task := self._tasks.get(run_id):
            await task
        run = await self.inspect_run(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        return run

    wait = wait_for_run

    async def usage(self, *, run_id: str | None = None) -> tuple[UsageRecord, ...]:
        await self.initialize()
        return await self.database.list_usage(run_id=run_id)

    async def fallback_for_run(self, run_id: str) -> FallbackProposal | None:
        return await self.database.get_fallback_for_run(run_id)

    async def approve_fallback(self, proposal_id: str) -> Run:
        proposal = await self._fallback_by_id(proposal_id)
        original = await self.inspect_run(proposal.original_run_id)
        if original is None:
            raise KeyError("original run not found")
        # Bind approval to the original execution and consume a signed response.
        request_payload = {
            "proposal_id": proposal.id,
            "original_run_id": proposal.original_run_id,
            "harness_id": proposal.route.harness_id,
            "reason": proposal.reason,
        }
        approval_req = self.approvals.request_approval(
            execution_id=original.id,
            attempt_id=proposal.id,
            directive_payload=request_payload,
            reason=proposal.reason,
        )
        approval_resp = self.approvals.sign_response(approval_req, approved=True)
        self.approvals.verify_response(
            approval_resp,
            expected_execution_id=original.id,
            expected_attempt_id=proposal.id,
            expected_directive_hash=approval_req.directive_hash,
        )
        self.delivery_publisher.publish_approval_request(
            {
                "approval_id": approval_req.approval_id,
                "execution_id": original.id,
                "attempt_id": proposal.id,
                "reason": proposal.reason,
                "approved": True,
            },
            idempotency_key=f"approval:{approval_req.approval_id}",
        )
        # Cross-harness fallback is a clean retry — never resume the failed session.
        request = RunRequest(task=original.task, workspace=original.workspace)
        continuation = await self.start_run(
            request=request,
            route=proposal.route,
            task_context_id=original.task_context_id,
            continuation_of_run_id=original.id,
        )
        await self.database.approve_fallback(proposal.id, continuation.id)
        return continuation

    async def cancel(self, run_id: str) -> Run:
        await self.initialize()
        run = await self.database.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        if run.status in TERMINAL_STATUSES:
            return run
        cancelled = await self.database.update_run(
            run_id, status=RunStatus.CANCELLED, finished_at=utc_now()
        )
        terminated = await self.runtime.cancel(run_id)
        if not terminated and (task := self._tasks.get(run_id)):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        from joymesh.execution import ExecutionCheckpoint

        self.checkpoints.save(
            ExecutionCheckpoint(
                execution_id=run.id,
                attempt_id=run.id,
                harness_id=run.harness_id,
                native_session_id=run.native_session_id,
                status=RunStatus.CANCELLED.value,
                directive_json=None,
                updated_at=utc_now(),
            )
        )
        await self._event(
            run_id,
            EventType.RUN_CANCELLED,
            "Run cancelled",
            {
                "process_tree_terminated": terminated,
                "native_session_id": run.native_session_id,
            },
        )
        self.delivery_publisher.publish_event(
            event_type=EventType.RUN_CANCELLED.value,
            payload={
                "run_id": run_id,
                "harness_id": run.harness_id,
                "process_tree_terminated": terminated,
            },
            idempotency_key=f"cancel:{run_id}",
        )
        await self.delivery_worker.flush_once()
        return cancelled

    async def _execute(self, run_id: str) -> None:
        run = await self.database.get_run(run_id)
        request = self._requests.get(run_id)
        if run is None or request is None:
            return
        adapter = self.registry.get(run.harness_id)
        started_at = utc_now()
        await self.database.update_run(run_id, status=RunStatus.RUNNING, started_at=started_at)
        await self._event(run_id, EventType.RUN_STARTED, "Run started")
        self.runtime_snapshots.mark_run_started(run.harness_id)
        output: list[str] = []
        usage_input = 0
        usage_output = 0

        async def on_started(pid: int) -> None:
            await self.database.update_run(run_id, process_id=pid)

        async def on_line(stream: str, line: str) -> None:
            nonlocal usage_input, usage_output
            output.append(line)
            observation = adapter.normalize_output(
                run_id=run_id, sequence=0, stream=stream, line=line
            )
            await self.database.append_event(observation.event)
            if observation.native_session_id:
                await self.database.update_run(
                    run_id, native_session_id=observation.native_session_id
                )
                await self._event(
                    run_id,
                    EventType.SESSION_IDENTIFIED,
                    "Native session identified",
                    {"native_session_id": observation.native_session_id},
                )
            if observation.usage:
                usage_input += observation.usage.input_tokens
                usage_output += observation.usage.output_tokens
                if run.subscription_id:
                    await self.database.record_usage(
                        subscription_id=run.subscription_id,
                        run_id=run_id,
                        input_tokens=observation.usage.input_tokens,
                        output_tokens=observation.usage.output_tokens,
                        amount=observation.usage.cost or 0,
                    )
                    await self._event(run_id, EventType.USAGE_RECORDED, "Usage recorded")

        async def _publish_runtime_observation(
            *,
            success: bool,
            failure_kind: FailureKind | None = None,
            detail: str | None = None,
        ) -> None:
            finished = utc_now()
            duration_ms = max(0.0, (finished - started_at).total_seconds() * 1000.0)
            snapshot = await self.runtime_snapshots.observe_execution(
                run.harness_id,
                success=success,
                failure_kind=failure_kind,
                detail=detail,
                duration_ms=duration_ms,
                input_tokens=usage_input,
                output_tokens=usage_output,
            )
            entry = snapshot.harness(run.harness_id)
            await self._event(
                run_id,
                EventType.RUNTIME_SNAPSHOT_UPDATED,
                "Runtime snapshot updated",
                {"harness": entry.as_dict() if entry is not None else None},
            )

        try:
            exit_code = await self.runtime.execute(
                run_id=run_id,
                launch=adapter.build_launch_spec(request),
                on_line=on_line,
                on_started=on_started,
            )
            current = await self.database.get_run(run_id)
            if current is None or current.status is RunStatus.CANCELLED:
                self.runtime_snapshots.observations.mark_running(run.harness_id, delta=-1)
                return
            if exit_code == 0:
                await self.database.update_run(
                    run_id,
                    status=RunStatus.COMPLETED,
                    finished_at=utc_now(),
                    exit_code=0,
                )
                await _publish_runtime_observation(success=True)
                await self._event(run_id, EventType.RUN_COMPLETED, "Run completed")
            else:
                failure = adapter.classify_failure(exit_code=exit_code, output="\n".join(output))
                await self.database.update_run(
                    run_id,
                    status=RunStatus.FAILED,
                    finished_at=utc_now(),
                    exit_code=exit_code,
                    error=failure.message,
                )
                await _publish_runtime_observation(
                    success=False,
                    failure_kind=failure.kind,
                    detail=failure.message + "\n" + "\n".join(output[-20:]),
                )
                await self._event(run_id, EventType.RUN_FAILED, failure.message)
                if failure.kind is FailureKind.RATE_LIMIT:
                    await self._handle_rate_limit(run)
        except HarnessTimeoutError as exc:
            await self.database.update_run(
                run_id, status=RunStatus.TIMED_OUT, finished_at=utc_now(), error=str(exc)
            )
            await _publish_runtime_observation(
                success=False,
                failure_kind=FailureKind.TIMEOUT,
                detail=str(exc),
            )
            await self._event(run_id, EventType.RUN_TIMED_OUT, str(exc))
        except asyncio.CancelledError:
            self.runtime_snapshots.observations.mark_running(run.harness_id, delta=-1)
            raise
        except Exception as exc:
            await self.database.update_run(
                run_id, status=RunStatus.FAILED, finished_at=utc_now(), error=str(exc)
            )
            await _publish_runtime_observation(
                success=False,
                failure_kind=FailureKind.UNKNOWN,
                detail=str(exc),
            )
            await self._event(run_id, EventType.RUN_FAILED, str(exc))
        finally:
            self._requests.pop(run_id, None)

    async def _handle_rate_limit(self, run: Run) -> None:
        if run.subscription_id:
            await self.database.set_subscription_state(
                run.subscription_id, SubscriptionState.RATE_LIMITED
            )
        await self._event(run.id, EventType.RATE_LIMIT_ENCOUNTERED, "Rate limit encountered")
        preview = await self.preview_routes(
            task=run.task,
            workspace=run.workspace,
            paid_routes_approved=True,
        )
        route = preview.selected
        if route is None:
            return
        profiles = {profile.id: profile for profile in await self.list_subscriptions()}
        profile = profiles.get(route.subscription_id or "")
        requires_approval = bool(profile and profile.requires_paid_approval)
        proposal = FallbackProposal(
            id=str(uuid4()),
            original_run_id=run.id,
            route=route,
            requires_approval=requires_approval,
            reason="Selected harness encountered a rate limit",
            created_at=utc_now(),
        )
        await self.database.create_fallback(proposal)
        await self._event(run.id, EventType.FALLBACK_PROPOSED, "Fallback proposed")
        if requires_approval:
            await self._event(
                run.id, EventType.APPROVAL_REQUESTED, "Paid fallback requires approval"
            )

    async def _fallback_by_id(self, proposal_id: str) -> FallbackProposal:
        async with self.database.sessions() as session:
            from joymesh.persistence import FallbackProposalRow

            row = await session.get(FallbackProposalRow, proposal_id)
            if row is None:
                raise KeyError(f"unknown fallback proposal: {proposal_id}")
            return self.database._fallback_model(row)

    async def _event(
        self,
        run_id: str,
        event_type: EventType,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> NormalizedEvent:
        return await self.database.append_event(
            NormalizedEvent(
                run_id=run_id,
                sequence=0,
                type=event_type,
                message=message,
                payload=payload or {},
            )
        )


def _create_certification_workspace() -> str:
    return tempfile.mkdtemp(prefix="joymesh-certification-")


def _remove_certification_workspace(workspace: str) -> None:
    shutil.rmtree(workspace, ignore_errors=True)


def _read_certification_output(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip() == "JOYMESH_CERTIFICATION_OK"
    except OSError:
        return False


async def _initialize_git_repository(workspace: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "git",
        "init",
        "--quiet",
        workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"failed to initialize certification repository: {stderr.decode()}")
