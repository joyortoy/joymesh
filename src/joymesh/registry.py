"""Compatibility facade for the data-driven harness registry."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from joymesh.adapters import HarnessAdapter
from joymesh.harnesses.contracts import AdapterMaturity, HarnessDefinition
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.models import (
    HarnessAvailability,
    HarnessDescriptor,
    SupportStatus,
)


class AdapterRegistry(HarnessRegistry):
    """Old name retained for SDK compatibility."""

    def __init__(
        self,
        adapters: Iterable[HarnessAdapter] | None = None,
        definitions: Iterable[HarnessDefinition] | None = None,
    ) -> None:
        super().__init__(adapters=adapters, definitions=definitions)

    async def detect(self) -> tuple[HarnessDescriptor, ...]:
        descriptors: list[HarnessDescriptor] = []
        for adapter in self.list():
            configured, configured_available = _configured_executable(adapter.executable_name)
            if configured is not None:
                available = configured_available
                executable = configured if available else None
            else:
                result = (await self.discover(adapter.manifest.harness_id))[0]
                available = bool(result.installations)
                executable = result.installations[0].executable if result.installations else None
            definition = self.definition(adapter.manifest.harness_id)
            descriptors.append(
                HarnessDescriptor(
                    manifest=adapter.manifest,
                    availability=(
                        HarnessAvailability.AVAILABLE
                        if available
                        else HarnessAvailability.UNAVAILABLE
                    ),
                    executable=executable,
                    support_status=(
                        SupportStatus.UNAVAILABLE
                        if not available
                        else (
                            SupportStatus.SUPPORTED
                            if adapter.conformance_passed
                            or definition.maturity is AdapterMaturity.STABLE
                            else SupportStatus.EXPERIMENTAL
                        )
                    ),
                    detail=None if available else "executable_not_found",
                )
            )
        return tuple(descriptors)


def _configured_executable(value: str) -> tuple[str | None, bool]:
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        return None, False
    return str(path), path.is_file()
