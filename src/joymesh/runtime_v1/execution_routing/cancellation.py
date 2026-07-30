"""Cancellation registry — maps execution_id → active backend/attempt for cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now

CancelFn = Callable[[], Awaitable[Mapping[str, Any]]]


@dataclass
class ActiveExecution:
    execution_id: str
    attempt_id: str
    backend_id: str
    harness_id: str | None
    cancel_fn: CancelFn | None = None
    cancelled: bool = False
    cancel_count: int = 0
    late_events_ignored: int = 0
    created_at: Any = field(default_factory=utc_now)


class CancellationRegistry:
    """Idempotent cancellation tracking for live executions."""

    def __init__(self) -> None:
        self._active: dict[str, ActiveExecution] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        execution_id: str,
        backend_id: str,
        harness_id: str | None = None,
        attempt_id: str | None = None,
        cancel_fn: CancelFn | None = None,
    ) -> ActiveExecution:
        async with self._lock:
            row = ActiveExecution(
                execution_id=execution_id,
                attempt_id=attempt_id or f"execution_attempt_{uuid4().hex}",
                backend_id=backend_id,
                harness_id=harness_id,
                cancel_fn=cancel_fn,
            )
            self._active[execution_id] = row
            return row

    async def update_attempt(
        self,
        execution_id: str,
        *,
        attempt_id: str,
        backend_id: str,
        harness_id: str | None = None,
        cancel_fn: CancelFn | None = None,
    ) -> ActiveExecution | None:
        async with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                return None
            row.attempt_id = attempt_id
            row.backend_id = backend_id
            row.harness_id = harness_id
            if cancel_fn is not None:
                row.cancel_fn = cancel_fn
            return row

    def get(self, execution_id: str) -> ActiveExecution | None:
        return self._active.get(execution_id)

    def is_cancelled(self, execution_id: str) -> bool:
        row = self._active.get(execution_id)
        return bool(row and row.cancelled)

    async def cancel(self, execution_id: str) -> Mapping[str, Any]:
        async with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                return {
                    "ok": True,
                    "idempotent": True,
                    "detail": "no active execution",
                    "execution_id": execution_id,
                }
            row.cancel_count += 1
            if row.cancelled:
                return {
                    "ok": True,
                    "idempotent": True,
                    "detail": "already cancelled",
                    "execution_id": execution_id,
                    "attempt_id": row.attempt_id,
                    "backend_id": row.backend_id,
                    "cancel_count": row.cancel_count,
                }
            row.cancelled = True
            cancel_fn = row.cancel_fn
        cleanup: Mapping[str, Any] = {"cancelled": True}
        if cancel_fn is not None:
            cleanup = await cancel_fn()
        return {
            "ok": True,
            "idempotent": False,
            "execution_id": execution_id,
            "attempt_id": row.attempt_id,
            "backend_id": row.backend_id,
            "harness_id": row.harness_id,
            "cleanup": dict(cleanup),
            "cancel_count": row.cancel_count,
        }

    def note_late_event(self, execution_id: str) -> bool:
        """Return True if the event should be ignored (post-cancel)."""

        row = self._active.get(execution_id)
        if row is None or not row.cancelled:
            return False
        row.late_events_ignored += 1
        return True

    async def clear(self, execution_id: str) -> None:
        async with self._lock:
            self._active.pop(execution_id, None)
