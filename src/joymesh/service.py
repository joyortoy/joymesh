"""Application service used by the JoyMesh SDK, CLI, and API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from joymesh.models import (
    Capability,
    EventType,
    HarnessDescriptor,
    NormalizedEvent,
    RouteCandidate,
    RoutePreview,
    RoutePreviewRequest,
    Run,
    RunStatus,
    SubscriptionCreate,
    SubscriptionProfile,
    utc_now,
)
from joymesh.persistence import Database
from joymesh.registry import AdapterRegistry
from joymesh.routing import Router
from joymesh.runtime import HarnessRuntime
from joymesh.workspace import resolve_workspace


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

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._initialized:
                return
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
        request = RoutePreviewRequest(
            task=task,
            workspace=str(resolved),
            required_capabilities=required_capabilities or frozenset(),
            preferred_harness=preferred_harness,
        )
        return await self.router.preview(request)

    async def run(
        self,
        *,
        task: str,
        workspace: str | Path,
        route: RouteCandidate | None = None,
    ) -> Run:
        await self.initialize()
        resolved = resolve_workspace(workspace)
        selected = route
        if selected is None:
            selected = (await self.preview_routes(task=task, workspace=resolved)).selected
        if selected is None or not selected.eligible:
            raise NoRouteError("no eligible harness route")

        self.registry.get(selected.harness_id)
        run = Run(
            id=str(uuid4()),
            task=task,
            workspace=str(resolved),
            harness_id=selected.harness_id,
            subscription_id=selected.subscription_id,
            status=RunStatus.QUEUED,
            created_at=utc_now(),
        )
        await self.database.create_run(run)
        await self._event(
            run.id,
            EventType.RUN_QUEUED,
            "Run queued",
            {"harness_id": run.harness_id},
        )
        task_handle = asyncio.create_task(self._execute(run.id), name=f"joymesh-run-{run.id}")
        self._tasks[run.id] = task_handle
        task_handle.add_done_callback(lambda _task: self._tasks.pop(run.id, None))
        return run

    async def inspect_run(self, run_id: str) -> Run | None:
        await self.initialize()
        return await self.database.get_run(run_id)

    async def events(self, run_id: str, *, after: int = 0) -> tuple[NormalizedEvent, ...]:
        await self.initialize()
        return await self.database.list_events(run_id, after=after)

    async def wait(self, run_id: str) -> Run:
        task = self._tasks.get(run_id)
        if task is not None:
            await task
        run = await self.inspect_run(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        return run

    async def cancel(self, run_id: str) -> Run:
        await self.initialize()
        run = await self.database.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return run

        cancelled = await self.database.update_run(
            run_id,
            status=RunStatus.CANCELLED,
            finished_at=utc_now(),
        )
        terminated = await self.runtime.cancel(run_id)
        task = self._tasks.get(run_id)
        if not terminated and task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._event(run_id, EventType.RUN_CANCELLED, "Run cancelled")
        return cancelled

    async def _execute(self, run_id: str) -> None:
        run = await self.database.get_run(run_id)
        if run is None:
            return
        adapter = self.registry.get(run.harness_id)
        await self.database.update_run(run_id, status=RunStatus.RUNNING, started_at=utc_now())
        await self._event(run_id, EventType.RUN_STARTED, "Run started")

        async def on_line(stream: str, line: str) -> None:
            event = adapter.normalize_output(
                run_id=run_id,
                sequence=0,
                stream=stream,
                line=line,
            )
            await self.database.append_event(event)

        try:
            exit_code = await self.runtime.execute(
                run_id=run_id,
                command=adapter.build_command(run.task, run.workspace),
                cwd=run.workspace,
                on_line=on_line,
            )
            current = await self.database.get_run(run_id)
            if current is None or current.status is RunStatus.CANCELLED:
                return
            if exit_code == 0:
                await self.database.update_run(
                    run_id,
                    status=RunStatus.SUCCEEDED,
                    finished_at=utc_now(),
                    exit_code=exit_code,
                )
                await self._event(run_id, EventType.RUN_SUCCEEDED, "Run succeeded")
            else:
                await self.database.update_run(
                    run_id,
                    status=RunStatus.FAILED,
                    finished_at=utc_now(),
                    exit_code=exit_code,
                    error=f"Harness exited with status {exit_code}",
                )
                await self._event(
                    run_id,
                    EventType.RUN_FAILED,
                    f"Harness exited with status {exit_code}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.database.update_run(
                run_id,
                status=RunStatus.FAILED,
                finished_at=utc_now(),
                error=str(exc),
            )
            await self._event(run_id, EventType.RUN_FAILED, str(exc))

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
