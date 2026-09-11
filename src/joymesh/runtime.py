"""Async subprocess supervision shared by harness adapters.

Stream line policy
------------------
Harness stdout/stderr is consumed as newline-delimited records (NDJSON-friendly).

* Ordinary and reasonably large JSON tool records are accepted up to
  ``MAX_STREAM_LINE_BYTES`` (4 MiB) per line, excluding the trailing newline.
* The asyncio ``StreamReader.readline`` default 64 KiB limit is intentionally
  bypassed: lines are assembled from bounded ``read()`` chunks so a single
  large tool JSON line does not raise an unhandled stream limit error.
* Memory use is O(``MAX_STREAM_LINE_BYTES``) per stream, never unbounded.
* Overflow policy: if a record exceeds ``MAX_STREAM_LINE_BYTES`` without a
  newline, ``HarnessStreamOverflowError`` is raised. The owned child process
  group is terminated and reaped; the oversized partial is discarded (not
  delivered to ``on_line``). Callers must treat overflow as a hard run failure.

Cleanup policy
--------------
Timeout, stream overflow, ``on_line`` / callback failures, and task
cancellation all terminate the owned process group (``start_new_session`` on
POSIX) and drain/reap pipes. Only the process group rooted at the launched
PID is signaled — never unrelated processes.

Pipe ownership
--------------
While ``execute`` is actively consuming stdout/stderr, those streams have a
single reader (the consume tasks). ``cancel`` only signals the owned process
group; it must not drain pipes concurrently. Drain runs only after consume
tasks have finished (in ``execute`` cleanup).

Orphan policy
-------------
If the session leader exits while a child in the same process group still
holds the redirected pipes open, ``asyncio`` ``process.wait`` may not observe
leader exit promptly (especially after ``fork``). Leader liveness is therefore
also polled via ``kill(pid, 0)``. When the leader PID is gone but the owned
process group remains, that group is signaled so pipes can EOF. Only the
owned pgid (== leader pid from ``start_new_session``) is signaled.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable

from joymesh.models import LaunchSpec

LineHandler = Callable[[str, str], Awaitable[None]]
StartedHandler = Callable[[int], Awaitable[None]]

# Per-record cap for stdout/stderr lines (excluding trailing newline).
MAX_STREAM_LINE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_DRAIN_CHUNK_BYTES = 64 * 1024
_DRAIN_TIMEOUT_SECONDS = 0.5
_ORPHAN_JOIN_SECONDS = 0.25
_LEADER_POLL_SECONDS = 0.05
_WAIT_FALLBACK_SECONDS = 0.3


class HarnessTimeoutError(TimeoutError):
    pass


class HarnessStreamOverflowError(RuntimeError):
    """Raised when a single stream record exceeds ``MAX_STREAM_LINE_BYTES``."""


class HarnessRuntime:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()
        # run_ids whose execute() consume tasks currently own stdout/stderr.
        self._consuming: set[str] = set()

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

        try:
            if on_started is not None:
                await on_started(process.pid)

            async def consume(stream: asyncio.StreamReader | None, name: str) -> None:
                if stream is None:
                    return
                async for line in _iter_bounded_lines(stream):
                    await on_line(name, line)

            async def supervise() -> int:
                async with self._lock:
                    self._consuming.add(run_id)
                try:
                    async with asyncio.TaskGroup() as group:
                        group.create_task(consume(process.stdout, "stdout"))
                        group.create_task(consume(process.stderr, "stderr"))
                        group.create_task(_reap_orphans_when_leader_gone(process))
                    return await _resolve_returncode(process)
                finally:
                    async with self._lock:
                        self._consuming.discard(run_id)

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
        except asyncio.CancelledError:
            await self._terminate_process_tree(process, grace_period=0.2)
            raise
        except HarnessTimeoutError:
            raise
        except BaseException:
            await self._terminate_process_tree(process, grace_period=0.2)
            raise
        finally:
            # Consume tasks (if any) have exited; safe to drain as sole reader.
            await self._terminate_process_tree(process, grace_period=0.2)
            await self._drain_pipes(process)
            async with self._lock:
                self._consuming.discard(run_id)
                self._processes.pop(run_id, None)

    async def cancel(self, run_id: str, *, grace_period: float = 2.0) -> bool:
        async with self._lock:
            process = self._processes.get(run_id)
            consuming = run_id in self._consuming
        if process is None:
            return False
        if (
            process.returncode is not None
            and not _pid_alive(process.pid)
            and not await _process_group_alive(process.pid)
        ):
            return False
        # Never drain here while execute() may still be reading the pipes.
        await self._terminate_process_tree(process, grace_period=grace_period)
        if not consuming:
            await self._drain_pipes(process)
        return True

    async def active_run_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._processes))

    async def _terminate_and_reap(
        self, process: asyncio.subprocess.Process, *, grace_period: float
    ) -> None:
        await self._terminate_process_tree(process, grace_period=grace_period)
        await self._drain_pipes(process)

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process, *, grace_period: float
    ) -> None:
        """Signal only the owned session/process group rooted at ``process.pid``."""
        await _signal_owned_group(process, hard=False)

        if process.returncode is None and _pid_alive(process.pid):
            try:
                await asyncio.wait_for(process.wait(), grace_period)
            except TimeoutError:
                await _signal_owned_group(process, hard=True)
                try:
                    await asyncio.wait_for(process.wait(), grace_period)
                except TimeoutError:
                    pass

        # Leader may already be gone while orphans in the owned pgid hold pipes.
        if await _wait_process_group_exit(process.pid, min(grace_period, _ORPHAN_JOIN_SECONDS)):
            return
        await _signal_owned_group(process, hard=True)
        await _wait_process_group_exit(process.pid, _ORPHAN_JOIN_SECONDS)

    @staticmethod
    async def _drain_pipes(process: asyncio.subprocess.Process) -> None:
        async def drain(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            try:
                while await stream.read(_DRAIN_CHUNK_BYTES):
                    pass
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                return

        try:
            await asyncio.wait_for(
                asyncio.gather(drain(process.stdout), drain(process.stderr)),
                timeout=_DRAIN_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.CancelledError):
            return


async def _reap_orphans_when_leader_gone(process: asyncio.subprocess.Process) -> None:
    """When the session leader PID disappears, kill remaining owned group members.

    Polls ``kill(pid, 0)`` because ``process.wait`` may not complete promptly after
    ``fork`` even though the leader has already exited. Children that inherit the
    redirected pipes keep consume() blocked on EOF until the owned group is gone.
    """
    while _pid_alive(process.pid):  # noqa: ASYNC110 - poll OS pid liveness
        await asyncio.sleep(_LEADER_POLL_SECONDS)

    if not await _process_group_alive(process.pid):
        return
    await _signal_owned_group(process, hard=False)
    if not await _wait_process_group_exit(process.pid, _ORPHAN_JOIN_SECONDS):
        await _signal_owned_group(process, hard=True)
        await _wait_process_group_exit(process.pid, _ORPHAN_JOIN_SECONDS)


async def _resolve_returncode(process: asyncio.subprocess.Process) -> int:
    if process.returncode is not None:
        return process.returncode
    try:
        return await asyncio.wait_for(process.wait(), _WAIT_FALLBACK_SECONDS)
    except TimeoutError:
        raise RuntimeError("harness exit status unavailable after bounded wait") from None


async def _signal_owned_group(process: asyncio.subprocess.Process, *, hard: bool) -> None:
    """Signal only the process group created via ``start_new_session`` (pgid == pid)."""
    if os.name == "posix":
        sig = signal.SIGKILL if hard else signal.SIGTERM
        try:
            os.killpg(process.pid, sig)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    if process.returncode is not None or not _pid_alive(process.pid):
        return
    try:
        if hard:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _process_group_alive(pgid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


async def _wait_process_group_exit(pgid: int, join_seconds: float) -> bool:
    """Return True if the owned process group is gone within ``join_seconds``."""
    if os.name != "posix":
        return True
    deadline = asyncio.get_running_loop().time() + max(join_seconds, 0.0)
    while True:
        if not await _process_group_alive(pgid):
            return True
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return not await _process_group_alive(pgid)
        await asyncio.sleep(min(_LEADER_POLL_SECONDS, remaining))


async def _iter_bounded_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded text lines assembled from chunked reads.

    Bypasses ``StreamReader.readline``'s 64 KiB default so large but bounded
    NDJSON records are delivered intact. Raises ``HarnessStreamOverflowError``
    when a record exceeds ``MAX_STREAM_LINE_BYTES``.
    """
    buffer = bytearray()
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            if buffer:
                if len(buffer) > MAX_STREAM_LINE_BYTES:
                    raise HarnessStreamOverflowError(
                        f"stream record exceeded {MAX_STREAM_LINE_BYTES} bytes before EOF"
                    )
                yield buffer.decode(errors="replace").rstrip("\r\n")
            return

        buffer.extend(chunk)
        while True:
            newline_at = buffer.find(b"\n")
            if newline_at < 0:
                break
            if newline_at > MAX_STREAM_LINE_BYTES:
                raise HarnessStreamOverflowError(
                    f"stream record exceeded {MAX_STREAM_LINE_BYTES} bytes (newline-delimited)"
                )
            line = bytes(buffer[:newline_at])
            del buffer[: newline_at + 1]
            yield line.decode(errors="replace").rstrip("\r")

        if len(buffer) > MAX_STREAM_LINE_BYTES:
            raise HarnessStreamOverflowError(
                f"stream record exceeded {MAX_STREAM_LINE_BYTES} bytes without a newline"
            )
