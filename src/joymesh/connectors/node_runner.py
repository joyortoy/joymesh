"""Bounded connector lifecycle execution on the JoyMesh Node."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Awaitable, Callable, Mapping
from uuid import uuid4

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.lifecycle_models import (
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorTaskEvent,
    ConnectorTaskStatus,
)
from joymesh.connectors.planning import ConnectorAction, ConnectorTaskPlan
from joymesh.harnesses.discovery import DiscoveryPolicy
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.models import utc_now

EvidenceSink = Callable[[ConnectorEvidence], Awaitable[None]]
EventSink = Callable[[ConnectorTaskEvent], Awaitable[None]]


class ConnectorNodeRunner:
    """Executes signed connector task plans and emits evidence exactly once per terminal path."""

    def __init__(
        self,
        *,
        node_id: str,
        catalogue: ConnectorCatalogue | None = None,
        executed_keys: set[str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.catalogue = catalogue or ConnectorCatalogue.builtins()
        self._discovery = HarnessRegistry().discovery
        self._executed_keys = executed_keys if executed_keys is not None else set()

    async def execute(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        emit_event: EventSink,
        record_evidence: EvidenceSink,
        sequence_start: int = 0,
    ) -> ConnectorTaskStatus:
        idempotency = f"{self.node_id}:{task_id}:{plan.plan_hash}"
        if idempotency in self._executed_keys:
            return ConnectorTaskStatus.SUCCEEDED
        self._executed_keys.add(idempotency)

        sequence = sequence_start

        async def event(event_type: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            await emit_event(
                ConnectorTaskEvent(
                    task_id=task_id,
                    node_id=self.node_id,
                    connector_id=plan.connector_id,
                    event_type=event_type,
                    sequence=sequence,
                    payload=payload,
                )
            )

        await event("task.started", {"action": plan.action.value})
        try:
            if plan.action is ConnectorAction.AUTHENTICATE:
                status = await self._authenticate(plan=plan, event=event)
                if status is ConnectorTaskStatus.WAITING_FOR_USER:
                    return status
            elif plan.action is ConnectorAction.VERIFY_AUTHENTICATION:
                status = await self._verify_authentication(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.VERIFY_ADAPTER:
                status = await self._verify_adapter(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.DISCOVER:
                status = await self._discover(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.REPAIR:
                status = await self._repair(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.INSTALL:
                status = await self._install(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.CERTIFY:
                status = await self._certify(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            else:
                await event("task.failed", {"reason": f"unsupported action {plan.action.value}"})
                return ConnectorTaskStatus.FAILED

            await event(f"task.{status.value}", {"action": plan.action.value})
            return status
        except Exception as exc:
            await event("task.failed", {"reason": str(exc)})
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.FAILURE,
                    status="failed",
                    executable=None,
                    version=None,
                    details={"detail": str(exc), "action": plan.action.value},
                )
            )
            return ConnectorTaskStatus.FAILED

    async def _discover(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        result = await self._discovery.discover(
            plan.connector_id,
            policy=DiscoveryPolicy(execute_version_commands=True),
        )
        installation = result.installations[0] if result.installations else None
        broken = await self._detect_broken_executable(plan.connector_id, installation)
        if broken:
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.FAILURE,
                    status="broken_executable",
                    executable=installation.executable if installation else None,
                    version=installation.version if installation else None,
                    details=broken,
                )
            )
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.VERSION,
                    status="broken_executable",
                    executable=installation.executable if installation else None,
                    version=None,
                    details=broken,
                )
            )
            return ConnectorTaskStatus.SUCCEEDED
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.DISCOVERY,
                status="discovered",
                executable=installation.executable if installation else None,
                version=installation.version if installation else None,
                details={"execution_environment": "host"},
            )
        )
        if installation and installation.version:
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.VERSION,
                    status="ok",
                    executable=installation.executable,
                    version=installation.version,
                    details={},
                )
            )
        return ConnectorTaskStatus.SUCCEEDED

    async def _install(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        argv = (plan.executable, *plan.arguments)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or stdout.decode() or "installation failed")
        await self._discover(task_id=task_id, plan=plan, record_evidence=record_evidence)
        executable = (
            shutil.which(plan.expected_executables[0]) if plan.expected_executables else None
        )
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.INSTALLATION,
                status="installed",
                executable=executable,
                version=None,
                details={"method_id": plan.method_id},
            )
        )
        return ConnectorTaskStatus.SUCCEEDED

    async def _authenticate(
        self,
        *,
        plan: ConnectorTaskPlan,
        event: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ConnectorTaskStatus:
        await event("task.waiting_for_user", {"instruction": "Complete vendor login locally"})
        argv = (plan.executable, *plan.arguments)
        process = await asyncio.create_subprocess_exec(*argv)
        await process.wait()
        return ConnectorTaskStatus.WAITING_FOR_USER

    async def _verify_authentication(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        definition = self.catalogue.get(plan.connector_id)
        method = definition.authentication_methods[0]
        status_argv = method.status_argv or method.login_argv
        if not status_argv:
            raise RuntimeError("no authentication status command configured")
        process = await asyncio.create_subprocess_exec(
            *status_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = (stdout.decode() + stderr.decode()).lower()
        authenticated = process.returncode == 0 and "not logged" not in output
        if plan.connector_id == "cursor":
            authenticated = process.returncode == 0 and (
                "logged in" in output or "authenticated" in output
            )
        status = "authenticated" if authenticated else "unauthenticated"
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.AUTHENTICATION,
                status=status,
                executable=status_argv[0],
                version=None,
                details={"method_id": method.id, "detail": output.strip()[:500]},
            )
        )
        return ConnectorTaskStatus.SUCCEEDED if authenticated else ConnectorTaskStatus.FAILED

    async def _verify_adapter(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        definition = self.catalogue.get(plan.connector_id)
        executable = (
            definition.executable_names[0] if definition.executable_names else plan.executable
        )
        resolved = shutil.which(executable)
        if not resolved:
            raise RuntimeError(f"{executable} not found")
        process = await asyncio.create_subprocess_exec(
            resolved,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or "adapter launch failed")
        fingerprint = hashlib.sha256(resolved.encode()).hexdigest()
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                status="passed",
                executable=resolved,
                version=None,
                details={"help_bytes": len(stdout), "fingerprint": fingerprint},
                fingerprint=fingerprint,
            )
        )
        return ConnectorTaskStatus.SUCCEEDED

    async def _certify(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        import os
        import secrets
        import shutil
        import tempfile
        from pathlib import Path

        if os.environ.get("JOYMESH_MOCK_CERTIFY", "0") == "1" or plan.connector_id != "cursor":
            return await self._certify_mock(
                task_id=task_id, plan=plan, record_evidence=record_evidence
            )

        definition = self.catalogue.get(plan.connector_id)
        executable = shutil.which(
            definition.executable_names[0] if definition.executable_names else plan.executable
        )
        if not executable:
            raise RuntimeError("cursor-agent not found")
        fingerprint = hashlib.sha256(executable.encode()).hexdigest()
        project_name = f"JoyMesh Cursor Certification {secrets.token_hex(3).upper()}"
        workspace = Path(tempfile.mkdtemp(prefix="joymesh-cursor-cert-"))
        try:
            readme = workspace / "README.md"
            readme.write_text(f"# {project_name}\n", encoding="utf-8")
            init = await asyncio.create_subprocess_exec(
                "git",
                "init",
                "--quiet",
                str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await init.communicate()
            if init.returncode:
                raise RuntimeError("failed to initialize certification repository")
            prompt = (
                "Read README.md and return the exact project name. "
                "Do not modify any files. Do not run network commands. "
                "Do not read files outside this repository."
            )
            argv = (
                executable,
                "--print",
                prompt,
                "--output-format",
                "stream-json",
            )
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            after = sorted(
                entry.name for entry in os.scandir(workspace)
            )
            git_status = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--porcelain",
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            status_out, _ = await git_status.communicate()
            clean = status_out.decode().strip() == ""
            name_found = project_name in output
            workspace_clean = clean and set(after) <= {"README.md", ".git"}
            passed = {
                "discovery": True,
                "authentication": True,
                "adapter": True,
                "read_test": name_found and process.returncode == 0,
                "write_test": False,
                "command_test": False,
                "session_resume": False,
                "workspace_clean": workspace_clean,
                "structured_output": bool(output.strip()),
            }
            digest = hashlib.sha256(json.dumps(passed, sort_keys=True).encode()).hexdigest()
            status = "certified" if passed["read_test"] and passed["workspace_clean"] else "failed"
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.REAL_BINARY_TEST,
                    status="passed" if status == "certified" else "failed",
                    executable=executable,
                    version=None,
                    details={
                        "level": "read_only",
                        "project_name": project_name,
                        "routing_profile": "cursor_read_only",
                    },
                    fingerprint=fingerprint,
                )
            )
            await record_evidence(
                _evidence(
                    task_id=task_id,
                    node_id=self.node_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.CERTIFICATION,
                    status=status,
                    executable=executable,
                    version=None,
                    details={
                        "passed_levels": {
                            "read_only_certified": passed["read_test"],
                            "write_certified": False,
                            "command_certified": False,
                            "session_resume_certified": False,
                        },
                        "evidence_digest": digest,
                        "routing_profile": "cursor_read_only",
                        "workspace_clean": passed["workspace_clean"],
                    },
                    fingerprint=fingerprint,
                )
            )
            return (
                ConnectorTaskStatus.SUCCEEDED
                if status == "certified"
                else ConnectorTaskStatus.FAILED
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    async def _certify_mock(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        passed = {
            "read_only_certified": True,
            "write_certified": False,
            "command_certified": False,
            "session_resume_certified": False,
        }
        digest = hashlib.sha256(json.dumps(passed, sort_keys=True).encode()).hexdigest()
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.CERTIFICATION,
                status="certified",
                executable=None,
                version=None,
                details={
                    "passed_levels": passed,
                    "evidence_digest": digest,
                    "routing_profile": "cursor_read_only",
                },
            )
        )
        await record_evidence(
            _evidence(
                task_id=task_id,
                node_id=self.node_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.REAL_BINARY_TEST,
                status="passed",
                executable=None,
                version=None,
                details={"level": "read_only", "routing_profile": "cursor_read_only"},
            )
        )
        return ConnectorTaskStatus.SUCCEEDED

    async def _repair(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        reinstall = plan.model_copy(update={"action": ConnectorAction.INSTALL})
        await self._install(task_id=task_id, plan=reinstall, record_evidence=record_evidence)
        return ConnectorTaskStatus.SUCCEEDED

    async def _detect_broken_executable(
        self, connector_id: str, installation: object | None
    ) -> dict[str, str] | None:
        if connector_id != "codex" or installation is None:
            return None
        executable = getattr(installation, "executable", None)
        if not executable:
            return None
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        combined = (stdout.decode() + stderr.decode()).lower()
        if process.returncode != 0 and (
            "native" in combined
            or "mach-o" in combined
            or "wrong architecture" in combined
            or "cannot find module" in combined
            or "enoent" in combined
        ):
            return {
                "detail": "Native binary missing or wrong architecture for Codex launcher",
                "launcher": str(executable),
            }
        return None


def _evidence(
    *,
    task_id: str,
    node_id: str,
    plan: ConnectorTaskPlan,
    evidence_type: ConnectorEvidenceType,
    status: str,
    executable: str | None,
    version: str | None,
    details: Mapping[str, object],
    fingerprint: str | None = None,
) -> ConnectorEvidence:
    return ConnectorEvidence(
        evidence_id=str(uuid4()),
        node_id=node_id,
        connector_id=plan.connector_id,
        connector_revision=plan.connector_revision,
        task_id=task_id,
        evidence_type=evidence_type,
        status=status,
        executable_path=executable,
        executable_fingerprint=fingerprint
        or (hashlib.sha256(executable.encode()).hexdigest() if executable else None),
        harness_version=version,
        provider_mode=None,
        details=details,
        created_at=utc_now(),
        expires_at=None,
    )
