"""In-memory factual observation store (usage / quality / latency).

Never stores prompts, code, workspace paths, or user identity.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime

from joymesh.models import FailureKind, utc_now
from joymesh.runtime_snapshot.contracts import (
    LatencySnapshot,
    QualityLevel,
    QualitySnapshot,
    UsageSnapshot,
)


@dataclass
class _HarnessStats:
    input_tokens: int = 0
    output_tokens: int = 0
    execution_count: int = 0
    last_execution: datetime | None = None
    durations_ms: list[float] = field(default_factory=list)
    last_quality: QualityLevel = QualityLevel.UNKNOWN
    active_runs: int = 0


class ObservationStore:
    """Thread-safe per-harness factual aggregates."""

    def __init__(self, *, max_latency_samples: int = 64) -> None:
        self._max_samples = max(8, int(max_latency_samples))
        self._stats: dict[str, _HarnessStats] = {}
        self._lock = threading.RLock()

    def _ensure(self, harness_id: str) -> _HarnessStats:
        stats = self._stats.get(harness_id)
        if stats is None:
            stats = _HarnessStats()
            self._stats[harness_id] = stats
        return stats

    def mark_running(self, harness_id: str, *, delta: int) -> None:
        with self._lock:
            stats = self._ensure(harness_id)
            stats.active_runs = max(0, stats.active_runs + delta)

    def record_execution(
        self,
        harness_id: str,
        *,
        success: bool,
        failure_kind: FailureKind | None = None,
        duration_ms: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        with self._lock:
            stats = self._ensure(harness_id)
            stats.execution_count += 1
            stats.last_execution = utc_now()
            stats.input_tokens += max(0, int(input_tokens))
            stats.output_tokens += max(0, int(output_tokens))
            if duration_ms is not None and duration_ms >= 0:
                stats.durations_ms.append(float(duration_ms))
                if len(stats.durations_ms) > self._max_samples:
                    stats.durations_ms = stats.durations_ms[-self._max_samples :]
            if success:
                stats.last_quality = QualityLevel.GOOD
            elif failure_kind is not None:
                stats.last_quality = QualityLevel.BAD
            else:
                stats.last_quality = QualityLevel.UNKNOWN

    def active_runs(self, harness_id: str) -> int:
        with self._lock:
            stats = self._stats.get(harness_id)
            return 0 if stats is None else stats.active_runs

    def usage(self, harness_id: str) -> UsageSnapshot:
        with self._lock:
            stats = self._stats.get(harness_id)
            if stats is None:
                return UsageSnapshot()
            avg = None
            if stats.durations_ms:
                avg = sum(stats.durations_ms) / len(stats.durations_ms)
            return UsageSnapshot(
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                total_tokens=stats.input_tokens + stats.output_tokens,
                execution_count=stats.execution_count,
                last_execution=stats.last_execution,
                average_duration_ms=avg,
            )

    def quality(self, harness_id: str) -> QualitySnapshot:
        with self._lock:
            stats = self._stats.get(harness_id)
            if stats is None:
                return QualitySnapshot()
            return QualitySnapshot(level=stats.last_quality)

    def latency(self, harness_id: str) -> LatencySnapshot:
        with self._lock:
            stats = self._stats.get(harness_id)
            if stats is None or not stats.durations_ms:
                return LatencySnapshot()
            samples = list(stats.durations_ms)
            average = sum(samples) / len(samples)
            last = samples[-1]
            ordered = sorted(samples)
            index = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
            return LatencySnapshot(
                average_ms=average,
                last_ms=last,
                p95_ms=ordered[index],
            )
