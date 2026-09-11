"""Production hardening tests: delivery, directive, approval, diagnostics."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from joymesh.adapters.fake import FakeHarnessAdapter
from joymesh.control_plane.security import ExpiredMessageError, ReplayDetectedError
from joymesh.delivery import (
    TRANSPORT_VERSION,
    DeliveryOutbox,
    DeliveryWorker,
    MemoryDeliveryTransport,
    RuntimeDeliveryPublisher,
    TransportVersionError,
    UnixSocketDeliveryServer,
    UnixSocketDeliveryTransport,
)
from joymesh.delivery.transports.protocol import assert_compatible_version
from joymesh.diagnostics import (
    ProviderDiagnosticCode,
    classify_detail,
    from_availability,
    from_failure_kind,
)
from joymesh.execution import (
    ApprovalContinuationService,
    DirectiveValidationError,
    ExecutionDirective,
    validate_directive,
)
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import (
    BillingRoute,
    Capability,
    FailureKind,
    RouteCandidate,
    RunRequest,
    RunStatus,
    SubscriptionCreate,
    utc_now,
)
from joymesh.quota.contracts import HarnessAvailability
from joymesh.registry import AdapterRegistry
from joymesh.runtime_snapshot.contracts import RuntimeSnapshot
from joymesh.runtime_snapshot.validators import RuntimeSnapshotValidationError
from joymesh.service import JoyMesh
from tests.fixtures.fake_harness_definition import fake_harness_definition
from tests.quota_test_utils import install_ready_quota


@pytest.fixture
async def mesh(tmp_path: Path):
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    instance = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    install_ready_quota(instance)
    await instance.initialize()
    await instance.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="fake",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
            max_concurrency=4,
        )
    )
    yield instance
    await instance.close()


@pytest.mark.asyncio
async def test_delivery_publish_replay_duplicate_and_compaction(tmp_path: Path) -> None:
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3", max_entries=5)
    publisher = RuntimeDeliveryPublisher(outbox)
    transport = MemoryDeliveryTransport()
    worker = DeliveryWorker(outbox, transport, max_attempts=3)

    snap = RuntimeSnapshot(
        snapshot_id="s1",
        observed_at=utc_now(),
        harnesses=(),
        schema_version=1,
    )
    first = publisher.publish_snapshot(snap)
    dup = publisher.publish_snapshot(snap)
    assert first.envelope_id == dup.envelope_id or first.idempotency_key == dup.idempotency_key
    assert outbox.size() == 1

    await worker.flush_once()
    assert transport.published
    assert outbox.size() == 0

    # Crash recovery / replay: re-append and flush again.
    publisher.publish_event(
        event_type="runtime.snapshot_updated",
        payload={"harness_id": "fake"},
        idempotency_key="evt:1",
    )
    # Simulate process restart with same outbox file.
    outbox2 = DeliveryOutbox(tmp_path / "outbox.sqlite3", max_entries=5)
    worker2 = DeliveryWorker(outbox2, MemoryDeliveryTransport())
    assert outbox2.size() == 1
    await worker2.flush_once()
    assert outbox2.size() == 0

    # Bounded growth / compaction — use a dedicated outbox.
    bounded = DeliveryOutbox(tmp_path / "bounded.sqlite3", max_entries=5)
    bounded_publisher = RuntimeDeliveryPublisher(bounded)
    for index in range(10):
        bounded_publisher.publish_event(
            event_type="ping",
            payload={"i": index},
            idempotency_key=f"ping:{index}",
        )
    assert bounded.size() <= 5
    outbox.close()
    outbox2.close()
    bounded.close()


@pytest.mark.asyncio
async def test_unix_transport_reconnect_heartbeat_version(tmp_path: Path) -> None:
    sock = Path("/tmp") / f"joymesh-delivery-{uuid4().hex}.sock"
    server = UnixSocketDeliveryServer(sock)
    await server.start()
    transport = UnixSocketDeliveryTransport(sock)
    await transport.connect()
    await transport.heartbeat()
    assert transport.negotiated_version() == TRANSPORT_VERSION

    outbox = DeliveryOutbox(tmp_path / "u.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox)
    worker = DeliveryWorker(outbox, transport)
    env = publisher.publish_event(
        event_type="heartbeat_probe",
        payload={"ok": True},
        idempotency_key="hb:1",
    )
    await worker.flush_once()
    assert any(item.envelope_id == env.envelope_id for item in server.received)

    await transport.close()
    await transport.connect()  # reconnect
    await transport.heartbeat()

    with pytest.raises(TransportVersionError):
        assert_compatible_version(TRANSPORT_VERSION + 1)

    await transport.close()
    await server.stop()
    outbox.close()


@pytest.mark.asyncio
async def test_directive_validation_expiry_auth_fallback_capability(tmp_path: Path) -> None:
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'dir.db'}",
        registry=registry,
    )
    install_ready_quota(mesh)
    await mesh.initialize()

    expired = ExecutionDirective(
        execution_id="exec-1",
        attempt_id="att-1",
        selected_harness="fake",
        required_capabilities=frozenset(),
        routing_decision_id="rd-1",
        runtime_projection_revision="rev-1",
        authorization_reference="auth-1",
        expires_at=utc_now() - timedelta(seconds=1),
    )
    with pytest.raises(DirectiveValidationError) as exc:
        await validate_directive(
            expired,
            registry=mesh.registry,
            runtime_snapshots=mesh.runtime_snapshots,
            harness_enabled=True,
        )
    assert exc.value.code is ProviderDiagnosticCode.DIRECTIVE_EXPIRED

    missing_cap = ExecutionDirective(
        execution_id="exec-2",
        attempt_id="att-2",
        selected_harness="fake",
        required_capabilities=frozenset({Capability.MCP}),
        routing_decision_id="rd-2",
        runtime_projection_revision="rev-2",
        authorization_reference="auth-2",
        expires_at=utc_now() + timedelta(minutes=5),
    )
    with pytest.raises(DirectiveValidationError) as exc2:
        await validate_directive(
            missing_cap,
            registry=mesh.registry,
            runtime_snapshots=mesh.runtime_snapshots,
            harness_enabled=True,
        )
    assert exc2.value.code is ProviderDiagnosticCode.CAPABILITY_MISMATCH

    fallback = ExecutionDirective(
        execution_id="exec-3",
        attempt_id="att-3",
        selected_harness="fake",
        allowed_fallbacks=("opencode",),
        required_capabilities=frozenset(),
        routing_decision_id="rd-3",
        runtime_projection_revision="rev-3",
        authorization_reference="auth-3",
        expires_at=utc_now() + timedelta(minutes=5),
        fallback_authorization_references=frozenset({"other"}),
    )
    with pytest.raises(DirectiveValidationError) as exc3:
        await validate_directive(
            fallback,
            registry=mesh.registry,
            runtime_snapshots=mesh.runtime_snapshots,
            is_fallback=True,
            harness_enabled=True,
        )
    assert exc3.value.code is ProviderDiagnosticCode.FALLBACK_NOT_AUTHORIZED
    await mesh.close()


@pytest.mark.asyncio
async def test_start_run_with_valid_directive(mesh: JoyMesh, tmp_path: Path) -> None:
    profile = (await mesh.list_subscriptions())[0]
    directive = ExecutionDirective(
        execution_id="exec-ok",
        attempt_id="att-ok",
        selected_harness="fake",
        required_capabilities=frozenset(),
        routing_decision_id="rd-ok",
        runtime_projection_revision="rev-ok",
        authorization_reference="auth-ok",
        expires_at=utc_now() + timedelta(minutes=10),
    )
    request = RunRequest(
        task="NORMAL",
        workspace=str(tmp_path),
        execution_id="exec-ok",
        directive=directive.as_dict(),
    )
    route = RouteCandidate(
        harness_id="fake",
        subscription_id=profile.id,
        score=1.0,
        eligible=True,
        reasons=("directive",),
    )
    run = await mesh.start_run(request=request, route=route)
    completed = await mesh.wait(run.id)
    assert completed.status is RunStatus.COMPLETED
    checkpoint = mesh.checkpoints.get("exec-ok")
    assert checkpoint is not None


def test_approval_request_approve_reject_expiry_replay() -> None:
    service = ApprovalContinuationService(default_ttl_seconds=60)
    payload = {"execution_id": "e1", "harness": "fake"}
    request = service.request_approval(
        execution_id="e1",
        attempt_id="a1",
        directive_payload=payload,
        reason="paid fallback",
    )
    approved = service.sign_response(request, approved=True)
    service.verify_response(
        approved,
        expected_execution_id="e1",
        expected_attempt_id="a1",
        expected_directive_hash=request.directive_hash,
    )
    with pytest.raises(ReplayDetectedError):
        service.verify_response(
            approved,
            expected_execution_id="e1",
            expected_attempt_id="a1",
            expected_directive_hash=request.directive_hash,
        )

    rejected_req = service.request_approval(
        execution_id="e2",
        attempt_id="a2",
        directive_payload=payload,
        reason="denied",
    )
    rejected = service.sign_response(rejected_req, approved=False)
    with pytest.raises(PermissionError):
        service.verify_response(
            rejected,
            expected_execution_id="e2",
            expected_attempt_id="a2",
            expected_directive_hash=rejected_req.directive_hash,
        )

    expired_req = service.request_approval(
        execution_id="e3",
        attempt_id="a3",
        directive_payload=payload,
        reason="late",
        expires_at=utc_now() - timedelta(seconds=1),
    )
    with pytest.raises(ExpiredMessageError):
        service.sign_response(expired_req, approved=True)


@pytest.mark.asyncio
async def test_cancellation_terminates_and_persists_checkpoint(
    mesh: JoyMesh, tmp_path: Path
) -> None:
    profile = (await mesh.list_subscriptions())[0]
    request = RunRequest(task="CONCURRENT", workspace=str(tmp_path))
    route = RouteCandidate(
        harness_id="fake",
        subscription_id=profile.id,
        score=1.0,
        eligible=True,
        reasons=("cancel",),
    )
    run = await mesh.start_run(request=request, route=route)
    for _ in range(100):
        state = await mesh.inspect_run(run.id)
        if state and state.status is RunStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    cancelled = await mesh.cancel(run.id)
    assert cancelled.status is RunStatus.CANCELLED
    events = await mesh.events(run.id)
    assert any(event.type.value == "run.cancelled" for event in events)
    checkpoint = mesh.checkpoints.get(run.id)
    assert checkpoint is not None
    assert checkpoint.status == "cancelled"


@pytest.mark.asyncio
async def test_resume_queue_and_snapshot_recovery(tmp_path: Path) -> None:
    db = tmp_path / "resume.db"
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(fake_harness_definition(), *builtin_catalogue()),
    )
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{db}", registry=registry)
    install_ready_quota(mesh)
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="fake",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    # Seed outbox + checkpoint, then "restart".
    mesh.delivery_publisher.publish_event(
        event_type="pre-restart",
        payload={"n": 1},
        idempotency_key="pre:1",
    )
    from joymesh.execution import ExecutionCheckpoint

    mesh.checkpoints.save(
        ExecutionCheckpoint(
            execution_id="exec-resume",
            attempt_id="att-1",
            harness_id="fake",
            native_session_id="native-1",
            status="running",
            directive_json=None,
            updated_at=utc_now(),
        )
    )
    await mesh.close()

    mesh2 = JoyMesh(database_url=f"sqlite+aiosqlite:///{db}", registry=registry)
    install_ready_quota(mesh2)
    await mesh2.initialize()
    assert mesh2.checkpoints.get("exec-resume") is not None
    assert mesh2.checkpoints.get("exec-resume").status == "interrupted"
    # Queue recovery: pending item drained on initialize flush.
    await mesh2.delivery_worker.flush_once()
    snap = await mesh2.get_runtime_snapshot(refresh=True)
    assert snap.schema_version == 1
    # Supported resume: native session preserved for retry.
    cp = mesh2.checkpoints.get("exec-resume")
    assert cp is not None
    assert cp.native_session_id == "native-1"
    await mesh2.close()


def test_provider_diagnostics_normalization() -> None:
    assert (
        from_availability(HarnessAvailability.AUTHENTICATION_REQUIRED)
        is ProviderDiagnosticCode.AUTHENTICATION_REQUIRED
    )
    assert from_failure_kind(FailureKind.QUOTA_EXHAUSTED) is ProviderDiagnosticCode.QUOTA_EXHAUSTED
    assert classify_detail("API key missing") is ProviderDiagnosticCode.CONFIGURATION_REQUIRED
    assert classify_detail("out of credits") is ProviderDiagnosticCode.QUOTA_EXHAUSTED
    assert classify_detail("rate limit 429") is ProviderDiagnosticCode.RATE_LIMITED
    assert classify_detail("token expired auth") is ProviderDiagnosticCode.AUTHENTICATION_EXPIRED
    assert classify_detail("credential missing") is ProviderDiagnosticCode.CREDENTIAL_MISSING
    assert classify_detail("unsupported version") is ProviderDiagnosticCode.UNSUPPORTED_VERSION


@pytest.mark.asyncio
async def test_privacy_no_forbidden_fields_in_delivery(tmp_path: Path) -> None:
    outbox = DeliveryOutbox(tmp_path / "priv.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox)
    with pytest.raises(RuntimeSnapshotValidationError):
        publisher.publish_event(
            event_type="bad",
            payload={"api_key": "SECRET", "prompt": "do stuff"},
            idempotency_key="bad:1",
        )
    # Clean factual payload is accepted.
    publisher.publish_event(
        event_type="ok",
        payload={"harness_id": "fake", "duration_ms": 12},
        idempotency_key="ok:1",
    )
    pending = outbox.pending()
    assert pending
    blob = json.dumps(pending[0].envelope.as_dict())
    assert "SECRET" not in blob
    assert "do stuff" not in blob
    outbox.close()


@pytest.mark.asyncio
async def test_performance_measurements(tmp_path: Path, mesh: JoyMesh) -> None:
    # Measured values only — written for the implementation report.
    t0 = time.perf_counter()
    snap = await mesh.get_runtime_snapshot(refresh=True)
    snapshot_ms = (time.perf_counter() - t0) * 1000.0

    outbox = mesh._delivery_outbox
    t1 = time.perf_counter()
    mesh.delivery_publisher.publish_snapshot(snap)
    publish_ms = (time.perf_counter() - t1) * 1000.0

    t2 = time.perf_counter()
    await mesh.delivery_worker.flush_once()
    replay_ms = (time.perf_counter() - t2) * 1000.0

    t3 = time.perf_counter()
    recovered = DeliveryOutbox(
        Path(str(outbox.path)),
        max_entries=outbox.max_entries,
    )
    _ = recovered.pending()
    recover_ms = (time.perf_counter() - t3) * 1000.0
    size = recovered.size()
    recovered.close()

    report = tmp_path / "perf.json"
    report.write_text(
        json.dumps(
            {
                "runtime_snapshot_latency_ms": round(snapshot_ms, 3),
                "publish_latency_ms": round(publish_ms, 3),
                "replay_latency_ms": round(replay_ms, 3),
                "queue_recovery_time_ms": round(recover_ms, 3),
                "queue_size": size,
            },
            indent=2,
        )
    )
    assert snapshot_ms >= 0
    assert publish_ms >= 0
    data = json.loads(report.read_text())
    assert "runtime_snapshot_latency_ms" in data
