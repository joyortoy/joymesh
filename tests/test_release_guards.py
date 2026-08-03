"""Architecture guards for the three release tickets."""

from __future__ import annotations

import inspect
from pathlib import Path

from joymesh.delivery import (
    MemoryDeliveryTransport,
    UnixSocketDeliveryTransport,
    build_delivery_transport,
    resolve_delivery_settings,
)
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.delivery.transports.unix_socket import UnixSocketDeliveryServer
from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS, HarnessRegistry
from joymesh.service import JoyMesh


def test_production_unix_mode_does_not_instantiate_memory() -> None:
    settings = resolve_delivery_settings(environ={})
    transport = build_delivery_transport(settings)
    assert isinstance(transport, UnixSocketDeliveryTransport)
    assert type(transport) is not MemoryDeliveryTransport


def test_socket_listener_delegates_to_intake(tmp_path: Path) -> None:
    from joymesh.delivery import RuntimeStateIntakeService
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        intake = RuntimeStateIntakeService(tmp_path / "i.sqlite3")
    server = UnixSocketDeliveryServer(tmp_path / "x.sock", intake=intake)
    assert server.intake is intake
    source = inspect.getsource(UnixSocketDeliveryServer._dispatch)
    assert "intake.accept" in source
    assert "resolve_route" not in source
    assert "Router" not in source


def test_joymesh_intake_is_deprecated_reference() -> None:
    from joymesh.delivery import intake as intake_mod

    text = Path(intake_mod.__file__).read_text(encoding="utf-8")
    assert "DEPRECATED" in text
    assert "joycli.runtime.intake" in text


def test_outbox_removed_only_after_ack(tmp_path: Path) -> None:
    from joymesh.delivery import (
        DeliveryEnvelope,
        DeliveryKind,
        PublisherIdentity,
    )

    outbox = DeliveryOutbox(tmp_path / "o.sqlite3")
    env = DeliveryEnvelope.build(
        kind=DeliveryKind.RUNTIME_EVENT,
        sequence=1,
        publisher=PublisherIdentity(publisher_id="joymesh"),
        payload={"event_type": "x", "payload": {}},
        idempotency_key="ack-only",
    )
    outbox.append(env)
    assert outbox.size() == 1
    outbox.mark_sent(env.envelope_id)
    assert outbox.size() == 1
    outbox.mark_acked(env.envelope_id)
    assert outbox.size() == 0
    outbox.close()


def test_approve_fallback_never_resumes_failed_session() -> None:
    source = inspect.getsource(JoyMesh.approve_fallback)
    assert "resume_session_id" not in source


def test_fake_adapter_not_production_registered() -> None:
    ids = {a.manifest.harness_id for a in HarnessRegistry().list()}
    assert "fake" not in ids
    assert FORBIDDEN_PRODUCTION_HARNESS_IDS.isdisjoint(ids)


def test_fresh_install_script_unsets_pythonpath() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_fresh_install.sh"
    text = script.read_text(encoding="utf-8")
    assert "unset PYTHONPATH" in text
    assert 'PYTHONPATH="' not in text or text.count("unset PYTHONPATH") >= 2
