"""Neutral JoyMesh worker runtime contracts and lease validation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from joymesh.models import utc_now
from joymesh.runtime_v1.contracts import (
    ExecutionLeaseToken,
    WorkerCapacityReport,
    WorkerHeartbeat,
    WorkerReport,
)
from joymesh.runtime_v1.models import CreateRuntimeTaskBody, RuntimeTaskStatus
from joymesh.runtime_v1.service import RuntimeService, build_ready_cursor_node
from joymesh.runtime_v1.workers import (
    LeaseValidationError,
    WorkerLeaseValidator,
    build_worker_heartbeat,
    build_worker_report,
)


def test_worker_report_serialisation() -> None:
    runtime = RuntimeService()
    snapshot = build_ready_cursor_node(node_id="mac", workspace_id="ws-1")
    report = runtime.build_worker_report(snapshot)
    payload = report.as_dict()
    assert payload["worker_id"] == "mac"
    assert payload["online"] is True
    assert "cursor" in {item["harness_id"] for item in payload["harnesses"]}
    assert "organisation_quota" not in payload
    assert "placement_score" not in payload
    assert "fairness" not in payload


def test_register_node_publishes_neutral_report_not_fleet() -> None:
    runtime = RuntimeService()
    assert not hasattr(runtime, "fleet") or getattr(runtime, "fleet", None) is None
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws-1"))
    report = runtime.latest_worker_report("mac")
    assert isinstance(report, WorkerReport)
    assert report.worker_id == "mac"


def test_heartbeat_generation_and_sequence() -> None:
    snapshot = build_ready_cursor_node(node_id="mac", workspace_id="ws-1")
    report = build_worker_report(snapshot)
    hb1 = build_worker_heartbeat(report, sequence=1)
    hb2 = build_worker_heartbeat(report, sequence=2)
    assert isinstance(hb1, WorkerHeartbeat)
    assert hb2.sequence == 2
    validator = WorkerLeaseValidator()
    validator.accept_heartbeat_sequence("mac", 1)
    validator.accept_heartbeat_sequence("mac", 2)
    with pytest.raises(LeaseValidationError, match="sequence"):
        validator.accept_heartbeat_sequence("mac", 2)


def test_lease_validation_success_and_rejections() -> None:
    validator = WorkerLeaseValidator()
    now = utc_now()
    lease = ExecutionLeaseToken(
        lease_id="lease-1",
        worker_id="mac",
        execution_id="exec-1",
        attempt_id="att-1",
        generation=1,
        fencing_token=3,
        expires_at=now + timedelta(minutes=5),
        signature="sig",
    )
    accepted = validator.validate(
        lease, worker_id="mac", execution_id="exec-1", expected_signature="sig"
    )
    assert accepted.lease_id == "lease-1"
    with pytest.raises(LeaseValidationError) as wrong_worker:
        validator.validate(lease, worker_id="other")
    assert wrong_worker.value.reason_code == "wrong_worker"
    with pytest.raises(LeaseValidationError) as expired:
        validator.validate(
            ExecutionLeaseToken(
                lease_id="lease-2",
                worker_id="mac",
                execution_id="exec-1",
                attempt_id="att-1",
                generation=1,
                fencing_token=4,
                expires_at=now - timedelta(seconds=1),
            ),
            worker_id="mac",
        )
    assert expired.value.reason_code == "expired"
    with pytest.raises(LeaseValidationError) as replay:
        validator.validate(lease, worker_id="mac", execution_id="exec-1", expected_signature="sig")
    assert replay.value.reason_code == "replay"


def test_capacity_report_has_no_org_policy_fields() -> None:
    capacity = WorkerCapacityReport(cpu=2, ram_mb=4096, parallel_execution_limit=2)
    payload = capacity.as_dict()
    forbidden = {
        "organisation_quota",
        "reservation",
        "fairness_score",
        "placement_score",
        "subscription",
        "token_allowance",
    }
    assert forbidden.isdisjoint(payload)


@pytest.mark.asyncio
async def test_runtime_remote_lease_without_fleet_scheduler() -> None:
    runtime = RuntimeService()
    runtime.register_node(build_ready_cursor_node(node_id="mac", workspace_id="ws-1"))
    task = await runtime.create_task(
        CreateRuntimeTaskBody(
            workspace_id="ws-1",
            prompt="summarise",
            policy_profile="read_only",
            requested_capabilities=("repository.read", "structured_output"),
        ),
        user_id="user",
    )
    assert task.status is RuntimeTaskStatus.LEASED
    assert getattr(runtime, "fleet", None) is None


def test_distributed_scheduler_package_is_compat_only() -> None:
    with pytest.warns(DeprecationWarning):
        import joymesh.runtime_v1.distributed_scheduler as deprecated

    assert deprecated.WorkerReport is WorkerReport
    with pytest.raises(ImportError, match="removed"):
        _ = deprecated.DistributedScheduler  # type: ignore[attr-defined]


def test_architecture_no_fleet_scheduler_modules() -> None:
    root = Path(__file__).resolve().parents[1] / "src/joymesh/runtime_v1"
    # Production scheduling modules must not exist beside the compat shim.
    forbidden_files = {
        "placement.py",
        "fairness.py",
        "queue.py",
        "ha.py",
        "registry.py",
        "worker_leases.py",
        "service.py",
        "store.py",
        "heartbeats.py",
        "models.py",
        "states.py",
    }
    package = root / "distributed_scheduler"
    assert package.is_dir()
    present = {path.name for path in package.iterdir() if path.suffix == ".py"}
    assert present <= {"__init__.py"}
    assert forbidden_files.isdisjoint(present)


def test_architecture_no_joycli_or_joypay_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src/joymesh"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if "import joycli" in text or "from joycli" in text:
            offenders.append(str(path))
        if "import joypay" in text or "from joypay" in text:
            offenders.append(str(path))
    assert offenders == []


def test_architecture_forbidden_control_plane_symbols_absent_from_public_path() -> None:
    """JoyMesh must not export fleet-scheduling control-plane classes."""

    import joymesh.runtime_v1 as runtime_v1

    forbidden = {
        "DistributedScheduler",
        "PlacementEngine",
        "FairnessPolicy",
        "FairnessController",
        "SchedulerHACoordinator",
        "ExecutionQueue",
        "OrganisationQuota",
        "SubscriptionPolicy",
    }
    exported = set(dir(runtime_v1))
    assert forbidden.isdisjoint(exported)
