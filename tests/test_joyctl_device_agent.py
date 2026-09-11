from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from joymesh.delivery.joyctl_device import (
    DeviceAgentError,
    WorkspaceResolver,
    changed_paths,
    event_id,
    execute_claimed_job,
    parse_workspace_mappings,
)
from joymesh.models import LaunchSpec


class FakeProtocol:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        self.messages.append(message)
        return {"accepted": True}


class FakeAdapter:
    def build_launch_spec(self, request: Any) -> LaunchSpec:
        return LaunchSpec(
            argv=("fake",),
            cwd=request.workspace,
            env={},
            timeout_seconds=10,
        )


class FakeRuntime:
    async def execute(self, *, run_id: str, launch: LaunchSpec, on_line: Any) -> int:
        await on_line("stdout", '{"type":"turn.completed"}')
        (Path(launch.cwd) / "result.txt").write_text("real change\n", encoding="utf-8")
        return 0


def git(workspace: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=workspace, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-m", "baseline")
    return tmp_path


def test_workspace_mapping_requires_explicit_absolute_repository(repository: Path) -> None:
    mapping = parse_workspace_mappings([f"joyspa-main={repository}"])
    assert WorkspaceResolver(mapping).resolve("joyspa-main") == repository.resolve()
    with pytest.raises(DeviceAgentError):
        parse_workspace_mappings(["joyspa-main=relative"])
    with pytest.raises(DeviceAgentError):
        WorkspaceResolver(mapping).resolve("unknown")


def test_event_ids_are_stable_and_sequence_specific() -> None:
    assert event_id("exec-1", 1) == event_id("exec-1", 1)
    assert event_id("exec-1", 1) != event_id("exec-1", 2)


def test_changed_paths_are_repository_relative(repository: Path) -> None:
    (repository / "new.txt").write_text("value\n", encoding="utf-8")
    assert changed_paths(repository) == ("new.txt",)


def test_real_job_execution_reports_factual_evidence(repository: Path) -> None:
    protocol = FakeProtocol()
    job = {
        "job_id": "job-1",
        "execution_id": "execution-1",
        "mission_id": "mission-1",
        "workspace_ref": "joyspa-main",
        "instruction": "Make a real change",
        "lease_id": "lease-1",
        "lease_token": "lease-token",
        "grant": {"production_mutation": False},
        "constraints": {
            "workspace_ref": "joyspa-main",
            "timeout_seconds": 10,
            "required_commands": [],
        },
    }
    result = asyncio.run(
        execute_claimed_job(
            job,
            resolver=WorkspaceResolver({"joyspa-main": repository}),
            protocol=protocol,
            adapter=FakeAdapter(),  # type: ignore[arg-type]
            runtime=FakeRuntime(),  # type: ignore[arg-type]
        )
    )
    event_types = [
        message["event"]["type"] for message in protocol.messages if message["type"] == "event"
    ]
    assert result["success"] is True
    assert result["changed_paths"] == ["result.txt"]
    assert event_types[-1] == "completed"
    assert "evidence_produced" in event_types
    assert [
        message["event"]["sequence"] for message in protocol.messages if message["type"] == "event"
    ] == list(range(1, len(event_types) + 1))


def test_dirty_workspace_is_rejected_before_harness(repository: Path) -> None:
    (repository / "dirty.txt").write_text("existing user work\n", encoding="utf-8")
    with pytest.raises(DeviceAgentError, match="dirty"):
        asyncio.run(
            execute_claimed_job(
                {
                    "job_id": "job-1",
                    "execution_id": "execution-1",
                    "workspace_ref": "joyspa-main",
                    "instruction": "Do not run",
                    "lease_id": "lease-1",
                    "lease_token": "lease-token",
                    "grant": {"production_mutation": False},
                    "constraints": {"workspace_ref": "joyspa-main"},
                },
                resolver=WorkspaceResolver({"joyspa-main": repository}),
                protocol=FakeProtocol(),
                adapter=FakeAdapter(),  # type: ignore[arg-type]
                runtime=FakeRuntime(),  # type: ignore[arg-type]
            )
        )
