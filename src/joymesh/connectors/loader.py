"""Load and validate JSON-compatible YAML connector definitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from importlib.resources import files
from pathlib import Path

from joymesh.connectors.models import ConnectorDefinition


class ConnectorCatalogueError(ValueError):
    pass


class ConnectorCatalogue:
    def __init__(self, definitions: Iterable[ConnectorDefinition]) -> None:
        ordered = sorted(definitions, key=lambda item: item.harness_id)
        self._definitions: dict[str, ConnectorDefinition] = {}
        executable_claims: dict[str, str] = {}
        for definition in ordered:
            if definition.harness_id in self._definitions:
                raise ConnectorCatalogueError(f"duplicate connector id: {definition.harness_id}")
            self._definitions[definition.harness_id] = definition
            for executable in definition.executable_names:
                previous = executable_claims.get(executable)
                if previous and previous != definition.harness_id:
                    raise ConnectorCatalogueError(
                        f"unresolved executable claim: {executable} "
                        f"({previous}, {definition.harness_id})"
                    )
                executable_claims[executable] = definition.harness_id

    @classmethod
    def builtins(cls) -> ConnectorCatalogue:
        root = files("joymesh.connectors.catalogue")
        definitions = []
        for resource in sorted(root.iterdir(), key=lambda item: item.name):
            if resource.name.endswith(".yaml"):
                definitions.append(ConnectorDefinition.model_validate_json(resource.read_text()))
        return cls(definitions)

    @classmethod
    def from_directory(cls, path: Path) -> ConnectorCatalogue:
        return cls(
            ConnectorDefinition.model_validate_json(item.read_text(encoding="utf-8"))
            for item in sorted(path.glob("*.yaml"))
        )

    def all(self) -> tuple[ConnectorDefinition, ...]:
        return tuple(self._definitions.values())

    def get(self, connector_id: str) -> ConnectorDefinition:
        try:
            return self._definitions[connector_id]
        except KeyError as exc:
            raise KeyError(f"unknown connector: {connector_id}") from exc

    def stale(self, *, max_age_days: int = 90) -> tuple[ConnectorDefinition, ...]:
        return tuple(item for item in self.all() if item.source_review_age_days > max_age_days)

    def revision_digest(self) -> str:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in self.all()],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()
