"""Thread-safe TTL cache for published runtime snapshots."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from joymesh.runtime_snapshot.contracts import RuntimeSnapshot

DEFAULT_TTL_SECONDS = 60.0


@dataclass
class _CacheEntry:
    snapshot: RuntimeSnapshot
    expires_at: float


class RuntimeSnapshotCache:
    """Small in-process cache for whole-mesh runtime snapshots."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._entry: _CacheEntry | None = None
        self._by_harness: dict[str, RuntimeSnapshot] = {}
        self._lock = threading.RLock()

    def get(self) -> RuntimeSnapshot | None:
        now = time.monotonic()
        with self._lock:
            if self._entry is None:
                return None
            if self._entry.expires_at <= now:
                self._entry = None
                return None
            return self._entry.snapshot

    def put(self, snapshot: RuntimeSnapshot, *, ttl_seconds: float | None = None) -> None:
        ttl = self._ttl if ttl_seconds is None else max(0.0, float(ttl_seconds))
        with self._lock:
            self._entry = _CacheEntry(
                snapshot=snapshot,
                expires_at=time.monotonic() + ttl,
            )

    def invalidate(self) -> None:
        with self._lock:
            self._entry = None

    def clear(self) -> None:
        self.invalidate()
