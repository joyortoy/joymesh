"""Publish runtime facts into the durable outbox (and optional live transport)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from typing import Any

from joymesh.control_plane.security import (
    generate_node_keypair,
    public_key_from_private,
    sign_bytes,
)
from joymesh.delivery.contracts import (
    DeliveryEnvelope,
    DeliveryKind,
    PublisherIdentity,
)
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.runtime_snapshot.contracts import RuntimeSnapshot
from joymesh.runtime_snapshot.validators import assert_privacy


class RuntimeDeliveryPublisher:
    """Append validated envelopes to the outbox; optionally sign payloads."""

    def __init__(
        self,
        outbox: DeliveryOutbox,
        *,
        publisher_id: str = "joymesh",
        organisation_id: str = "local",
        sign: bool = True,
        private_key: str | None = None,
        key_id: str | None = None,
    ) -> None:
        self.outbox = outbox
        self._private_key: str | None = None
        public_key: str | None = None
        if sign:
            configured_key = private_key or os.environ.get("JOYMESH_RUNTIME_SIGNING_KEY")
            key_path = os.environ.get("JOYMESH_RUNTIME_SIGNING_KEY_PATH")
            if configured_key is None and key_path:
                configured_key = Path(key_path).expanduser().read_text(encoding="utf-8").strip()
            if configured_key:
                self._private_key = configured_key.strip()
                public_key = public_key_from_private(self._private_key)
            else:
                self._private_key, public_key = generate_node_keypair()
        self.key_id = key_id or os.environ.get("JOYMESH_RUNTIME_SIGNING_KEY_ID") or (
            f"ed25519:{hashlib.sha256(public_key.encode()).hexdigest()[:16]}"
            if public_key
            else None
        )
        self.identity = PublisherIdentity(
            publisher_id=publisher_id,
            public_key=public_key,
            organisation_id=organisation_id,
        )

    def publish_snapshot(self, snapshot: RuntimeSnapshot) -> DeliveryEnvelope:
        payload = snapshot.as_dict()
        assert_privacy(payload)
        return self._append(
            kind=DeliveryKind.RUNTIME_SNAPSHOT,
            payload=payload,
            idempotency_key=f"snapshot:{snapshot.snapshot_id}",
        )

    def publish_event(
        self,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> DeliveryEnvelope:
        body = {"event_type": event_type, "payload": dict(payload)}
        assert_privacy(body)
        return self._append(
            kind=DeliveryKind.RUNTIME_EVENT,
            payload=body,
            idempotency_key=idempotency_key,
        )

    def publish_approval_request(
        self,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> DeliveryEnvelope:
        body = dict(payload)
        assert_privacy(body)
        return self._append(
            kind=DeliveryKind.APPROVAL_REQUEST,
            payload=body,
            idempotency_key=idempotency_key,
        )

    def _append(
        self,
        *,
        kind: DeliveryKind,
        payload: Mapping[str, Any],
        idempotency_key: str | None,
    ) -> DeliveryEnvelope:
        sequence = self.outbox.next_sequence()
        envelope = DeliveryEnvelope.build(
            kind=kind,
            sequence=sequence,
            publisher=self.identity,
            payload=payload,
            key_id=self.key_id,
            signature_algorithm="ed25519" if self._private_key else None,
            idempotency_key=idempotency_key,
        )
        if self._private_key is not None:
            envelope = replace(
                envelope,
                signature=sign_bytes(envelope.canonical_signed_bytes(), self._private_key),
            )
        self.outbox.append(envelope)
        return envelope
