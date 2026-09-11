"""Claude Code connector implementing the generic ConnectorRuntime protocol.

CLI contract observed on ``@anthropic-ai/claude-code`` 2.1.220:

* Executable: ``claude``
* Version: ``claude --version`` / ``-v``
* Non-interactive: ``claude --print --output-format stream-json --verbose <prompt>``
* Auth status: ``claude auth status --json``
* Auth login: ``claude auth login``
* Workspace: process cwd (live-test / execute set cwd to the workspace)
* Read-only: ``--permission-mode plan`` + ``--tools`` allow-list + ``--disallowedTools``
* Structured output: JSONL with ``system`` / ``assistant`` / ``user`` / ``result``
* Permission denials: ``result.permission_denials`` and denied tool results
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.process_utils import executable_fingerprint, terminate_process_tree
from joymesh.models import utc_now
from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES
from joymesh.runtime_v1.certification import ReadOnlyRepositoryProfile
from joymesh.runtime_v1.connector_protocol import (
    AdapterVerificationResult,
    AuthenticationEvidence,
    AuthenticationResult,
    CancellationResult,
    CertificationProfileDefinition,
    ConnectorExecutionContext,
    ConnectorPlan,
    ConnectorRunRequest,
    ConnectorRuntimeNotice,
    DiscoveryResult,
    HarnessEvent,
)
from joymesh.security import filter_environment

# Catalogue harness_id remains "claude-code"; runtime connector_id is "claude".
_CATALOGUE_ID = "claude-code"

_READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
_DISALLOWED_TOOLS = (
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Agent",
)

_PROVIDER_OVERRIDE_ENV = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_BASE",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


class ClaudeConnectorRuntime:
    """Claude Code discovery, auth, argv, stream-json parsing, and cancellation."""

    connector_id = "claude"
    display_name = "Claude Code"

    def __init__(self, catalogue: ConnectorCatalogue | None = None) -> None:
        self.catalogue = catalogue or ConnectorCatalogue.builtins()
        definition = self.catalogue.get(_CATALOGUE_ID)
        self.connector_revision = definition.revision
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._read_only = ReadOnlyRepositoryProfile()

    def declared_capabilities(self) -> frozenset[str]:
        return READ_ONLY_CAPABILITIES

    def certification_profiles(self) -> tuple[CertificationProfileDefinition, ...]:
        return (
            CertificationProfileDefinition(
                profile_id=self._read_only.profile_id,
                profile_revision=self._read_only.profile_revision,
                required_capabilities=self._read_only.required_capabilities,
                description="Isolated read-only repository certification",
            ),
        )

    def adapter_verification_notice(self) -> ConnectorRuntimeNotice | None:
        return ConnectorRuntimeNotice(
            event_type="connector_plan_restriction",
            connector_id=self.connector_id,
            display_name=self.display_name,
            reason_code="connector_plan_restriction",
            message=(
                "Claude Code read-only mode uses --permission-mode plan with "
                "tool allow/deny lists; subscription or API usage may incur cost."
            ),
            recoverable=True,
            recommended_action="approve_and_continue",
        )

    def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
        # Claude enforces read-only via argv (--permission-mode / --tools).
        # No additional secret env vars are injected.
        del read_only
        return {}

    def permission_enforcement_method(self) -> str:
        """Classify read-only enforcement (connector-local metadata)."""

        return "native_permission_mode+tool_filtering"

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
        executable = shutil.which("claude")
        if not executable:
            return DiscoveryResult(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                reason_code="executable_not_found",
                details={"detail": "claude not found", "node_id": context.node_id},
            )
        version, broken = await self._probe_version_and_health(executable)
        if broken:
            return DiscoveryResult(
                executable_path=executable,
                version=None,
                fingerprint=executable_fingerprint(executable),
                installed=True,
                usable=False,
                reason_code="broken_executable",
                details={
                    "node_id": context.node_id,
                    "detail": broken,
                    "status": "broken_executable",
                },
            )
        if version is None:
            return DiscoveryResult(
                executable_path=executable,
                version=None,
                fingerprint=executable_fingerprint(executable),
                installed=True,
                usable=False,
                reason_code="unsupported_version",
                details={
                    "node_id": context.node_id,
                    "detail": "unable to parse Claude Code version",
                },
            )
        return DiscoveryResult(
            executable_path=executable,
            version=version,
            fingerprint=executable_fingerprint(executable),
            installed=True,
            usable=True,
            reason_code=None,
            details={"node_id": context.node_id, "installation_path": executable},
        )

    async def inspect_authentication(
        self, context: ConnectorExecutionContext
    ) -> AuthenticationResult:
        evidence = await self.verify_authentication(context)
        return AuthenticationResult(
            authenticated=evidence.status == "authenticated",
            method_id=evidence.method_id,
            detail=str(evidence.details.get("detail", "")),
            version=evidence.version,
            fingerprint=evidence.fingerprint,
        )

    async def build_authentication_plan(self, context: ConnectorExecutionContext) -> ConnectorPlan:
        definition = self.catalogue.get(_CATALOGUE_ID)
        method = next(item for item in definition.authentication_methods if item.login_argv)
        argv = method.login_argv
        plan_id = str(uuid4())
        payload = {
            "plan_id": plan_id,
            "connector_id": self.connector_id,
            "action": "authenticate",
            "argv": list(argv),
            "node_id": context.node_id,
        }
        digest = hashlib.sha256(str(sorted(payload.items())).encode()).hexdigest()
        return ConnectorPlan(
            plan_id=plan_id,
            connector_id=self.connector_id,
            connector_revision=self.connector_revision,
            action="authenticate",
            executable=argv[0],
            arguments=tuple(argv[1:]),
            plan_hash=digest,
            expires_at=utc_now() + timedelta(minutes=15),
            risk_level="medium",
        )

    async def verify_authentication(
        self, context: ConnectorExecutionContext
    ) -> AuthenticationEvidence:
        definition = self.catalogue.get(_CATALOGUE_ID)
        method = next(
            (item for item in definition.authentication_methods if item.status_argv),
            None,
        )
        if method is None or not method.status_argv:
            # Fallback to observed CLI contract when catalogue lacks status_argv.
            status_argv: Sequence[str] = ("claude", "auth", "status", "--json")
            method_id = "claude-account"
        else:
            status_argv = tuple(method.status_argv)
            method_id = method.id
        process = await asyncio.create_subprocess_exec(
            *status_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode() + stderr.decode()
        status = self.classify_auth_status(
            output, returncode=process.returncode if process.returncode is not None else 1
        )
        auth_mode = classify_claude_auth_mode(output, returncode=process.returncode or 1)
        executable = shutil.which(status_argv[0]) or status_argv[0]
        version, _broken = await self._probe_version_and_health(executable)
        override = detect_provider_override()
        details: dict[str, Any] = {
            "detail": _redact(output.strip()[:500]),
            "node_id": context.node_id,
            "auth_mode": auth_mode,
            "permission_enforcement": self.permission_enforcement_method(),
        }
        if override:
            details["provider_override"] = override
            details["provider_notice"] = "connector_provider_override_active"
        # API-key presence alone is not authentication; auth status JSON must agree.
        if status == "unauthenticated" and os.environ.get("ANTHROPIC_API_KEY"):
            details["api_key_env_present"] = True
            details["billing_risk"] = "api_key_possibly_billable"
        return AuthenticationEvidence(
            status=status,
            method_id=method_id,
            executable_path=executable,
            fingerprint=executable_fingerprint(executable),
            version=version,
            details=details,
        )

    def classify_auth_status(self, output: str, *, returncode: int) -> str:
        return classify_claude_auth_status(output, returncode=returncode)

    async def verify_adapter(self, context: ConnectorExecutionContext) -> AdapterVerificationResult:
        discovery = await self.discover(context)
        if not discovery.usable or not discovery.executable_path:
            return AdapterVerificationResult(
                False,
                discovery.executable_path,
                discovery.fingerprint,
                discovery.version,
                {
                    "detail": discovery.details.get("detail") or discovery.reason_code,
                    "reason_code": discovery.reason_code,
                    "node_id": context.node_id,
                },
            )
        resolved = discovery.executable_path
        process = await asyncio.create_subprocess_exec(
            resolved,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        help_text = (stdout.decode() + stderr.decode()).lower()
        passed = process.returncode == 0 and "--print" in help_text and "stream-json" in help_text
        return AdapterVerificationResult(
            passed=passed,
            executable_path=resolved,
            fingerprint=executable_fingerprint(resolved),
            version=discovery.version,
            details={
                "structured_output_supported": "stream-json" in help_text,
                "permission_mode_supported": "permission-mode" in help_text,
                "tools_flag_supported": "--tools" in help_text,
                "node_id": context.node_id,
            },
        )

    def build_exec_argv(
        self,
        *,
        executable: str,
        prompt: str,
        workspace_path: str,
        read_only: bool = True,
    ) -> Sequence[str]:
        del workspace_path  # Enforced by process cwd in execute / live-test.
        argv: list[str] = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if read_only:
            argv.extend(
                [
                    "--permission-mode",
                    "plan",
                    "--tools",
                    ",".join(_READ_ONLY_TOOLS),
                    "--disallowedTools",
                    ",".join(_DISALLOWED_TOOLS),
                ]
            )
        argv.append(prompt)
        return tuple(argv)

    def build_read_only_cert_argv(
        self,
        *,
        executable: str,
        prompt: str,
        workspace: Path,
    ) -> Sequence[str]:
        return self.build_exec_argv(
            executable=executable,
            prompt=prompt,
            workspace_path=str(workspace),
            read_only=True,
        )

    def parse_events(self, output: str) -> Sequence[Mapping[str, Any]]:
        return parse_claude_stream_json(output)

    async def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]:
        del context
        executable = shutil.which("claude")
        if not executable:
            raise RuntimeError("claude not found")
        argv = list(
            self.build_exec_argv(
                executable=executable,
                prompt=request.prompt,
                workspace_path=request.workspace_path,
                read_only=True,
            )
        )
        extra = dict(self.execution_environment(read_only=True))
        env = filter_environment(extra_keys=frozenset(extra))
        env.update(extra)
        sequence = 0
        sequence += 1
        yield HarnessEvent(
            event_type="task.started",
            sequence=sequence,
            payload={"argv": [*argv[:-1], "<PROMPT>"], "connector_id": self.connector_id},
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=request.workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self._processes[request.execution_id] = process
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=request.timeout_seconds
            )
        except TimeoutError:
            await terminate_process_tree(process)
            sequence += 1
            yield HarnessEvent(
                event_type="task.failed",
                sequence=sequence,
                payload={"reason": "timeout"},
            )
            return
        finally:
            self._processes.pop(request.execution_id, None)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        for item in self.parse_events(output):
            sequence += 1
            yield HarnessEvent(
                event_type="task.progress",
                sequence=sequence,
                payload={
                    "connector_event": item.get("event_type") or item.get("type"),
                    "redacted": True,
                },
            )
        sequence += 1
        yield HarnessEvent(
            event_type="task.succeeded" if process.returncode == 0 else "task.failed",
            sequence=sequence,
            payload={
                "returncode": process.returncode,
                "output_digest": hashlib.sha256(output.encode()).hexdigest(),
            },
        )

    async def cancel(
        self, execution_id: str, context: ConnectorExecutionContext
    ) -> CancellationResult:
        del context
        process = self._processes.get(execution_id)
        if process is None:
            return CancellationResult(True, False, "no active process")
        result = await terminate_process_tree(process)
        return CancellationResult(
            cancelled=bool(result.get("cancelled")),
            lingering=bool(result.get("lingering")),
            detail=str(result.get("signal")),
        )

    async def _probe_version_and_health(self, executable: str) -> tuple[str | None, str | None]:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=filter_environment(),
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except OSError as exc:
            return None, str(exc)
        except TimeoutError:
            return None, "Claude Code version probe timed out"
        combined = (stdout.decode() + stderr.decode()).strip()
        lowered = combined.lower()
        if process.returncode != 0 and (
            "enoent" in lowered
            or "not found" in lowered
            or "cannot execute" in lowered
            or "mach-o" in lowered
            or "wrong architecture" in lowered
        ):
            return None, "Claude Code launcher is broken or not executable"
        if not combined:
            return None, None
        return combined.splitlines()[0][:200], None


def detect_provider_override() -> dict[str, str] | None:
    """Return sanitised provider-override env names present (no values)."""

    present = [name for name in _PROVIDER_OVERRIDE_ENV if os.environ.get(name)]
    if not present:
        return None
    return {"env_keys": ",".join(present)}


def classify_claude_auth_mode(output: str, *, returncode: int) -> str:
    """Fine-grained auth mode for connector details (not the neutral status)."""

    text = output.strip()
    lowered = text.lower()
    if "rate limit" in lowered:
        return "connector_rate_limited"
    if "quota" in lowered or "usage limit" in lowered:
        return "connector_quota_exhausted"
    if "plan" in lowered and ("restrict" in lowered or "limit" in lowered):
        return "connector_plan_restriction"
    data = _maybe_json(text)
    if isinstance(data, dict):
        if data.get("loggedIn") is True:
            method = str(data.get("authMethod") or "").lower()
            provider = str(data.get("apiProvider") or "").lower()
            if provider and provider not in {"firstparty", "first_party", "anthropic", ""}:
                return "authenticated_provider_override"
            if method in {"api_key", "apikey", "api-key", "env"}:
                return "authenticated_api_key"
            if method in {"oauth", "subscription", "claudeai", "claude_ai", "claude.ai"}:
                return "authenticated_subscription"
            if method and method != "none":
                return "authenticated_subscription"
            return "authenticated_subscription"
        if data.get("loggedIn") is False or str(data.get("authMethod") or "").lower() == "none":
            return "authentication_required"
    if returncode != 0:
        if "invalid" in lowered or "config" in lowered:
            return "configuration_invalid"
        return "authentication_unknown"
    if "not logged" in lowered or "please run" in lowered or "login" in lowered:
        return "authentication_required"
    return "authentication_unknown"


def classify_claude_auth_status(output: str, *, returncode: int) -> str:
    """Map Claude auth output into connector-neutral status strings."""

    mode = classify_claude_auth_mode(output, returncode=returncode)
    if mode.startswith("authenticated_"):
        return "authenticated"
    if mode == "connector_rate_limited":
        return "plan_restricted"
    if mode == "connector_quota_exhausted":
        return "quota_exhausted"
    if mode == "connector_plan_restriction":
        return "plan_restricted"
    if mode == "configuration_invalid":
        return "configuration_invalid"
    if mode == "authentication_required":
        return "unauthenticated"
    text = output.lower()
    if "rate limit" in text:
        return "plan_restricted"
    if "quota" in text or "usage limit" in text:
        return "quota_exhausted"
    if returncode != 0 and not text.strip():
        return "unauthenticated"
    return "unauthenticated"


def parse_claude_stream_json(output: str) -> list[dict[str, Any]]:
    """Normalize Claude stream-json / JSONL into connector-neutral event maps."""

    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            # Map highly specific textual denials when no JSON exists.
            lowered = line.lower()
            if "permission" in lowered and "denied" in lowered:
                events.append(
                    {
                        "event_type": "permission.denied",
                        "native_type": "text",
                        "reason_code": "permission_denied",
                        "error": line[:300],
                    }
                )
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        events.extend(_normalize_claude_payload(payload))
    return events


def _normalize_claude_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    native = str(payload.get("type") or "")
    events: list[dict[str, Any]] = []

    if native == "system" and str(payload.get("subtype") or "") == "init":
        events.append(
            {
                "event_type": "run.started",
                "native_type": native,
                "session_id": payload.get("session_id"),
                "tools": payload.get("tools"),
                "model": payload.get("model"),
                "permission_mode": payload.get("permissionMode"),
                "api_key_source": payload.get("apiKeySource"),
            }
        )
        return events

    if native in {"assistant", "user"}:
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "text":
                    text = block.get("text")
                    events.append(
                        {
                            "event_type": "message.output",
                            "native_type": native,
                            "text": text,
                            "session_id": payload.get("session_id"),
                        }
                    )
                    events.extend(_denials_from_text(str(text or "")))
                elif block_type == "tool_use":
                    tool = str(block.get("name") or "")
                    events.append(
                        {
                            "event_type": "tool.call",
                            "native_type": native,
                            "tool": tool,
                            "status": "requested",
                            "session_id": payload.get("session_id"),
                        }
                    )
                elif block_type == "tool_result":
                    events.extend(
                        _events_from_tool_result(block, session_id=payload.get("session_id"))
                    )
        return events

    if native == "result":
        subtype = str(payload.get("subtype") or "")
        is_error = bool(payload.get("is_error"))
        events.append(
            {
                "event_type": "run.failed" if is_error or subtype == "error" else "run.completed",
                "native_type": native,
                "session_id": payload.get("session_id"),
                "usage": payload.get("usage"),
                "result": _redact(str(payload.get("result") or "")[:500]),
                "error": _redact(str(payload.get("error") or "")[:300]) or None,
            }
        )
        denials = payload.get("permission_denials")
        if isinstance(denials, list):
            for item in denials:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool_name") or item.get("tool") or "")
                events.append(
                    {
                        "event_type": "permission.denied",
                        "native_type": "permission_denials",
                        "tool": tool,
                        "status": "denied",
                        "reason_code": _reason_for_tool(tool),
                        "error": f"permission denied for tool {tool}",
                    }
                )
        error_text = str(payload.get("error") or payload.get("result") or "")
        events.extend(_denials_from_text(error_text))
        events.extend(_status_notices_from_text(error_text))
        return events

    if native == "stream_event":
        # Incremental deltas are ignored unless they carry a complete text payload.
        return events

    events.append({"event_type": "message.output", "native_type": native or "unknown"})
    return events


def _events_from_tool_result(block: dict[str, Any], *, session_id: object) -> list[dict[str, Any]]:
    content = block.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    is_error = bool(block.get("is_error"))
    lowered = text.lower()
    denied = is_error or any(
        token in lowered
        for token in ("permission denied", "not allowed", "disallowed", "unavailable tool")
    )
    if denied:
        tool = _tool_from_denial_text(text) or "unknown"
        return [
            {
                "event_type": "permission.denied",
                "native_type": "tool_result",
                "tool": tool,
                "status": "denied",
                "reason_code": _reason_for_tool(tool),
                "error": text[:300],
                "session_id": session_id,
            }
        ]
    return [
        {
            "event_type": "tool.call",
            "native_type": "tool_result",
            "status": "completed",
            "session_id": session_id,
        }
    ]


def _denials_from_text(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    events: list[dict[str, Any]] = []
    checks = (
        (
            "permission_denied_edit",
            ("edit", "write", "create file", "modify file", "delete file"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unable"),
        ),
        (
            "permission_denied_shell",
            ("bash", "shell", "terminal"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unavailable"),
        ),
        (
            "permission_denied_network",
            ("webfetch", "websearch", "web fetch", "web search", "network", "http"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unavailable"),
        ),
        (
            "permission_denied_external_path",
            ("outside", "external path", "outside the workspace", "add-dir"),
            ("not allowed", "denied", "disallowed", "cannot", "can't"),
        ),
        (
            "permission_denied_subprocess",
            ("subprocess", "spawn", "launch another process"),
            ("not allowed", "denied", "disallowed", "cannot", "can't"),
        ),
    )
    for reason, subjects, verbs in checks:
        if any(s in lowered for s in subjects) and any(v in lowered for v in verbs):
            events.append(
                {
                    "event_type": "permission.denied",
                    "native_type": "text",
                    "reason_code": reason,
                    "error": text[:300],
                }
            )
    return events


def _status_notices_from_text(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    if "rate limit" in lowered or "too many requests" in lowered:
        return [
            {
                "event_type": "message.output",
                "native_type": "text",
                "text": text[:300],
                "reason_code": "connector_rate_limited",
            }
        ]
    if "quota" in lowered or "usage limit" in lowered:
        return [
            {
                "event_type": "message.output",
                "native_type": "text",
                "text": text[:300],
                "reason_code": "connector_quota_exhausted",
            }
        ]
    if "not logged" in lowered or "please run /login" in lowered or "authentication" in lowered:
        return [
            {
                "event_type": "message.output",
                "native_type": "text",
                "text": text[:300],
                "reason_code": "connector_auth_required",
            }
        ]
    return []


def _reason_for_tool(tool: str) -> str:
    name = tool.lower()
    if name in {"edit", "write", "multiedit", "notebookedit"}:
        return "permission_denied_edit"
    if name in {"bash", "shell", "terminal"}:
        return "permission_denied_shell"
    if name in {"webfetch", "websearch", "web_search", "web_fetch"}:
        return "permission_denied_network"
    if name in {"agent", "task"}:
        return "permission_denied_subprocess"
    return "permission_denied"


def _tool_from_denial_text(text: str) -> str | None:
    match = re.search(r"unavailable tool ['\"]?([A-Za-z0-9_-]+)", text, flags=re.I)
    if match:
        return match.group(1)
    match = re.search(
        r"tool ['\"]?([A-Za-z0-9_-]+)['\"]? (?:is )?(?:not allowed|denied)",
        text,
        flags=re.I,
    )
    if match:
        return match.group(1)
    for tool in ("Bash", "Edit", "Write", "WebFetch", "WebSearch", "Agent", "MultiEdit"):
        if tool.lower() in text.lower():
            return tool
    return None


def _maybe_json(text: str) -> object | None:
    try:
        parsed: object = json.loads(text)
        return parsed
    except json.JSONDecodeError:
        # auth status may wrap JSON among ANSI/log noise
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed
            except json.JSONDecodeError:
                return None
        return None


def _redact(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("api_key", "token", "bearer", "cookie", "sk-ant", "sk-")):
        return "[redacted status]"
    return text
