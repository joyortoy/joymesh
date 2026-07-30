"""Shared argv-only process runner for harness adapters."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from joymesh.connectors.process_utils import terminate_process_tree
from joymesh.security import filter_environment, redact_secrets


class ProcessRunnerError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True)
class ProcessRunRequest:
    argv: Sequence[str]
    cwd: str
    timeout_seconds: float = 300.0
    extra_env_keys: frozenset[str] = frozenset()
    extra_env: Mapping[str, str] = field(default_factory=dict)
    max_stdout_bytes: int = 8_000_000
    max_stderr_bytes: int = 2_000_000
    run_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ProcessRunResult:
    ok: bool
    run_id: str
    returncode: int | None
    timed_out: bool
    cancelled: bool
    stdout: str
    stderr: str
    stdout_digest: str
    stderr_digest: str
    truncated: bool
    lingering: bool = False
    classification: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "run_id": self.run_id,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
            "truncated": self.truncated,
            "lingering": self.lingering,
            "classification": self.classification,
            "stdout_preview": redact_secrets(self.stdout[:500]),
            "stderr_preview": redact_secrets(self.stderr[:500]),
        }


class SafeProcessRunner:
    """argv-only subprocess runner with env allowlisting and process-group kill."""

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    def validate_cwd(self, cwd: str, *, allowed_roots: Sequence[str] | None = None) -> Path:
        path = Path(cwd).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ProcessRunnerError("workspace_unavailable", f"cwd unavailable: {cwd}") from exc
        if not resolved.is_dir():
            raise ProcessRunnerError("workspace_unavailable", f"cwd is not a directory: {cwd}")
        if ".." in Path(cwd).parts:
            raise ProcessRunnerError("workspace_traversal", "path traversal rejected")
        if allowed_roots:
            ok = False
            for root in allowed_roots:
                root_resolved = Path(root).expanduser().resolve()
                try:
                    resolved.relative_to(root_resolved)
                    ok = True
                    break
                except ValueError:
                    continue
            if not ok:
                raise ProcessRunnerError("workspace_escape", "cwd escapes allowed roots")
        return resolved

    async def run(self, request: ProcessRunRequest) -> ProcessRunResult:
        if not request.argv:
            raise ProcessRunnerError("invalid_argv", "argv must be non-empty")
        if any(not isinstance(item, str) for item in request.argv):
            raise ProcessRunnerError("invalid_argv", "argv must be strings only")
        # Reject shell-form: no joining into a single command string execution path.
        cwd = self.validate_cwd(request.cwd)
        env = filter_environment(extra_keys=request.extra_env_keys)
        for key, value in request.extra_env.items():
            if key in env or key in request.extra_env_keys:
                env[key] = value
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        self._processes[request.run_id] = process
        timed_out = False
        cancelled = False
        lingering = False
        try:
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(),
                    timeout=request.timeout_seconds,
                )
            except TimeoutError:
                timed_out = True
                kill = await terminate_process_tree(process)
                lingering = bool(kill.get("lingering"))
                stdout_b = b""
                stderr_b = b""
                try:
                    # Drain any remaining buffers if process already exited.
                    if process.stdout is not None:
                        stdout_b = await process.stdout.read()
                    if process.stderr is not None:
                        stderr_b = await process.stderr.read()
                except Exception:
                    pass
            if request.run_id in self._cancelled:
                cancelled = True
        finally:
            self._processes.pop(request.run_id, None)
            self._cancelled.discard(request.run_id)

        truncated = False
        if len(stdout_b) > request.max_stdout_bytes:
            stdout_b = stdout_b[: request.max_stdout_bytes]
            truncated = True
        if len(stderr_b) > request.max_stderr_bytes:
            stderr_b = stderr_b[: request.max_stderr_bytes]
            truncated = True
        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        returncode = process.returncode
        classification = _classify_exit(
            returncode=returncode,
            timed_out=timed_out,
            cancelled=cancelled,
        )
        ok = classification == "ok" and returncode == 0
        return ProcessRunResult(
            ok=ok,
            run_id=request.run_id,
            returncode=returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout=stdout,
            stderr=stderr,
            stdout_digest=hashlib.sha256(stdout.encode()).hexdigest(),
            stderr_digest=hashlib.sha256(stderr.encode()).hexdigest(),
            truncated=truncated,
            lingering=lingering,
            classification=classification,
        )

    async def stream_lines(
        self,
        request: ProcessRunRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Stream stdout lines; still applies the same safety controls as run()."""

        cwd = self.validate_cwd(request.cwd)
        env = filter_environment(extra_keys=request.extra_env_keys)
        for key, value in request.extra_env.items():
            if key in env or key in request.extra_env_keys:
                env[key] = value
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        self._processes[request.run_id] = process
        sequence = 0
        collected = 0
        try:
            assert process.stdout is not None
            while True:
                if request.run_id in self._cancelled:
                    await terminate_process_tree(process)
                    sequence += 1
                    yield {
                        "event_type": "execution.cancelled",
                        "sequence": sequence,
                        "line": "",
                    }
                    return
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=request.timeout_seconds,
                    )
                except TimeoutError:
                    await terminate_process_tree(process)
                    sequence += 1
                    yield {
                        "event_type": "execution.failed",
                        "sequence": sequence,
                        "reason": "timeout",
                    }
                    return
                if not line:
                    break
                collected += len(line)
                if collected > request.max_stdout_bytes:
                    await terminate_process_tree(process)
                    sequence += 1
                    yield {
                        "event_type": "execution.failed",
                        "sequence": sequence,
                        "reason": "output_limit",
                    }
                    return
                sequence += 1
                yield {
                    "event_type": "execution.output",
                    "sequence": sequence,
                    "line": redact_secrets(line.decode(errors="replace").rstrip("\n")),
                }
            await process.wait()
            sequence += 1
            yield {
                "event_type": "execution.completed"
                if process.returncode == 0
                else "execution.failed",
                "sequence": sequence,
                "returncode": process.returncode,
            }
        finally:
            self._processes.pop(request.run_id, None)
            self._cancelled.discard(request.run_id)

    async def cancel(self, run_id: str) -> Mapping[str, Any]:
        self._cancelled.add(run_id)
        process = self._processes.get(run_id)
        if process is None:
            return {"cancelled": True, "lingering": False, "detail": "no active process"}
        return await terminate_process_tree(process)


def _classify_exit(
    *,
    returncode: int | None,
    timed_out: bool,
    cancelled: bool,
) -> str:
    if cancelled:
        return "cancelled"
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "ok"
    if returncode is None:
        return "unknown"
    return "process_failure"
