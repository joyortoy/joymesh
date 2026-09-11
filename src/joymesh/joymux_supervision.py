"""Opt-in host supervision over JoyMux Unix RPC; never exposed to the child.

This observes parent lifecycle and channel health, NOT kernel sandbox denials.
Losing acknowledged heartbeats fails the run and cancels its owned process group.
It does not start a daemon, reconnect silently, or trust harness output as evidence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from joymesh.models import LaunchSpec
from joymesh.runtime import HarnessRuntime, LineHandler, StartedHandler


class SupervisionLost(RuntimeError):
    """JoyMux supervision could not be established or maintained."""

    rpc_method: str = ""
    failure_kind: str = ""


class _Channel:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.client_id = ""
        self.session_id = ""
        self.started = time.monotonic()

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with asyncio.timeout(self.timeout):
                if self.writer is None:
                    self.reader, self.writer = await asyncio.open_unix_connection(
                        self.path, limit=65536
                    )
                request_id = uuid4().hex
                self.writer.write(
                    (
                        json.dumps(
                            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
                        )
                        + "\n"
                    ).encode()
                )
                await self.writer.drain()
                assert self.reader is not None
                response = json.loads(await self.reader.readline())
                if (
                    not isinstance(response, dict)
                    or response.get("id") != request_id
                    or response.get("jsonrpc") != "2.0"
                    or "error" in response
                    or not isinstance(response.get("result"), dict)
                ):
                    raise ValueError("invalid acknowledgement")
                return cast(dict[str, Any], response["result"])
        except (OSError, ValueError, TimeoutError) as exc:
            # Neither server errors nor raw wire data belong in logs.
            error = SupervisionLost(f"JoyMux {method} acknowledgement unavailable")
            error.rpc_method = method
            error.failure_kind = type(exc).__name__
            raise error from exc

    async def bind(
        self,
        run_id: str,
        workspace: str,
        *,
        evidence_scope: str = "lifecycle_only_not_kernel_denials",
    ) -> None:
        result = await self.call(
            "client/register",
            {
                "protocol_version": "joymux.execution.v1",
                "client_name": "joymesh-parent-supervisor",
                "client_instance_id": f"joymesh-parent-{uuid4().hex}",
                "requested_capabilities": [],
                "metadata": {"source": "joymesh-parent", "run_id": run_id},
            },
        )
        self.client_id = result.get("client_id", "")
        if not isinstance(self.client_id, str) or not self.client_id:
            raise SupervisionLost("JoyMux registration missing client identity")
        result = await self.call(
            "session/create",
            {
                "client_id": self.client_id,
                "correlation_id": run_id,
                "workspace": workspace,
                "terminal": {"mode": "pipe", "cols": 120, "rows": 40},
                "environment": {},
                "persistence": "until_client_disconnect",
                "policy_context": {},
                "metadata": {
                    "display_name": "JoyMesh parent supervision",
                    "run_id": run_id,
                    "reporter": "joymesh-parent",
                    "evidence_scope": evidence_scope,
                },
            },
        )
        self.session_id = result.get("session_id", "")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise SupervisionLost("JoyMux session missing identity")
        await self.facts("ready")

    async def facts(self, state: str) -> None:
        result = await self.call(
            "session/report_facts",
            {
                "client_id": self.client_id,
                "session_id": self.session_id,
                "observed_resource_usage": {
                    "source": f"joymesh-parent:{state}",
                    "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                },
            },
        )
        if result.get("session_id") != self.session_id or result.get("connected") is not True:
            raise SupervisionLost("JoyMux did not acknowledge connected session")

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(self.writer.wait_closed(), self.timeout)


class JoyMuxSupervisedRuntime(HarnessRuntime):
    """Use as JoyMesh(runtime=JoyMuxSupervisedRuntime(socket_path)).

    Heartbeat timeout is a fail-closed condition for this opt-in runtime. The
    socket path is host configuration, not a value read from the workspace.
    Observations contain parent facts only, with bounded in-memory retention.
    """

    def __init__(self, socket_path: Path, *, interval: float = 1.0, timeout: float = 1.0):
        super().__init__()
        if not 0 < interval <= 5 or not 0 < timeout <= 5:
            raise ValueError("supervision intervals must be within (0, 5] seconds")
        self.socket_path = socket_path
        self.interval = interval
        self.timeout = timeout
        self.observations: deque[dict[str, Any]] = deque(maxlen=256)

    async def execute(
        self,
        *,
        run_id: str,
        launch: LaunchSpec,
        on_line: LineHandler,
        on_started: StartedHandler | None = None,
    ) -> int:
        channel = _Channel(self.socket_path, self.timeout)
        worker: asyncio.Task[int] | None = None
        heartbeat: asyncio.Task[None] | None = None
        stop = asyncio.Event()

        def record(event: str, **fields: Any) -> None:
            self.observations.append(
                {
                    "run_id": run_id,
                    "session_id": channel.session_id,
                    "event": event,
                    "monotonic_ns": time.monotonic_ns(),
                    **fields,
                }
            )

        async def started(pid: int) -> None:
            await channel.facts("running")
            record("started", pid=pid)
            if on_started is not None:
                await on_started(pid)

        async def watch() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), self.interval)
                    return
                except TimeoutError:
                    pass
                await channel.facts("running")

        try:
            await channel.bind(run_id, launch.cwd)
            record("ready")
            # Finish the start acknowledgement before the first heartbeat to
            # keep a single reader on the RPC stream.
            launch_ready = asyncio.Event()

            async def acknowledged_start(pid: int) -> None:
                await started(pid)
                launch_ready.set()

            async def watch_after_start() -> None:
                await launch_ready.wait()
                await watch()

            worker = asyncio.create_task(
                super().execute(
                    run_id=run_id,
                    launch=launch,
                    on_line=on_line,
                    on_started=acknowledged_start,
                )
            )
            heartbeat = asyncio.create_task(watch_after_start())
            done, _ = await asyncio.wait((worker, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
            result = await worker
            stop.set()
            await heartbeat
            await channel.facts("finished")
            await channel.call(
                "session/close",
                {
                    "client_id": channel.client_id,
                    "session_id": channel.session_id,
                },
            )
            record("finished", exit_code=result)
            return result
        except SupervisionLost:
            record("supervision_lost")
            raise
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if worker is not None:
                if not worker.done():
                    worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                record("cleanup_returned")
            await channel.close()
