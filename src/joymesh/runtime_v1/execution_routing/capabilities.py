"""Backend and execution capability identifiers (planner consumes these, not backend names)."""

from __future__ import annotations

from enum import StrEnum


class ExecutionCapability(StrEnum):
    INTERNET = "internet"
    GPU = "gpu"
    FILESYSTEM = "filesystem"
    PROVIDER_ROUTING = "provider_routing"
    SANDBOX = "sandbox"
    STREAMING = "streaming"
    PERSISTENT_WORKSPACE = "persistent_workspace"
    EPHEMERAL_WORKSPACE = "ephemeral_workspace"
    VISION = "vision"
    VOICE = "voice"
    BROWSER = "browser"
    MULTI_AGENT = "multi_agent"
    LONG_RUNNING = "long_running"
    REMOTE_WORKER = "remote_worker"
    LOCAL_PROCESS = "local_process"


# Stable harness ids — independent of which backend runs them.
KNOWN_HARNESSES: frozenset[str] = frozenset(
    {"codex", "claude", "opencode", "cursor", "vscode", "grok"}
)
