"""Reusable adapters for documented non-interactive CLI contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from joymesh.adapters.base import HarnessAdapter
from joymesh.harnesses.contracts import CapabilityState, HarnessDefinition
from joymesh.harnesses.protocols import decode_record_lenient
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

ArgvBuilder = Callable[[str, RunRequest], tuple[str, ...]]


class DocumentedCLIAdapter(HarnessAdapter):
    """Adapter configured from a catalogue definition and a small argv strategy."""

    def __init__(
        self,
        definition: HarnessDefinition,
        argv_builder: ArgvBuilder,
        *,
        executable: str | None = None,
        conformance_passed: bool = False,
    ) -> None:
        self.definition = definition
        self.executable_name = executable or definition.executables[0]
        self.version_args = definition.version_args
        self.environment_keys = frozenset(definition.environment_variables)
        self.conformance_passed = conformance_passed
        self._argv_builder = argv_builder

    @property
    def manifest(self) -> CapabilityManifest:
        supported = frozenset(
            capability
            for capability, state in self.definition.capabilities.items()
            if state in {CapabilityState.SUPPORTED, CapabilityState.EXPERIMENTAL}
        )
        return CapabilityManifest(
            harness_id=self.definition.id,
            display_name=self.definition.display_name,
            capabilities=supported,
            supports_resume=(
                self.definition.capabilities.get(Capability.SESSION_RESUME)
                in {CapabilityState.SUPPORTED, CapabilityState.EXPERIMENTAL}
            ),
            max_concurrency=4,
        )

    def build_launch_spec(self, request: RunRequest) -> LaunchSpec:
        return LaunchSpec(
            argv=self._argv_builder(self.executable_name, request),
            cwd=self.validate_workspace(request.workspace),
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
        native = decode_record_lenient(self.definition.protocol, line)
        native_type = str(
            native.get("type")
            or native.get("event")
            or native.get("subtype")
            or ("stderr" if stream == "stderr" else "output")
        )
        session_id = _first_value(
            native,
            ("session_id", "sessionId", "sessionID", "thread_id", "taskId", "uuid"),
        )
        message = _first_value(
            native,
            ("message", "text", "response", "content", "result", "error"),
        )
        usage_mapping = _find_mapping(native, ("usage", "stats", "tokens", "token_usage"))
        usage = _usage(usage_mapping) if usage_mapping else None
        progress_markers = {
            "init",
            "system",
            "tool_use",
            "tool_result",
            "step_start",
            "turn.started",
            "assistant_delta",
        }
        return AdapterObservation(
            event=NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=(
                    EventType.HARNESS_PROGRESS
                    if native_type in progress_markers
                    else EventType.HARNESS_OUTPUT
                ),
                message=redact_secrets(str(message or line)),
                payload={"stream": stream, "native_type": native_type},
            ),
            native_session_id=str(session_id) if session_id else None,
            usage=usage,
        )


def builtin_documented_adapters(
    definitions: tuple[HarnessDefinition, ...],
) -> tuple[DocumentedCLIAdapter, ...]:
    by_id = {definition.id: definition for definition in definitions}
    builders: dict[str, ArgvBuilder] = {
        "claude-code": _claude_argv,
        "gemini-cli": _gemini_argv,
        "github-copilot": _copilot_argv,
        "aider": _aider_argv,
        "goose": _goose_argv,
        "pi": _pi_argv,
        "continue": _continue_argv,
        "qwen-code": _qwen_argv,
        "cline": _cline_argv,
    }
    return tuple(
        DocumentedCLIAdapter(by_id[harness_id], builder) for harness_id, builder in builders.items()
    )


def _claude_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--print", request.task, "--output-format", "stream-json", "--verbose"]
    if request.resume_session_id:
        argv.extend(["--resume", request.resume_session_id])
    if request.permission_mode is PermissionMode.READ_ONLY:
        argv.extend(["--permission-mode", "plan"])
    elif request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--dangerously-skip-permissions")
    if request.model:
        argv.extend(["--model", request.model])
    for directory in request.additional_writable_directories:
        argv.extend(["--add-dir", directory])
    return tuple(argv)


def _gemini_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--prompt", request.task, "--output-format", "stream-json"]
    if request.resume_session_id:
        argv.extend(["--resume", request.resume_session_id])
    if request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--yolo")
    if request.model:
        argv.extend(["--model", request.model])
    if request.additional_writable_directories:
        argv.extend(
            [
                "--include-directories",
                ",".join(request.additional_writable_directories),
            ]
        )
    return tuple(argv)


def _copilot_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [
        executable,
        "--prompt",
        request.task,
        "--output-format=json",
        "--no-ask-user",
    ]
    if request.resume_session_id:
        argv.extend(["--resume", request.resume_session_id])
    if request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--allow-all-tools")
    if request.model:
        argv.extend(["--model", request.model])
    return tuple(argv)


def _aider_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [
        executable,
        "--message",
        request.task,
        "--no-auto-commits",
        "--no-pretty",
    ]
    if request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--yes")
    if request.model:
        argv.extend(["--model", request.model])
    return tuple(argv)


def _goose_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    return (executable, "run", "--text", request.task)


def _pi_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--print", request.task, "--mode", "json"]
    if request.resume_session_id:
        argv.extend(["--session", request.resume_session_id])
    if request.provider:
        argv.extend(["--provider", request.provider])
    if request.model:
        argv.extend(["--model", request.model])
    return tuple(argv)


def _continue_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--prompt", request.task]
    if request.resume_session_id:
        argv.append("--resume")
    if request.permission_mode is PermissionMode.READ_ONLY:
        argv.append("--readonly")
    elif request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--auto")
    if request.model:
        argv.extend(["--model", request.model])
    return tuple(argv)


def _qwen_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--prompt", request.task, "--output-format", "stream-json"]
    if request.resume_session_id:
        argv.extend(["--resume", request.resume_session_id])
    if request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.append("--yolo")
    if request.model:
        argv.extend(["--model", request.model])
    return tuple(argv)


def _cline_argv(executable: str, request: RunRequest) -> tuple[str, ...]:
    argv = [executable, "--json"]
    if request.permission_mode is PermissionMode.AUTO_APPROVE:
        argv.extend(["--auto-approve", "true"])
    argv.append(request.task)
    return tuple(argv)


def _first_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate is not None and candidate != "":
                return candidate
        for child in value.values():
            found = _first_value(child, keys)
            if found is not None and found != "":
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_value(child, keys)
            if found is not None and found != "":
                return found
    return None


def _find_mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    found = _first_value(value, keys)
    return found if isinstance(found, dict) else None


def _usage(value: dict[str, Any]) -> UsageDelta:
    return UsageDelta(
        input_tokens=_as_int(
            value.get("input_tokens")
            or value.get("input")
            or value.get("prompt_tokens")
            or value.get("prompt")
        ),
        output_tokens=_as_int(
            value.get("output_tokens")
            or value.get("output")
            or value.get("completion_tokens")
            or value.get("completion")
        ),
        cost=_as_float(value.get("cost") or value.get("total_cost_usd")),
    )


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
