"""Repository safety checks before coding-worker edits."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepositorySafetyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RepositorySafetyReport:
    path: str
    exists: bool
    is_git: bool
    branch: str | None
    dirty: bool
    dirty_files: tuple[str, ...]
    expected_branch: str | None
    branch_ok: bool


def assert_path_inside_repository(repository: Path, candidate: Path) -> Path:
    root = repository.resolve(strict=True)
    target = candidate.expanduser()
    try:
        resolved = target.resolve(strict=False)
    except OSError as exc:
        raise RepositorySafetyError("path_unresolvable", str(exc)) from exc
    if ".." in Path(candidate).parts:
        raise RepositorySafetyError("path_traversal", "path traversal rejected")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RepositorySafetyError(
            "path_escape", "edit path escapes allowed repository"
        ) from exc
    return resolved


def inspect_repository(
    path: str,
    *,
    expected_branch: str | None = None,
    expected_worktree: str | None = None,
) -> RepositorySafetyReport:
    root = Path(path).expanduser()
    if expected_worktree:
        worktree = Path(expected_worktree).expanduser()
        if root.resolve(strict=False) != worktree.resolve(strict=False):
            raise RepositorySafetyError(
                "worktree_mismatch",
                "repository path does not match expected worktree",
            )
    if not root.exists():
        raise RepositorySafetyError("repository_missing", f"repository missing: {path}")
    if not root.is_dir():
        raise RepositorySafetyError("repository_not_dir", f"not a directory: {path}")
    resolved = root.resolve(strict=True)
    is_git = (resolved / ".git").exists()
    branch: str | None = None
    dirty = False
    dirty_files: tuple[str, ...] = ()
    if is_git:
        branch = _git_stdout(resolved, ["rev-parse", "--abbrev-ref", "HEAD"])
        status = _git_stdout(resolved, ["status", "--porcelain"])
        lines = tuple(line[3:] for line in status.splitlines() if line.strip())
        dirty_files = lines
        dirty = bool(lines)
    branch_ok = expected_branch is None or branch == expected_branch
    if expected_branch is not None and not branch_ok:
        raise RepositorySafetyError(
            "branch_mismatch",
            f"expected branch {expected_branch}, found {branch}",
        )
    return RepositorySafetyReport(
        path=str(resolved),
        exists=True,
        is_git=is_git,
        branch=branch,
        dirty=dirty,
        dirty_files=dirty_files,
        expected_branch=expected_branch,
        branch_ok=branch_ok,
    )


def list_changed_files(path: str, *, before: tuple[str, ...] = ()) -> tuple[str, ...]:
    root = Path(path).resolve(strict=True)
    if not (root / ".git").exists():
        return ()
    status = _git_stdout(root, ["status", "--porcelain"])
    after = tuple(line[3:] for line in status.splitlines() if line.strip())
    before_set = set(before)
    return tuple(item for item in after if item not in before_set)


def _git_stdout(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()
