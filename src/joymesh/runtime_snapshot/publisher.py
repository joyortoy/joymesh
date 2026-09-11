"""Publish frozen runtime snapshots (no routing policy)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from joymesh.runtime_snapshot.contracts import RuntimeSnapshot
from joymesh.runtime_snapshot.validators import assert_privacy, validate_snapshot

Listener = Callable[[RuntimeSnapshot], None]


class RuntimeSnapshotPublisher:
    """Holds the last validated snapshot and notifies optional listeners."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest: RuntimeSnapshot | None = None
        self._listeners: list[Listener] = []

    def publish(self, snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
        validate_snapshot(snapshot)
        assert_privacy(snapshot.as_dict())
        with self._lock:
            self._latest = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            listener(snapshot)
        return snapshot

    def latest(self) -> RuntimeSnapshot | None:
        with self._lock:
            return self._latest

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def as_json(self, snapshot: RuntimeSnapshot | None = None) -> dict[str, Any]:
        target = snapshot if snapshot is not None else self.latest()
        if target is None:
            return {}
        payload = target.as_dict()
        assert_privacy(payload)
        return payload
