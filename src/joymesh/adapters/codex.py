"""Codex CLI adapter using the documented non-interactive JSONL interface."""

from __future__ import annotations

import json

from joymesh.adapters.base import HarnessAdapter
from joymesh.models import (
    AdapterObservation,
    Capability,
    CapabilityManifest,
    EventType,
    LaunchSpec,
    NormalizedEvent,
    RunRequest,
    UsageDelta,
)
from joymesh.security import redact_secrets


class CodexAdapter(HarnessAdapter):
    def __init__(self, executable: str = "codex", *, conformance_passed: bool = False) -> None:
        self.executable_name = executable
        self.conformance_passed = conformance_passed

    @property
    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            harness_id="codex",
            display_name="Codex CLI",
            capabilities=frozenset(
                {
                    Capability.FILE_READ,
                    Capability.FILE_WRITE,
                    Capability.SHELL,
                    Capability.STREAMING,
                    Capability.SESSION_RESUME,
                    Capability.TOOL_USE,
                }
            ),
            supports_resume=True,
            max_concurrency=4,
        )

    def build_launch_spec(self, request: RunRequest) -> LaunchSpec:
        argv = [
            self.executable_name,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            request.workspace,
        ]
        if request.model:
            argv.extend(["--model", request.model])
        for directory in request.additional_writable_directories:
            argv.extend(["--add-dir", directory])
        if request.resume_session_id:
            argv.extend(["resume", request.resume_session_id])
        argv.append(request.task)
        return LaunchSpec(
            argv=tuple(argv),
            cwd=request.workspace,
            env=self.launch_environment(),
            timeout_seconds=request.timeout_seconds,
        )

    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> AdapterObservation:
        try:
            native = json.loads(line)
        except json.JSONDecodeError:
            native = {"type": "output", "message": line}
        native_type = str(native.get("type", "output"))
        session_id = None
        usage = None
        message = native.get("message") or native.get("error")
        if native_type == "thread.started":
            session_id = native.get("thread_id") or native.get("thread", {}).get("id")
            message = "Codex session started"
        elif native_type == "item.completed":
            item = native.get("item", {})
            message = item.get("text") or item.get("content") or message
        elif native_type == "turn.completed":
            native_usage = native.get("usage", {})
            usage = UsageDelta(
                input_tokens=int(native_usage.get("input_tokens", 0)),
                output_tokens=int(native_usage.get("output_tokens", 0)),
            )
            message = "Codex turn completed"
        return AdapterObservation(
            event=NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=(
                    EventType.HARNESS_PROGRESS
                    if native_type in {"turn.started", "item.started"}
                    else EventType.HARNESS_OUTPUT
                ),
                message=redact_secrets(str(message or native_type)),
                payload={"stream": stream, "native_type": native_type},
            ),
            native_session_id=str(session_id) if session_id else None,
            usage=usage,
        )
