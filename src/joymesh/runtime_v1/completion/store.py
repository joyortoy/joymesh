"""In-memory completion record store with restart recovery scan."""

from __future__ import annotations

from joymesh.runtime_v1.completion.models import CompletionRecord
from joymesh.runtime_v1.completion.states import (
    RESUMABLE_COMPLETION_STATES,
    CompletionLifecycleState,
)


class CompletionStore:
    def __init__(self) -> None:
        self._records: dict[str, CompletionRecord] = {}

    def get(self, execution_id: str) -> CompletionRecord | None:
        return self._records.get(execution_id)

    def save(self, record: CompletionRecord) -> CompletionRecord:
        self._records[record.execution_id] = record
        return record

    def list_resumable(self) -> tuple[CompletionRecord, ...]:
        return tuple(
            item for item in self._records.values() if item.state in RESUMABLE_COMPLETION_STATES
        )

    def is_terminal(self, execution_id: str) -> bool:
        record = self._records.get(execution_id)
        if record is None:
            return False
        return record.state in {
            CompletionLifecycleState.COMPLETED,
            CompletionLifecycleState.FAILED,
            CompletionLifecycleState.BLOCKED,
            CompletionLifecycleState.CANCELLED,
            CompletionLifecycleState.TIMED_OUT,
        }
