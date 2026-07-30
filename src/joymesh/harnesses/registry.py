"""Harness definitions, aliases, adapters, discovery, and plugin loading."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points

from joymesh.adapters import CodexAdapter, HarnessAdapter, OpenCodeAdapter
from joymesh.harnesses.adapters import builtin_documented_adapters
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.harnesses.contracts import DiscoveryResult, HarnessDefinition
from joymesh.harnesses.discovery import DiscoveryPolicy, HarnessDiscovery

# IDs that must never appear in the production registry default set.
FORBIDDEN_PRODUCTION_HARNESS_IDS = frozenset({"fake", "joy"})


class HarnessRegistry:
    def __init__(
        self,
        adapters: Iterable[HarnessAdapter] | None = None,
        definitions: Iterable[HarnessDefinition] | None = None,
    ) -> None:
        catalogue = tuple(definitions or builtin_catalogue())
        if adapters is None:
            self._definitions = {
                definition.id: definition
                for definition in catalogue
                if definition.id not in FORBIDDEN_PRODUCTION_HARNESS_IDS
            }
        else:
            # Explicit adapter sets (tests) may include test-only definitions.
            self._definitions = {definition.id: definition for definition in catalogue}
        self._aliases: dict[str, str] = {}
        for definition in self._definitions.values():
            for alias in (definition.id, *definition.aliases):
                if (
                    adapters is None
                    and alias in FORBIDDEN_PRODUCTION_HARNESS_IDS
                ):
                    continue
                previous = self._aliases.get(alias)
                if previous is not None and previous != definition.id:
                    raise ValueError(f"harness alias collision: {alias}")
                self._aliases[alias] = definition.id
        self.discovery = HarnessDiscovery(tuple(self._definitions.values()))
        self._adapters: dict[str, HarnessAdapter] = {}
        self._allow_test_harnesses = adapters is not None
        selected = adapters
        if selected is None:
            selected = (
                CodexAdapter(),
                OpenCodeAdapter(),
                *builtin_documented_adapters(tuple(self._definitions.values())),
            )
        for adapter in selected:
            self.register(adapter)

    def resolve_id(self, harness_id_or_alias: str) -> str:
        if (
            harness_id_or_alias in FORBIDDEN_PRODUCTION_HARNESS_IDS
            and not self._allow_test_harnesses
        ):
            raise KeyError(f"harness removed from production: {harness_id_or_alias}")
        try:
            return self._aliases[harness_id_or_alias]
        except KeyError as exc:
            raise KeyError(f"unknown harness: {harness_id_or_alias}") from exc

    def definition(self, harness_id_or_alias: str) -> HarnessDefinition:
        return self._definitions[self.resolve_id(harness_id_or_alias)]

    def definitions(self) -> tuple[HarnessDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def register(self, adapter: HarnessAdapter, *, replace: bool = False) -> None:
        harness_id = adapter.manifest.harness_id
        if (
            harness_id in FORBIDDEN_PRODUCTION_HARNESS_IDS
            and not self._allow_test_harnesses
        ):
            raise ValueError(
                "refusing to register removed/test-only harness in production "
                f"registry: {harness_id}"
            )
        if harness_id not in self._definitions:
            raise ValueError(f"adapter has no harness definition: {harness_id}")
        if harness_id in self._adapters and not replace:
            raise ValueError(f"adapter already registered: {harness_id}")
        self._adapters[harness_id] = adapter

    def register_custom_definition(self, definition: HarnessDefinition) -> None:
        """Register a validated custom harness definition (not a forbidden id)."""

        if definition.id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
            raise ValueError(f"forbidden harness id: {definition.id}")
        self._definitions[definition.id] = definition
        for alias in (definition.id, *definition.aliases):
            self._aliases[alias] = definition.id

    def get(self, harness_id_or_alias: str) -> HarnessAdapter:
        harness_id = self.resolve_id(harness_id_or_alias)
        try:
            return self._adapters[harness_id]
        except KeyError as exc:
            definition = self._definitions[harness_id]
            reason = definition.unsupported_reason or "no executable adapter registered"
            raise KeyError(f"harness is discovery-only: {harness_id} ({reason})") from exc

    def list(self) -> tuple[HarnessAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))

    async def discover(
        self,
        harness_id_or_alias: str | None = None,
        *,
        policy: DiscoveryPolicy | None = None,
        overrides: dict[str, str] | None = None,
    ) -> tuple[DiscoveryResult, ...]:
        if harness_id_or_alias is None:
            return await self.discovery.discover_all(policy=policy, overrides=overrides)
        harness_id = self.resolve_id(harness_id_or_alias)
        return (await self.discovery.discover(harness_id, policy=policy, overrides=overrides),)

    def load_plugins(self, *, group: str = "joymesh.harness_adapters") -> tuple[str, ...]:
        loaded: list[str] = []
        for plugin in sorted(entry_points(group=group), key=lambda item: item.name):
            factory = plugin.load()
            adapter = factory()
            if not isinstance(adapter, HarnessAdapter):
                raise TypeError(f"plugin {plugin.name} did not return a HarnessAdapter")
            self.register(adapter)
            loaded.append(plugin.name)
        return tuple(loaded)
