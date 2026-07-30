"""Canonical capability registry and dependency expansion."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    description: str
    risk_level: str
    dependencies: frozenset[str]
    conflicts: frozenset[str]
    default_approval_class: str
    routable: bool
    experimental: bool
    definition_revision: str = "2026-07-29.1"


_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        "repository.read",
        "Read repository files and structure",
        "low",
        frozenset({"filesystem.read", "workspace.contained"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "repository.search",
        "Search repository contents",
        "low",
        frozenset({"repository.read"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "repository.summarise",
        "Summarise repository architecture or contents",
        "low",
        frozenset({"repository.read", "structured_output"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "repository.write",
        "Create or modify repository files",
        "high",
        frozenset({"filesystem.write"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "repository.patch",
        "Apply patches to repository files",
        "high",
        frozenset({"repository.write"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "filesystem.read",
        "Read files inside an approved workspace",
        "low",
        frozenset({"workspace.contained"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "filesystem.write",
        "Write files inside an approved workspace",
        "high",
        frozenset({"workspace.contained"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "structured_output",
        "Emit structured machine-readable output",
        "low",
        frozenset(),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "streaming_output",
        "Stream incremental execution events",
        "low",
        frozenset(),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "shell.execute",
        "Execute shell commands",
        "critical",
        frozenset({"process_tree.cancel"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "shell.cancel",
        "Cancel shell command processes",
        "medium",
        frozenset({"process_tree.cancel"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "git.read",
        "Read Git metadata and history",
        "low",
        frozenset({"repository.read"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "git.diff",
        "Inspect Git diffs",
        "low",
        frozenset({"git.read"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "git.commit",
        "Create Git commits",
        "high",
        frozenset({"repository.write", "filesystem.write"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "git.push",
        "Push Git commits to a remote",
        "critical",
        frozenset({"git.commit"}),
        frozenset(),
        "step_up",
        True,
        False,
    ),
    CapabilityDefinition(
        "dependency.install",
        "Install package dependencies",
        "critical",
        frozenset({"shell.execute"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "test.execute",
        "Run tests",
        "high",
        frozenset({"shell.execute", "process_tree.cancel"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "build.execute",
        "Run builds",
        "high",
        frozenset({"shell.execute", "process_tree.cancel"}),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "network.read",
        "Read from the network",
        "medium",
        frozenset(),
        frozenset(),
        "explicit",
        True,
        False,
    ),
    CapabilityDefinition(
        "network.write",
        "Write to the network",
        "critical",
        frozenset({"network.read"}),
        frozenset(),
        "step_up",
        True,
        False,
    ),
    CapabilityDefinition(
        "session.resume",
        "Resume a prior connector session",
        "medium",
        frozenset(),
        frozenset(),
        "explicit",
        True,
        True,
    ),
    CapabilityDefinition(
        "session.fork",
        "Fork a connector session",
        "medium",
        frozenset({"session.resume"}),
        frozenset(),
        "explicit",
        True,
        True,
    ),
    CapabilityDefinition(
        "browser.use",
        "Drive a browser",
        "high",
        frozenset({"network.write"}),
        frozenset(),
        "explicit",
        True,
        True,
    ),
    CapabilityDefinition(
        "mcp.use",
        "Call MCP tools",
        "high",
        frozenset(),
        frozenset(),
        "explicit",
        True,
        True,
    ),
    CapabilityDefinition(
        "images.read",
        "Read image inputs",
        "low",
        frozenset({"filesystem.read"}),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "workspace.contained",
        "Execution is confined to an approved workspace",
        "low",
        frozenset(),
        frozenset(),
        "none",
        True,
        False,
    ),
    CapabilityDefinition(
        "process_tree.cancel",
        "Cancel the full process tree for an execution",
        "medium",
        frozenset(),
        frozenset(),
        "none",
        True,
        False,
    ),
)


class CapabilityRegistry:
    def __init__(self, definitions: tuple[CapabilityDefinition, ...] | None = None) -> None:
        items = definitions or _CAPABILITIES
        self._by_id = {item.capability_id: item for item in items}

    def get(self, capability_id: str) -> CapabilityDefinition:
        try:
            return self._by_id[capability_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {capability_id}") from exc

    def all(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def known(self, capability_id: str) -> bool:
        return capability_id in self._by_id


def expand_capabilities(
    requested: frozenset[str],
    *,
    prohibited: frozenset[str] = frozenset(),
    registry: CapabilityRegistry | None = None,
) -> frozenset[str]:
    """Expand requested capabilities with dependencies; reject unknowns and conflicts."""

    caps = registry or CapabilityRegistry()
    unknown = sorted(item for item in requested | prohibited if not caps.known(item))
    if unknown:
        raise ValueError(f"unknown capabilities: {', '.join(unknown)}")
    overridden = sorted(requested & prohibited)
    if overridden:
        raise ValueError(
            "prohibited capabilities override requested capabilities: " + ", ".join(overridden)
        )
    expanded: set[str] = set()
    stack = list(requested)
    while stack:
        current = stack.pop()
        if current in expanded:
            continue
        if current in prohibited:
            raise ValueError(f"prohibited capability required by expansion: {current}")
        definition = caps.get(current)
        expanded.add(current)
        for dependency in definition.dependencies:
            if dependency not in expanded:
                stack.append(dependency)
    for capability_id in expanded:
        conflicts = caps.get(capability_id).conflicts & expanded
        if conflicts:
            raise ValueError(
                f"capability {capability_id} conflicts with {', '.join(sorted(conflicts))}"
            )
    return frozenset(expanded)


READ_ONLY_CAPABILITIES = frozenset(
    {
        "repository.read",
        "repository.search",
        "repository.summarise",
        "filesystem.read",
        "structured_output",
        "streaming_output",
        "workspace.contained",
        "process_tree.cancel",
    }
)

MUTATING_CAPABILITIES = frozenset(
    {
        "repository.write",
        "repository.patch",
        "filesystem.write",
        "shell.execute",
        "git.commit",
        "git.push",
        "dependency.install",
        "test.execute",
        "build.execute",
        "network.write",
    }
)
