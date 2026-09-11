"""Workspace validation used before launching harness processes."""

from __future__ import annotations

from pathlib import Path


class InvalidWorkspaceError(ValueError):
    pass


def resolve_workspace(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise InvalidWorkspaceError(f"workspace does not exist: {value}")
    if not path.is_dir():
        raise InvalidWorkspaceError(f"workspace is not a directory: {value}")
    return path
