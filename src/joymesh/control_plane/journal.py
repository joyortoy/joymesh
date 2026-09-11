"""Durable local task journal for the JoyMesh Node."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from joymesh.models import utc_now


@dataclass(frozen=True)
class LocalTaskJournalEntry:
    task_id: str
    plan_hash: str
    connector_id: str
    connector_revision: str
    status: str
    accepted_at: datetime | None
    started_at: datetime | None
    terminal_at: datetime | None
    terminal_result_digest: str | None
    last_sequence_number: int


class NodeTaskJournal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            path.expanduser()
            if path is not None
            else Path("~/.local/share/joymesh/node-task-journal.sqlite3").expanduser()
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_journal (
                    task_id TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    connector_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    accepted_at TEXT,
                    started_at TEXT,
                    terminal_at TEXT,
                    terminal_result_digest TEXT,
                    last_sequence_number INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (task_id, plan_hash)
                )
                """
            )
            connection.commit()

    def get(self, task_id: str, plan_hash: str) -> LocalTaskJournalEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_journal WHERE task_id = ? AND plan_hash = ?",
                (task_id, plan_hash),
            ).fetchone()
        return None if row is None else _from_row(row)

    def accept(
        self,
        *,
        task_id: str,
        plan_hash: str,
        connector_id: str,
        connector_revision: str,
    ) -> LocalTaskJournalEntry:
        existing = self.get(task_id, plan_hash)
        if existing is not None:
            return existing
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_journal (
                    task_id, plan_hash, connector_id, connector_revision, status,
                    accepted_at, started_at, terminal_at, terminal_result_digest,
                    last_sequence_number
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0)
                """,
                (task_id, plan_hash, connector_id, connector_revision, "accepted", now),
            )
            connection.commit()
        entry = self.get(task_id, plan_hash)
        assert entry is not None
        return entry

    def mark_started(self, task_id: str, plan_hash: str) -> LocalTaskJournalEntry:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_journal
                SET status = 'running', started_at = ?
                WHERE task_id = ? AND plan_hash = ? AND terminal_at IS NULL
                """,
                (utc_now().isoformat(), task_id, plan_hash),
            )
            connection.commit()
        entry = self.get(task_id, plan_hash)
        assert entry is not None
        return entry

    def mark_terminal(
        self,
        task_id: str,
        plan_hash: str,
        *,
        status: str,
        result_digest: str,
        sequence: int,
    ) -> LocalTaskJournalEntry:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_journal
                SET status = ?, terminal_at = ?, terminal_result_digest = ?,
                    last_sequence_number = ?
                WHERE task_id = ? AND plan_hash = ? AND terminal_at IS NULL
                """,
                (status, utc_now().isoformat(), result_digest, sequence, task_id, plan_hash),
            )
            connection.commit()
        entry = self.get(task_id, plan_hash)
        assert entry is not None
        return entry

    def update_sequence(self, task_id: str, plan_hash: str, sequence: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_journal
                SET last_sequence_number = ?
                WHERE task_id = ? AND plan_hash = ?
                """,
                (sequence, task_id, plan_hash),
            )
            connection.commit()

    def summary(self) -> dict[str, object]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM task_journal").fetchall()
        entries = [_from_row(row) for row in rows]
        active = [
            {
                "task_id": item.task_id,
                "plan_hash": item.plan_hash,
                "status": item.status,
                "last_sequence_number": item.last_sequence_number,
            }
            for item in entries
            if item.terminal_at is None
        ]
        terminal = [
            {
                "task_id": item.task_id,
                "plan_hash": item.plan_hash,
                "status": item.status,
                "terminal_result_digest": item.terminal_result_digest,
                "last_sequence_number": item.last_sequence_number,
            }
            for item in entries
            if item.terminal_at is not None
        ]
        return {"active": active, "terminal": terminal}


def _from_row(row: sqlite3.Row) -> LocalTaskJournalEntry:
    return LocalTaskJournalEntry(
        task_id=row["task_id"],
        plan_hash=row["plan_hash"],
        connector_id=row["connector_id"],
        connector_revision=row["connector_revision"],
        status=row["status"],
        accepted_at=_parse(row["accepted_at"]),
        started_at=_parse(row["started_at"]),
        terminal_at=_parse(row["terminal_at"]),
        terminal_result_digest=row["terminal_result_digest"],
        last_sequence_number=int(row["last_sequence_number"]),
    )


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
