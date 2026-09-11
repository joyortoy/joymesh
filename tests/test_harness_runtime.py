"""Regression tests for HarnessRuntime stream bounds and cleanup."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from joymesh.models import LaunchSpec
from joymesh.runtime import (
    MAX_STREAM_LINE_BYTES,
    HarnessRuntime,
    HarnessStreamOverflowError,
    HarnessTimeoutError,
)


def _launch(argv: list[str], *, cwd: str, timeout: float | None = 30.0) -> LaunchSpec:
    return LaunchSpec(
        argv=tuple(argv),
        cwd=cwd,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        timeout_seconds=timeout,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_large_line_above_asyncio_default_limit(tmp_path: Path) -> None:
    """Lines >64 KiB must not trip StreamReader.readline's default limit."""
    payload_bytes = 70 * 1024
    script = tmp_path / "large_line.py"
    script.write_text(
        f"import sys\nsys.stdout.write('X' * {payload_bytes} + '\\n')\nsys.stdout.flush()\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    lines: list[tuple[str, str]] = []

    async def on_line(stream: str, line: str) -> None:
        lines.append((stream, line))

    code = await runtime.execute(
        run_id="large-ok",
        launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
        on_line=on_line,
    )
    assert code == 0
    assert any(name == "stdout" and len(text) == payload_bytes for name, text in lines)
    assert await runtime.active_run_ids() == ()


@pytest.mark.asyncio
async def test_stream_bound_overflow_terminates_owned_process(tmp_path: Path) -> None:
    oversize = MAX_STREAM_LINE_BYTES + 4096
    script = tmp_path / "overflow.py"
    script.write_text(
        "import sys, time\n"
        f"sys.stdout.write('Y' * {oversize})\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    pid_box: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid

    async def on_line(stream: str, line: str) -> None:
        return None

    with pytest.raises(ExceptionGroup) as exc_info:
        await runtime.execute(
            run_id="overflow",
            launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
            on_line=on_line,
            on_started=on_started,
        )
    assert any(isinstance(exc, HarnessStreamOverflowError) for exc in exc_info.value.exceptions)
    assert await runtime.active_run_ids() == ()
    pid = pid_box["pid"]
    # Owned process must be gone; ESRCH means already reaped.
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_timeout_terminates_process_group(tmp_path: Path) -> None:
    script = tmp_path / "tree_sleep.py"
    script.write_text(
        "import os, time, sys\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(60)\n"
        "    raise SystemExit(0)\n"
        "print(child, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    child_pid: dict[str, int] = {}
    parent_pid: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        parent_pid["pid"] = pid

    async def on_line(stream: str, line: str) -> None:
        if stream == "stdout" and line.strip().isdigit():
            child_pid["pid"] = int(line.strip())

    with pytest.raises(HarnessTimeoutError):
        await runtime.execute(
            run_id="timeout-tree",
            launch=_launch(
                [sys.executable, str(script)],
                cwd=str(tmp_path),
                timeout=0.3,
            ),
            on_line=on_line,
            on_started=on_started,
        )

    assert await runtime.active_run_ids() == ()
    for key, box in (("parent", parent_pid), ("child", child_pid)):
        assert "pid" in box, f"missing {key} pid"
        with pytest.raises(ProcessLookupError):
            os.kill(box["pid"], 0)


@pytest.mark.asyncio
async def test_callback_error_terminates_and_clears_run(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "import time\nprint('before', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    pid_box: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid

    async def on_line(stream: str, line: str) -> None:
        raise RuntimeError("callback boom")

    with pytest.raises(ExceptionGroup) as exc_info:
        await runtime.execute(
            run_id="callback-fail",
            launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
            on_line=on_line,
            on_started=on_started,
        )
    assert any(
        isinstance(exc, RuntimeError) and "callback boom" in str(exc)
        for exc in exc_info.value.exceptions
    )
    assert await runtime.active_run_ids() == ()
    with pytest.raises(ProcessLookupError):
        os.kill(pid_box["pid"], 0)


@pytest.mark.asyncio
async def test_cancel_terminates_owned_process(tmp_path: Path) -> None:
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    runtime = HarnessRuntime()
    pid_box: dict[str, int] = {}
    started = asyncio.Event()

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid
        started.set()

    async def on_line(stream: str, line: str) -> None:
        return None

    task = asyncio.create_task(
        runtime.execute(
            run_id="cancel-me",
            launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
            on_line=on_line,
            on_started=on_started,
        )
    )
    await started.wait()
    assert await runtime.cancel("cancel-me") is True
    code = await asyncio.wait_for(task, timeout=5)
    assert code is not None
    assert await runtime.active_run_ids() == ()
    with pytest.raises(ProcessLookupError):
        os.kill(pid_box["pid"], 0)


@pytest.mark.asyncio
async def test_on_started_error_clears_active_run_ids(tmp_path: Path) -> None:
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    runtime = HarnessRuntime()
    pid_box: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid
        raise RuntimeError("on_started boom")

    async def on_line(stream: str, line: str) -> None:
        return None

    with pytest.raises(RuntimeError, match="on_started boom"):
        await runtime.execute(
            run_id="started-fail",
            launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
            on_line=on_line,
            on_started=on_started,
        )

    assert await runtime.active_run_ids() == ()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _pid_alive(pid_box["pid"]):  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    assert not _pid_alive(pid_box["pid"])


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process groups / fork require POSIX")
async def test_exited_leader_orphan_child_holding_pipes(tmp_path: Path) -> None:
    """Leader exits immediately; child keeps stdout open until owned group kill."""
    marker = tmp_path / "child.pid"
    script = tmp_path / "orphan_pipes.py"
    script.write_text(
        "import os, sys, time\n"
        f"marker = {str(marker)!r}\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    # Keep the redirected pipe alive after the leader exits.\n"
        "    with open(marker, 'w', encoding='utf-8') as fh:\n"
        "        fh.write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "print(child, flush=True)\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    child_pid: dict[str, int] = {}
    parent_pid: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        parent_pid["pid"] = pid

    async def on_line(stream: str, line: str) -> None:
        if stream == "stdout" and line.strip().isdigit():
            child_pid["pid"] = int(line.strip())

    code = await asyncio.wait_for(
        runtime.execute(
            run_id="orphan-leader",
            launch=_launch(
                [sys.executable, str(script)],
                cwd=str(tmp_path),
                timeout=8.0,
            ),
            on_line=on_line,
            on_started=on_started,
        ),
        timeout=10.0,
    )
    assert code is not None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not marker.exists():  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    if "pid" not in child_pid and marker.exists():
        child_pid["pid"] = int(marker.read_text(encoding="utf-8").strip())
    assert "pid" in child_pid
    assert await runtime.active_run_ids() == ()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and (  # noqa: ASYNC110
        _pid_alive(parent_pid["pid"]) or _pid_alive(child_pid["pid"])
    ):
        await asyncio.sleep(0.05)
    assert not _pid_alive(parent_pid["pid"])
    assert not _pid_alive(child_pid["pid"])


@pytest.mark.asyncio
async def test_cancel_while_consuming_does_not_dual_read_pipes(tmp_path: Path) -> None:
    """cancel must signal only; consume remains the sole pipe reader."""
    script = tmp_path / "stream_sleep.py"
    script.write_text(
        "import sys, time\n"
        "for i in range(200):\n"
        "    print(f'line-{i}', flush=True)\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    started = asyncio.Event()
    seen = asyncio.Event()
    lines: list[str] = []
    pid_box: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid
        started.set()

    async def on_line(stream: str, line: str) -> None:
        if stream == "stdout":
            lines.append(line)
            seen.set()

    task = asyncio.create_task(
        runtime.execute(
            run_id="cancel-consume",
            launch=_launch([sys.executable, str(script)], cwd=str(tmp_path)),
            on_line=on_line,
            on_started=on_started,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.wait_for(seen.wait(), timeout=5)
    assert "cancel-consume" in await runtime.active_run_ids()
    assert await runtime.cancel("cancel-consume", grace_period=0.5) is True
    code = await asyncio.wait_for(task, timeout=5)
    assert code is not None
    assert lines  # consume owned the pipe through cancel
    assert await runtime.active_run_ids() == ()
    assert not _pid_alive(pid_box["pid"])


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="SIGTERM ignore behavior is POSIX-specific")
async def test_overflow_while_child_ignores_term(tmp_path: Path) -> None:
    """Overflow must escalate to SIGKILL when the owned child ignores TERM."""
    oversize = MAX_STREAM_LINE_BYTES + 4096
    script = tmp_path / "overflow_ignore_term.py"
    script.write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"sys.stdout.write('Z' * {oversize})\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    runtime = HarnessRuntime()
    pid_box: dict[str, int] = {}

    async def on_started(pid: int) -> None:
        pid_box["pid"] = pid

    async def on_line(stream: str, line: str) -> None:
        return None

    with pytest.raises(ExceptionGroup) as exc_info:
        await asyncio.wait_for(
            runtime.execute(
                run_id="overflow-ign-term",
                launch=_launch(
                    [sys.executable, str(script)],
                    cwd=str(tmp_path),
                    timeout=10.0,
                ),
                on_line=on_line,
                on_started=on_started,
            ),
            timeout=12.0,
        )
    assert any(isinstance(exc, HarnessStreamOverflowError) for exc in exc_info.value.exceptions)
    assert await runtime.active_run_ids() == ()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _pid_alive(pid_box["pid"]):  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    assert not _pid_alive(pid_box["pid"])


@pytest.mark.asyncio
async def test_unknown_exit_status_is_not_reported_as_success() -> None:
    from joymesh.runtime import _resolve_returncode

    class UnknownExitProcess:
        returncode = None

        async def wait(self):
            raise TimeoutError("synthetic unavailable status")

    with pytest.raises(RuntimeError, match="exit status unavailable"):
        await _resolve_returncode(UnknownExitProcess())
