"""Coding worker task/result contracts adapted to JoyMesh runtime IDs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


CodingWorkerStatus = Literal["completed", "failed", "cancelled"]
TestStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class CodingWorkerRepository:
    path: str
    branch: str | None = None
    worktree: str | None = None


@dataclass(frozen=True)
class CodingWorkerAllowedActions:
    edit_files: bool = True
    run_tests: bool = True
    commit: bool = False
    push: bool = False


@dataclass(frozen=True)
class CodingWorkerTask:
    task_id: str
    mission_id: str
    execution_id: str
    correlation_id: str
    repository: CodingWorkerRepository
    objective: str
    constraints: tuple[str, ...] = ()
    context_package: Any = None
    allowed_actions: CodingWorkerAllowedActions = field(
        default_factory=CodingWorkerAllowedActions
    )
    worker_id: str = "local-codex-worker"
    lease_ttl_seconds: int = 60
    timeout_seconds: int = 300


@dataclass(frozen=True)
class CodingWorkerTestResult:
    command: str
    status: TestStatus
    output_summary: str


@dataclass(frozen=True)
class ArtifactReference:
    kind: str
    uri: str
    digest: str | None = None


@dataclass(frozen=True)
class EvidenceReference:
    kind: str
    uri: str
    summary: str


@dataclass(frozen=True)
class CodingWorkerResult:
    task_id: str
    status: CodingWorkerStatus
    summary: str
    changed_files: tuple[str, ...] = ()
    tests_run: tuple[CodingWorkerTestResult, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    error: str | None = None
    worker_id: str | None = None
    lease_id: str | None = None
    fencing_token: int | None = None
    progress_events: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "tests_run": [
                {
                    "command": item.command,
                    "status": item.status,
                    "output_summary": item.output_summary,
                }
                for item in self.tests_run
            ],
            "artifacts": [
                {"kind": item.kind, "uri": item.uri, "digest": item.digest}
                for item in self.artifacts
            ],
            "evidence": [
                {"kind": item.kind, "uri": item.uri, "summary": item.summary}
                for item in self.evidence
            ],
            "error": self.error,
            "worker_id": self.worker_id,
            "lease_id": self.lease_id,
            "fencing_token": self.fencing_token,
            "progress_events": list(self.progress_events),
        }


PUBLIC_PROGRESS_EVENTS: tuple[str, ...] = (
    "Worker acquired task",
    "Repository opened",
    "Implementation started",
    "Tests running",
    "Verification evidence prepared",
    "Task completed",
)
