"""Async subprocess supervision shared by harness adapters."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence

LineHandler = Callable[[str, str], Awaitable[None]]


class HarnessRuntime:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        run_id: str,
        command: Sequence[str],
        cwd: str,
        on_line: LineHandler,
    ) -> int:
        if not command:
            raise ValueError("harness command cannot be empty")

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        async with self._lock:
            self._processes[run_id] = process

        async def consume(stream: asyncio.StreamReader | None, name: str) -> None:
            if stream is None:
                return
            while line := await stream.readline():
                await on_line(name, line.decode(errors="replace").rstrip("\r\n"))

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(consume(process.stdout, "stdout"))
                group.create_task(consume(process.stderr, "stderr"))
            return await process.wait()
        finally:
            async with self._lock:
                self._processes.pop(run_id, None)

    async def cancel(self, run_id: str, *, grace_period: float = 2.0) -> bool:
        async with self._lock:
            process = self._processes.get(run_id)
        if process is None or process.returncode is not None:
            return False
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), grace_period)
        except TimeoutError:
            process.kill()
            await process.wait()
        return True

    async def active_run_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._processes))
