"""Harness adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from joymesh.models import CapabilityManifest, HarnessDescriptor, NormalizedEvent


class HarnessAdapter(ABC):
    """Translates one native harness into the stable JoyMesh protocol."""

    @property
    @abstractmethod
    def manifest(self) -> CapabilityManifest:
        """Return static capability metadata."""

    @abstractmethod
    async def detect(self) -> HarnessDescriptor:
        """Inspect whether the harness can run on this machine."""

    @abstractmethod
    def build_command(self, task: str, workspace: str) -> Sequence[str]:
        """Build an argv sequence without invoking a shell."""

    @abstractmethod
    def normalize_output(
        self,
        *,
        run_id: str,
        sequence: int,
        stream: str,
        line: str,
    ) -> NormalizedEvent:
        """Translate one native output line into a normalized event."""
