"""Async subprocess supervision shared by harness adapters."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable

from joymesh.models import LaunchSpec

LineHandler = Callable[[str, str], Awaitable[None]]
StartedHandler = Callable[[int], Awaitable[None]]


class HarnessTimeoutError(TimeoutError):
    pass


class HarnessRuntime:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        run_id: str,
        launch: LaunchSpec,
        on_line: LineHandler,
        on_started: StartedHandler | None = None,
    ) -> int:
        if not launch.argv:
            raise ValueError("harness command cannot be empty")

        process = await asyncio.create_subprocess_exec(
            *launch.argv,
            cwd=launch.cwd,
            env=launch.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        async with self._lock:
            self._processes[run_id] = process
        if on_started is not None:
            await on_started(process.pid)

        async def consume(stream: asyncio.StreamReader | None, name: str) -> None:
            if stream is None:
                return
            while line := await stream.readline():
                await on_line(name, line.decode(errors="replace").rstrip("\r\n"))

        async def supervise() -> int:
            async with asyncio.TaskGroup() as group:
                group.create_task(consume(process.stdout, "stdout"))
                group.create_task(consume(process.stderr, "stderr"))
            return await process.wait()

        try:
            if launch.timeout_seconds is None:
                return await supervise()
            try:
                async with asyncio.timeout(launch.timeout_seconds):
                    return await supervise()
            except TimeoutError as exc:
                await self._terminate_process_tree(process, grace_period=0.2)
                raise HarnessTimeoutError(
                    f"Harness timed out after {launch.timeout_seconds:g} seconds"
                ) from exc
        finally:
            async with self._lock:
                self._processes.pop(run_id, None)

    async def cancel(self, run_id: str, *, grace_period: float = 2.0) -> bool:
        async with self._lock:
            process = self._processes.get(run_id)
        if process is None or process.returncode is not None:
            return False
        await self._terminate_process_tree(process, grace_period=grace_period)
        return True

    async def active_run_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._processes))

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process, *, grace_period: float
    ) -> None:
        if process.returncode is not None:
            return
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), grace_period)
        except TimeoutError:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            await process.wait()
