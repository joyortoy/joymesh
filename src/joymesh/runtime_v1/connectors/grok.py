"""Grok Build connector implementing the generic ConnectorRuntime protocol.

CLI contract observed on ``@xai-official/grok`` 0.2.114:

* Executable: ``grok``
* Version: ``grok --version`` / ``-v``
* Non-interactive: ``grok --no-auto-update -p <prompt> --output-format streaming-json``
* Auth probe: ``grok models`` (prints authentication state)
* Login: ``grok login`` / ``grok login --device-code``
* Workspace: ``--cwd`` and process cwd
* Read-only: ``--sandbox strict`` + ``--permission-mode plan`` + tool allow/deny
  lists + ``--disable-web-search`` + ``--no-subagents``
* Structured output: JSONL with ``text`` / ``thought`` / ``end`` / ``error``
* ACP: ``grok agent stdio`` (capability only; certification uses CLI subprocess)
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

_CATALOGUE_ID = "grok"

_READ_ONLY_TOOLS = ("read_file", "grep", "list_dir")
_DISALLOWED_TOOLS = (
    "search_replace",
    "run_terminal_cmd",
    "web_search",
    "web_fetch",
    "Agent",
)

_PROVIDER_OVERRIDE_ENV = (
    "GROK_CLI_CHAT_PROXY_BASE_URL",
    "XAI_API_BASE_URL",
    "GROK_OIDC_ISSUER",
)


class GrokConnectorRuntime:
    """Grok Build discovery, auth, argv, streaming-json parsing, and cancellation."""

    connector_id = "grok"
    display_name = "Grok Build"

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
                "Grok Build read-only mode uses --sandbox strict with plan permission "
                "mode and tool allow/deny lists; subscription or API usage may incur "
                "cost. Telemetry is disabled for certification via environment policy. "
                "macOS sandbox does not block child-process network (Linux does)."
            ),
            recoverable=True,
            recommended_action="approve_and_continue",
        )

    def permission_enforcement_method(self) -> str:
        return "native_sandbox+native_permission_rules+tool_filtering"

    def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
        if not read_only:
            return {}
        # Bound product analytics / trace upload for certification runs.
        return {
            "GROK_TELEMETRY_ENABLED": "0",
            "GROK_TELEMETRY_TRACE_UPLOAD": "0",
        }

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
        executable = shutil.which("grok")
        if not executable:
            return DiscoveryResult(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                reason_code="executable_not_found",
                details={"detail": "grok not found", "node_id": context.node_id},
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
                    "detail": "unable to parse Grok Build version",
                },
            )
        privacy = classify_grok_privacy_state()
        return DiscoveryResult(
            executable_path=executable,
            version=version,
            fingerprint=executable_fingerprint(executable),
            installed=True,
            usable=True,
            reason_code=None,
            details={
                "node_id": context.node_id,
                "installation_path": executable,
                "acp_supported": True,
                "acp_command": "grok agent stdio",
                "privacy": privacy,
            },
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
            status_argv: Sequence[str] = ("grok", "models")
            method_id = "grok-account"
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
        auth_mode = classify_grok_auth_mode(output, returncode=process.returncode or 1)
        # API key env alone is not enough — models output must agree when key is set.
        if status == "unauthenticated" and os.environ.get("XAI_API_KEY"):
            auth_mode = "authenticated_api_key"
            # Still unauthenticated until models succeeds; flag billing risk.
        executable = shutil.which(status_argv[0]) or status_argv[0]
        version, _broken = await self._probe_version_and_health(executable)
        override = detect_provider_override()
        privacy = classify_grok_privacy_state()
        details: dict[str, Any] = {
            "detail": _redact(output.strip()[:500]),
            "node_id": context.node_id,
            "auth_mode": auth_mode,
            "permission_enforcement": self.permission_enforcement_method(),
            "privacy": privacy,
            "billing_risk": _billing_risk(auth_mode),
            "default_model": _default_model_from_models(output),
        }
        if override:
            details["provider_override"] = override
            details["provider_notice"] = "connector_provider_override_active"
        if os.environ.get("XAI_API_KEY"):
            details["api_key_env_present"] = True
        return AuthenticationEvidence(
            status=status,
            method_id=method_id,
            executable_path=executable,
            fingerprint=executable_fingerprint(executable),
            version=version,
            details=details,
        )

    def classify_auth_status(self, output: str, *, returncode: int) -> str:
        return classify_grok_auth_status(output, returncode=returncode)

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
        passed = (
            process.returncode == 0
            and "--single" in help_text
            and "streaming-json" in help_text
            and "--sandbox" in help_text
        )
        return AdapterVerificationResult(
            passed=passed,
            executable_path=resolved,
            fingerprint=executable_fingerprint(resolved),
            version=discovery.version,
            details={
                "structured_output_supported": "streaming-json" in help_text,
                "sandbox_supported": "--sandbox" in help_text,
                "acp_supported": "agent" in help_text,
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
        argv: list[str] = [
            executable,
            "--no-auto-update",
            "-p",
            prompt,
            "--output-format",
            "streaming-json",
            "--cwd",
            workspace_path,
        ]
        if read_only:
            argv.extend(
                [
                    "--sandbox",
                    "strict",
                    "--permission-mode",
                    "plan",
                    "--tools",
                    ",".join(_READ_ONLY_TOOLS),
                    "--disallowed-tools",
                    ",".join(_DISALLOWED_TOOLS),
                    "--disable-web-search",
                    "--no-subagents",
                    "--deny",
                    "Bash",
                    "--deny",
                    "Edit",
                    "--deny",
                    "Write",
                    "--deny",
                    "MCPTool",
                ]
            )
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
        return parse_grok_streaming_json(output)

    async def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]:
        del context
        executable = shutil.which("grok")
        if not executable:
            raise RuntimeError("grok not found")
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
            payload={"argv": _redact_argv(argv), "connector_id": self.connector_id},
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
            return None, "Grok Build version probe timed out"
        combined = (stdout.decode() + stderr.decode()).strip()
        lowered = combined.lower()
        if process.returncode != 0 and (
            "enoent" in lowered
            or "not found" in lowered
            or "cannot execute" in lowered
            or "mach-o" in lowered
            or "wrong architecture" in lowered
        ):
            return None, "Grok Build launcher is broken or not executable"
        if not combined:
            return None, None
        return combined.splitlines()[0][:200], None


def detect_provider_override() -> dict[str, str] | None:
    present = [name for name in _PROVIDER_OVERRIDE_ENV if os.environ.get(name)]
    if not present:
        return None
    return {"env_keys": ",".join(present)}


def classify_grok_privacy_state() -> dict[str, str]:
    """Sanitised privacy/telemetry assessment (no secret values)."""

    telemetry = os.environ.get("GROK_TELEMETRY_ENABLED")
    config = _read_config_keys()
    telemetry_cfg = config.get("telemetry")
    features_telemetry = config.get("features.telemetry")
    if telemetry == "0" or telemetry_cfg == "false" or features_telemetry == "false":
        telemetry_state = "telemetry_disabled"
    elif telemetry == "1" or telemetry_cfg == "true" or features_telemetry == "true":
        telemetry_state = "telemetry_enabled"
    else:
        telemetry_state = "telemetry_disabled"  # product default for new installs often off
    # No conclusive CLI switch for mandatory cloud codebase upload; local indexing only.
    # Trace-upload env is not equivalent to repository upload.
    upload_state = "remote_repository_upload_unknown"
    return {
        "telemetry": telemetry_state,
        "repository_upload": upload_state,
        "retention": "retention_policy_unknown",
        "codebase_indexing": str(config.get("features.codebase_indexing") or "unknown"),
    }


def classify_grok_auth_mode(output: str, *, returncode: int) -> str:
    text = output.strip()
    lowered = text.lower()
    if "rate limit" in lowered:
        return "connector_rate_limited"
    if "quota" in lowered or "usage limit" in lowered:
        return "connector_quota_exhausted"
    if "plan" in lowered and ("restrict" in lowered or "upgrade" in lowered):
        return "connector_plan_restriction"
    if "not authenticated" in lowered or "not signed in" in lowered:
        if os.environ.get("XAI_API_KEY"):
            return "authenticated_api_key"
        return "authentication_required"
    if detect_provider_override():
        if "available models" in lowered or "default model" in lowered:
            return "authenticated_provider_override"
    if "available models" in lowered or "default model" in lowered:
        if os.environ.get("XAI_API_KEY"):
            return "authenticated_api_key"
        if "free" in lowered and "trial" in lowered:
            return "authenticated_free_access"
        return "authenticated_subscription"
    if returncode != 0:
        if "invalid" in lowered or "config" in lowered:
            return "configuration_invalid"
        return "authentication_unknown"
    return "authentication_unknown"


def classify_grok_auth_status(output: str, *, returncode: int) -> str:
    mode = classify_grok_auth_mode(output, returncode=returncode)
    if mode.startswith("authenticated_"):
        # API key env without successful models access stays unauthenticated.
        if mode == "authenticated_api_key" and (
            "not authenticated" in output.lower() or "not signed in" in output.lower()
        ):
            return "unauthenticated"
        if "not authenticated" in output.lower() or "not signed in" in output.lower():
            return "unauthenticated"
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
    if "quota" in text:
        return "quota_exhausted"
    return "unauthenticated"


def parse_grok_streaming_json(output: str) -> list[dict[str, Any]]:
    """Normalize Grok streaming-json / JSONL into connector-neutral event maps."""

    events: list[dict[str, Any]] = []
    started = False
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            events.extend(_denials_from_text(line))
            events.extend(_status_notices_from_text(line))
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        native = str(payload.get("type") or "")
        if not started:
            events.append(
                {
                    "event_type": "run.started",
                    "native_type": "stream",
                }
            )
            started = True
        if native == "text":
            text = payload.get("data") or payload.get("text")
            events.append(
                {
                    "event_type": "message.output",
                    "native_type": native,
                    "text": text,
                }
            )
            events.extend(_denials_from_text(str(text or "")))
        elif native == "thought":
            events.append(
                {
                    "event_type": "message.output",
                    "native_type": native,
                    "text": payload.get("data"),
                    "reasoning": True,
                }
            )
        elif native == "end":
            events.append(
                {
                    "event_type": "run.completed",
                    "native_type": native,
                    "session_id": payload.get("sessionId") or payload.get("session_id"),
                    "usage": payload.get("usage"),
                    "stop_reason": payload.get("stopReason"),
                }
            )
        elif native == "error":
            message = str(payload.get("message") or payload.get("error") or "")
            events.append(
                {
                    "event_type": "run.failed",
                    "native_type": native,
                    "error": _redact(message[:300]),
                }
            )
            events.extend(_denials_from_text(message))
            events.extend(_status_notices_from_text(message))
        elif native in {"tool", "tool_use", "tool_call", "tool_result"}:
            events.extend(_events_from_tool_payload(payload))
        elif native == "permission_denied":
            tool = str(payload.get("tool") or payload.get("tool_name") or "")
            events.append(
                {
                    "event_type": "permission.denied",
                    "native_type": native,
                    "tool": tool,
                    "reason_code": _reason_for_tool(tool),
                    "error": _redact(str(payload.get("message") or "")[:300]),
                }
            )
        else:
            # Forward-compatible unknown events.
            events.append({"event_type": "message.output", "native_type": native or "unknown"})
        # Also map free-form denial fields.
        if isinstance(payload.get("permission_denials"), list):
            for item in payload["permission_denials"]:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool_name") or item.get("tool") or "")
                events.append(
                    {
                        "event_type": "permission.denied",
                        "native_type": "permission_denials",
                        "tool": tool,
                        "reason_code": _reason_for_tool(tool),
                        "error": f"permission denied for tool {tool}",
                    }
                )
    return events


def _events_from_tool_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_data = payload.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    tool = str(
        payload.get("tool")
        or payload.get("name")
        or payload.get("tool_name")
        or data.get("name")
        or ""
    )
    status = str(payload.get("status") or "requested")
    text = str(payload.get("message") or payload.get("error") or payload.get("output") or "")
    lowered = text.lower()
    if status in {"denied", "error", "failed"} or any(
        token in lowered for token in ("permission denied", "not allowed", "disallowed")
    ):
        return [
            {
                "event_type": "permission.denied",
                "native_type": str(payload.get("type") or "tool"),
                "tool": tool,
                "status": "denied",
                "reason_code": _reason_for_tool(tool),
                "error": text[:300],
            }
        ]
    return [
        {
            "event_type": "tool.call",
            "native_type": str(payload.get("type") or "tool"),
            "tool": tool,
            "status": status,
        }
    ]


def _denials_from_text(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    events: list[dict[str, Any]] = []
    checks = (
        (
            "permission_denied_edit",
            ("edit", "write", "search_replace", "create file", "modify file", "delete file"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unable", "blocked"),
        ),
        (
            "permission_denied_shell",
            ("bash", "shell", "terminal", "run_terminal_cmd"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unavailable", "blocked"),
        ),
        (
            "permission_denied_network",
            ("web_search", "web_fetch", "webfetch", "websearch", "network", "http", "url"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "unavailable", "blocked"),
        ),
        (
            "permission_denied_external_path",
            ("outside", "external path", "outside the workspace", "strict sandbox"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "blocked"),
        ),
        (
            "permission_denied_subprocess",
            ("subprocess", "spawn", "launch another process"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "blocked"),
        ),
        (
            "permission_denied_plugin",
            ("plugin", "marketplace"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "blocked"),
        ),
        (
            "permission_denied_mcp",
            ("mcp", "mcp server", "mcptool"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "blocked"),
        ),
        (
            "permission_denied_subagent",
            ("subagent", "agent(", "no-subagents"),
            ("not allowed", "denied", "disallowed", "cannot", "can't", "blocked"),
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
    if "not signed in" in lowered or "not authenticated" in lowered:
        return [
            {
                "event_type": "message.output",
                "native_type": "text",
                "text": text[:300],
                "reason_code": "connector_auth_required",
            }
        ]
    if "repository upload" in lowered or "codebase upload" in lowered:
        return [
            {
                "event_type": "message.output",
                "native_type": "text",
                "text": text[:300],
                "reason_code": "remote_repository_upload_active",
            }
        ]
    return []


def _reason_for_tool(tool: str) -> str:
    name = tool.lower().replace("-", "_")
    if name in {"search_replace", "edit", "write", "multiedit", "notebookedit"}:
        return "permission_denied_edit"
    if name in {"run_terminal_cmd", "bash", "shell", "terminal"}:
        return "permission_denied_shell"
    if name in {"web_search", "web_fetch", "webfetch", "websearch"}:
        return "permission_denied_network"
    if name in {"agent"} or name.startswith("agent"):
        return "permission_denied_subagent"
    if "mcp" in name:
        return "permission_denied_mcp"
    if "plugin" in name:
        return "permission_denied_plugin"
    return "permission_denied"


def _billing_risk(auth_mode: str) -> str:
    if auth_mode == "authenticated_api_key":
        return "api_key_possibly_billable"
    if auth_mode == "authenticated_free_access":
        return "free_allowance"
    if auth_mode == "authenticated_subscription":
        return "subscription_backed"
    if auth_mode == "authenticated_provider_override":
        return "provider_override_unknown_cost"
    return "unknown"


def _default_model_from_models(output: str) -> str | None:
    match = re.search(r"Default model:\s*(\S+)", output)
    if match:
        return match.group(1)
    return None


def _read_config_keys() -> dict[str, str]:
    cfg = Path.home() / ".grok" / "config.toml"
    if not cfg.exists():
        return {}
    out: dict[str, str] = {}
    section = ""
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip()
            continue
        if "=" in s:
            key, value = s.split("=", 1)
            key = key.strip()
            value = value.split("#", 1)[0].strip().strip('"').strip("'")
            full = f"{section}.{key}" if section else key
            # Never store long values or secret-looking keys.
            if any(t in key.lower() for t in ("key", "token", "secret", "password")):
                continue
            out[full if section else key] = value[:80]
            if section:
                out[key] = value[:80]
    return out


def _redact_argv(argv: Sequence[str]) -> list[str]:
    if not argv:
        return []
    return [*list(argv[:-1]), "<PROMPT>"]


def _redact(text: str) -> str:
    lowered = text.lower()
    if any(
        token in lowered
        for token in ("api_key", "xai-", "token", "bearer", "cookie", "sk-", "authorization")
    ):
        return "[redacted status]"
    return text
