"""Deterministic fake harness used for tests and demonstrations."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from joymesh.adapters.base import HarnessAdapter
from joymesh.models import (
    Capability,
    CapabilityManifest,
    EventType,
    HarnessAvailability,
    HarnessDescriptor,
    NormalizedEvent,
)


class FakeHarnessAdapter(HarnessAdapter):
    def __init__(self, *, step_delay: float = 0.01) -> None:
        self.step_delay = step_delay

    @property
    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            harness_id="fake",
            display_name="Fake Harness",
            capabilities=frozenset(
                {
                    Capability.FILE_READ,
                    Capability.FILE_WRITE,
                    Capability.SHELL,
                    Capability.STREAMING,
                }
            ),
            max_concurrency=8,
        )

    async def detect(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            manifest=self.manifest,
            availability=HarnessAvailability.AVAILABLE,
            executable=sys.executable,
            detail="Bundled deterministic adapter",
        )

    def build_command(self, task: str, workspace: str) -> Sequence[str]:
        return (
            sys.executable,
            "-m",
            "joymesh.fake_worker",
            "--task",
            task,
            "--workspace",
            workspace,
            "--delay",
            str(self.step_delay),
        )

    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> NormalizedEvent:
        if stream == "stderr":
            return NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=EventType.HARNESS_OUTPUT,
                message=line,
                payload={"stream": stream, "level": "error"},
            )

        try:
            native = json.loads(line)
        except json.JSONDecodeError:
            native = {"type": "output", "message": line}

        native_type = native.get("type")
        event_type = (
            EventType.HARNESS_PROGRESS
            if native_type == "progress"
            else EventType.HARNESS_OUTPUT
        )
        return NormalizedEvent(
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            message=str(native.get("message", "")),
            payload={"stream": stream, "native_type": native_type},
        )
