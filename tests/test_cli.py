import json
from pathlib import Path

from typer.testing import CliRunner

from joymesh.cli import app
from joymesh.config import TelemetryMode, set_telemetry_mode
from joymesh.telemetry import reset_telemetry_service_for_tests

runner = CliRunner()


def test_cli_detect_and_run(monkeypatch, tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("JOYMESH_DATABASE_URL", database_url)
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    reset_telemetry_service_for_tests()
    set_telemetry_mode(TelemetryMode.NEVER)

    detected = runner.invoke(app, ["harness", "detect"])
    assert detected.exit_code == 0, detected.output
    harness_ids = {item["manifest"]["harness_id"] for item in json.loads(detected.output)}
    assert "fake" not in harness_ids
    assert "joy" not in harness_ids

    completed = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--task", "CLI demo"],
    )
    # Without a ready funded harness, run must fail with a real setup error (not fake success).
    assert completed.exit_code != 0
    reset_telemetry_service_for_tests()


def test_cli_harness_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("JOYMESH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    result = runner.invoke(app, ["harness", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "enabled" in payload
    assert payload["default"] is None
