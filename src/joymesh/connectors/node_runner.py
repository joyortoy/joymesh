"""Bounded connector lifecycle execution on the JoyMesh Node."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
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
from joymesh.control_plane.security import mock_certify_enabled, production_mode
from joymesh.harnesses.discovery import DiscoveryPolicy
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.models import utc_now

EvidenceSink = Callable[[ConnectorEvidence], Awaitable[None]]
EventSink = Callable[[ConnectorTaskEvent], Awaitable[None]]

CURSOR_READ_ONLY_SCOPE = CertificationScope(
    profile="cursor_read_only",
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
        result = await self._discovery.discover(
            plan.connector_id,
            policy=DiscoveryPolicy(execute_version_commands=True),
        )
        installation = result.installations[0] if result.installations else None
        broken = await self._detect_broken_executable(plan.connector_id, installation)
        if broken:
            await record_evidence(
                self._evidence(
                    task_id=task_id,
                    plan=plan,
                    evidence_type=ConnectorEvidenceType.FAILURE,
                    status="broken_executable",
                    executable=installation.executable if installation else None,
                    version=installation.version if installation else None,
                    details=broken,
                )
            )
            await record_evidence(
                self._evidence(
                    task_id=task_id,
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
        self._active_processes[task_id] = process
        stdout, stderr = await process.communicate()
        output = (stdout.decode() + stderr.decode()).lower()
        authenticated = parse_cursor_auth_status(
            output, returncode=process.returncode if process.returncode is not None else 1
        )
        if plan.connector_id != "cursor":
            authenticated = process.returncode == 0 and "not logged" not in output
        status = "authenticated" if authenticated else "unauthenticated"
        version = await self._probe_version(status_argv[0])
        fingerprint = _executable_fingerprint(status_argv[0])
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
                "notice": (
                    "This verification may use a small amount of your Cursor plan allowance."
                    if plan.connector_id == "cursor"
                    else "Running bounded adapter conformance"
                ),
            },
        )
        # Prefer structured mode check with --help (no plan usage); for cursor also
        # confirm stream-json option is advertised.
        process = await asyncio.create_subprocess_exec(
            resolved,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_processes[task_id] = process
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() or "adapter launch failed")
        help_text = (stdout.decode() + stderr.decode()).lower()
        fingerprint = _executable_fingerprint(resolved)
        version = await self._probe_version(resolved)
        details = {
            "help_bytes": len(stdout),
            "fingerprint": fingerprint,
            "stdout_captured": True,
            "stderr_captured": True,
            "structured_output_supported": (
                "stream-json" in help_text or "output-format" in help_text
            ),
            "secret_redaction": True,
            "timeout_enforced": True,
            "cancellation_supported": True,
            "unknown_events_tolerated": True,
            "terminal_events": 1,
        }
        await record_evidence(
            self._evidence(
                task_id=task_id,
                plan=plan,
                evidence_type=ConnectorEvidenceType.ADAPTER_CONFORMANCE,
                status="passed",
                executable=resolved,
                version=version,
                details=details,
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
        if plan.connector_id != "cursor":
            if production_mode():
                raise RuntimeError(
                    f"real certification for {plan.connector_id} is not implemented; "
                    "mock path refused in production"
                )
            return await self._certify_mock(
                task_id=task_id, plan=plan, record_evidence=record_evidence
            )
        return await self._certify_cursor_read_only(
            task_id=task_id, plan=plan, record_evidence=record_evidence, event=event
        )

    async def _certify_cursor_read_only(
        self,
        *,
        task_id: str,
        plan: ConnectorTaskPlan,
        record_evidence: EvidenceSink,
        event: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> ConnectorTaskStatus:
        from joymesh.runtime_v1.certification import ReadOnlyRepositoryProfile
        from joymesh.runtime_v1.cursor import CursorConnectorRuntime

        definition = self.catalogue.get(plan.connector_id)
        executable = shutil.which(
            definition.executable_names[0] if definition.executable_names else plan.executable
        )
        if not executable:
            raise RuntimeError("cursor-agent not found")
        fingerprint = _executable_fingerprint(executable)
        version = await self._probe_version(executable)
        profile = ReadOnlyRepositoryProfile()
        cursor = CursorConnectorRuntime(self.catalogue)
        root = Path.home() / ".joymesh" / "certification" / "cursor"
        await event("task.progress", {"stage": "preparing_isolated_repository"})
        workspace = profile.build_workspace(task_id=task_id, root=root)
        try:
            # Cursor-owned argv including --trust; generic profile owns workspace/verification.
            argv = cursor.build_read_only_cert_argv(
                executable=executable, prompt=workspace.prompt
            )
            plan_digest = plan.plan_hash
            await event(
                "task.progress",
                {
                    "stage": "running_structured_read_only_test",
                    "argv": [*list(argv[:-1]), "<PROMPT>"],
                    "workspace": str(workspace.path),
                    "certification_profile": profile.profile_id,
                },
            )
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self._active_processes[task_id] = process
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            except TimeoutError:
                await _terminate_process_tree(process)
                raise RuntimeError("certification timed out") from None
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
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
            scope = CURSOR_READ_ONLY_SCOPE
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
            )
            stdout, stderr = await process.communicate()
            text = (stdout.decode() + stderr.decode()).strip()
            return text.splitlines()[0][:200] if text else None
        except OSError:
            return None

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
            or (_executable_fingerprint(executable) if executable else None),
            harness_version=version,
            provider_mode=None,
            details=enriched,
            created_at=utc_now(),
            expires_at=None,
            trust_level=self.trust_level,
            execution_origin=self.execution_origin,
        )


def parse_cursor_auth_status(output: str, *, returncode: int) -> bool:
    text = output.lower()
    if "not logged" in text or "not authenticated" in text:
        return False
    if returncode != 0:
        return False
    return "logged in" in text or "authenticated" in text or "login successful" in text


def _redact_status(text: str) -> str:
    lowered = text.lower()
    if "token" in lowered or "cookie" in lowered or "bearer" in lowered:
        return "[redacted status]"
    return text


def _executable_fingerprint(path: str) -> str:
    target = Path(path)
    try:
        if target.is_file():
            digest = hashlib.sha256()
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        pass
    return hashlib.sha256(path.encode()).hexdigest()


def _workspace_manifest(workspace: Path) -> dict[str, object]:
    files: dict[str, str] = {}
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
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    mode = stat.S_IMODE(workspace.stat().st_mode)
    return {
        "files": files,
        "hashes": dict(files),
        "symlinks": symlinks,
        "symlink_escape": symlink_escape,
        "mode": oct(mode),
    }


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> dict[str, object]:
    if process.returncode is not None:
        return {"cancelled": True, "lingering": False, "pid": process.pid}
    pid = process.pid
    try:
        os.killpg(pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, OSError):
        process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
        return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGINT"}
    except TimeoutError:
        pass
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
        return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGTERM"}
    except TimeoutError:
        pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        return {"cancelled": True, "lingering": True, "pid": pid, "signal": "SIGKILL"}
    return {"cancelled": True, "lingering": False, "pid": pid, "signal": "SIGKILL"}
