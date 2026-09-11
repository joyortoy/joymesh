from pathlib import Path

import pytest

from joymesh.harnesses.adapters import builtin_documented_adapters
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.models import PermissionMode, RunRequest
from joymesh.runtime_v1.connectors.cursor import CursorConnectorRuntime


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_cursor_permission_modes_keep_sandbox_enabled(tmp_path: Path, mode) -> None:
    adapter = next(
        a
        for a in builtin_documented_adapters(builtin_catalogue())
        if a.manifest.harness_id == "cursor"
    )
    argv = adapter.build_launch_spec(
        RunRequest(task="probe", workspace=str(tmp_path), permission_mode=mode)
    ).argv
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert ("--force" in argv) == (mode == PermissionMode.AUTO_APPROVE)
    if mode == PermissionMode.READ_ONLY:
        assert argv[argv.index("--mode") + 1] == "plan"
        assert "--force" not in argv


@pytest.mark.parametrize("read_only", [True, False])
def test_outbound_cursor_connector_preserves_isolation(read_only) -> None:
    argv = CursorConnectorRuntime().build_exec_argv(
        executable="cursor-agent", prompt="probe", workspace_path="/tmp", read_only=read_only
    )
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert ("--mode" in argv) == read_only
    if read_only:
        assert argv[argv.index("--mode") + 1] == "plan"


def test_certification_uses_plan_and_sandbox(tmp_path: Path) -> None:
    argv = CursorConnectorRuntime().build_read_only_cert_argv(
        executable="cursor-agent", prompt="probe", workspace=tmp_path
    )
    assert argv[argv.index("--mode") + 1] == "plan"
    assert argv[argv.index("--sandbox") + 1] == "enabled"
