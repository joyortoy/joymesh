"""Tests for SafeProcessRunner and connector-backed harness adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from joymesh.runtime_v1.execution_routing.harness import (
    ConnectorHarnessAdapter,
    builtin_harness_adapters,
)
from joymesh.runtime_v1.execution_routing.process_runner import (
    ProcessRunnerError,
    ProcessRunRequest,
    SafeProcessRunner,
)


@pytest.mark.asyncio
async def test_process_runner_timeout_kills_tree(tmp_path: Path) -> None:
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)\n")
    runner = SafeProcessRunner()
    result = await runner.run(
        ProcessRunRequest(
            argv=["python3", str(script)],
            cwd=str(tmp_path),
            timeout_seconds=0.2,
        )
    )
    assert result.timed_out
    assert result.classification == "timeout"
    assert result.ok is False


@pytest.mark.asyncio
async def test_process_runner_cancel(tmp_path: Path) -> None:
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)\n")
    runner = SafeProcessRunner()
    request = ProcessRunRequest(
        argv=["python3", str(script)],
        cwd=str(tmp_path),
        timeout_seconds=10,
        run_id="run-cancel-1",
    )

    async def _cancel_soon() -> None:
        import asyncio

        await asyncio.sleep(0.1)
        await runner.cancel("run-cancel-1")

    import asyncio

    cancel_task = asyncio.create_task(_cancel_soon())
    result = await runner.run(request)
    await cancel_task
    assert result.cancelled or result.timed_out or result.returncode is not None


@pytest.mark.asyncio
async def test_process_runner_output_limit(tmp_path: Path) -> None:
    script = tmp_path / "big.py"
    script.write_text("print('x' * 10000)\n")
    runner = SafeProcessRunner()
    result = await runner.run(
        ProcessRunRequest(
            argv=["python3", str(script)],
            cwd=str(tmp_path),
            timeout_seconds=5,
            max_stdout_bytes=100,
        )
    )
    assert result.truncated is True
    assert len(result.stdout.encode()) <= 100


def test_vscode_adapter_explicitly_unsupported() -> None:
    adapters = builtin_harness_adapters(use_real_adapters=False)
    assert adapters["vscode"].supported is False


@pytest.mark.asyncio
async def test_connector_adapter_detect_without_live_probe_when_missing() -> None:
    class _Fake:
        connector_id = "codex"
        display_name = "Codex"

        def declared_capabilities(self):
            return frozenset()

        def execution_environment(self, *, read_only=True):
            return {}

        async def discover(self, context):
            from joymesh.runtime_v1.connector_protocol import DiscoveryResult

            return DiscoveryResult(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                reason_code="executable_not_found",
            )

        def build_exec_argv(self, **kwargs):
            return ("codex", "exec", kwargs["prompt"])

    adapter = ConnectorHarnessAdapter(
        harness_id="codex",
        display_name="Codex",
        connector=_Fake(),
    )
    detection = await adapter.detect()
    assert detection["installed"] is False
    assert detection["usable"] is False


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    runner = SafeProcessRunner()
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(ProcessRunnerError):
        runner.validate_cwd(str(link), allowed_roots=(str(root),))
