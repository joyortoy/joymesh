"""Deterministic, policy-controlled executable discovery."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from joymesh.harnesses.contracts import (
    DiscoveryEvidence,
    DiscoveryResult,
    HarnessDefinition,
    HarnessInstallation,
    InstallSource,
)
from joymesh.security import filter_environment


class DiscoveryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    execute_version_commands: bool = False
    version_timeout_seconds: float = 3


class HarnessDiscovery:
    def __init__(self, definitions: tuple[HarnessDefinition, ...]) -> None:
        self._definitions = {definition.id: definition for definition in definitions}
        self._cache: dict[tuple[object, ...], DiscoveryResult] = {}
        self._lock = asyncio.Lock()

    def invalidate(self, harness_id: str | None = None) -> None:
        if harness_id is None:
            self._cache.clear()
            return
        self._cache = {
            key: value for key, value in self._cache.items() if value.harness_id != harness_id
        }

    async def discover(
        self,
        harness_id: str,
        *,
        environment: Mapping[str, str] | None = None,
        overrides: Mapping[str, str] | None = None,
        policy: DiscoveryPolicy | None = None,
    ) -> DiscoveryResult:
        definition = self._definitions[harness_id]
        selected_policy = policy or DiscoveryPolicy()
        selected_environment = dict(environment or os.environ)
        selected_overrides = dict(overrides or {})
        key = (
            harness_id,
            selected_environment.get("PATH", ""),
            tuple(sorted(selected_overrides.items())),
            selected_policy.execute_version_commands,
        )
        async with self._lock:
            if cached := self._cache.get(key):
                return cached
            result = await self._discover(
                definition,
                selected_environment,
                selected_overrides,
                selected_policy,
            )
            self._cache[key] = result
            return result

    async def discover_all(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        overrides: Mapping[str, str] | None = None,
        policy: DiscoveryPolicy | None = None,
    ) -> tuple[DiscoveryResult, ...]:
        return tuple(
            [
                await self.discover(
                    harness_id,
                    environment=environment,
                    overrides=overrides,
                    policy=policy,
                )
                for harness_id in sorted(self._definitions)
            ]
        )

    async def _discover(
        self,
        definition: HarnessDefinition,
        environment: Mapping[str, str],
        overrides: Mapping[str, str],
        policy: DiscoveryPolicy,
    ) -> DiscoveryResult:
        if definition.id == "fake":
            return DiscoveryResult(harness_id="fake", installations=())

        candidates: list[tuple[Path, InstallSource, str]] = []
        if override := overrides.get(definition.id):
            candidates.append(
                (
                    _expanded_path(override),
                    InstallSource.OVERRIDE,
                    "caller override",
                )
            )

        path_entries = [
            Path(item) for item in environment.get("PATH", "").split(os.pathsep) if item
        ]
        standard_entries = _standard_paths()
        for executable in definition.executables:
            for directory in [*path_entries, *standard_entries]:
                candidates.append(
                    (
                        directory / executable,
                        _source_for_path(directory),
                        "PATH entry" if directory in path_entries else "standard executable path",
                    )
                )

        seen: set[Path] = set()
        installations: list[HarnessInstallation] = []
        for precedence, (candidate, source, reason) in enumerate(candidates):
            try:
                resolved = candidate.resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if resolved in seen or not resolved.is_file() or not os.access(resolved, os.X_OK):
                continue
            seen.add(resolved)
            version = (
                await self._version(definition, resolved, environment, policy)
                if policy.execute_version_commands
                else None
            )
            evidence = DiscoveryEvidence(
                source=source,
                candidate=str(resolved),
                reason=reason,
                precedence=precedence,
            )
            installations.append(
                HarnessInstallation(
                    harness_id=definition.id,
                    executable=str(resolved),
                    version=version,
                    source=source,
                    evidence=(evidence,),
                )
            )
        return DiscoveryResult(
            harness_id=definition.id,
            installations=tuple(installations),
            unavailable_reason=None if installations else "executable_not_found",
        )

    @staticmethod
    async def _version(
        definition: HarnessDefinition,
        executable: Path,
        environment: Mapping[str, str],
        policy: DiscoveryPolicy,
    ) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *definition.version_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=filter_environment(environment),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=policy.version_timeout_seconds,
            )
        except (OSError, TimeoutError):
            return None
        if process.returncode != 0:
            return None
        first_line = (stdout or stderr).decode(errors="replace").strip().splitlines()
        if not first_line:
            return None
        value = first_line[0][:300]
        if definition.version_pattern:
            match = re.search(definition.version_pattern, value)
            return match.group(1) if match else None
        return value


def _standard_paths() -> tuple[Path, ...]:
    home = Path.home()
    paths = [
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        home / ".local" / "bin",
        home / ".npm-global" / "bin",
        home / ".cargo" / "bin",
        home / ".local" / "pipx" / "venvs",
    ]
    if sys.platform == "win32":
        paths.extend(
            Path(item) for name in ("LOCALAPPDATA", "APPDATA") if (item := os.environ.get(name))
        )
    return tuple(paths)


def _expanded_path(value: str) -> Path:
    return Path(os.path.expanduser(value))


def _source_for_path(path: Path) -> InstallSource:
    lowered = str(path).lower()
    if "homebrew" in lowered or lowered.startswith("/opt/homebrew"):
        return InstallSource.HOMEBREW
    if "npm" in lowered or "node" in lowered:
        return InstallSource.NPM
    if "pipx" in lowered:
        return InstallSource.PIPX
    if "/uv/" in lowered:
        return InstallSource.UV
    if ".cargo" in lowered:
        return InstallSource.CARGO
    return InstallSource.PATH
