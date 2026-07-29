"""OpenCode CLI adapter using `opencode run --format json`."""

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
    PermissionMode,
    RunRequest,
    UsageDelta,
)
from joymesh.security import redact_secrets


class OpenCodeAdapter(HarnessAdapter):
    def __init__(self, executable: str = "opencode", *, conformance_passed: bool = False) -> None:
        self.executable_name = executable
        self.conformance_passed = conformance_passed

    @property
    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            harness_id="opencode",
            display_name="OpenCode",
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
            "run",
            "--format",
            "json",
            "--dir",
            request.workspace,
        ]
        if request.resume_session_id:
            argv.extend(["--session", request.resume_session_id])
        if request.permission_mode is PermissionMode.AUTO_APPROVE:
            argv.append("--auto")
        if request.model:
            model = f"{request.provider}/{request.model}" if request.provider else request.model
            argv.extend(["--model", model])
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
        part = native.get("part", {})
        session_id = native.get("sessionID") or native.get("session_id") or part.get("sessionID")
        message = native.get("message") or part.get("text") or native_type
        usage = None
        tokens = part.get("tokens") or native.get("usage")
        if tokens:
            usage = UsageDelta(
                input_tokens=int(tokens.get("input", tokens.get("input_tokens", 0))),
                output_tokens=int(tokens.get("output", tokens.get("output_tokens", 0))),
                cost=part.get("cost"),
            )
        return AdapterObservation(
            event=NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=(
                    EventType.HARNESS_PROGRESS
                    if native_type in {"step_start", "tool_use"}
                    else EventType.HARNESS_OUTPUT
                ),
                message=redact_secrets(str(message)),
                payload={"stream": stream, "native_type": native_type},
            ),
            native_session_id=str(session_id) if session_id else None,
            usage=usage,
        )
