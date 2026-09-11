"""Real child processes, synthetic Unix peer, no MCP or production daemons."""

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from joymesh.joymux_supervision import JoyMuxSupervisedRuntime, SupervisionLost
from joymesh.models import LaunchSpec


@contextlib.asynccontextmanager
async def peer(*, fail_at: int = 0, failure: str = "disconnect"):
    calls = []
    connections = set()
    handlers = set()

    async def handle(reader, writer):
        connections.add(writer)
        handlers.add(asyncio.current_task())
        try:
            while line := await reader.readline():
                request = json.loads(line)
                calls.append(request)
                if request["method"] == "client/register":
                    result = {"client_id": "client-test"}
                elif request["method"] == "session/create":
                    result = {"session_id": "session-test"}
                else:
                    result = {"session_id": "session-test", "connected": True}
                response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                if len(calls) == fail_at:
                    if failure == "disconnect":
                        return
                    if failure == "stall":
                        await asyncio.Event().wait()
                    if failure == "wrong_id":
                        response["id"] = "unrelated"
                    if failure == "disconnected":
                        result["connected"] = False
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            connections.discard(writer)
            handlers.discard(asyncio.current_task())

    # Unix pathname length limits prohibit using the full pytest tmp_path.
    with tempfile.TemporaryDirectory(prefix="jm-supervisor-", dir="/tmp") as root:
        path = Path(root) / "runtime.sock"
        server = await asyncio.start_unix_server(handle, path)
        try:
            yield path, calls
        finally:
            server.close()
            for writer in tuple(connections):
                writer.close()
            tasks = tuple(handlers)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()


def launch(tmp_path, script):
    return LaunchSpec(
        argv=(sys.executable, "-u", "-c", script),
        cwd=str(tmp_path),
        env=dict(os.environ),
        timeout_seconds=10,
    )


async def discard(stream, line):
    pass


def assert_dead(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_acknowledged_lifecycle_does_not_forward_child_claims(tmp_path):
    async with peer() as (path, calls):
        runtime = JoyMuxSupervisedRuntime(path, interval=0.02, timeout=0.2)
        assert (
            await runtime.execute(
                run_id="owned-test",
                launch=launch(tmp_path, "print('fake security success')"),
                on_line=discard,
            )
            == 0
        )
    serialized = json.dumps(calls)
    assert "fake security success" not in serialized
    assert "input_tokens" not in serialized
    assert [item["event"] for item in runtime.observations] == [
        "ready",
        "started",
        "finished",
        "cleanup_returned",
    ]
    assert calls[-2]["params"]["observed_resource_usage"]["source"] == "joymesh-parent:finished"
    assert calls[-1]["method"] == "session/close"
    assert await runtime.active_run_ids() == ()


async def test_unavailable_monitor_prevents_launch(tmp_path):
    runtime = JoyMuxSupervisedRuntime(tmp_path / "absent.sock", timeout=0.1)
    canary = tmp_path / "must-not-run"
    with pytest.raises(SupervisionLost):
        await runtime.execute(
            run_id="never-start",
            launch=launch(tmp_path, f"open({str(canary)!r}, 'w').close()"),
            on_line=discard,
        )
    assert not canary.exists()


@pytest.mark.parametrize("failure", ["disconnect", "stall", "wrong_id", "disconnected"])
async def test_monitor_loss_stops_owned_child(tmp_path, failure):
    # Calls 1-4 register, create, ready, running; call 5 is a heartbeat.
    async with peer(fail_at=5, failure=failure) as (path, _):
        runtime = JoyMuxSupervisedRuntime(path, interval=0.05, timeout=0.1)
        with pytest.raises(SupervisionLost):
            await runtime.execute(
                run_id="stop-test",
                launch=launch(tmp_path, "import time; time.sleep(20)"),
                on_line=discard,
            )
    events = list(runtime.observations)
    pid = next(e["pid"] for e in events if e["event"] == "started")
    assert_dead(pid)
    loss = next(e["monotonic_ns"] for e in events if e["event"] == "supervision_lost")
    stopped = next(e["monotonic_ns"] for e in events if e["event"] == "cleanup_returned")
    assert 0 <= stopped - loss < 2_000_000_000
    assert await runtime.active_run_ids() == ()


async def test_caller_cancellation_cleans_up(tmp_path):
    started = asyncio.Event()
    pid_box = []

    async def on_started(pid):
        pid_box.append(pid)
        started.set()

    async with peer() as (path, _):
        runtime = JoyMuxSupervisedRuntime(path, interval=0.05, timeout=0.1)
        task = asyncio.create_task(
            runtime.execute(
                run_id="cancel-test",
                launch=launch(tmp_path, "import time; time.sleep(20)"),
                on_line=discard,
                on_started=on_started,
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_dead(pid_box[0])
        assert await runtime.active_run_ids() == ()
