"""Harness adapter contract."""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from joymesh.models import (
    AdapterObservation,
    Capability,
    CapabilityManifest,
    FailureKind,
    HarnessAvailability,
    HarnessDescriptor,
    HarnessFailure,
    LaunchSpec,
    RunRequest,
    SupportStatus,
)
from joymesh.security import filter_environment


class UnsupportedFeatureError(NotImplementedError):
    pass


class HarnessAdapter(ABC):
    """Translates one native harness into the stable JoyMesh protocol."""

    @property
    @abstractmethod
    def manifest(self) -> CapabilityManifest:
        """Return static capability metadata."""

    async def detect(self) -> HarnessDescriptor:
        """Inspect whether the harness can run on this machine."""
        executable = shutil.which(self.executable_name)
        if executable is None:
            return HarnessDescriptor(
                manifest=self.manifest,
                availability=HarnessAvailability.UNAVAILABLE,
                support_status=SupportStatus.UNAVAILABLE,
                detail=f"{self.executable_name} not found on PATH",
            )
        process = await asyncio.create_subprocess_exec(
            executable,
            *self.version_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=filter_environment(extra_keys=self.environment_keys),
        )
        stdout, stderr = await process.communicate()
        version = (stdout or stderr).decode(errors="replace").strip().splitlines()[0]
        if process.returncode != 0:
            return HarnessDescriptor(
                manifest=self.manifest,
                availability=HarnessAvailability.UNAVAILABLE,
                executable=executable,
                version=version or None,
                support_status=SupportStatus.UNAVAILABLE,
                detail=f"version probe exited with status {process.returncode}",
            )
        return HarnessDescriptor(
            manifest=self.manifest,
            availability=HarnessAvailability.AVAILABLE,
            executable=executable,
            version=version,
            support_status=(
                SupportStatus.SUPPORTED if self.conformance_passed else SupportStatus.EXPERIMENTAL
            ),
        )

    @abstractmethod
    def build_launch_spec(self, request: RunRequest) -> LaunchSpec:
        """Build a launch specification without invoking a shell."""

    @abstractmethod
    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> AdapterObservation:
        """Translate one native output line into a normalized event."""

    def classify_failure(self, *, exit_code: int, output: str) -> HarnessFailure:
        lowered = output.lower()
        if "rate limit" in lowered or "too many requests" in lowered or "429" in lowered:
            return HarnessFailure(
                kind=FailureKind.RATE_LIMIT,
                message="Harness rate limit encountered",
                retryable=True,
            )
        if "quota" in lowered and ("exhaust" in lowered or "limit" in lowered):
            return HarnessFailure(
                kind=FailureKind.QUOTA_EXHAUSTED,
                message="Harness quota exhausted",
            )
        if "unauthorized" in lowered or "authentication" in lowered:
            return HarnessFailure(
                kind=FailureKind.AUTHENTICATION,
                message="Harness authentication failed",
            )
        return HarnessFailure(
            kind=FailureKind.PROCESS,
            message=f"Harness exited with status {exit_code}",
            retryable=False,
        )

    def require_feature(self, capability: Capability) -> None:
        if capability not in self.manifest.capabilities:
            raise UnsupportedFeatureError(
                f"{self.manifest.harness_id} does not support {capability.value}"
            )

    def launch_environment(self) -> dict[str, str]:
        return filter_environment(extra_keys=self.environment_keys)

    @staticmethod
    def validate_workspace(workspace: str) -> str:
        return str(Path(workspace).resolve())

    executable_name: str
    version_args: tuple[str, ...] = ("--version",)
    environment_keys: frozenset[str] = frozenset()
    conformance_passed: bool = False
