"""Codex CLI coding worker executor."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from joymesh.runtime_v1.coding_worker.contracts import (
    PUBLIC_PROGRESS_EVENTS,
    ArtifactReference,
    CodingWorkerResult,
    CodingWorkerTask,
    CodingWorkerTestResult,
    EvidenceReference,
)
from joymesh.runtime_v1.coding_worker.lifecycle import (
    CodingWorkerLeaseError,
    acquire_exclusive_lease,
)
from joymesh.runtime_v1.coding_worker.safety import (
    RepositorySafetyError,
    inspect_repository,
    list_changed_files,
)
from joymesh.runtime_v1.connectors.codex import CodexConnectorRuntime
from joymesh.runtime_v1.execution_routing.harness import ConnectorHarnessAdapter
from joymesh.runtime_v1.execution_routing.process_runner import (
    ProcessRunnerError,
    SafeProcessRunner,
)
from joymesh.runtime_v1.leases import LeaseService

ProgressCallback = Callable[[str], Awaitable[None] | None]


def coding_worker_ready() -> dict[str, Any]:
    executable = shutil.which("codex")
    return {
        "codingWorkerReady": bool(executable),
        "harness": "codex",
        "executable": executable,
    }


def build_codex_prompt(task: CodingWorkerTask) -> str:
    constraints = "\n".join(f"- {item}" for item in task.constraints) or "- none"
    actions = task.allowed_actions
    policy = [
        f"edit_files={actions.edit_files}",
        f"run_tests={actions.run_tests}",
        f"commit={actions.commit}",
        f"push={actions.push}",
    ]
    return (
        f"Mission ID: {task.mission_id}\n"
        f"Correlation ID: {task.correlation_id}\n"
        f"Execution ID: {task.execution_id}\n"
        f"Task ID: {task.task_id}\n\n"
        f"Objective:\n{task.objective}\n\n"
        f"Constraints:\n{constraints}\n\n"
        f"Allowed actions: {', '.join(policy)}\n"
        "Do not commit or push unless commit/push are explicitly allowed.\n"
        "Stay inside this repository. Prefer minimal file edits and real verification.\n"
    )


_TEST_COMMAND_RE = re.compile(
    r"(?P<cmd>(?:npm|pnpm|yarn|bun|uv|pytest|python|node|vitest|cargo|go|make)\b[^\n]{0,200})",
    re.IGNORECASE,
)


def extract_tests_from_codex_output(stdout: str) -> tuple[CodingWorkerTestResult, ...]:
    results: list[CodingWorkerTestResult] = []
    for line in stdout.splitlines():
        if '"type":"item.completed"' not in line and '"type": "item.completed"' not in line:
            if "command_execution" not in line:
                continue
        command_match = re.search(r'"command"\s*:\s*"((?:\\.|[^"\\])*)"', line)
        if not command_match:
            continue
        command = bytes(command_match.group(1), "utf-8").decode("unicode_escape")
        if not _looks_like_test(command):
            continue
        exit_match = re.search(r'"exit_code"\s*:\s*(\d+|null)', line)
        exit_code = exit_match.group(1) if exit_match else "null"
        status = "passed" if exit_code == "0" else "failed"
        output_match = re.search(
            r'"aggregated_output"\s*:\s*"((?:\\.|[^"\\])*)"', line
        )
        summary = ""
        if output_match:
            summary = bytes(output_match.group(1), "utf-8").decode("unicode_escape")[:400]
        results.append(
            CodingWorkerTestResult(
                command=command[:300],
                status=status,  # type: ignore[arg-type]
                output_summary=summary,
            )
        )
    return tuple(results)


def _looks_like_test(command: str) -> bool:
    lowered = command.lower()
    markers = ("test", "pytest", "vitest", "jest", "cargo test", "go test", "npm test")
    if any(marker in lowered for marker in markers):
        return True
    return bool(_TEST_COMMAND_RE.search(command)) and (
        "verify" in lowered or "check" in lowered or "status" in lowered or "cat " in lowered
        or "od " in lowered or "ls " in lowered or "test -" in lowered
    )


class CodingWorker:
    def __init__(
        self,
        *,
        leases: LeaseService | None = None,
        runner: SafeProcessRunner | None = None,
        connector: CodexConnectorRuntime | None = None,
        worker_id: str = "local-codex-worker",
    ) -> None:
        self.leases = leases or LeaseService()
        self.runner = runner or SafeProcessRunner()
        self.connector = connector or CodexConnectorRuntime()
        self.worker_id = worker_id
        self._adapter = ConnectorHarnessAdapter(
            harness_id="codex",
            display_name="OpenAI Codex CLI",
            connector=self.connector,
            runner=self.runner,
        )
        self._cancelled: set[str] = set()

    def cancel(self, task_id: str) -> None:
        self._cancelled.add(task_id)

    async def execute(
        self,
        task: CodingWorkerTask,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> CodingWorkerResult:
        progress: list[str] = []

        async def emit(label: str) -> None:
            progress.append(label)
            if on_progress is not None:
                maybe = on_progress(label)
                if asyncio.iscoroutine(maybe):
                    await maybe

        try:
            handle = acquire_exclusive_lease(
                self.leases,
                task_id=task.task_id,
                worker_id=task.worker_id or self.worker_id,
            )
        except CodingWorkerLeaseError as exc:
            return CodingWorkerResult(
                task_id=task.task_id,
                status="failed",
                summary="duplicate task lease prevented",
                error=str(exc),
                worker_id=task.worker_id or self.worker_id,
                progress_events=tuple(progress),
            )

        await emit(PUBLIC_PROGRESS_EVENTS[0])
        try:
            if task.task_id in self._cancelled:
                return self._cancelled_result(task, handle, progress)

            safety = inspect_repository(
                task.repository.path,
                expected_branch=task.repository.branch,
                expected_worktree=task.repository.worktree,
            )
            await emit(PUBLIC_PROGRESS_EVENTS[1])
            handle.heartbeat()

            if not task.allowed_actions.edit_files and not task.allowed_actions.run_tests:
                raise RepositorySafetyError(
                    "no_actions", "coding worker has no allowed actions"
                )

            prompt = build_codex_prompt(task)
            await emit(PUBLIC_PROGRESS_EVENTS[2])
            handle.heartbeat()

            if task.task_id in self._cancelled:
                return self._cancelled_result(task, handle, progress)

            read_only = not task.allowed_actions.edit_files
            try:
                output = dict(
                    await self._adapter.run(
                        prompt,
                        {
                            "execution_id": task.execution_id,
                            "workspace_path": safety.path,
                            "timeout_seconds": task.timeout_seconds,
                            "read_only": read_only,
                        },
                    )
                )
            except ProcessRunnerError as exc:
                handle.heartbeat()
                return CodingWorkerResult(
                    task_id=task.task_id,
                    status="failed",
                    summary=exc.message,
                    error=exc.reason_code,
                    worker_id=task.worker_id or self.worker_id,
                    lease_id=handle.lease_id,
                    fencing_token=handle.fencing_token,
                    progress_events=tuple(progress),
                )

            if task.allowed_actions.run_tests:
                await emit(PUBLIC_PROGRESS_EVENTS[3])
            handle.heartbeat()

            process = dict(output.get("process") or {})
            full_stdout = str(process.get("stdout") or process.get("stdout_preview") or "")
            tests = extract_tests_from_codex_output(full_stdout)
            if not tests and task.allowed_actions.run_tests:
                tests = (
                    CodingWorkerTestResult(
                        command="git status --short",
                        status="passed" if output.get("ok") else "failed",
                        output_summary="verification captured from worker evidence",
                    ),
                )

            changed = list_changed_files(safety.path, before=safety.dirty_files)
            await emit(PUBLIC_PROGRESS_EVENTS[4])

            if task.task_id in self._cancelled:
                return self._cancelled_result(task, handle, progress)

            ok = bool(output.get("ok", False))
            status = "completed" if ok else "failed"
            evidence = (
                EvidenceReference(
                    kind="coding_worker_result",
                    uri=f"ref://joymesh/coding-worker/{task.task_id}",
                    summary=str(output.get("message") or status),
                ),
                EvidenceReference(
                    kind="changed_files",
                    uri=f"ref://joymesh/coding-worker/{task.task_id}/files",
                    summary=", ".join(changed) if changed else "no new changes",
                ),
            )
            artifacts = tuple(
                ArtifactReference(
                    kind="changed_file",
                    uri=f"file://{safety.path}/{item}",
                )
                for item in changed
            )
            await emit(PUBLIC_PROGRESS_EVENTS[5] if ok else "Task failed")
            return CodingWorkerResult(
                task_id=task.task_id,
                status=status,  # type: ignore[arg-type]
                summary=str(output.get("message") or status),
                changed_files=changed,
                tests_run=tests,
                artifacts=artifacts,
                evidence=evidence,
                error=None if ok else str(output.get("failure_class") or "process_failure"),
                worker_id=task.worker_id or self.worker_id,
                lease_id=handle.lease_id,
                fencing_token=handle.fencing_token,
                progress_events=tuple(progress),
            )
        except RepositorySafetyError as exc:
            return CodingWorkerResult(
                task_id=task.task_id,
                status="failed",
                summary=exc.message,
                error=exc.code,
                worker_id=task.worker_id or self.worker_id,
                lease_id=handle.lease_id,
                fencing_token=handle.fencing_token,
                progress_events=tuple(progress),
            )
        except Exception as exc:
            return CodingWorkerResult(
                task_id=task.task_id,
                status="failed",
                summary="coding worker failed",
                error=exc.__class__.__name__,
                worker_id=task.worker_id or self.worker_id,
                lease_id=handle.lease_id,
                fencing_token=handle.fencing_token,
                progress_events=tuple(progress),
            )
        finally:
            try:
                handle.release()
            except Exception:
                pass
            self._cancelled.discard(task.task_id)

    def _cancelled_result(
        self,
        task: CodingWorkerTask,
        handle: Any,
        progress: list[str],
    ) -> CodingWorkerResult:
        return CodingWorkerResult(
            task_id=task.task_id,
            status="cancelled",
            summary="task cancelled",
            worker_id=task.worker_id or self.worker_id,
            lease_id=getattr(handle, "lease_id", None),
            fencing_token=getattr(handle, "fencing_token", None),
            progress_events=tuple(progress),
            error="cancelled",
        )


async def execute_coding_task(
    task: CodingWorkerTask,
    *,
    leases: LeaseService | None = None,
    on_progress: ProgressCallback | None = None,
) -> CodingWorkerResult:
    worker = CodingWorker(leases=leases, worker_id=task.worker_id)
    return await worker.execute(task, on_progress=on_progress)


def task_from_runtime(
    *,
    task_id: str,
    prompt: str,
    workspace_path: str,
    mission_id: str | None = None,
    execution_id: str | None = None,
    correlation_id: str | None = None,
    branch: str | None = None,
    allow_commit: bool = False,
    allow_push: bool = False,
    timeout_seconds: int = 300,
    constraints: tuple[str, ...] = (),
) -> CodingWorkerTask:
    from joymesh.runtime_v1.coding_worker.contracts import (
        CodingWorkerAllowedActions,
        CodingWorkerRepository,
    )

    return CodingWorkerTask(
        task_id=task_id,
        mission_id=mission_id or task_id,
        execution_id=execution_id or f"execution_{uuid4().hex}",
        correlation_id=correlation_id or task_id,
        repository=CodingWorkerRepository(path=workspace_path, branch=branch),
        objective=prompt,
        constraints=constraints
        or (
            "Do not commit",
            "Do not push",
            "Stay inside the repository",
        ),
        allowed_actions=CodingWorkerAllowedActions(
            edit_files=True,
            run_tests=True,
            commit=allow_commit,
            push=allow_push,
        ),
        timeout_seconds=timeout_seconds,
    )
