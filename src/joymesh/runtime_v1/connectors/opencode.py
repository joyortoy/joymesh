"""OpenCode connector implementing the generic ConnectorRuntime protocol.

CLI behaviour (from https://opencode.ai/docs/cli/ and --format json cheatsheets):

* Executable: ``opencode``
* Version: ``opencode --version`` / ``-v``
* Non-interactive: ``opencode run --format json --dir <workspace> <prompt>``
* Auth status: ``opencode auth list`` (also ``auth ls``)
* Structured output: JSONL on stdout with types ``step_start``, ``tool_use``,
  ``text``, ``step_finish``
* Read-only: no dedicated sandbox flag; use ``OPENCODE_PERMISSION`` JSON to deny
  ``edit`` / ``bash`` / network tools while allowing ``read`` / ``glob`` / ``grep``
* Workspace: ``--dir`` sets the project directory; also used as process cwd
* Exit: non-zero on failure; rate-limit/quota messages appear in text/stderr
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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

_READ_ONLY_PERMISSION = {
    "edit": "deny",
    "bash": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "task": "deny",
    "external_directory": "deny",
    "doom_loop": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
}


class OpenCodeConnectorRuntime:
    """OpenCode-specific discovery, auth, argv, JSONL parsing, and cancellation."""

    connector_id = "opencode"
    display_name = "OpenCode"

    def __init__(self, catalogue: ConnectorCatalogue | None = None) -> None:
        self.catalogue = catalogue or ConnectorCatalogue.builtins()
        definition = self.catalogue.get(self.connector_id)
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
                "OpenCode read-only mode is enforced via OPENCODE_PERMISSION deny rules "
                "for edit/bash/network tools; provider usage may still incur cost."
            ),
            recoverable=True,
            recommended_action="approve_and_continue",
        )

    def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
        if not read_only:
            return {}
        return {"OPENCODE_PERMISSION": json.dumps(_READ_ONLY_PERMISSION, sort_keys=True)}

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
        executable = shutil.which("opencode")
        if not executable:
            return DiscoveryResult(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                reason_code="executable_not_found",
                details={"detail": "opencode not found", "node_id": context.node_id},
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
                    "detail": "unable to parse OpenCode version",
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
        definition = self.catalogue.get(self.connector_id)
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
        definition = self.catalogue.get(self.connector_id)
        method = next(item for item in definition.authentication_methods if item.status_argv)
        status_argv = method.status_argv
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
        executable = shutil.which(status_argv[0]) or status_argv[0]
        version, _broken = await self._probe_version_and_health(executable)
        detail = _redact(output.strip()[:500])
        # OpenCode Zen exposes free models without stored credentials. When auth
        # list reports an empty credential store, probe model availability before
        # treating the connector as unauthenticated.
        if status == "unauthenticated":
            free_access = await self._probe_anonymous_provider_access(executable)
            if free_access:
                status = "authenticated"
                detail = (
                    "OpenCode Zen free models available without stored credentials "
                    f"(auth list: {detail or 'empty'})"
                )
        return AuthenticationEvidence(
            status=status,
            method_id=method.id,
            executable_path=executable,
            fingerprint=executable_fingerprint(executable),
            version=version,
            details={
                "detail": detail,
                "node_id": context.node_id,
            },
        )

    def classify_auth_status(self, output: str, *, returncode: int) -> str:
        return classify_opencode_auth_status(output, returncode=returncode)

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
            "run",
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        help_text = (stdout.decode() + stderr.decode()).lower()
        passed = process.returncode == 0 and "--format" in help_text
        return AdapterVerificationResult(
            passed=passed,
            executable_path=resolved,
            fingerprint=executable_fingerprint(resolved),
            version=discovery.version,
            details={
                "structured_output_supported": "json" in help_text,
                "dir_flag_supported": "--dir" in help_text,
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
        del read_only  # Enforced via execution_environment OPENCODE_PERMISSION.
        return (
            executable,
            "run",
            "--format",
            "json",
            "--dir",
            workspace_path,
            prompt,
        )

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
        return parse_opencode_jsonl(output)

    async def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]:
        del context
        executable = shutil.which("opencode")
        if not executable:
            raise RuntimeError("opencode not found")
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
            stdout, stderr = await process.communicate()
        except OSError as exc:
            return None, str(exc)
        combined = (stdout.decode() + stderr.decode()).strip()
        lowered = combined.lower()
        if process.returncode != 0 and (
            "enoent" in lowered
            or "not found" in lowered
            or "cannot execute" in lowered
            or "mach-o" in lowered
            or "wrong architecture" in lowered
        ):
            return None, "OpenCode launcher is broken or not executable"
        if not combined:
            return None, None
        return combined.splitlines()[0][:200], None

    async def _probe_anonymous_provider_access(self, executable: str) -> bool:
        """Return True when OpenCode exposes usable free/anonymous provider models."""

        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=filter_environment(),
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except (OSError, TimeoutError):
            return False
        if process.returncode not in (0, None):
            return False
        text = (stdout.decode() + stderr.decode()).lower()
        return "opencode/" in text and ("-free" in text or "big-pickle" in text)


def classify_opencode_auth_status(output: str, *, returncode: int) -> str:
    text = output.lower()
    if "quota" in text or "usage limit" in text:
        return "quota_exhausted"
    if "rate limit" in text:
        return "plan_restricted"
    if "expired" in text and ("token" in text or "auth" in text or "login" in text):
        return "expired"
    # Real CLI prints "0 credentials" when auth.json is empty.
    if (
        "0 credentials" in text
        or "no credentials" in text
        or "not logged" in text
        or "unauthenticated" in text
        or "no providers" in text
    ):
        return "unauthenticated"
    if returncode != 0 and not text.strip():
        return "unauthenticated"
    if returncode != 0 and ("error" in text or "invalid" in text):
        if "config" in text:
            return "configuration_invalid"
        return "unauthenticated"
    if returncode != 0:
        return "unauthenticated"
    # Authenticated listings name a provider with an active credential row.
    provider_tokens = (
        "anthropic",
        "openai",
        "google",
        "gemini",
        "amazon",
        "azure",
        "copilot",
        "bedrock",
        "openrouter",
        "groq",
        "mistral",
        "deepseek",
    )
    has_provider = any(token in text for token in provider_tokens)
    has_active = any(
        marker in text for marker in ("configured", "logged", "api key", "oauth", "active", "@")
    )
    if has_provider and has_active:
        return "authenticated"
    if has_provider and "credential" in text:
        return "authenticated"
    return "unauthenticated"


def parse_opencode_jsonl(output: str) -> list[dict[str, Any]]:
    """Normalize OpenCode JSONL into connector-neutral event maps."""

    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        native = str(payload.get("type") or "")
        part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
        if native == "step_start":
            events.append({"event_type": "run.started", "native_type": native})
        elif native == "text":
            text = part.get("text") if isinstance(part, dict) else payload.get("text")
            events.append(
                {
                    "event_type": "message.output",
                    "native_type": native,
                    "text": text,
                }
            )
            # When denied network tools are withheld from the toolset, the model may
            # report unavailability in text without emitting an invalid tool_use.
            text_l = str(text or "").lower()
            mentions_network_tool = any(
                token in text_l for token in ("webfetch", "websearch", "web search")
            )
            reports_unavailable = any(
                token in text_l
                for token in (
                    "don't have",
                    "do not have",
                    "not available",
                    "unavailable",
                    "not listed",
                    "neither is listed",
                    "cannot make network",
                    "can't make network",
                    "no network",
                )
            )
            if mentions_network_tool and reports_unavailable:
                events.append(
                    {
                        "event_type": "permission.denied",
                        "native_type": native,
                        "tool": "webfetch",
                        "status": "denied",
                        "error": str(text)[:300],
                        "reason_code": "permission_denied_network",
                    }
                )
        elif native == "tool_use":
            tool = part.get("tool") if isinstance(part, dict) else None
            state = part.get("state") if isinstance(part, dict) else None
            status = state.get("status") if isinstance(state, dict) else None
            error = None
            output_text = ""
            if isinstance(state, dict):
                error = state.get("error")
                raw_output = state.get("output")
                if isinstance(raw_output, str):
                    output_text = raw_output
                if (
                    error is None
                    and output_text
                    and any(
                        token in output_text.lower()
                        for token in (
                            "denied",
                            "permission",
                            "not allowed",
                            "forbidden",
                            "unavailable tool",
                        )
                    )
                ):
                    error = output_text[:300]
            event_type = "tool.call"
            reason_code = None
            denied_tool = str(tool or "").lower()
            unavailable = None
            blob = f"{error or ''} {output_text}".lower()
            if "unavailable tool" in blob:
                # OpenCode withholds denied tools and reports attempts as tool=invalid.
                match = re.search(r"unavailable tool ['\"]?([a-z0-9_-]+)", blob)
                if match:
                    unavailable = match.group(1).lower()
                    denied_tool = unavailable
            if (
                status in {"error", "failed", "denied"}
                or error
                or unavailable
                or denied_tool == "invalid"
            ):
                if unavailable or "unavailable tool" in blob or denied_tool == "invalid":
                    event_type = "permission.denied"
                    tool_name = unavailable or denied_tool
                    if tool_name in {"edit", "write", "apply_patch", "multiedit", "delete"}:
                        reason_code = "permission_denied_edit"
                    elif tool_name in {"bash", "shell", "terminal"}:
                        reason_code = "permission_denied_shell"
                    elif tool_name in {
                        "webfetch",
                        "websearch",
                        "web_search",
                        "fetch",
                        "http",
                    }:
                        reason_code = "permission_denied_network"
                    else:
                        reason_code = "permission_denied"
            events.append(
                {
                    "event_type": event_type,
                    "native_type": native,
                    "tool": unavailable or tool,
                    "status": status,
                    "error": error,
                    "reason_code": reason_code,
                }
            )
        elif native == "step_finish":
            events.append(
                {
                    "event_type": "run.completed",
                    "native_type": native,
                    "usage": part.get("tokens") if isinstance(part, dict) else None,
                }
            )
        else:
            events.append({"event_type": "message.output", "native_type": native or "unknown"})
    return events


def _redact(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("api_key", "token", "bearer", "cookie", "sk-")):
        return "[redacted status]"
    return text
