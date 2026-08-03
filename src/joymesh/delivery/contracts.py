"""Runtime update delivery contracts (JoyMesh → JoyCLI).

Facts only. No prompts, source, credentials, or workspace content.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now

SCHEMA_VERSION = 1
TRANSPORT_VERSION = 1


class DeliveryKind(StrEnum):
    RUNTIME_SNAPSHOT = "runtime_snapshot"
    RUNTIME_EVENT = "runtime_event"
    APPROVAL_REQUEST = "approval_request"
    HEARTBEAT = "heartbeat"


class DeliveryAckStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    ACKED = "acked"
    FAILED = "failed"
    DROPPED = "dropped"


@dataclass(frozen=True)
class PublisherIdentity:
    publisher_id: str
    public_key: str | None = None
    instance_id: str = field(default_factory=lambda: str(uuid4()))
    organisation_id: str = "local"

    def as_dict(self) -> dict[str, Any]:
        return {
            "publisher_id": self.publisher_id,
            "public_key": self.public_key,
            "instance_id": self.instance_id,
            "organisation_id": self.organisation_id,
        }


@dataclass(frozen=True)
class DeliveryEnvelope:
    """Authenticated delivery envelope for JoyCLI consumption."""

    envelope_id: str
    kind: DeliveryKind
    sequence: int
    observed_at: datetime
    publisher: PublisherIdentity
    payload: Mapping[str, Any]
    payload_hash: str
    schema_version: int = SCHEMA_VERSION
    transport_version: int = TRANSPORT_VERSION
    signature: str | None = None
    key_id: str | None = None
    signature_algorithm: str | None = None
    idempotency_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "kind": self.kind.value,
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "publisher": self.publisher.as_dict(),
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "schema_version": self.schema_version,
            "transport_version": self.transport_version,
            "signature": self.signature,
            "key_id": self.key_id,
            "signature_algorithm": self.signature_algorithm,
            "idempotency_key": self.idempotency_key or self.envelope_id,
        }

    def canonical_signed_bytes(self) -> bytes:
        signed = {
            "protocol_version": self.transport_version,
            "publisher_id": self.publisher.publisher_id,
            "organisation_id": self.publisher.organisation_id,
            "sequence": self.sequence,
            "message_type": self.kind.value,
            "content_hash": self.payload_hash,
            "observed_at": self.observed_at.isoformat(),
            "payload_hash": self.payload_hash,
        }
        return json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def hash_payload(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        kind: DeliveryKind,
        sequence: int,
        publisher: PublisherIdentity,
        payload: Mapping[str, Any],
        signature: str | None = None,
        key_id: str | None = None,
        signature_algorithm: str | None = None,
        idempotency_key: str | None = None,
        envelope_id: str | None = None,
    ) -> DeliveryEnvelope:
        return cls(
            envelope_id=envelope_id or str(uuid4()),
            kind=kind,
            sequence=sequence,
            observed_at=utc_now(),
            publisher=publisher,
            payload=dict(payload),
            payload_hash=cls.hash_payload(payload),
            signature=signature,
            key_id=key_id,
            signature_algorithm=signature_algorithm,
            idempotency_key=idempotency_key or "",
        )


@dataclass(frozen=True)
class DeliveryAck:
    envelope_id: str
    status: DeliveryAckStatus
    received_at: datetime
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "status": self.status.value,
            "received_at": self.received_at.isoformat(),
            "detail": self.detail,
        }
