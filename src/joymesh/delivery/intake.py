"""DEPRECATED reference/test runtime-state intake.

Canonical production ownership lives in JoyCLI:

    joycli.runtime.intake.RuntimeStateIntakeService
    joycli.runtime.intake.UnixSocketRuntimeListener

This module remains only as a protocol-compatible reference for JoyMesh unit
tests. Production composition must not select it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from joymesh.delivery.contracts import (
    TRANSPORT_VERSION,
    DeliveryAckStatus,
    DeliveryEnvelope,
    DeliveryKind,
    PublisherIdentity,
)
from joymesh.models import utc_now
from joymesh.runtime_snapshot.validators import assert_privacy

_DEPRECATION = (
    "joymesh.delivery.intake.RuntimeStateIntakeService is a deprecated "
    "reference/test intake. Use JoyCLI joycli.runtime.intake as the canonical "
    "production receiver (joyctl runtime intake-serve)."
)


class IntakeRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class IntakeRecord:
    envelope_id: str
    kind: str
    sequence: int
    idempotency_key: str
    payload_hash: str
    accepted_at: datetime
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "kind": self.kind,
            "sequence": self.sequence,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "accepted_at": self.accepted_at.isoformat(),
            "payload": self.payload,
        }


class RuntimeStateIntakeService:
    """Deprecated reference intake — not the production JoyCLI owner."""

    def __init__(
        self,
        path: str | Path,
        *,
        allowed_publisher_ids: frozenset[str] | None = None,
        require_publisher_id: str | None = "joymesh",
    ) -> None:
        warnings.warn(_DEPRECATION, DeprecationWarning, stacklevel=2)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.allowed_publisher_ids = allowed_publisher_ids or frozenset({"joymesh"})
        self.require_publisher_id = require_publisher_id
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()
        self.received: list[DeliveryEnvelope] = []

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_intake (
                    envelope_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    publisher_id TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    accepted_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_idempotency "
                "ON runtime_intake(idempotency_key)"
            )
            self._conn.commit()

    def accept(self, envelope: DeliveryEnvelope) -> DeliveryAckStatus:
        self._validate(envelope)
        key = envelope.idempotency_key or envelope.envelope_id
        now = utc_now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM runtime_intake WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] == envelope.payload_hash:
                    self.received.append(envelope)
                    return DeliveryAckStatus.ACKED
                raise IntakeRejected(
                    "duplicate_conflict",
                    "idempotency key reused with conflicting payload",
                )
            self._conn.execute(
                """
                INSERT INTO runtime_intake(
                    envelope_id, kind, sequence, idempotency_key, payload_hash,
                    publisher_id, envelope_json, accepted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.envelope_id,
                    envelope.kind.value,
                    envelope.sequence,
                    key,
                    envelope.payload_hash,
                    envelope.publisher.publisher_id,
                    json.dumps(envelope.as_dict(), sort_keys=True),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
            self.received.append(envelope)
            return DeliveryAckStatus.ACKED

    def list_records(self, *, limit: int = 100) -> tuple[IntakeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runtime_intake ORDER BY sequence ASC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def size(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM runtime_intake").fetchone()
            return int(row["c"] if row else 0)

    def _validate(self, envelope: DeliveryEnvelope) -> None:
        if envelope.transport_version != TRANSPORT_VERSION:
            raise IntakeRejected(
                "transport_version_mismatch",
                f"expected {TRANSPORT_VERSION} got {envelope.transport_version}",
            )
        publisher_id = envelope.publisher.publisher_id
        if self.require_publisher_id and publisher_id != self.require_publisher_id:
            raise IntakeRejected(
                "invalid_publisher_identity",
                f"publisher_id {publisher_id!r} not accepted",
            )
        if publisher_id not in self.allowed_publisher_ids:
            raise IntakeRejected(
                "invalid_publisher_identity",
                f"publisher_id {publisher_id!r} not allowed",
            )
        expected = DeliveryEnvelope.hash_payload(envelope.payload)
        if envelope.payload_hash != expected:
            raise IntakeRejected(
                "payload_hash_mismatch",
                "payload hash does not match payload body",
            )
        try:
            assert_privacy(dict(envelope.payload))
        except Exception as exc:
            raise IntakeRejected("privacy_violation", str(exc)) from exc
        try:
            DeliveryKind(envelope.kind)
        except ValueError as exc:
            raise IntakeRejected("invalid_kind", str(exc)) from exc

    def _row_to_record(self, row: sqlite3.Row) -> IntakeRecord:
        payload = json.loads(row["envelope_json"])
        return IntakeRecord(
            envelope_id=row["envelope_id"],
            kind=row["kind"],
            sequence=int(row["sequence"]),
            idempotency_key=row["idempotency_key"],
            payload_hash=row["payload_hash"],
            accepted_at=datetime.fromisoformat(row["accepted_at"]),
            payload=payload.get("payload") or {},
        )


def envelope_from_dict(data: dict[str, Any]) -> DeliveryEnvelope:
    publisher = data.get("publisher") or {}
    return DeliveryEnvelope(
        envelope_id=str(data["envelope_id"]),
        kind=DeliveryKind(data["kind"]),
        sequence=int(data["sequence"]),
        observed_at=datetime.fromisoformat(str(data["observed_at"]))
        if data.get("observed_at")
        else utc_now(),
        publisher=PublisherIdentity(
            publisher_id=str(publisher.get("publisher_id", "")),
            public_key=publisher.get("public_key"),
            instance_id=str(publisher.get("instance_id", "unknown")),
            organisation_id=str(publisher.get("organisation_id", "local")),
        ),
        payload=data.get("payload") or {},
        payload_hash=str(data.get("payload_hash", "")),
        schema_version=int(data.get("schema_version", 1)),
        transport_version=int(data.get("transport_version", TRANSPORT_VERSION)),
        signature=data.get("signature"),
        key_id=data.get("key_id"),
        signature_algorithm=data.get("signature_algorithm"),
        idempotency_key=str(data.get("idempotency_key") or data["envelope_id"]),
    )
