import json
from pathlib import Path

from typer.testing import CliRunner

from joymesh.cli import app

runner = CliRunner()


def test_cli_detect_and_run(monkeypatch, tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("JOYMESH_DATABASE_URL", database_url)

    detected = runner.invoke(app, ["harness", "detect"])
    assert detected.exit_code == 0, detected.output
    assert any(item["manifest"]["harness_id"] == "fake" for item in json.loads(detected.output))

    completed = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--task", "CLI demo"],
    )
    assert completed.exit_code == 0, completed.output
    assert json.loads(completed.output)["status"] == "completed"
