"""Deterministic fake harness — **test-only**.

Do not register this adapter in the production ``HarnessRegistry`` default set.
Import it only from tests or explicit test fixtures.
"""

from __future__ import annotations

import json
import sys

from joymesh.adapters.base import HarnessAdapter
from joymesh.models import (
    AdapterObservation,
    Capability,
    CapabilityManifest,
    EventType,
    HarnessAvailability,
    HarnessDescriptor,
    LaunchSpec,
    NormalizedEvent,
    RunRequest,
    SupportStatus,
    UsageDelta,
)
from joymesh.security import redact_secrets

# Sentinel — architecture tests assert this is never a production default.
TEST_ONLY_HARNESS_ID = "fake"
REMOVED_PRODUCTION_HARNESS_IDS = frozenset({"fake", "joy"})


class FakeHarnessAdapter(HarnessAdapter):
    """Bundled deterministic adapter for unit/integration tests only."""

    executable_name = sys.executable
    conformance_passed = True

    def __init__(self, *, step_delay: float = 0.01) -> None:
        self.step_delay = step_delay

    @property
    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            harness_id=TEST_ONLY_HARNESS_ID,
            display_name="Fake Harness (test-only)",
            capabilities=frozenset(
                {
                    Capability.FILE_READ,
                    Capability.FILE_WRITE,
                    Capability.SHELL,
                    Capability.STREAMING,
                    Capability.SESSION_RESUME,
                }
            ),
            max_concurrency=8,
        )

    async def detect(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            manifest=self.manifest,
            availability=HarnessAvailability.AVAILABLE,
            executable=sys.executable,
            version="bundled-test",
            support_status=SupportStatus.SUPPORTED,
            detail="Test-only deterministic adapter; not a production harness",
        )

    def build_launch_spec(self, request: RunRequest) -> LaunchSpec:
        argv = [
            sys.executable,
            "-m",
            "joymesh.fake_worker",
            "--task",
            request.task,
            "--workspace",
            request.workspace,
            "--delay",
            str(self.step_delay),
        ]
        if request.resume_session_id:
            argv.extend(["--session", request.resume_session_id])
        return LaunchSpec(
            argv=tuple(argv),
            cwd=request.workspace,
            env=self.launch_environment(),
            timeout_seconds=request.timeout_seconds,
        )

    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> AdapterObservation:
        if stream == "stderr":
            return AdapterObservation(
                event=NormalizedEvent(
                    run_id=run_id,
                    sequence=sequence,
                    type=EventType.HARNESS_OUTPUT,
                    message=redact_secrets(line),
                    payload={"stream": stream, "level": "error"},
                )
            )

        try:
            native = json.loads(line)
        except json.JSONDecodeError:
            native = {"type": "output", "message": line}

        native_type = native.get("type")
        event_type = (
            EventType.HARNESS_PROGRESS if native_type == "progress" else EventType.HARNESS_OUTPUT
        )
        session_id = native.get("session_id")
        usage = None
        if native_type == "usage":
            usage = UsageDelta(
                input_tokens=int(native.get("input_tokens", 0)),
                output_tokens=int(native.get("output_tokens", 0)),
            )
        return AdapterObservation(
            event=NormalizedEvent(
                run_id=run_id,
                sequence=sequence,
                type=event_type,
                message=redact_secrets(str(native.get("message", ""))),
                payload={"stream": stream, "native_type": native_type},
            ),
            native_session_id=str(session_id) if session_id else None,
            usage=usage,
        )
