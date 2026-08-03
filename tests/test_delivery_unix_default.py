"""Unix socket production default + JoyCLI intake + architecture guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from joymesh.delivery import (
    TRANSPORT_VERSION,
    DeliveryEnvelope,
    DeliveryKind,
    DeliveryOutbox,
    DeliverySettings,
    DeliveryTransportMode,
    DeliveryWorker,
    MemoryDeliveryTransport,
    PublisherIdentity,
    RuntimeDeliveryPublisher,
    RuntimeStateIntakeService,
    UnixSocketDeliveryServer,
    UnixSocketDeliveryTransport,
    build_delivery_transport,
    default_production_transport_mode,
    resolve_delivery_settings,
)
from joymesh.delivery.factory import DisabledDeliveryTransport
from joymesh.delivery.intake import IntakeRejected
from joymesh.delivery.transports.unix_socket import remove_stale_socket
from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS, HarnessRegistry
from joymesh.service import JoyMesh


def _sock() -> Path:
    return Path("/tmp") / f"joymesh-delivery-{uuid4().hex}.sock"


def test_production_local_default_is_unix_socket() -> None:
    assert default_production_transport_mode() is DeliveryTransportMode.UNIX_SOCKET
    settings = resolve_delivery_settings(environ={})
    assert settings.transport is DeliveryTransportMode.UNIX_SOCKET
    transport = build_delivery_transport(settings)
    assert isinstance(transport, UnixSocketDeliveryTransport)
    assert not isinstance(transport, MemoryDeliveryTransport)


def test_memory_requires_explicit_configuration() -> None:
    settings = resolve_delivery_settings(
        environ={"JOYMESH_DELIVERY_TRANSPORT": "memory"}
    )
    assert settings.transport is DeliveryTransportMode.MEMORY
    transport = build_delivery_transport(settings)
    assert isinstance(transport, MemoryDeliveryTransport)

    production = resolve_delivery_settings(environ={})
    assert production.transport is not DeliveryTransportMode.MEMORY


def test_composition_root_selects_unix_socket_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOYMESH_DELIVERY_TRANSPORT", raising=False)
    monkeypatch.delenv("JOYMESH_DELIVERY_SOCKET", raising=False)
    sock = _sock()
    monkeypatch.setenv("JOYMESH_DELIVERY_SOCKET", str(sock))
    mesh = JoyMesh(database_url="sqlite+aiosqlite:///:memory:")
    assert isinstance(mesh.delivery_transport, UnixSocketDeliveryTransport)
    assert mesh.delivery_settings.transport is DeliveryTransportMode.UNIX_SOCKET
    assert not isinstance(mesh.delivery_transport, MemoryDeliveryTransport)


@pytest.mark.asyncio
async def test_missing_socket_does_not_switch_to_memory_and_preserves_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOYMESH_DELIVERY_TRANSPORT", raising=False)
    sock = _sock()
    settings = DeliverySettings(
        transport=DeliveryTransportMode.UNIX_SOCKET,
        socket_path=sock,
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        delivery_settings=settings,
    )
    assert isinstance(mesh.delivery_transport, UnixSocketDeliveryTransport)
    await mesh.initialize()
    try:
        mesh.delivery_publisher.publish_event(
            event_type="runtime.probe",
            payload={"ok": True},
            idempotency_key="probe-1",
        )
        drained = await mesh.delivery_worker.flush_once()
        assert drained == 0
        assert mesh._delivery_outbox.size() >= 1
        assert isinstance(mesh.delivery_transport, UnixSocketDeliveryTransport)
    finally:
        await mesh.close()


@pytest.mark.asyncio
async def test_joycli_later_start_reconnects_and_delivers(tmp_path: Path) -> None:
    sock = _sock()
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox)
    transport = UnixSocketDeliveryTransport(sock)
    worker = DeliveryWorker(outbox, transport, poll_interval=0.05)
    publisher.publish_event(
        event_type="runtime.probe",
        payload={"n": 1},
        idempotency_key="later-1",
    )
    assert await worker.flush_once() == 0
    assert outbox.size() == 1

    server = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
    await server.start()
    try:
        await worker.start()
        for _ in range(40):
            if outbox.size() == 0:
                break
            await asyncio.sleep(0.05)
        assert outbox.size() == 0
        assert server.intake.size() >= 1
    finally:
        await worker.stop()
        await server.stop()
        outbox.close()


@pytest.mark.asyncio
async def test_joycli_restart_replay_and_idempotent_duplicate(tmp_path: Path) -> None:
    sock = _sock()
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox)
    env = publisher.publish_event(
        event_type="runtime.probe",
        payload={"n": 2},
        idempotency_key="replay-1",
    )
    server = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
    await server.start()
    transport = UnixSocketDeliveryTransport(sock)
    worker = DeliveryWorker(outbox, transport)
    try:
        assert await worker.flush_once() == 1
        assert outbox.size() == 0
        await transport.close()
        await server.stop()

        server2 = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
        await server2.start()
        # After ACK deletion, same idempotency key may be enqueued again for
        # reconciliation; JoyCLI intake remains idempotent.
        publisher.publish_event(
            event_type="runtime.probe",
            payload={"n": 2},
            idempotency_key="replay-1",
        )
        assert outbox.size() == 1
        transport2 = UnixSocketDeliveryTransport(sock)
        worker2 = DeliveryWorker(outbox, transport2)
        drained = await worker2.flush_once()
        assert drained == 1, worker2.health()
        assert outbox.size() == 0
        assert server2.intake.size() == 1
        # Direct republish of original envelope is also idempotently ACKed.
        await transport2.connect()
        ack = await transport2.publish(env)
        assert ack.status.value == "acked"
        await transport2.close()
        assert server2.intake.size() == 1
        await server2.stop()
    finally:
        await worker.stop()
        outbox.close()


def test_conflicting_duplicate_rejected(tmp_path: Path) -> None:
    intake = RuntimeStateIntakeService(tmp_path / "intake.sqlite3")
    publisher = PublisherIdentity(publisher_id="joymesh")
    first = DeliveryEnvelope.build(
        kind=DeliveryKind.RUNTIME_EVENT,
        sequence=1,
        publisher=publisher,
        payload={"event_type": "x", "payload": {"a": 1}},
        idempotency_key="conflict-1",
    )
    assert intake.accept(first).value == "acked"
    conflict = DeliveryEnvelope.build(
        kind=DeliveryKind.RUNTIME_EVENT,
        sequence=2,
        publisher=publisher,
        payload={"event_type": "x", "payload": {"a": 2}},
        idempotency_key="conflict-1",
    )
    with pytest.raises(IntakeRejected) as exc:
        intake.accept(conflict)
    assert exc.value.code == "duplicate_conflict"
    intake.close()


@pytest.mark.asyncio
async def test_invalid_protocol_version_and_publisher_rejected(tmp_path: Path) -> None:
    sock = _sock()
    server = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(
            (f'{{"type":"hello","transport_version":{TRANSPORT_VERSION + 99}}}\n').encode()
        )
        await writer.drain()
        raw = await reader.readline()
        response = raw.decode()
        assert "transport_version_mismatch" in response or "error" in response
        writer.close()
        await writer.wait_closed()

        intake = RuntimeStateIntakeService(tmp_path / "bad-pub.sqlite3")
        bad = DeliveryEnvelope.build(
            kind=DeliveryKind.RUNTIME_EVENT,
            sequence=1,
            publisher=PublisherIdentity(publisher_id="not-joymesh"),
            payload={"event_type": "x", "payload": {}},
        )
        with pytest.raises(IntakeRejected) as exc:
            intake.accept(bad)
        assert exc.value.code == "invalid_publisher_identity"
        intake.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stale_socket_handled_safely(tmp_path: Path) -> None:
    sock = _sock()
    sock.write_text("not-a-live-socket")
    assert remove_stale_socket(sock) is True
    assert not sock.exists()
    server = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
    await server.start()
    await server.stop()


@pytest.mark.asyncio
async def test_ack_required_before_outbox_deletion(tmp_path: Path) -> None:
    sock = _sock()
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox)
    publisher.publish_event(
        event_type="runtime.probe",
        payload={"n": 3},
        idempotency_key="ack-1",
    )
    transport = UnixSocketDeliveryTransport(sock)
    worker = DeliveryWorker(outbox, transport)
    assert await worker.flush_once() == 0
    assert outbox.size() == 1
    server = UnixSocketDeliveryServer(sock, intake_path=tmp_path / "intake.sqlite3")
    await server.start()
    try:
        assert await worker.flush_once() == 1
        assert outbox.size() == 0
    finally:
        await server.stop()
        outbox.close()


def test_socket_listener_delegates_to_intake(tmp_path: Path) -> None:
    intake = RuntimeStateIntakeService(tmp_path / "intake.sqlite3")
    server = UnixSocketDeliveryServer(_sock(), intake=intake)
    assert server.intake is intake


def test_fake_not_production_registered() -> None:
    registry = HarnessRegistry()
    ids = {item.manifest.harness_id for item in registry.list()}
    assert "fake" not in ids
    assert FORBIDDEN_PRODUCTION_HARNESS_IDS.isdisjoint(ids)


def test_disabled_transport_built_explicitly() -> None:
    transport = build_delivery_transport(
        DeliverySettings(transport=DeliveryTransportMode.DISABLED)
    )
    assert isinstance(transport, DisabledDeliveryTransport)


def test_cross_harness_fallback_never_resumes_failed_session_source() -> None:
    # Guard: approve_fallback must not set resume_session_id (source inspection).
    import inspect

    from joymesh import service as service_mod

    source = inspect.getsource(service_mod.JoyMesh.approve_fallback)
    assert "resume_session_id" not in source
