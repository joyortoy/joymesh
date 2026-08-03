"""Production fault-injection tests for JoyMesh delivery and config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joymesh.control_plane.security import generate_node_keypair, sign_bytes, verify_bytes
from joymesh.delivery.backup import DeliveryBackupError, backup_delivery_outbox, restore_delivery_outbox
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.delivery.publisher import RuntimeDeliveryPublisher
from joymesh.models import utc_now
from joymesh.production.validate import validate_production_config
from joymesh.runtime_snapshot.contracts import RuntimeSnapshot


def test_production_missing_signing_key_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY", raising=False)
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY_PATH", raising=False)
    monkeypatch.setenv("JOYMESH_DELIVERY_SOCKET", str(tmp_path / "sock"))
    result = validate_production_config()
    assert result.ok is False
    assert any(i.code == "missing_signing_key" for i in result.issues)

    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    with pytest.raises(RuntimeError, match="production signing key required"):
        RuntimeDeliveryPublisher(outbox)
    outbox.close()


def test_invalid_signature_rejected_by_verifier(tmp_path: Path) -> None:
    private_key, public_key = generate_node_keypair()
    outbox = DeliveryOutbox(tmp_path / "fault-outbox.sqlite3")
    try:
        publisher = RuntimeDeliveryPublisher(
            outbox,
            private_key=private_key,
            key_id="fault-key",
        )
        envelope = publisher.publish_event(
            event_type="runtime.fault",
            payload={"ok": True},
            idempotency_key="fault-invalid-sig",
        )
        with pytest.raises(Exception):
            verify_bytes(envelope.canonical_signed_bytes(), "invalid-signature", public_key)
    finally:
        outbox.close()


def test_outbox_restore_checksum_mismatch(tmp_path: Path) -> None:
    outbox_path = tmp_path / "outbox.sqlite3"
    outbox = DeliveryOutbox(outbox_path)
    outbox.close()
    backup_dir = tmp_path / "backup"
    backup_delivery_outbox(outbox_path=outbox_path, destination=backup_dir)
    db = backup_dir / "delivery_outbox.sqlite3"
    db.write_bytes(db.read_bytes() + b"corrupt")
    with pytest.raises(DeliveryBackupError, match="checksum mismatch"):
        restore_delivery_outbox(backup_dir=backup_dir, outbox_path=tmp_path / "restored.sqlite3", force=True)


def test_backup_interrupt_corrupt_manifest(tmp_path: Path) -> None:
    outbox_path = tmp_path / "outbox.sqlite3"
    DeliveryOutbox(outbox_path).close()
    backup_dir = tmp_path / "backup"
    backup_delivery_outbox(outbox_path=outbox_path, destination=backup_dir)
    manifest = backup_dir / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"]["delivery_outbox.sqlite3"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeliveryBackupError, match="checksum mismatch"):
        restore_delivery_outbox(backup_dir=backup_dir, outbox_path=tmp_path / "restored.sqlite3", force=True)


def test_outbox_max_entries_from_production_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_MAX_OUTBOX_ENTRIES", "3")
    from joymesh.production.config import load_production_config

    cfg = load_production_config()
    outbox = DeliveryOutbox(tmp_path / "bounded.sqlite3", max_entries=cfg.max_outbox_entries)
    publisher = RuntimeDeliveryPublisher(outbox, private_key=generate_node_keypair()[0], sign=True)
    for index in range(5):
        publisher.publish_event(
            event_type="fault.fill",
            payload={"i": index},
            idempotency_key=f"fill:{index}",
        )
    assert outbox.size() <= 3
    outbox.close()


def test_revoked_key_rejected_on_joycli_side(tmp_path: Path) -> None:
    pytest.importorskip("joycli")
    from joycli.runtime.intake import RuntimeStateIntakeService, RuntimeIntakeStore
    from joycli.runtime.intake.contracts import DeliveryKind, RuntimeDeliveryEnvelope, RuntimePublisherIdentity
    from joycli.runtime.intake.key_store import DurablePublisherKeyStore

    private_key, public_key = generate_node_keypair()
    key_store = DurablePublisherKeyStore(tmp_path / "keys.json")
    key_store.add(
        key_id="mesh-key",
        public_key=public_key,
        publisher_id="joymesh",
        organisation_id="local",
    )
    key_store.revoke("mesh-key", reason="incident")
    registry = key_store.as_registry()
    intake = RuntimeStateIntakeService(
        RuntimeIntakeStore(),
        allow_unsigned=False,
        key_registry=registry,
    )
    payload = {"harnesses": []}
    base = RuntimeDeliveryEnvelope(
        envelope_id="revoked",
        kind=DeliveryKind.RUNTIME_SNAPSHOT,
        sequence=1,
        observed_at=utc_now(),
        publisher=RuntimePublisherIdentity(publisher_id="joymesh", organisation_id="local", public_key=public_key),
        payload=payload,
        payload_hash=RuntimeDeliveryEnvelope.hash_payload(payload),
        idempotency_key="revoked",
    )
    env = RuntimeDeliveryEnvelope(
        envelope_id=base.envelope_id,
        kind=base.kind,
        sequence=base.sequence,
        observed_at=base.observed_at,
        publisher=base.publisher,
        payload=base.payload,
        payload_hash=base.payload_hash,
        idempotency_key=base.idempotency_key,
        key_id="mesh-key",
        signature_algorithm="ed25519",
        signature=sign_bytes(base.canonical_signed_bytes(), private_key),
    )
    result = intake.ingest_envelope(envelope=env)
    assert result.code == "publisher_key_inactive"
    intake.close()
