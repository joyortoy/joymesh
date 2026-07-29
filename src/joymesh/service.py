"""SDK-first application service shared by the CLI and REST API."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

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
from joymesh.registry import AdapterRegistry
from joymesh.routing import Router
from joymesh.runtime import HarnessRuntime, HarnessTimeoutError
from joymesh.runtime_v1.service import RuntimeService
from joymesh.runtime_v1.store import RuntimeStore
from joymesh.workspace import resolve_workspace

TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


class NoRouteError(RuntimeError):
    pass


class JoyMesh:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        registry: AdapterRegistry | None = None,
        runtime: HarnessRuntime | None = None,
    ) -> None:
        self.database = Database(database_url)
        self.connector_catalogue = ConnectorCatalogue.builtins()
        self.connector_planner = ConnectorPlanner(self.connector_catalogue)
        self._connector_lifecycle: ConnectorLifecycleCoordinator | None = None
        self._runtime_service: RuntimeService | None = None
        self.registry = registry or AdapterRegistry()
        self.runtime = runtime or HarnessRuntime()
        self.router = Router(self.registry, self.database)
        self.lifecycle = HarnessLifecycleService(
            self.registry.definitions(),
            self.registry.discovery,
        )
        self.certification = CertificationService(self.registry, self.database)
        self.control_plane = ControlPlane()
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, RunRequest] = {}

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if not self._initialized:
                await self.database.initialize()
                self._connector_lifecycle = build_coordinator(self.database, self.connector_planner)
                self._runtime_service = RuntimeService(
                    store=RuntimeStore(self.database),
                )
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

    async def close(self) -> None:
        for run_id in await self.runtime.active_run_ids():
            await self.cancel(run_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self.database.close()
        self._initialized = False

    async def detect_harnesses(self) -> tuple[HarnessDescriptor, ...]:
        await self.initialize()
        return await self.registry.detect()

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
        self._requests[run.id] = normalized_request
        await self._event(run.id, EventType.RUN_QUEUED, "Run queued")
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
        selected = route or await self.resolve_route(
            request=selected_request,
            preferred_harness=None if harness == "auto" else harness,
        )
        return await self.start_run(request=selected_request, route=selected)

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
        await self._event(run_id, EventType.RUN_CANCELLED, "Run cancelled")
        return cancelled

    async def _execute(self, run_id: str) -> None:
        run = await self.database.get_run(run_id)
        request = self._requests.get(run_id)
        if run is None or request is None:
            return
        adapter = self.registry.get(run.harness_id)
        await self.database.update_run(run_id, status=RunStatus.RUNNING, started_at=utc_now())
        await self._event(run_id, EventType.RUN_STARTED, "Run started")
        output: list[str] = []

        async def on_started(pid: int) -> None:
            await self.database.update_run(run_id, process_id=pid)

        async def on_line(stream: str, line: str) -> None:
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
            if observation.usage and run.subscription_id:
                await self.database.record_usage(
                    subscription_id=run.subscription_id,
                    run_id=run_id,
                    input_tokens=observation.usage.input_tokens,
                    output_tokens=observation.usage.output_tokens,
                    amount=observation.usage.cost or 0,
                )
                await self._event(run_id, EventType.USAGE_RECORDED, "Usage recorded")

        try:
            exit_code = await self.runtime.execute(
                run_id=run_id,
                launch=adapter.build_launch_spec(request),
                on_line=on_line,
                on_started=on_started,
            )
            current = await self.database.get_run(run_id)
            if current is None or current.status is RunStatus.CANCELLED:
                return
            if exit_code == 0:
                await self.database.update_run(
                    run_id,
                    status=RunStatus.COMPLETED,
                    finished_at=utc_now(),
                    exit_code=0,
                )
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
                await self._event(run_id, EventType.RUN_FAILED, failure.message)
                if failure.kind is FailureKind.RATE_LIMIT:
                    await self._handle_rate_limit(run)
        except HarnessTimeoutError as exc:
            await self.database.update_run(
                run_id, status=RunStatus.TIMED_OUT, finished_at=utc_now(), error=str(exc)
            )
            await self._event(run_id, EventType.RUN_TIMED_OUT, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.database.update_run(
                run_id, status=RunStatus.FAILED, finished_at=utc_now(), error=str(exc)
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
