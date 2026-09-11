"""Process helpers shared by connector adapters and the node runner."""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
from pathlib import Path


def executable_fingerprint(path: str) -> str:
    target = Path(path)
    try:
        if target.is_file():
            digest = hashlib.sha256()
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        pass
    return hashlib.sha256(path.encode()).hexdigest()


async def terminate_process_tree(process: asyncio.subprocess.Process) -> dict[str, object]:
    if process.returncode is not None:
        return {"cancelled": True, "lingering": False, "pid": process.pid}
    pid = process.pid
    try:
        os.killpg(pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, OSError):
        process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
        return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGINT"}
    except TimeoutError:
        pass
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
        return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGTERM"}
    except TimeoutError:
        pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        return {"cancelled": True, "lingering": True, "pid": pid, "signal": "SIGKILL"}
    return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGKILL"}
