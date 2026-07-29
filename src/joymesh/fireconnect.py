"""FireConnect provider bridge discovery and configuration."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from joymesh.models import FireConnectStatus, FireConnectTarget
from joymesh.security import filter_environment, redact_secrets

FIRECONNECT_TARGETS = frozenset(
    {"claude", "codex", "opencode", "pi", "cursor", "vscode", "deepagents"}
)
JOYMESH_RUNNABLE_TARGETS = frozenset({"codex", "opencode"})


class FireConnectError(RuntimeError):
    pass


class FireConnectClient:
    def __init__(self, executable: str | None = None) -> None:
        self._configured_executable = executable

    def executable(self) -> str | None:
        if self._configured_executable:
            return self._configured_executable
        if discovered := shutil.which("fireconnect"):
            return discovered
        fallback = Path.home() / ".local" / "bin" / "fireconnect"
        return str(fallback) if fallback.is_file() else None

    async def status(self) -> FireConnectStatus:
        executable = self.executable()
        if executable is None:
            return FireConnectStatus(
                available=False,
                detail="FireConnect is not installed on this machine",
            )

        try:
            global_status = await self._json(executable, "status", "--json")
        except FireConnectError as exc:
            return FireConnectStatus(available=False, detail=str(exc))

        raw_targets = global_status.get("perHarness", [])
        model_results = await asyncio.gather(
            *(self._target_model(executable, str(item.get("id", ""))) for item in raw_targets)
        )
        targets = tuple(
            FireConnectTarget(
                id=str(item.get("id", "")),
                enabled=bool(item.get("enabled", False)),
                model=model,
                reads_from=_optional_string(item.get("readsFrom")),
                storage=_optional_string(item.get("storage")),
                joymesh_runnable=str(item.get("id", "")) in JOYMESH_RUNNABLE_TARGETS,
            )
            for item, model in zip(raw_targets, model_results, strict=True)
            if item.get("id")
        )
        auth = global_status.get("auth") or {}
        environment = global_status.get("environment") or {}
        return FireConnectStatus(
            available=True,
            signed_in=bool(auth.get("signedIn", False)),
            version=_optional_string(environment.get("cliVersion")),
            backend=_optional_string(global_status.get("backendLabel")),
            detail=_optional_string(auth.get("reason")),
            targets=targets,
        )

    async def connect(self, harness_id: str, model: str) -> FireConnectStatus:
        self._validate_target(harness_id)
        executable = self._require_executable()
        await self._run(executable, harness_id, "on", "--model", model)
        return await self.status()

    async def disconnect(self, harness_id: str) -> FireConnectStatus:
        self._validate_target(harness_id)
        executable = self._require_executable()
        await self._run(executable, harness_id, "off")
        return await self.status()

    async def _target_model(self, executable: str, harness_id: str) -> str | None:
        if harness_id not in FIRECONNECT_TARGETS:
            return None
        try:
            status = await self._json(executable, harness_id, "status", "--json")
        except FireConnectError:
            return None
        current = status.get("current") or {}
        return _optional_string(current.get("main") or status.get("model"))

    def _require_executable(self) -> str:
        executable = self.executable()
        if executable is None:
            raise FireConnectError("FireConnect is not installed on this machine")
        return executable

    @staticmethod
    def _validate_target(harness_id: str) -> None:
        if harness_id not in FIRECONNECT_TARGETS:
            raise FireConnectError(f"unsupported FireConnect harness: {harness_id}")

    async def _json(self, executable: str, *args: str) -> dict[str, Any]:
        output = await self._run(executable, *args)
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise FireConnectError("FireConnect returned invalid status data") from exc
        if not isinstance(value, dict):
            raise FireConnectError("FireConnect returned invalid status data")
        return value

    @staticmethod
    async def _run(executable: str, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode(errors="replace").strip()
        error = stderr.decode(errors="replace").strip()
        if process.returncode != 0:
            detail = redact_secrets(error or output or f"exit status {process.returncode}")
            raise FireConnectError(detail)
        return output


def _optional_string(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None
