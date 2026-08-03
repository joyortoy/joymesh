"""Exact source identity binding for JoyLegal contracts."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@dataclass(frozen=True)
class SourceIdentity:
    producer_system: str
    repository_root: str
    commit: str
    branch: str
    tag: str | None
    dirty: bool
    package_version: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def package_version() -> str:
    try:
        return version("joymesh")
    except PackageNotFoundError:
        return "0.1.0"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def collect_source_identity(
    repo_root: Path,
    *,
    producer_system: str = "joymesh",
    package_version_value: str | None = None,
) -> SourceIdentity:
    root = repo_root.resolve()
    commit = _git(root, "rev-parse", "HEAD") or "unknown"
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    tag = _git(root, "describe", "--tags", "--exact-match") or None
    dirty = bool(_git(root, "status", "--porcelain"))
    return SourceIdentity(
        producer_system=producer_system,
        repository_root=str(root),
        commit=commit,
        branch=branch,
        tag=tag,
        dirty=dirty,
        package_version=package_version_value or package_version(),
    )


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[3]
