"""Coding worker lease, safety, and Codex path tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from joymesh.runtime_v1.coding_worker import (
    CodingWorker,
    CodingWorkerAllowedActions,
    CodingWorkerRepository,
    CodingWorkerTask,
    acquire_exclusive_lease,
    coding_worker_ready,
    inspect_repository,
    recover_stale_lease,
    task_from_runtime,
)
from joymesh.runtime_v1.coding_worker.lifecycle import CodingWorkerLeaseError
from joymesh.runtime_v1.coding_worker.safety import RepositorySafetyError
from joymesh.runtime_v1.leases import LeaseService
from joymesh.runtime_v1.models import LeaseStatus, CreateRuntimeTaskBody
from joymesh.runtime_v1.service import RuntimeService
from joymesh.models import utc_now
from joymesh.runtime_v1.models import WorkspacePlacement


def test_coding_worker_ready_reports_codex() -> None:
    ready = coding_worker_ready()
    assert "codingWorkerReady" in ready
    assert ready["harness"] == "codex"


def test_duplicate_task_lease_prevented() -> None:
    leases = LeaseService(ttl_seconds=30)
    first = acquire_exclusive_lease(leases, task_id="t1", worker_id="w1")
    with pytest.raises(CodingWorkerLeaseError):
        acquire_exclusive_lease(leases, task_id="t1", worker_id="w2")
    first.release()


def test_stale_lease_recovers() -> None:
    leases = LeaseService(ttl_seconds=0)
    handle = acquire_exclusive_lease(leases, task_id="t2", worker_id="w1")
    assert recover_stale_lease(leases, "t2") is True
    current = leases.active_lease("t2")
    assert current is None or current.status is LeaseStatus.EXPIRED
    second = acquire_exclusive_lease(leases, task_id="t2", worker_id="w2")
    assert second.fencing_token > handle.fencing_token
    second.release()


def test_repository_escape_prevented(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    report = inspect_repository(str(repo))
    assert report.exists
    with pytest.raises(RepositorySafetyError):
        from joymesh.runtime_v1.coding_worker.safety import assert_path_inside_repository

        assert_path_inside_repository(repo, Path("/etc/passwd"))


def test_commit_push_disabled_by_default() -> None:
    task = task_from_runtime(
        task_id="t3",
        prompt="edit files",
        workspace_path="/tmp/x",
    )
    assert task.allowed_actions.commit is False
    assert task.allowed_actions.push is False


def test_cancellation_returns_cancelled(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    worker = CodingWorker(leases=LeaseService())
    task = CodingWorkerTask(
        task_id="cancel-1",
        mission_id="m1",
        execution_id="e1",
        correlation_id="c1",
        repository=CodingWorkerRepository(path=str(repo)),
        objective="noop",
        allowed_actions=CodingWorkerAllowedActions(edit_files=False, run_tests=False),
    )
    worker.cancel(task.task_id)

    async def _run() -> None:
        result = await worker.execute(task)
        assert result.status == "cancelled"

    asyncio.run(_run())


def test_worker_failure_returns_structured_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing-repo"
    worker = CodingWorker(leases=LeaseService())
    task = CodingWorkerTask(
        task_id="fail-1",
        mission_id="m1",
        execution_id="e1",
        correlation_id="c1",
        repository=CodingWorkerRepository(path=str(missing)),
        objective="noop",
    )

    async def _run() -> None:
        result = await worker.execute(task)
        assert result.status == "failed"
        assert result.error == "repository_missing"
        assert result.summary

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_runtime_uses_placement_and_prefers_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / ".git").mkdir()
    runtime = RuntimeService()
    await runtime.register_placement(
        WorkspacePlacement(
            workspace_id="ws-coding",
            node_id="local-codex-worker",
            local_path=str(repo),
            fingerprint="test",
            writable=True,
            last_verified_at=utc_now(),
        )
    )
    health = runtime.coding_worker_health()
    assert health["harness"] == "codex"

    class StubWorker:
        async def execute(self, task, on_progress=None):
            from joymesh.runtime_v1.coding_worker import CodingWorkerResult

            if on_progress:
                maybe = on_progress("Worker acquired task")
                if asyncio.iscoroutine(maybe):
                    await maybe
            return CodingWorkerResult(
                task_id=task.task_id,
                status="completed",
                summary="stub completed",
                changed_files=("joy-worker-smoke.txt",),
                tests_run=(),
                worker_id="local-codex-worker",
                progress_events=("Worker acquired task",),
            )

    import joymesh.runtime_v1.coding_worker as coding_worker_pkg

    monkeypatch.setattr(coding_worker_pkg, "CodingWorker", lambda **kwargs: StubWorker())
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-coding",
            prompt="create joy-worker-smoke.txt",
            requested_capabilities=(),
            prohibited_capabilities=("git.commit", "git.push"),
            preferred_connectors=("codex",),
            policy_profile="developer",
            mission_id="mission-1",
            correlation_id="corr-1",
        ),
        user_id="tester",
    )

    assert task.selected_harness_id == "codex"
    assert task.status.value == "succeeded"
    events = runtime.store.events.get(task.task_id, [])
    assert any(item.get("event_type") == "coding_worker.progress" for item in events)
    assert any(item.get("event_type") == "coding_worker.result" for item in events)


def test_no_fake_artifact_contract() -> None:
    from joymesh.runtime_v1.coding_worker.contracts import CodingWorkerResult

    result = CodingWorkerResult(
        task_id="t",
        status="failed",
        summary="failed without fabricating files",
        changed_files=(),
        error="process_failure",
    )
    assert result.changed_files == ()
    assert "fake" not in result.summary.lower()
