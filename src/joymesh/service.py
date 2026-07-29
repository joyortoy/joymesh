"""SDK-first application service shared by the CLI and REST API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from joymesh.models import (
    Capability,
    EventType,
    FailureKind,
    FallbackProposal,
    HarnessDescriptor,
    NormalizedEvent,
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
        self.registry = registry or AdapterRegistry()
        self.runtime = runtime or HarnessRuntime()
        self.router = Router(self.registry, self.database)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, RunRequest] = {}

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if not self._initialized:
                await self.database.initialize()
                self._initialized = True

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

    async def list_subscriptions(self) -> tuple[SubscriptionProfile, ...]:
        await self.initialize()
        return await self.database.list_subscriptions()

    async def create_subscription(self, data: SubscriptionCreate) -> SubscriptionProfile:
        await self.initialize()
        self.registry.get(data.harness_id)
        return await self.database.create_subscription(data)

    async def resolve_route(
        self, *, request: RunRequest, preferred_harness: str | None = None
    ) -> RouteCandidate:
        preview = await self.preview_routes(
            task=request.task,
            workspace=request.workspace,
            required_capabilities=request.required_capabilities,
            preferred_harness=preferred_harness,
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
    ) -> RoutePreview:
        await self.initialize()
        resolved = resolve_workspace(workspace)
        return await self.router.preview(
            RoutePreviewRequest(
                task=task,
                workspace=str(resolved),
                required_capabilities=required_capabilities or frozenset(),
                preferred_harness=preferred_harness,
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
        task: str,
        workspace: str | Path,
        route: RouteCandidate | None = None,
    ) -> Run:
        request = RunRequest(task=task, workspace=str(workspace))
        selected = route or await self.resolve_route(request=request)
        return await self.start_run(request=request, route=selected)

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
        preview = await self.preview_routes(task=run.task, workspace=run.workspace)
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
