from pathlib import Path

from joymesh.harnesses.adapters import builtin_documented_adapters
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import PermissionMode, RunRequest


def _adapter(harness_id: str):
    return next(
        item
        for item in builtin_documented_adapters(builtin_catalogue())
        if item.manifest.harness_id == harness_id
    )


def test_cursor_headless_argv_trusts_workspace(tmp_path: Path) -> None:
    spec = _adapter("cursor").build_launch_spec(RunRequest(task="probe", workspace=str(tmp_path)))
    assert spec.argv[0] == "/usr/bin/sandbox-exec"
    index = spec.argv.index("cursor-agent")
    assert spec.argv[index : index + 5] == (
        "cursor-agent",
        "--print",
        "--output-format",
        "stream-json",
        "--trust",
    )
    assert spec.argv[-1] == "probe"
    assert "--force" not in spec.argv

    auto = _adapter("cursor").build_launch_spec(
        RunRequest(
            task="probe",
            workspace=str(tmp_path),
            permission_mode=PermissionMode.AUTO_APPROVE,
        )
    )
    assert "--force" in auto.argv
    assert "--trust" in auto.argv


def test_grok_default_argv_is_not_read_only_cert(tmp_path: Path) -> None:
    spec = _adapter("grok").build_launch_spec(RunRequest(task="probe", workspace=str(tmp_path)))
    assert spec.argv[:6] == (
        "grok",
        "--no-auto-update",
        "-p",
        "probe",
        "--output-format",
        "streaming-json",
    )
    assert "--sandbox" not in spec.argv
    assert "strict" not in spec.argv

    auto = _adapter("grok").build_launch_spec(
        RunRequest(
            task="probe",
            workspace=str(tmp_path),
            permission_mode=PermissionMode.AUTO_APPROVE,
        )
    )
    assert auto.argv[auto.argv.index("--permission-mode") + 1] == "bypassPermissions"

    read_only = _adapter("grok").build_launch_spec(
        RunRequest(
            task="probe",
            workspace=str(tmp_path),
            permission_mode=PermissionMode.READ_ONLY,
        )
    )
    assert read_only.argv[read_only.argv.index("--sandbox") + 1] == "strict"
    assert "plan" in read_only.argv
