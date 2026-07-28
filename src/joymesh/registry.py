"""Adapter registration and discovery."""

from __future__ import annotations

from collections.abc import Iterable

from joymesh.adapters import CodexAdapter, FakeHarnessAdapter, HarnessAdapter, OpenCodeAdapter
from joymesh.models import HarnessDescriptor


class AdapterRegistry:
    def __init__(self, adapters: Iterable[HarnessAdapter] | None = None) -> None:
        self._adapters: dict[str, HarnessAdapter] = {}
        for adapter in adapters or (
            FakeHarnessAdapter(),
            CodexAdapter(),
            OpenCodeAdapter(),
        ):
            self.register(adapter)

    def register(self, adapter: HarnessAdapter) -> None:
        harness_id = adapter.manifest.harness_id
        if harness_id in self._adapters:
            raise ValueError(f"adapter already registered: {harness_id}")
        self._adapters[harness_id] = adapter

    def get(self, harness_id: str) -> HarnessAdapter:
        try:
            return self._adapters[harness_id]
        except KeyError as exc:
            raise KeyError(f"unknown harness: {harness_id}") from exc

    def list(self) -> tuple[HarnessAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))

    async def detect(self) -> tuple[HarnessDescriptor, ...]:
        return tuple([await adapter.detect() for adapter in self.list()])
