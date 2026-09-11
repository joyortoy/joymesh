from pathlib import Path

from joymesh.adapters.codex import UNIX_IPC_CONFIG, CodexAdapter
from joymesh.models import RunRequest


def test_codex_launch_enables_unix_socket_ipc(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".joymux").mkdir()
    spec = CodexAdapter().build_launch_spec(
        RunRequest(task="probe runtime.sock", workspace=str(tmp_path / "ws"))
    )
    argv = spec.argv
    assert argv[1:3] == ("exec", "--json")
    sandbox_index = argv.index("--sandbox")
    assert argv[sandbox_index + 1] == "workspace-write"
    assert "-c" in argv
    assert UNIX_IPC_CONFIG in argv
    joymux_dir = str(tmp_path / ".joymux")
    assert "--add-dir" in argv
    assert joymux_dir in argv
    assert argv[-1] == "probe runtime.sock"
