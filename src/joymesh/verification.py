"""Deterministic macOS verification; no model routing or worker-authored receipts."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from joymesh.cursor_sandbox import write_confinement_profile

OUTPUT_LIMIT = 4 * 1024 * 1024


def _capture(process: subprocess.Popen, deadline: float, *, tail_bytes: int = 2000) -> tuple[str | None, dict]:
    """Bounded pipe capture: no child-controlled output file can fill the disk."""
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    failure = None
    with selectors.DefaultSelector() as selector:
        for name in buffers:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            if time.monotonic() >= deadline:
                failure = "timeout"
                break
            for key, _ in selector.select(min(0.02, max(0, deadline - time.monotonic()))):
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                remaining = OUTPUT_LIMIT - len(buffer)
                buffer.extend(data[:remaining])
                if len(data) > remaining:
                    failure = "output_limit"
                    break
            if failure or process.poll() is not None:
                break
        # A child can close both output pipes and continue running.
        if failure is None:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "timeout"
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        # Drain bytes remaining after exit, without allowing inherited pipes to hang.
        for name, buffer in buffers.items():
            stream = getattr(process, name)
            while failure is None:
                try:
                    data = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    break
                if not data:
                    break
                remaining = OUTPUT_LIMIT - len(buffer)
                buffer.extend(data[:remaining])
                if len(data) > remaining:
                    failure = "output_limit"
            stream.close()
    return failure, {name: {"size_bytes": len(data), "captured_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "tail": data[-tail_bytes:].decode("utf-8", errors="replace")}
                     for name, data in buffers.items()}


def verify_commands(commands: list[str], workspace: str, timeout: float) -> list[dict]:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("verification sandbox unavailable")
    if not commands or timeout <= 0:
        raise ValueError("explicit commands and positive timeout required")
    parsed = []
    for command in commands:
        argv = shlex.split(command)
        if not argv or any(token in {";", "&&", "||", "|", ">", "<", "&"} for token in argv):
            raise ValueError("verification requires direct argv, not shell operators")
        if not Path(argv[0]).is_absolute():
            raise ValueError("verification executable must be absolute")
        parsed.append(argv)
    receipts = []
    deadline = time.monotonic() + timeout
    for command, argv in zip(commands, parsed, strict=True):
        with tempfile.TemporaryDirectory(prefix="joymesh-verify-") as directory:
            scratch = Path(directory).resolve()
            profile = write_confinement_profile(Path(workspace), scratch, (), read_only=True)
            profile = "\n".join(line for line in profile.splitlines() if "network-outbound" not in line)
            profile += "\n(deny network* (with send-signal SIGKILL))"
            profile += "\n(deny syscall-unix (syscall-number SYS_setsid) (syscall-number SYS_setpgid) (syscall-number SYS_posix_spawn) (with send-signal SIGKILL))"
            # Fatal forbidden writes cannot be caught and ignored by the command.
            profile = profile.replace("(deny file-write*)", "(deny file-write* (with send-signal SIGKILL))")
            # CPython probes DTrace at startup. Keep it denied, nonfatal.
            profile += '\n(deny file-write* (literal "/dev/dtracehelper"))'
            started = time.time_ns()
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", str(Path(__file__).with_name("verification_bootstrap.py"))],
                cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(scratch),
                     "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1",
                     "PYTHONPYCACHEPREFIX": str(scratch / 'pycache'),
                     "LANG": "en_US.UTF-8"},
            )
            try:
                bootstrap = json.dumps({"profile": profile, "argv": argv}).encode()
                if len(bootstrap) >= 65536:
                    raise ValueError("verification bootstrap request too large")
                process.stdin.write(bootstrap)
                process.stdin.close()
                failure, outputs = _capture(process, deadline)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            receipts.append({"command": command, "argv": argv, "cwd": str(Path(workspace).resolve()),
                                 "started_ns": started, "ended_ns": time.time_ns(), "pid": process.pid,
                                 "exit_code": process.returncode, "failure": failure,
                                 "sandbox": "macos-readonly-no-network-v2", **outputs})
            if failure or process.returncode:
                break
    return receipts
