from datetime import UTC, datetime

import pytest

from joymesh.models import RunStatus
from joymesh.persistence import Database, RunRow


def row(status, exit_code):
    return RunRow(
        id="historical-run",
        task="synthetic",
        workspace="/tmp",
        harness_id="cursor",
        status=status,
        exit_code=exit_code,
        created_at=datetime.now(UTC),
        task_context_id="context",
    )


def test_legacy_successful_record_can_be_read():
    record = row("succeeded", 0)
    result = Database._run_model(record)
    assert result.status is RunStatus.COMPLETED
    assert result.exit_code == 0
    assert record.status == "succeeded"  # Reading never rewrites persisted history.


@pytest.mark.parametrize(
    "status,exit_code", [("succeeded", 1), ("succeeded", None), ("mystery", 0)]
)
def test_ambiguous_status_is_not_guessed_successful(status, exit_code):
    with pytest.raises(ValueError):
        Database._run_model(row(status, exit_code))
