from __future__ import annotations

from joymesh.control_plane.security import generate_node_keypair, verify_bytes
from joymesh.delivery import DeliveryOutbox, RuntimeDeliveryPublisher


def test_runtime_publisher_signs_canonical_envelope_by_default(tmp_path) -> None:
    private_key, public_key = generate_node_keypair()
    publisher = RuntimeDeliveryPublisher(
        DeliveryOutbox(tmp_path / "outbox.sqlite3"),
        private_key=private_key,
        key_id="test-ed25519",
        organisation_id="test-org",
    )
    envelope = publisher.publish_event(
        event_type="runtime.test",
        payload={"ok": True},
        idempotency_key="signed-default",
    )
    assert envelope.signature is not None
    assert envelope.signature_algorithm == "ed25519"
    assert envelope.key_id == "test-ed25519"
    assert envelope.publisher.organisation_id == "test-org"
    verify_bytes(envelope.canonical_signed_bytes(), envelope.signature, public_key)
    publisher.outbox.close()
