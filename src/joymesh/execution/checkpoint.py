"""Durable execution checkpoint store for resume / restart recovery."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from joymesh.models import utc_now


@dataclass(frozen=True)
class ExecutionCheckpoint:
    execution_id: str
    attempt_id: str
    harness_id: str
    native_session_id: str | None
    status: str
    directive_json: str | None
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "harness_id": self.harness_id,
            "native_session_id": self.native_session_id,
            "status": self.status,
            "directive_json": self.directive_json,
            "updated_at": self.updated_at.isoformat(),
        }


class CheckpointStore:
    """Local durable checkpoints (no prompts / workspace contents)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_checkpoints (
                execution_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                harness_id TEXT NOT NULL,
                native_session_id TEXT,
                status TEXT NOT NULL,
                directive_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, checkpoint: ExecutionCheckpoint) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO execution_checkpoints(
                    execution_id, attempt_id, harness_id, native_session_id,
                    status, directive_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    attempt_id=excluded.attempt_id,
                    harness_id=excluded.harness_id,
                    native_session_id=excluded.native_session_id,
                    status=excluded.status,
                    directive_json=excluded.directive_json,
                    updated_at=excluded.updated_at
                """,
                (
                    checkpoint.execution_id,
                    checkpoint.attempt_id,
                    checkpoint.harness_id,
                    checkpoint.native_session_id,
                    checkpoint.status,
                    checkpoint.directive_json,
                    checkpoint.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, execution_id: str) -> ExecutionCheckpoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM execution_checkpoints WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionCheckpoint(
            execution_id=row["execution_id"],
            attempt_id=row["attempt_id"],
            harness_id=row["harness_id"],
            native_session_id=row["native_session_id"],
            status=row["status"],
            directive_json=row["directive_json"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_resumable(self) -> tuple[ExecutionCheckpoint, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM execution_checkpoints
                WHERE status IN ('running', 'queued', 'interrupted')
                ORDER BY updated_at ASC
                """
            ).fetchall()
        return tuple(
            ExecutionCheckpoint(
                execution_id=row["execution_id"],
                attempt_id=row["attempt_id"],
                harness_id=row["harness_id"],
                native_session_id=row["native_session_id"],
                status=row["status"],
                directive_json=row["directive_json"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )

    def mark_interrupted(self) -> int:
        """On process restart, mark previously running executions as interrupted."""
        now = utc_now().isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE execution_checkpoints
                SET status='interrupted', updated_at=?
                WHERE status IN ('running', 'queued')
                """,
                (now,),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)


def default_checkpoint_path() -> Path:
    root = Path.home() / ".local" / "share" / "joymesh"
    root.mkdir(parents=True, exist_ok=True)
    return root / "execution_checkpoints.sqlite3"
