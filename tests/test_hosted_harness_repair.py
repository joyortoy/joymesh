import asyncio
import os
import sys

import pytest

from joymesh.delivery.joyctl_device import DeviceAgentError, _adapter_for, _job_harness_id
from joymesh.models import LaunchSpec, RunRequest
from joymesh.runtime import HarnessRuntime


@pytest.mark.parametrize("harness", ["codex", "opencode", "grok"])
def test_explicit_harness_requires_grant(harness):
    job = {"constraints": {"harness_id": harness}, "grant": {"capabilities": [harness]}}
    assert _job_harness_id(job) == harness
    assert _adapter_for(harness).manifest.harness_id == harness
    job["grant"]["capabilities"] = []
    with pytest.raises(DeviceAgentError, match="not authorized"):
        _job_harness_id(job)


def test_legacy_and_alias():
    assert _job_harness_id({}) == "codex"
    assert (
        _job_harness_id(
            {"constraints": {"harness_id": "openai"}, "grant": {"capabilities": ["codex"]}}
        )
        == "codex"
    )
    for h in ["fake", "auto", "unknown", 42]:
        with pytest.raises(DeviceAgentError):
            _job_harness_id({"constraints": {"harness_id": h}})


def test_grok_current_cli_flags(tmp_path):
    spec = _adapter_for("grok").build_launch_spec(RunRequest(task="test", workspace=str(tmp_path)))
    assert "--no-auto-update" not in spec.argv
    assert "--sandbox" in spec.argv


def test_large_lines_and_eof(tmp_path):
    async def check():
        runtime = HarnessRuntime()
        seen = []

        async def line(stream, value):
            seen.append((stream, value))

        spec = LaunchSpec(
            argv=(
                sys.executable,
                "-c",
                "import sys;print('x'*100000);print('y'*2000000);sys.stdout.write('tail')",
            ),
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout_seconds=10,
        )
        assert await runtime.execute(run_id="large", launch=spec, on_line=line) == 0
        out = [v for s, v in seen if s == "stdout"]
        assert len(out) == 3 and len(out[0]) == 100000
        assert out[1].endswith("[harness line truncated at 1 MiB]")
        assert len(out[1]) < 1024 * 1024 + 100 and out[2] == "tail"
        assert await runtime.active_run_ids() == ()

    asyncio.run(check())


def test_callback_failure_reaps_process(tmp_path):
    async def check():
        runtime = HarnessRuntime()
        pids = []

        async def started(pid):
            pids.append(pid)

        async def line(stream, value):
            raise RuntimeError("sink unavailable")

        spec = LaunchSpec(
            argv=(sys.executable, "-u", "-c", "import time;print('ready');time.sleep(60)"),
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout_seconds=10,
        )
        with pytest.raises(ExceptionGroup):
            await runtime.execute(run_id="sink", launch=spec, on_line=line, on_started=started)
        assert await runtime.active_run_ids() == ()
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)

    asyncio.run(check())


def test_private_native_reasoning_is_not_telemetry():
    import json

    from joymesh.delivery.joyctl_device import _is_private_harness_record

    for payload in [
        {"type": "thought", "data": "private"},
        {"item": {"type": "reasoning"}},
        {"params": {"update": {"sessionUpdate": "agent_thought_chunk"}}},
    ]:
        assert _is_private_harness_record(json.dumps(payload))
    assert not _is_private_harness_record('{"type":"text","data":"public answer"}')


def test_grok_hook_profile_keeps_explicit_sandbox(tmp_path, monkeypatch):
    import subprocess

    from pathlib import Path

    class FakeProtocol:
        def request(self, message):
            return {"accepted": True}

    class FakeRuntime:
        async def execute(self, *, run_id, launch, on_line):
            (Path(launch.cwd) / "result.txt").write_text("verified")
            return 0

    from joymesh.delivery.joyctl_device import WorkspaceResolver, execute_claimed_job

    for args in [
        ("init",),
        ("config", "user.email", "test@local"),
        ("config", "user.name", "Test"),
        ("commit", "--allow-empty", "-m", "baseline"),
    ]:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setenv("JOYMESH_GROK_SANDBOX_PROFILE", "joymesh-hooks")

    class Runtime(FakeRuntime):
        async def execute(self, *, run_id, launch, on_line):
            assert launch.argv[launch.argv.index("--sandbox") + 1] == "joymesh-hooks"
            assert launch.argv[launch.argv.index("--permission-mode") + 1] == "auto"
            assert launch.env["JOYMUX_DATA_DIR"].endswith(".grok/joymesh-route-state")
            assert launch.env["JOYMUX_SOCKET"].endswith(".joymux/runtime.sock")
            return await super().execute(run_id=run_id, launch=launch, on_line=on_line)

    job = {
        "job_id": "probe",
        "execution_id": "exec",
        "mission_id": "mission",
        "workspace_ref": "test",
        "instruction": "probe",
        "lease_id": "lease",
        "lease_token": "token",
        "grant": {"production_mutation": False, "capabilities": ["grok", "write_repository"]},
        "constraints": {"workspace_ref": "test", "harness_id": "grok", "required_commands": []},
    }
    result = asyncio.run(
        execute_claimed_job(
            job,
            resolver=WorkspaceResolver({"test": tmp_path}),
            protocol=FakeProtocol(),
            runtime=Runtime(),
        )
    )
    assert result["success"]
