"""Cursor connector implementing the generic ConnectorRuntime protocol."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.node_runner import (
    _executable_fingerprint,
    _terminate_process_tree,
    parse_cursor_auth_status,
)
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
    DiscoveryResult,
    HarnessEvent,
)


class CursorConnectorRuntime:
    """Cursor-specific executable, argv, auth parsing, and --trust handling."""

    connector_id = "cursor"

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

    async def discover(self, context: ConnectorExecutionContext) -> DiscoveryResult:
        executable = shutil.which("cursor-agent")
        if not executable:
            return DiscoveryResult(None, None, None, {"detail": "cursor-agent not found"})
        version = await self._probe_version(executable)
        return DiscoveryResult(
            executable_path=executable,
            version=version,
            fingerprint=_executable_fingerprint(executable),
            details={"node_id": context.node_id},
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

    async def build_authentication_plan(
        self, context: ConnectorExecutionContext
    ) -> ConnectorPlan:
        definition = self.catalogue.get(self.connector_id)
        method = next(
            item for item in definition.authentication_methods if item.login_argv
        )
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
        )
        stdout, stderr = await process.communicate()
        output = (stdout.decode() + stderr.decode()).lower()
        authenticated = parse_cursor_auth_status(
            output, returncode=process.returncode if process.returncode is not None else 1
        )
        executable = status_argv[0]
        return AuthenticationEvidence(
            status="authenticated" if authenticated else "unauthenticated",
            method_id=method.id,
            executable_path=executable,
            fingerprint=_executable_fingerprint(shutil.which(executable) or executable),
            version=await self._probe_version(executable),
            details={"detail": output.strip()[:500], "node_id": context.node_id},
        )

    async def verify_adapter(
        self, context: ConnectorExecutionContext
    ) -> AdapterVerificationResult:
        resolved = shutil.which("cursor-agent")
        if not resolved:
            return AdapterVerificationResult(False, None, None, None, {"detail": "missing"})
        process = await asyncio.create_subprocess_exec(
            resolved,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        help_text = (stdout.decode() + stderr.decode()).lower()
        passed = process.returncode == 0 and (
            "stream-json" in help_text or "output-format" in help_text
        )
        return AdapterVerificationResult(
            passed=passed,
            executable_path=resolved,
            fingerprint=_executable_fingerprint(resolved),
            version=await self._probe_version(resolved),
            details={
                "structured_output_supported": "stream-json" in help_text,
                "node_id": context.node_id,
            },
        )

    async def execute(
        self,
        request: ConnectorRunRequest,
        context: ConnectorExecutionContext,
    ) -> AsyncIterator[HarnessEvent]:
        executable = shutil.which("cursor-agent")
        if not executable:
            raise RuntimeError("cursor-agent not found")
        # Cursor-owned argv: --trust is required for non-interactive workspaces.
        argv = (
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--trust",
            request.prompt,
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
            start_new_session=True,
        )
        self._processes[request.execution_id] = process
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=request.timeout_seconds
            )
        except TimeoutError:
            await _terminate_process_tree(process)
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
        sequence += 1
        yield HarnessEvent(
            event_type="task.progress",
            sequence=sequence,
            payload={"bytes": len(output), "redacted": True},
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
        process = self._processes.get(execution_id)
        if process is None:
            return CancellationResult(True, False, "no active process")
        result = await _terminate_process_tree(process)
        return CancellationResult(
            cancelled=bool(result.get("cancelled")),
            lingering=bool(result.get("lingering")),
            detail=str(result.get("signal")),
        )

    def build_read_only_cert_argv(self, *, executable: str, prompt: str) -> tuple[str, ...]:
        return (
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--trust",
            prompt,
        )

    async def certify_read_only(
        self, *, task_id: str, node_id: str
    ) -> Mapping[str, Any]:
        """Run generic read-only profile with Cursor-owned argv."""

        executable = shutil.which("cursor-agent")
        if not executable:
            raise RuntimeError("cursor-agent not found")
        root = Path.home() / ".joymesh" / "certification" / "cursor"
        workspace = self._read_only.build_workspace(task_id=task_id, root=root)
        try:
            argv = self.build_read_only_cert_argv(
                executable=executable, prompt=workspace.prompt
            )
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            verification = self._read_only.verify_result(
                workspace,
                output=output,
                returncode=process.returncode if process.returncode is not None else 1,
            )
            scope = self._read_only.produce_scope()
            return {
                "passed": verification.passed,
                "reasons": verification.reasons,
                "project_name": workspace.project_name,
                "prompt_digest": workspace.prompt_digest,
                "argv": [*argv[:-1], "<PROMPT>"],
                "fingerprint": _executable_fingerprint(executable),
                "version": await self._probe_version(executable),
                "scope": {
                    "profile": scope.profile,
                    "structured_execution": scope.structured_execution,
                    "repository_read": scope.repository_read,
                    "repository_write": scope.repository_write,
                    "shell_commands": scope.shell_commands,
                    "session_resume": scope.session_resume,
                    "network_access": scope.network_access,
                },
                "node_id": node_id,
                "connector_revision": self.connector_revision,
                "profile_id": self._read_only.profile_id,
                "profile_revision": self._read_only.profile_revision,
            }
        finally:
            self._read_only.cleanup(workspace)

    async def _probe_version(self, executable: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            text = (stdout.decode() + stderr.decode()).strip()
            return text.splitlines()[0][:200] if text else None
        except OSError:
            return None
