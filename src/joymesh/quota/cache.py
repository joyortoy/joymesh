"""Thread-safe TTL cache for quota snapshots."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from joymesh.quota.contracts import QuotaSnapshot

DEFAULT_TTL_SECONDS = 60.0


@dataclass
class _CacheEntry:
    snapshot: QuotaSnapshot
    expires_at: float


class QuotaCache:
    """Small in-process cache. Successful runs refresh; failures invalidate immediately."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, harness_id: str) -> QuotaSnapshot | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(harness_id)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(harness_id, None)
                return None
            return entry.snapshot

    def put(self, snapshot: QuotaSnapshot, *, ttl_seconds: float | None = None) -> None:
        ttl = self._ttl if ttl_seconds is None else max(0.0, float(ttl_seconds))
        with self._lock:
            self._entries[snapshot.harness_id] = _CacheEntry(
                snapshot=snapshot,
                expires_at=time.monotonic() + ttl,
            )

    def invalidate(self, harness_id: str | None = None) -> None:
        with self._lock:
            if harness_id is None:
                self._entries.clear()
            else:
                self._entries.pop(harness_id, None)

    def clear(self) -> None:
        self.invalidate(None)
