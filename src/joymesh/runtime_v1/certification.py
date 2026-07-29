"""Generic certification profiles; connector adapters supply argv and parsing."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from joymesh.connectors.lifecycle_models import (
    CertificationScope,
    ConnectorExecutionOrigin,
    EvidenceTrustLevel,
)
from joymesh.models import utc_now
from joymesh.runtime_v1.capabilities import READ_ONLY_CAPABILITIES


@dataclass(frozen=True)
class CertificationWorkspace:
    path: Path
    project_name: str
    before_manifest: Mapping[str, object]
    prompt: str
    prompt_digest: str


@dataclass(frozen=True)
class CertificationVerification:
    passed: bool
    reasons: tuple[str, ...]
    after_manifest: Mapping[str, object]
    git_clean: bool
    name_found: bool


class CertificationProfile(Protocol):
    profile_id: str
    profile_revision: str
    required_capabilities: frozenset[str]

    def build_workspace(self, *, task_id: str, root: Path) -> CertificationWorkspace: ...

    def verify_result(
        self,
        workspace: CertificationWorkspace,
        *,
        output: str,
        returncode: int,
    ) -> CertificationVerification: ...

    def produce_scope(self) -> CertificationScope: ...


class ReadOnlyRepositoryProfile:
    profile_id = "read_only_repository"
    profile_revision = "2026-07-29.1"
    required_capabilities = READ_ONLY_CAPABILITIES

    def build_workspace(self, *, task_id: str, root: Path) -> CertificationWorkspace:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = root / task_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(mode=0o700)
        project_name = f"JoyMesh Cursor Certification {secrets.token_hex(3).upper()}"
        readme = workspace / "README.md"
        readme.write_text(f"# {project_name}\n", encoding="utf-8")
        _git(["init", "--quiet", str(workspace)])
        _git(["-C", str(workspace), "add", "README.md"])
        _git(
            [
                "-C",
                str(workspace),
                "-c",
                "user.email=cert@joymesh.local",
                "-c",
                "user.name=JoyMesh Cert",
                "commit",
                "-m",
                "certification baseline",
                "--quiet",
            ]
        )
        prompt = (
            "Read README.md and return the exact project name.\n"
            "Do not modify any files.\n"
            "Do not create files.\n"
            "Do not delete files.\n"
            "Do not access paths outside this repository.\n"
            "Do not run shell commands.\n"
            "Do not install dependencies.\n"
            "Do not use Git commands."
        )
        return CertificationWorkspace(
            path=workspace,
            project_name=project_name,
            before_manifest=workspace_manifest(workspace),
            prompt=prompt,
            prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(),
        )

    def verify_result(
        self,
        workspace: CertificationWorkspace,
        *,
        output: str,
        returncode: int,
    ) -> CertificationVerification:
        after = workspace_manifest(workspace.path)
        git_clean = _git_status_clean(workspace.path)
        name_found = workspace.project_name in output
        reasons: list[str] = []
        if returncode != 0:
            reasons.append(f"process exited with {returncode}")
        if not name_found:
            reasons.append("exact project name missing from output")
        if after["files"] != workspace.before_manifest["files"]:
            reasons.append("file set changed")
        if after["hashes"] != workspace.before_manifest["hashes"]:
            reasons.append("file hashes changed")
        if not git_clean:
            reasons.append("git working tree dirty")
        if after["symlink_escape"]:
            reasons.append("symlink escape detected")
        return CertificationVerification(
            passed=not reasons,
            reasons=tuple(reasons),
            after_manifest=after,
            git_clean=git_clean,
            name_found=name_found,
        )

    def produce_scope(self) -> CertificationScope:
        return CertificationScope(
            profile="cursor_read_only",
            structured_execution=True,
            repository_read=True,
            repository_write=False,
            shell_commands=False,
            session_resume=False,
            network_access=False,
            event_streaming=True,
            workspace_containment=True,
            cancellation=True,
        )

    def cleanup(self, workspace: CertificationWorkspace) -> None:
        shutil.rmtree(workspace.path, ignore_errors=True)


PROFILE_CATALOGUE: dict[str, ReadOnlyRepositoryProfile] = {
    "read_only_repository": ReadOnlyRepositoryProfile(),
    "discovery": ReadOnlyRepositoryProfile(),  # placeholder registry entries
}


def workspace_manifest(workspace: Path) -> dict[str, object]:
    files: dict[str, str] = {}
    symlinks: list[str] = []
    symlink_escape = False
    root = workspace.resolve()
    for path in sorted(workspace.rglob("*")):
        rel = str(path.relative_to(workspace))
        if path.is_symlink():
            symlinks.append(rel)
            try:
                resolved = path.resolve()
                if root not in resolved.parents and resolved != root:
                    symlink_escape = True
            except OSError:
                symlink_escape = True
            continue
        if path.is_file() and ".git" not in path.parts:
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    mode = stat.S_IMODE(workspace.stat().st_mode)
    return {
        "files": files,
        "hashes": dict(files),
        "symlinks": symlinks,
        "symlink_escape": symlink_escape,
        "mode": oct(mode),
    }


def evidence_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(dict(payload), sort_keys=True).encode()).hexdigest()


def node_attested_meta(
    *,
    origin: ConnectorExecutionOrigin = ConnectorExecutionOrigin.REMOTE_NODE,
    trust: EvidenceTrustLevel = EvidenceTrustLevel.NODE_ATTESTED,
) -> dict[str, str]:
    return {
        "execution_origin": origin.value,
        "trust_level": trust.value,
        "certified_at": utc_now().isoformat(),
        "evidence_id": str(uuid4()),
    }


def _git(argv: Sequence[str]) -> None:
    import subprocess

    completed = subprocess.run(["git", *argv], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "git command failed")


def _git_status_clean(workspace: Path) -> bool:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == ""
