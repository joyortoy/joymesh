"""Bounded durable delivery outbox (crash-safe, atomic SQLite).

Exists only to guarantee JoyCLI delivery of runtime facts — not analytics.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from joymesh.delivery.contracts import (
    DeliveryAckStatus,
    DeliveryEnvelope,
    DeliveryKind,
    PublisherIdentity,
)
from joymesh.models import utc_now

DEFAULT_MAX_ENTRIES = 2000


@dataclass(frozen=True)
class OutboxRecord:
    envelope: DeliveryEnvelope
    status: DeliveryAckStatus
    attempts: int
    created_at: datetime
    updated_at: datetime


class DeliveryOutbox:
    """Local durable queue with bounded growth and compaction."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_outbox (
                    envelope_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_idempotency "
                "ON delivery_outbox(idempotency_key)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_outbox_status_seq "
                "ON delivery_outbox(status, sequence)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def next_sequence(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM delivery_meta WHERE key='sequence'"
            ).fetchone()
            current = int(row["value"]) if row else 0
            nxt = current + 1
            self._conn.execute(
                "INSERT INTO delivery_meta(key, value) VALUES('sequence', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(nxt),),
            )
            self._conn.commit()
            return nxt

    def append(self, envelope: DeliveryEnvelope) -> OutboxRecord:
        """Append envelope. Duplicate idempotency_key is suppressed (idempotent resend)."""
        now = utc_now().isoformat()
        key = envelope.idempotency_key or envelope.envelope_id
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM delivery_outbox WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                return self._row_to_record(existing)
            self._conn.execute(
                """
                INSERT INTO delivery_outbox(
                    envelope_id, kind, sequence, status, attempts, idempotency_key,
                    payload_hash, envelope_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.envelope_id,
                    envelope.kind.value,
                    envelope.sequence,
                    DeliveryAckStatus.PENDING.value,
                    key,
                    envelope.payload_hash,
                    json.dumps(envelope.as_dict(), sort_keys=True),
                    now,
                    now,
                ),
            )
            self._conn.commit()
            self.compact()
            row = self._conn.execute(
                "SELECT * FROM delivery_outbox WHERE envelope_id=?",
                (envelope.envelope_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_record(row)

    def mark_sent(self, envelope_id: str) -> None:
        self._set_status(envelope_id, DeliveryAckStatus.SENT, bump_attempts=True)

    def mark_acked(self, envelope_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM delivery_outbox WHERE envelope_id=?",
                (envelope_id,),
            )
            self._conn.commit()

    def mark_failed(self, envelope_id: str) -> None:
        self._set_status(envelope_id, DeliveryAckStatus.FAILED, bump_attempts=True)

    def pending(self, *, limit: int = 100) -> tuple[OutboxRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM delivery_outbox
                WHERE status IN (?, ?)
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (
                    DeliveryAckStatus.PENDING.value,
                    DeliveryAckStatus.FAILED.value,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def size(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM delivery_outbox").fetchone()
            return int(row["c"] if row else 0)

    def compact(self) -> int:
        """Drop oldest entries until size fits max_entries."""
        with self._lock:
            deleted = 0
            while True:
                count = int(
                    self._conn.execute(
                        "SELECT COUNT(*) AS c FROM delivery_outbox"
                    ).fetchone()["c"]
                )
                if count <= self.max_entries:
                    break
                row = self._conn.execute(
                    "SELECT envelope_id FROM delivery_outbox ORDER BY sequence ASC LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                self._conn.execute(
                    "DELETE FROM delivery_outbox WHERE envelope_id=?",
                    (row["envelope_id"],),
                )
                deleted += 1
            if deleted:
                self._conn.commit()
            return deleted

    def _set_status(
        self,
        envelope_id: str,
        status: DeliveryAckStatus,
        *,
        bump_attempts: bool,
    ) -> None:
        now = utc_now().isoformat()
        with self._lock:
            if bump_attempts:
                self._conn.execute(
                    """
                    UPDATE delivery_outbox
                    SET status=?, attempts=attempts+1, updated_at=?
                    WHERE envelope_id=?
                    """,
                    (status.value, now, envelope_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE delivery_outbox
                    SET status=?, updated_at=?
                    WHERE envelope_id=?
                    """,
                    (status.value, now, envelope_id),
                )
            self._conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> OutboxRecord:
        payload = json.loads(row["envelope_json"])
        publisher = payload.get("publisher") or {}
        envelope = DeliveryEnvelope(
            envelope_id=payload["envelope_id"],
            kind=DeliveryKind(payload["kind"]),
            sequence=int(payload["sequence"]),
            observed_at=datetime.fromisoformat(payload["observed_at"]),
            publisher=PublisherIdentity(
                publisher_id=publisher.get("publisher_id", "joymesh"),
                public_key=publisher.get("public_key"),
                instance_id=publisher.get("instance_id", "unknown"),
                organisation_id=publisher.get("organisation_id", "local"),
            ),
            payload=payload.get("payload") or {},
            payload_hash=payload["payload_hash"],
            schema_version=int(payload.get("schema_version", 1)),
            transport_version=int(payload.get("transport_version", 1)),
            signature=payload.get("signature"),
            key_id=payload.get("key_id"),
            signature_algorithm=payload.get("signature_algorithm"),
            idempotency_key=payload.get("idempotency_key") or payload["envelope_id"],
        )
        return OutboxRecord(
            envelope=envelope,
            status=DeliveryAckStatus(row["status"]),
            attempts=int(row["attempts"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def default_outbox_path() -> Path:
    root = Path.home() / ".local" / "share" / "joymesh"
    root.mkdir(parents=True, exist_ok=True)
    return root / "delivery_outbox.sqlite3"
