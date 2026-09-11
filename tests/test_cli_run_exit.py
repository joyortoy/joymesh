"""CLI ``joymesh run`` exit-code contract for terminal run statuses."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from joymesh.cli import app
from joymesh.config import TelemetryMode, set_telemetry_mode
from joymesh.models import Run, RunStatus, utc_now
from joymesh.service import NoRouteError
from joymesh.telemetry import reset_telemetry_service_for_tests

runner = CliRunner()


@pytest.fixture(autouse=True)
def placement_for_exit_tests(monkeypatch):
    monkeypatch.setattr(
        "joymesh.cli.fetch_context_placement",
        lambda **kwargs: {
            "schema": "joy.context_placement_decision/v1",
            "selected_harness": "fake",
            "requirements_id": "req-cli-exit",
            "executable": True,
        },
    )


def _completed_run(*, status: RunStatus, error: str | None = None) -> Run:
    return Run(
        id="run-cli-exit",
        task="demo",
        workspace="/tmp",
        harness_id="fake",
        subscription_id=None,
        status=status,
        created_at=utc_now(),
        task_context_id="ctx-cli-exit",
        error=error,
        exit_code=1 if status is RunStatus.FAILED else None,
    )


def _patch_run_wait(monkeypatch, completed: Run) -> None:
    mesh = AsyncMock()
    mesh.run = AsyncMock(return_value=completed)
    mesh.wait = AsyncMock(return_value=completed)
    mesh.close = AsyncMock()
    mesh.usage = AsyncMock(return_value=())

    class _MeshFactory:
        def __call__(self) -> AsyncMock:
            return mesh

    monkeypatch.setattr("joymesh.cli.JoyMesh", _MeshFactory())
    monkeypatch.setattr("joymesh.cli._maybe_prompt_telemetry_consent", lambda: None)
    monkeypatch.setattr("joymesh.cli._maybe_send_run_telemetry", lambda *a, **k: None)


def test_cli_run_failed_status_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    reset_telemetry_service_for_tests()
    set_telemetry_mode(TelemetryMode.NEVER)
    completed = _completed_run(
        status=RunStatus.FAILED,
        error="HarnessStreamOverflowError:stream_record_overflow",
    )
    _patch_run_wait(monkeypatch, completed)

    result = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--task", "fail"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["error"] == completed.error
    reset_telemetry_service_for_tests()


def test_cli_run_timed_out_and_cancelled_exit_nonzero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    reset_telemetry_service_for_tests()
    set_telemetry_mode(TelemetryMode.NEVER)

    for status in (RunStatus.TIMED_OUT, RunStatus.CANCELLED):
        _patch_run_wait(monkeypatch, _completed_run(status=status))
        result = runner.invoke(
            app,
            ["run", "--workspace", str(tmp_path), "--task", status.value],
        )
        assert result.exit_code == 1, status
        assert json.loads(result.stdout)["status"] == status.value
    reset_telemetry_service_for_tests()


def test_cli_run_completed_still_exits_zero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    reset_telemetry_service_for_tests()
    set_telemetry_mode(TelemetryMode.NEVER)
    completed = _completed_run(status=RunStatus.COMPLETED)
    completed = completed.model_copy(update={"exit_code": 0, "error": None})
    _patch_run_wait(monkeypatch, completed)

    result = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--task", "ok"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "completed"
    reset_telemetry_service_for_tests()


def test_cli_run_no_route_still_exits_two(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    reset_telemetry_service_for_tests()
    set_telemetry_mode(TelemetryMode.NEVER)

    mesh = AsyncMock()
    mesh.run = AsyncMock(
        side_effect=NoRouteError(
            "no eligible harness route",
            remediation="Enable a funded harness.",
        )
    )
    mesh.close = AsyncMock()

    class _MeshFactory:
        def __call__(self) -> AsyncMock:
            return mesh

    monkeypatch.setattr("joymesh.cli.JoyMesh", _MeshFactory())
    monkeypatch.setattr("joymesh.cli._maybe_prompt_telemetry_consent", lambda: None)

    result = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--task", "nope"],
    )
    assert result.exit_code == 2
    assert "no eligible harness route" in result.output
    assert "Enable a funded harness." in result.output
    reset_telemetry_service_for_tests()
