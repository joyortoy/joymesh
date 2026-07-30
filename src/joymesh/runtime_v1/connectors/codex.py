"""Codex connector implementing the generic ConnectorRuntime protocol."""

from __future__ import annotations

import asyncio
import hashlib
import json
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


class CodexConnectorRuntime:
    """Codex-specific discovery, auth, argv, JSONL parsing, and cancellation."""

    connector_id = "codex"
    display_name = "OpenAI Codex CLI"

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
        return None

    def execution_environment(self, *, read_only: bool = True) -> Mapping[str, str]:
        del read_only
        return {}

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
        executable = shutil.which("codex")
        if not executable:
            return DiscoveryResult(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                reason_code="executable_not_found",
                details={"detail": "codex not found", "node_id": context.node_id},
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
                    "status": "broken_executable",
                    "detail": broken,
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
                    "status": "unsupported_version",
                    "detail": "unable to parse Codex version",
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
        return AuthenticationEvidence(
            status=status,
            method_id=method.id,
            executable_path=executable,
            fingerprint=executable_fingerprint(executable),
            version=version,
            details={
                "detail": _redact(output.strip()[:500]),
                "node_id": context.node_id,
            },
        )

    def classify_auth_status(self, output: str, *, returncode: int) -> str:
        return classify_codex_auth_status(output, returncode=returncode)

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
                    "status": discovery.reason_code,
                    "node_id": context.node_id,
                },
            )
        resolved = discovery.executable_path
        process = await asyncio.create_subprocess_exec(
            resolved,
            "exec",
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        help_text = (stdout.decode() + stderr.decode()).lower()
        passed = process.returncode == 0 and "--json" in help_text
        return AdapterVerificationResult(
            passed=passed,
            executable_path=resolved,
            fingerprint=executable_fingerprint(resolved),
            version=discovery.version,
            details={
                "structured_output_supported": "--json" in help_text,
                "sandbox_read_only_supported": "read-only" in help_text,
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
        argv = [executable, "exec", "--json"]
        if read_only:
            argv.extend(["--sandbox", "read-only"])
        argv.extend(["-C", workspace_path, prompt])
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
        return parse_codex_jsonl(output)

    async def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]:
        del context
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("codex not found")
        argv = list(
            self.build_exec_argv(
                executable=executable,
                prompt=request.prompt,
                workspace_path=request.workspace_path,
                read_only=True,
            )
        )
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
            start_new_session=True,
            env=filter_environment(extra_keys=frozenset(self.execution_environment())),
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
        events = self.parse_events(output)
        for item in events:
            sequence += 1
            yield HarnessEvent(
                event_type="task.progress",
                sequence=sequence,
                payload={"connector_event": item.get("type"), "redacted": True},
            )
        sequence += 1
        yield HarnessEvent(
            event_type="task.succeeded" if process.returncode == 0 else "task.failed",
            sequence=sequence,
            payload={
                "returncode": process.returncode,
                "output_digest": hashlib.sha256(output.encode()).hexdigest(),
                "event_count": len(events),
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
            or "native" in lowered
            or "mach-o" in lowered
            or "wrong architecture" in lowered
            or "cannot find module" in lowered
        ):
            return None, "Native binary missing or wrong architecture for Codex launcher"
        if not combined:
            return None, None
        return combined.splitlines()[0][:200], None


def classify_codex_auth_status(output: str, *, returncode: int) -> str:
    text = output.lower()
    if "expired" in text and ("login" in text or "auth" in text or "token" in text):
        return "expired"
    if "not logged" in text or "logged out" in text or "not authenticated" in text:
        return "unauthenticated"
    if returncode != 0:
        return "unauthenticated"
    if "logged in" in text or "authenticated" in text:
        return "authenticated"
    return "unauthenticated"


def parse_codex_jsonl(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _redact(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("api_key", "token", "bearer", "cookie")):
        return "[redacted status]"
    return text
