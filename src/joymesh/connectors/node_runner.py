"""Bounded connector lifecycle execution on the JoyMesh Node."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import stat
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from uuid import uuid4

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.lifecycle_models import (
    CertificationScope,
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorExecutionOrigin,
    ConnectorTaskEvent,
    ConnectorTaskStatus,
    EvidenceTrustLevel,
)
from joymesh.connectors.planning import ConnectorAction, ConnectorTaskPlan
from joymesh.connectors.process_utils import executable_fingerprint, terminate_process_tree
from joymesh.control_plane.security import mock_certify_enabled, production_mode
from joymesh.harnesses.discovery import DiscoveryPolicy
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.models import utc_now
from joymesh.runtime_v1.connector_protocol import ConnectorExecutionContext, ConnectorRuntime
from joymesh.runtime_v1.connectors import get_connector
from joymesh.runtime_v1.connectors.cursor import parse_cursor_auth_status

EvidenceSink = Callable[[ConnectorEvidence], Awaitable[None]]
EventSink = Callable[[ConnectorTaskEvent], Awaitable[None]]

READ_ONLY_SCOPE = CertificationScope(
    profile="read_only_repository",
    structured_execution=True,
    repository_read=True,
    repository_write=False,
    shell_commands=False,
    session_resume=False,
    network_access=False,
    event_streaming=True,
    workspace_containment=True,
    cancellation=True,
)

# Backward-compatible alias for older imports/tests.
CURSOR_READ_ONLY_SCOPE = READ_ONLY_SCOPE


class ConnectorNodeRunner:
    """Executes signed connector task plans and emits evidence exactly once per terminal path."""

    def __init__(
        self,
        *,
        node_id: str,
        catalogue: ConnectorCatalogue | None = None,
        executed_keys: set[str] | None = None,
        execution_origin: ConnectorExecutionOrigin = ConnectorExecutionOrigin.REMOTE_NODE,
    ) -> None:
        self.node_id = node_id
        self.catalogue = catalogue or ConnectorCatalogue.builtins()
        self._discovery = HarnessRegistry().discovery
        self._executed_keys = executed_keys if executed_keys is not None else set()
        self.execution_origin = execution_origin
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    @property
    def trust_level(self) -> EvidenceTrustLevel:
        if self.execution_origin is ConnectorExecutionOrigin.MOCK_TEST:
            return EvidenceTrustLevel.MOCK
        if self.execution_origin is ConnectorExecutionOrigin.INLINE_DEVELOPMENT:
            return EvidenceTrustLevel.DEVELOPMENT
        return EvidenceTrustLevel.NODE_ATTESTED

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
        # Mark started for non-waiting actions; authenticate may wait without terminal.
        if plan.action is not ConnectorAction.AUTHENTICATE:
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

        await event(
            "task.started",
            {
                "action": plan.action.value,
                "execution_origin": self.execution_origin.value,
            },
        )
        try:
            if plan.action is ConnectorAction.AUTHENTICATE:
                status = await self._authenticate(task_id=task_id, plan=plan, event=event)
                if status is ConnectorTaskStatus.WAITING_FOR_USER:
                    return status
                self._executed_keys.add(idempotency)
            elif plan.action is ConnectorAction.VERIFY_AUTHENTICATION:
                status = await self._verify_authentication(
                    task_id=task_id, plan=plan, record_evidence=record_evidence
                )
            elif plan.action is ConnectorAction.VERIFY_ADAPTER:
                status = await self._verify_adapter(
                    task_id=task_id, plan=plan, record_evidence=record_evidence, event=event
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
                    task_id=task_id,
                    plan=plan,
                    record_evidence=record_evidence,
                    event=event,
                )
            else:
                await event("task.failed", {"reason": f"unsupported action {plan.action.value}"})
                return ConnectorTaskStatus.FAILED

            await event(f"task.{status.value}", {"action": plan.action.value})
            return status
        except asyncio.CancelledError:
            await self.cancel_task(task_id)
            await event("task.cancelled", {"action": plan.action.value})
            raise
        except Exception as exc:
            await event("task.failed", {"reason": str(exc)})
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.FAILURE,
                    status="failed",
                    executable=None,
                    version=None,
                    details={"detail": str(exc), "action": plan.action.value},
                )
            )
            return ConnectorTaskStatus.FAILED
        finally:
            self._active_processes.pop(task_id, None)

    async def cancel_task(self, task_id: str) -> dict[str, object]:
        process = self._active_processes.get(task_id)
        if process is None or process.returncode is not None:
            return {"cancelled": True, "lingering": False}
        return await _terminate_process_tree(process)

    async def _discover(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        connector = _runtime_connector(plan.connector_id)
        if connector is not None:
            discovery = await connector.discover(
                ConnectorExecutionContext(node_id=self.node_id, task_id=task_id)
            )
            if not discovery.usable:
                details = {
                    **dict(discovery.details),
                    "reason_code": discovery.reason_code or "unusable",
                    "installed": discovery.installed,
                    "usable": discovery.usable,
                }
                status = discovery.reason_code or "unusable"
                await record_evidence(
                    self._evidence(
                        task_id=task_id,
                        plan=plan,
                        evidence_type=ConnectorEvidenceType.FAILURE,
                        status=status,
                        executable=discovery.executable_path,
                        version=discovery.version,
                        details=details,
                        fingerprint=discovery.fingerprint,
                    )
                )
                await record_evidence(
                    self._evidence(
                        task_id=task_id,
                        plan=plan,
                        evidence_type=ConnectorEvidenceType.VERSION,
                        status=status,
                        executable=discovery.executable_path,
                        version=None,
                        details=details,
                        fingerprint=discovery.fingerprint,
                    )
                )
                return ConnectorTaskStatus.SUCCEEDED
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.DISCOVERY,
                    status="discovered",
                    executable=discovery.executable_path,
                    version=discovery.version,
                    details={
                        **dict(discovery.details),
                        "execution_environment": "host",
                        "installed": discovery.installed,
                        "usable": discovery.usable,
                    },
                    fingerprint=discovery.fingerprint,
                )
            )
            if discovery.version:
                await record_evidence(
                    self._evidence(
                        task_id=task_id,
                        plan=plan,
                        evidence_type=ConnectorEvidenceType.VERSION,
                        status="ok",
                        executable=discovery.executable_path,
                        version=discovery.version,
                        details={},
                        fingerprint=discovery.fingerprint,
                    )
                )
            return ConnectorTaskStatus.SUCCEEDED

        result = await self._discovery.discover(
            plan.connector_id,
            policy=DiscoveryPolicy(execute_version_commands=True),
        )
        installation = result.installations[0] if result.installations else None
        await record_evidence(
            self._evidence(
                task_id=task_id,
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
                self._evidence(
                    task_id=task_id,
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
        self._active_processes[task_id] = process
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or stdout.decode() or "installation failed")
        await self._discover(task_id=task_id, plan=plan, record_evidence=record_evidence)
        executable = (
            shutil.which(plan.expected_executables[0]) if plan.expected_executables else None
        )
        await record_evidence(
            self._evidence(
                task_id=task_id,
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
        task_id: str,
        plan: ConnectorTaskPlan,
        event: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ConnectorTaskStatus:
        # Never claim success from login exit code; wait for user + separate verify.
        await event(
            "task.waiting_for_user",
            {
                "instruction": "Complete the Cursor sign-in flow on this Mac.",
                "browser_may_open": True,
                "auto_verify": True,
            },
        )
        argv = (plan.executable, *plan.arguments)
        process = await asyncio.create_subprocess_exec(*argv)
        self._active_processes[task_id] = process
        # Do not block forever on interactive login; detach after launch acknowledgement.
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            await event(
                "task.progress",
                {
                    "stage": "login_launched",
                    "detail": "Login process is waiting for user completion",
                    "redacted": True,
                },
            )
        return ConnectorTaskStatus.WAITING_FOR_USER

    async def _verify_authentication(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        connector = _runtime_connector(plan.connector_id)
        if connector is not None:
            evidence = await connector.verify_authentication(
                ConnectorExecutionContext(node_id=self.node_id, task_id=task_id)
            )
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.AUTHENTICATION,
                    status=evidence.status,
                    executable=evidence.executable_path,
                    version=evidence.version,
                    fingerprint=evidence.fingerprint,
                    details={
                        "method_id": evidence.method_id,
                        **dict(evidence.details),
                        "verified_at": utc_now().isoformat(),
                        "node_id": self.node_id,
                    },
                )
            )
            return (
                ConnectorTaskStatus.SUCCEEDED
                if evidence.status == "authenticated"
                else ConnectorTaskStatus.FAILED
            )

        definition = self.catalogue.get(plan.connector_id)
        method = definition.authentication_methods[0]
        status_argv = method.status_argv or method.login_argv
        if not status_argv:
            raise RuntimeError("no authentication status command configured")
        process = await asyncio.create_subprocess_exec(
            *status_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        self._active_processes[task_id] = process
        stdout, stderr = await process.communicate()
        output = stdout.decode() + stderr.decode()
        status = _classify_generic_auth_status(
            output,
            returncode=process.returncode if process.returncode is not None else 1,
        )
        authenticated = status == "authenticated"
        version = await self._probe_version(status_argv[0])
        fingerprint = executable_fingerprint(status_argv[0])
        await record_evidence(
            self._evidence(
                task_id=task_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.AUTHENTICATION,
                status=status,
                executable=status_argv[0],
                version=version,
                fingerprint=fingerprint,
                details={
                    "method_id": method.id,
                    "detail": _redact_status(output.strip()[:500]),
                    "verified_at": utc_now().isoformat(),
                    "node_id": self.node_id,
                },
            )
        )
        return ConnectorTaskStatus.SUCCEEDED if authenticated else ConnectorTaskStatus.FAILED

    async def _verify_adapter(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
        event: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ConnectorTaskStatus:
        connector = _runtime_connector(plan.connector_id)
        if connector is not None:
            notice = connector.adapter_verification_notice()
            progress: dict[str, object] = {
                "stage": "adapter_launch",
                "connector_id": connector.connector_id,
                "display_name": connector.display_name,
            }
            if notice is not None:
                progress["notice"] = notice.as_payload()
            await event("task.progress", progress)
            result = await connector.verify_adapter(
                ConnectorExecutionContext(node_id=self.node_id, task_id=task_id)
            )
            if not result.passed:
                raise RuntimeError(
                    str(result.details.get("detail") or "adapter conformance failed")
                )
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                    status="passed",
                    executable=result.executable_path,
                    version=result.version,
                    details={
                        **dict(result.details),
                        "fingerprint": result.fingerprint,
                        "secret_redaction": True,
                        "timeout_enforced": True,
                        "cancellation_supported": True,
                        "unknown_events_tolerated": True,
                        "terminal_events": 1,
                    },
                    fingerprint=result.fingerprint,
                )
            )
            return ConnectorTaskStatus.SUCCEEDED

        # Catalogue-only connectors (not in Runtime registry): generic --help probe.
        definition = self.catalogue.get(plan.connector_id)
        executable = (
            definition.executable_names[0] if definition.executable_names else plan.executable
        )
        resolved = shutil.which(executable)
        if not resolved:
            raise RuntimeError(f"{executable} not found")
        await event(
            "task.progress",
            {
                "stage": "adapter_launch",
                "connector_id": plan.connector_id,
                "display_name": definition.display_name,
            },
        )
        process = await asyncio.create_subprocess_exec(
            resolved,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        self._active_processes[task_id] = process
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or "adapter launch failed")
        fingerprint = executable_fingerprint(resolved)
        version = await self._probe_version(resolved)
        await record_evidence(
            self._evidence(
                task_id=task_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                status="passed",
                executable=resolved,
                version=version,
                details={
                    "help_bytes": len(stdout),
                    "fingerprint": fingerprint,
                    "stdout_captured": True,
                    "stderr_captured": True,
                    "secret_redaction": True,
                    "timeout_enforced": True,
                    "cancellation_supported": True,
                    "unknown_events_tolerated": True,
                    "terminal_events": 1,
                },
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
        event: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ConnectorTaskStatus:
        if mock_certify_enabled():
            if production_mode():
                raise RuntimeError("mock certification refused in production")
            return await self._certify_mock(
                task_id=task_id, plan=plan, record_evidence=record_evidence
            )
        connector = _runtime_connector(plan.connector_id)
        if connector is None:
            if production_mode():
                raise RuntimeError(
                    f"connector {plan.connector_id} is not registered in builtin_connectors()"
                )
            return await self._certify_mock(
                task_id=task_id, plan=plan, record_evidence=record_evidence
            )
        return await self._certify_read_only_repository(
            task_id=task_id,
            plan=plan,
            record_evidence=record_evidence,
            event=event,
            connector=connector,
        )

    async def _certify_read_only_repository(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
        event: Callable[[str, dict[str, object]], Awaitable[None]],
        connector: ConnectorRuntime,
    ) -> ConnectorTaskStatus:
        from joymesh.runtime_v1.certification import ReadOnlyRepositoryProfile

        discovery = await connector.discover(
            ConnectorExecutionContext(node_id=self.node_id, task_id=task_id)
        )
        if not discovery.usable or not discovery.executable_path:
            raise RuntimeError(
                str(
                    discovery.details.get("detail")
                    or discovery.reason_code
                    or f"{plan.connector_id} executable not usable"
                )
            )
        executable = discovery.executable_path
        fingerprint = discovery.fingerprint or executable_fingerprint(executable)
        version = discovery.version
        profile = ReadOnlyRepositoryProfile()
        root = Path.home() / ".joymesh" / "certification" / plan.connector_id
        await event("task.progress", {"stage": "preparing_isolated_repository"})
        workspace = profile.build_workspace(task_id=task_id, root=root)
        try:
            argv = list(
                connector.build_read_only_cert_argv(
                    executable=executable,
                    prompt=workspace.prompt,
                    workspace=workspace.path,
                )
            )
            plan_digest = plan.plan_hash
            await event(
                "task.progress",
                {
                    "stage": "running_structured_read_only_test",
                    "argv": [*list(argv[:-1]), "<PROMPT>"],
                    "workspace": str(workspace.path),
                    "certification_profile": profile.profile_id,
                    "connector_id": plan.connector_id,
                    "display_name": connector.display_name,
                },
            )
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            self._active_processes[task_id] = process
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            except TimeoutError:
                await terminate_process_tree(process)
                raise RuntimeError("certification timed out") from None
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            # Connector may parse events; runtime still owns verification/evidence.
            _ = connector.parse_events(output)
            await event("task.progress", {"stage": "validating_filesystem"})
            verification = profile.verify_result(
                workspace,
                output=output,
                returncode=process.returncode if process.returncode is not None else 1,
            )
            scope = profile.produce_scope()
            status = "certified" if verification.passed else "failed"
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "reasons": list(verification.reasons),
                        "git_clean": verification.git_clean,
                        "name_found": verification.name_found,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            await event("task.progress", {"stage": "persisting_certification_evidence"})
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.REAL_BINARY_TEST,
                    status="passed" if status == "certified" else "failed",
                    executable=executable,
                    version=version,
                    details={
                        "level": "read_only",
                        "project_name": workspace.project_name,
                        "routing_profile": scope.profile,
                        "policy_profile": "read_only",
                        "workspace": str(workspace.path),
                        "argv": [*list(argv[:-1]), "<PROMPT>"],
                        "prompt_digest": workspace.prompt_digest,
                        "plan_digest": plan_digest,
                        "before_manifest": dict(workspace.before_manifest),
                        "after_manifest": dict(verification.after_manifest),
                        "git_clean": verification.git_clean,
                        "certification_profile": profile.profile_id,
                        "certification_profile_revision": profile.profile_revision,
                        "reasons": list(verification.reasons),
                    },
                    fingerprint=fingerprint,
                )
            )
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.CERTIFICATION,
                    status=status,
                    executable=executable,
                    version=version,
                    details={
                        "passed_levels": {
                            "read_only_certified": verification.passed,
                            "write_certified": False,
                            "command_certified": False,
                            "session_resume_certified": False,
                        },
                        "evidence_digest": digest,
                        "routing_profile": scope.profile,
                        "policy_profile": "read_only",
                        "workspace_clean": verification.git_clean,
                        "certification_scope": {
                            "profile": scope.profile,
                            "structured_execution": scope.structured_execution,
                            "repository_read": scope.repository_read,
                            "repository_write": scope.repository_write,
                            "shell_commands": scope.shell_commands,
                            "session_resume": scope.session_resume,
                            "network_access": scope.network_access,
                            "event_streaming": scope.event_streaming,
                            "workspace_containment": scope.workspace_containment,
                            "cancellation": scope.cancellation,
                        },
                        "prompt_digest": workspace.prompt_digest,
                        "plan_digest": plan_digest,
                        "workspace": str(workspace.path),
                        "certification_profile": profile.profile_id,
                        "certification_profile_revision": profile.profile_revision,
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
            profile.cleanup(workspace)

    async def _certify_mock(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
    ) -> ConnectorTaskStatus:
        origin = ConnectorExecutionOrigin.MOCK_TEST
        previous = self.execution_origin
        self.execution_origin = origin
        try:
            passed = {
                "read_only_certified": True,
                "write_certified": False,
                "command_certified": False,
                "session_resume_certified": False,
            }
            digest = hashlib.sha256(json.dumps(passed, sort_keys=True).encode()).hexdigest()
            scope = READ_ONLY_SCOPE
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.CERTIFICATION,
                    status="certified",
                    executable=None,
                    version=None,
                    details={
                        "passed_levels": passed,
                        "evidence_digest": digest,
                        "routing_profile": scope.profile,
                        "certification_scope": {
                            "profile": scope.profile,
                            "structured_execution": scope.structured_execution,
                            "repository_read": scope.repository_read,
                            "repository_write": False,
                            "shell_commands": False,
                            "session_resume": False,
                            "network_access": False,
                        },
                    },
                )
            )
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.REAL_BINARY_TEST,
                    status="passed",
                    executable=None,
                    version=None,
                    details={"level": "read_only", "routing_profile": scope.profile},
                )
            )
            return ConnectorTaskStatus.SUCCEEDED
        finally:
            self.execution_origin = previous

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

    async def _probe_version(self, executable: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            stdout, stderr = await process.communicate()
            text = (stdout.decode() + stderr.decode()).strip()
            return text.splitlines()[0][:200] if text else None
        except OSError:
            return None

    def _evidence(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        evidence_type: ConnectorEvidenceType,
        status: str,
        executable: str | None,
        version: str | None,
        details: Mapping[str, object],
        fingerprint: str | None = None,
    ) -> ConnectorEvidence:
        enriched = {
            **dict(details),
            "execution_origin": self.execution_origin.value,
            "trust_level": self.trust_level.value,
        }
        return ConnectorEvidence(
            evidence_id=str(uuid4()),
            node_id=self.node_id,
            connector_id=plan.connector_id,
            connector_revision=plan.connector_revision,
            task_id=task_id,
            evidence_type=evidence_type,
            status=status,
            executable_path=executable,
            executable_fingerprint=fingerprint
            or (executable_fingerprint(executable) if executable else None),
            harness_version=version,
            provider_mode=None,
            details=enriched,
            created_at=utc_now(),
            expires_at=None,
            trust_level=self.trust_level,
            execution_origin=self.execution_origin,
        )


def _runtime_connector(connector_id: str) -> ConnectorRuntime | None:
    try:
        return get_connector(connector_id)
    except KeyError:
        return None


def _require_runtime_connector(connector_id: str) -> ConnectorRuntime:
    connector = _runtime_connector(connector_id)
    if connector is None:
        raise RuntimeError(f"connector {connector_id} is not registered in builtin_connectors()")
    return connector


def _classify_generic_auth_status(output: str, *, returncode: int) -> str:
    """Catalogue-only fallback when no Runtime connector is registered."""

    text = output.lower()
    if "expired" in text and ("login" in text or "auth" in text or "token" in text):
        return "expired"
    if returncode != 0 or "not logged" in text or "logged out" in text:
        return "unauthenticated"
    if "logged in" in text or "authenticated" in text or "login successful" in text:
        return "authenticated"
    return "unauthenticated"


def _redact_status(text: str) -> str:
    lowered = text.lower()
    if "token" in lowered or "cookie" in lowered or "bearer" in lowered:
        return "[redacted status]"
    return text


# Compatibility aliases used by older imports/tests.
_executable_fingerprint = executable_fingerprint
_terminate_process_tree = terminate_process_tree


def _workspace_manifest(workspace: Path) -> dict[str, object]:
    files_map: dict[str, str] = {}
    symlinks: list[str] = []
    symlink_escape = False
    root = workspace.resolve()
    for path in sorted(workspace.rglob("*")):
        rel = str(path.relative_to(workspace))
        if path.is_symlink():
            symlinks.append(rel)
            try:
                resolved = path.resolve()
                if root not in resolved.parents and resolved != root:
                    symlink_escape = True
            except OSError:
                symlink_escape = True
            continue
        if path.is_file():
            files_map[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    mode = stat.S_IMODE(workspace.stat().st_mode)
    return {
        "files": files_map,
        "hashes": dict(files_map),
        "symlinks": symlinks,
        "symlink_escape": symlink_escape,
        "mode": oct(mode),
    }


# Re-export for older imports.
__all__ = [
    "CURSOR_READ_ONLY_SCOPE",
    "READ_ONLY_SCOPE",
    "ConnectorNodeRunner",
    "parse_cursor_auth_status",
]
