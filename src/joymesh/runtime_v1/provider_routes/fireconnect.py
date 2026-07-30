"""FireConnect provider-route manager (configuration layer, not a harness).

CLI contract observed on FireConnect v0.9.0:

* ``fireconnect status --json`` — sign-in, key storage, per-harness enablement
* ``fireconnect <harness> status --json`` — provider / model / auth flags
* ``fireconnect <harness> on [--model <id>]`` — enable Fireworks routing
* ``fireconnect <harness> off`` — restore previous harness configuration
* No headless repository execution command exists
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from joymesh.connectors.process_utils import executable_fingerprint
from joymesh.runtime_v1.provider_routes.protocol import (
    ProviderRoute,
    ProviderRouteAuthEvidence,
    ProviderRouteManagerDiscovery,
    ProviderRouteMutationApproval,
    ProviderRouteMutationResult,
)
from joymesh.runtime_v1.provider_routes.selection import native_route_for
from joymesh.security import filter_environment, redact_secrets

# FireConnect harness id → JoyMesh runtime connector_id
_HARNESS_TO_CONNECTOR: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "cursor": "cursor",
}
_CONNECTOR_TO_HARNESS = {value: key for key, value in _HARNESS_TO_CONNECTOR.items()}

# Supported by FireConnect but no JoyMesh ConnectorRuntime yet.
_UNSUPPORTED_JOYMESH_HARNESSES = frozenset({"pi", "vscode", "deepagents"})

_ALL_FIRECONNECT_HARNESSES = frozenset(_HARNESS_TO_CONNECTOR) | _UNSUPPORTED_JOYMESH_HARNESSES

_SECRET_EXTRA = re.compile(
    r"(?i)(fireworks[_-]?api[_-]?key|xai[_-]?api[_-]?key|anthropic[_-]?api[_-]?key)"
    r"\s*[=:]\s*\S+"
)


class FireConnectProviderRouteManager:
    """Provider-route manager backed by the FireConnect CLI."""

    manager_id = "fireconnect"
    display_name = "FireConnect"

    def __init__(self, executable: str | None = None) -> None:
        self._configured_executable = executable

    def redact_diagnostics(self, text: str) -> str:
        redacted = redact_secrets(text)
        return _SECRET_EXTRA.sub(r"\1=[REDACTED]", redacted)[:2000]

    async def discover(self) -> ProviderRouteManagerDiscovery:
        executable = self._resolve_executable()
        if not executable:
            return ProviderRouteManagerDiscovery(
                executable_path=None,
                version=None,
                fingerprint=None,
                installed=False,
                usable=False,
                supported_harnesses=(),
                status_capability="none",
                configuration_locations=(),
                reason_code="manager_not_installed",
                details={"detail": "fireconnect not found"},
            )
        version, broken = await self._probe_version(executable)
        if broken:
            return ProviderRouteManagerDiscovery(
                executable_path=executable,
                version=None,
                fingerprint=executable_fingerprint(executable),
                installed=True,
                usable=False,
                supported_harnesses=(),
                status_capability="none",
                configuration_locations=self._config_locations(),
                reason_code="broken_executable",
                details={"detail": broken},
            )
        if version is None:
            return ProviderRouteManagerDiscovery(
                executable_path=executable,
                version=None,
                fingerprint=executable_fingerprint(executable),
                installed=True,
                usable=False,
                supported_harnesses=(),
                status_capability="none",
                configuration_locations=self._config_locations(),
                reason_code="version_unreadable",
                details={"detail": "unable to parse fireconnect version"},
            )
        harnesses = tuple(sorted(_HARNESS_TO_CONNECTOR.values()))
        return ProviderRouteManagerDiscovery(
            executable_path=executable,
            version=version,
            fingerprint=executable_fingerprint(executable),
            installed=True,
            usable=True,
            supported_harnesses=harnesses,
            status_capability="json",
            configuration_locations=self._config_locations(),
            details={
                "fireconnect_only_harnesses": sorted(_UNSUPPORTED_JOYMESH_HARNESSES),
                "role": "provider_configuration_manager",
            },
        )

    async def inspect_auth(self) -> ProviderRouteAuthEvidence:
        executable = self._resolve_executable()
        if not executable:
            return ProviderRouteAuthEvidence(
                status="unavailable",
                detail="FireConnect is not installed",
                signed_in=False,
            )
        try:
            payload = await self._json(executable, "status", "--json")
        except FireConnectManagerError as exc:
            return ProviderRouteAuthEvidence(
                status="misconfigured",
                detail=self.redact_diagnostics(str(exc)),
                signed_in=False,
                details={"reason_code": "malformed_status"},
            )
        auth_obj = payload.get("auth")
        auth: dict[str, Any] = auth_obj if isinstance(auth_obj, dict) else {}
        signed_in = bool(auth.get("signedIn"))
        backend = _optional_str(payload.get("backendLabel") or payload.get("backend"))
        source = _optional_str(payload.get("activeKeySource"))
        if signed_in:
            status: str = "authenticated"
            detail = "FireConnect reports a signed-in Fireworks session"
        else:
            status = "unauthenticated"
            detail = self.redact_diagnostics(str(auth.get("reason") or "not signed in"))
        return ProviderRouteAuthEvidence(
            status=status,  # type: ignore[arg-type]
            detail=detail,
            signed_in=signed_in,
            credential_source=source or backend,
            details={
                "key_stored": bool(payload.get("keychainPresent")),
                "env_key_present": bool(payload.get("envPresent")),
                "backend": backend,
                # Never include email/account identifiers in details.
            },
        )

    async def list_supported_connectors(self) -> tuple[str, ...]:
        return tuple(sorted(_HARNESS_TO_CONNECTOR.values()))

    async def inspect_route(self, connector_id: str) -> ProviderRoute:
        harness = _CONNECTOR_TO_HARNESS.get(connector_id)
        if harness is None:
            if connector_id in _UNSUPPORTED_JOYMESH_HARNESSES:
                return ProviderRoute(
                    route_id=f"{connector_id}:fireworks",
                    display_name=f"{connector_id} via Fireworks (FireConnect)",
                    manager_id=self.manager_id,
                    connector_id=connector_id,
                    provider_id="fireworks",
                    model_id=None,
                    enabled=False,
                    available=False,
                    authenticated=False,
                    configuration_status="unsupported",
                    reason_code="route_not_supported",
                    details={"detail": "no JoyMesh ConnectorRuntime for this harness"},
                )
            return ProviderRoute(
                route_id=f"{connector_id}:fireworks",
                display_name=f"{connector_id} via Fireworks",
                manager_id=self.manager_id,
                connector_id=connector_id,
                provider_id="fireworks",
                model_id=None,
                enabled=False,
                available=False,
                authenticated=False,
                configuration_status="unsupported",
                reason_code="route_not_supported",
                details={"detail": f"FireConnect does not route connector {connector_id!r}"},
            )

        executable = self._resolve_executable()
        if not executable:
            return ProviderRoute(
                route_id=f"{connector_id}:fireworks",
                display_name=f"{connector_id} via Fireworks (FireConnect)",
                manager_id=self.manager_id,
                connector_id=connector_id,
                provider_id="fireworks",
                model_id=None,
                enabled=False,
                available=False,
                authenticated=False,
                configuration_status="unavailable",
                reason_code="manager_not_installed",
            )

        auth = await self.inspect_auth()
        try:
            payload = await self._json(executable, harness, "status", "--json")
        except FireConnectManagerError as exc:
            return ProviderRoute(
                route_id=f"{connector_id}:fireworks",
                display_name=f"{connector_id} via Fireworks (FireConnect)",
                manager_id=self.manager_id,
                connector_id=connector_id,
                provider_id="fireworks",
                model_id=None,
                enabled=False,
                available=auth.status == "authenticated",
                authenticated=False,
                configuration_status="invalid",
                reason_code="configuration_invalid",
                details={"detail": self.redact_diagnostics(str(exc))},
            )

        enabled = _harness_enabled(payload, global_enabled=await self._global_enabled(harness))
        model_id = _model_from_status(payload)
        provider = str(payload.get("provider") or "").lower()
        fireworks_active = enabled or provider in {"fireworks", "fireworks-ai", "firerouter"}
        has_token = bool(payload.get("hasAuthToken"))
        authenticated = auth.signed_in and (has_token or fireworks_active or enabled)
        config_status = "valid" if fireworks_active or not enabled else "native_default"
        if enabled and not auth.signed_in:
            config_status = "authentication_required"
        return ProviderRoute(
            route_id=f"{connector_id}:fireworks",
            display_name=f"{connector_id} via Fireworks (FireConnect)",
            manager_id=self.manager_id,
            connector_id=connector_id,
            provider_id="fireworks",
            model_id=model_id,
            enabled=bool(enabled or fireworks_active),
            available=auth.status == "authenticated",
            authenticated=bool(authenticated),
            configuration_status=config_status,
            credential_source=auth.credential_source,
            supports_enable=True,
            supports_disable=True,
            supports_status=True,
            supports_model_selection=True,
            supports_usage_status=harness == "claude",
            reason_code=None if auth.signed_in else "authentication_required",
            details={
                "harness_id": harness,
                "provider_reported": _optional_str(payload.get("provider")),
                "has_auth_token": has_token,
                "usage_status": "unknown",
                "quota_remaining": "unavailable",
            },
        )

    async def list_routes(self, connector_id: str | None = None) -> tuple[ProviderRoute, ...]:
        connectors = (
            (connector_id,) if connector_id else tuple(sorted(_HARNESS_TO_CONNECTOR.values()))
        )
        routes: list[ProviderRoute] = []
        for cid in connectors:
            routes.append(native_route_for(cid))
            routes.append(await self.inspect_route(cid))
        return tuple(routes)

    async def enable_route(
        self,
        connector_id: str,
        *,
        approval: ProviderRouteMutationApproval,
        model_id: str | None = None,
    ) -> ProviderRouteMutationResult:
        """Raw enable primitive — requires active coordinator mutation authority."""

        from joymesh.runtime_v1.provider_routes.authority import require_mutation_authority

        require_mutation_authority(manager_id=self.manager_id, connector_id=connector_id)
        self._require_approval(approval, action="enable", connector_id=connector_id)
        harness = self._require_harness(connector_id)
        executable = self._require_executable()
        previous = await self._snapshot(connector_id)
        argv: list[str] = [harness, "on"]
        chosen = model_id or approval.model_id
        if chosen:
            argv.extend(["--model", chosen])
        try:
            await self._run(executable, *argv)
        except FireConnectManagerError as exc:
            return ProviderRouteMutationResult(
                ok=False,
                action="enable",
                route=await self.inspect_route(connector_id),
                previous_snapshot=previous,
                reason_code="mutation_failed",
                message=self.redact_diagnostics(str(exc)),
            )
        route = await self.verify_route(connector_id)
        return ProviderRouteMutationResult(
            ok=route.enabled,
            action="enable",
            route=route,
            previous_snapshot=previous,
            reason_code=None if route.enabled else "configuration_invalid",
            message="Fireworks route enabled" if route.enabled else "enable did not activate route",
        )

    async def disable_route(
        self,
        connector_id: str,
        *,
        approval: ProviderRouteMutationApproval,
    ) -> ProviderRouteMutationResult:
        """Raw disable primitive — requires active coordinator mutation authority."""

        from joymesh.runtime_v1.provider_routes.authority import require_mutation_authority

        require_mutation_authority(manager_id=self.manager_id, connector_id=connector_id)
        self._require_approval(approval, action="disable", connector_id=connector_id)
        harness = self._require_harness(connector_id)
        executable = self._require_executable()
        previous = await self._snapshot(connector_id)
        try:
            await self._run(executable, harness, "off")
        except FireConnectManagerError as exc:
            return ProviderRouteMutationResult(
                ok=False,
                action="disable",
                route=await self.inspect_route(connector_id),
                previous_snapshot=previous,
                reason_code="mutation_failed",
                message=self.redact_diagnostics(str(exc)),
            )
        route = await self.verify_route(connector_id)
        restored = not route.enabled
        return ProviderRouteMutationResult(
            ok=restored,
            action="disable",
            route=route,
            previous_snapshot=previous,
            restored=restored,
            reason_code=None if restored else "restore_incomplete",
            message="previous configuration restored" if restored else "disable incomplete",
        )

    async def verify_route(self, connector_id: str) -> ProviderRoute:
        return await self.inspect_route(connector_id)

    def _resolve_executable(self) -> str | None:
        if self._configured_executable:
            return (
                self._configured_executable if Path(self._configured_executable).is_file() else None
            )
        if discovered := shutil.which("fireconnect"):
            return discovered
        fallback = Path.home() / ".local" / "bin" / "fireconnect"
        return str(fallback) if fallback.is_file() else None

    def _require_executable(self) -> str:
        executable = self._resolve_executable()
        if not executable:
            raise FireConnectManagerError("FireConnect is not installed")
        return executable

    @staticmethod
    def _require_harness(connector_id: str) -> str:
        harness = _CONNECTOR_TO_HARNESS.get(connector_id)
        if harness is None:
            raise FireConnectManagerError(f"route_not_supported for connector {connector_id!r}")
        return harness

    def _require_approval(
        self,
        approval: ProviderRouteMutationApproval,
        *,
        action: str,
        connector_id: str,
    ) -> None:
        if (
            not approval.approved
            or approval.action != action
            or approval.manager_id != self.manager_id
            or approval.connector_id != connector_id
            or not approval.nonce
        ):
            raise FireConnectManagerError("explicit provider-route mutation approval is required")
        # Automatic route switching remains disabled unless env explicitly set
        # for operator tooling; CLI still requires --approve + nonce.
        if os.environ.get("JOYMESH_ALLOW_PROVIDER_ROUTE_MUTATION") == "0":
            raise FireConnectManagerError("provider-route mutations are disabled by policy")

    async def _snapshot(self, connector_id: str) -> dict[str, Any]:
        route = await self.inspect_route(connector_id)
        return {
            "connector_id": connector_id,
            "provider_id": route.provider_id,
            "enabled": route.enabled,
            "model_id": route.model_id,
            "configuration_status": route.configuration_status,
            # Never include secrets.
        }

    async def _global_enabled(self, harness: str) -> bool | None:
        executable = self._resolve_executable()
        if not executable:
            return None
        try:
            payload = await self._json(executable, "status", "--json")
        except FireConnectManagerError:
            return None
        for item in payload.get("perHarness") or []:
            if isinstance(item, dict) and item.get("id") == harness:
                return bool(item.get("enabled"))
        return None

    async def _probe_version(self, executable: str) -> tuple[str | None, str | None]:
        try:
            raw = await self._run(executable, "--version", "--json")
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("version"):
                return str(data["version"]), None
        except (FireConnectManagerError, json.JSONDecodeError):
            pass
        try:
            text = await self._run(executable, "--version")
        except FireConnectManagerError as exc:
            return None, self.redact_diagnostics(str(exc))
        match = re.search(r"v?(\d+\.\d+\.\d+)", text)
        if match:
            return match.group(1), None
        stripped = text.strip()
        if stripped:
            return stripped[:80], None
        return None, "empty version output"

    @staticmethod
    def _config_locations() -> tuple[str, ...]:
        home = Path.home()
        return (
            str(home / ".fireconnect"),
            str(home / ".local" / "bin" / "fireconnect"),
        )

    async def _json(self, executable: str, *args: str) -> dict[str, Any]:
        output = await self._run(executable, *args)
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise FireConnectManagerError("FireConnect returned invalid status data") from exc
        if not isinstance(value, dict):
            raise FireConnectManagerError("FireConnect returned invalid status data")
        return value

    async def _run(self, executable: str, *args: str) -> str:
        env = filter_environment(extra_keys=frozenset({"HOME", "USER", "LOGNAME"}))
        # Preserve test doubles; never inject secret-bearing keys.
        for key, value in os.environ.items():
            if key.startswith("JOYMESH_FAKE_"):
                env[key] = value
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode(errors="replace").strip()
        error = stderr.decode(errors="replace").strip()
        if process.returncode != 0:
            detail = self.redact_diagnostics(error or output or f"exit {process.returncode}")
            raise FireConnectManagerError(detail)
        return output


class FireConnectManagerError(RuntimeError):
    pass


def _optional_str(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None


def _model_from_status(payload: Mapping[str, Any]) -> str | None:
    current = payload.get("current")
    if isinstance(current, dict):
        for key in ("main", "model", "default"):
            if current.get(key):
                return str(current[key])
    for key in ("model", "main"):
        if payload.get(key):
            return str(payload[key])
    defaults = payload.get("defaults")
    if isinstance(defaults, dict) and defaults.get("main"):
        # Prefer current over defaults for "exact configured"; defaults alone
        # are reported only when current is unset and route is enabled.
        return None
    return None


def _harness_enabled(payload: Mapping[str, Any], *, global_enabled: bool | None) -> bool:
    if global_enabled is not None:
        return global_enabled
    # Some status payloads omit explicit enabled; infer from provider.
    provider = str(payload.get("provider") or "").lower()
    if provider in {"fireworks", "fireworks-ai", "firerouter"}:
        return True
    if provider in {"default", "none", ""}:
        return False
    return bool(payload.get("enabled"))


def make_approval(
    *,
    action: str,
    connector_id: str,
    model_id: str | None = None,
    nonce: str | None = None,
) -> ProviderRouteMutationApproval:
    """Helper for CLI/tests to build an explicit mutation approval."""

    from typing import cast

    from joymesh.runtime_v1.provider_routes.protocol import MutationAction

    digest = (
        nonce
        or hashlib.sha256(
            f"fireconnect:{action}:{connector_id}:{model_id or ''}".encode()
        ).hexdigest()[:16]
    )
    return ProviderRouteMutationApproval(
        approved=True,
        action=cast(MutationAction, action),
        manager_id="fireconnect",
        connector_id=connector_id,
        nonce=digest,
        model_id=model_id,
    )


# Re-export mapping for tests / docs.
FIRECONNECT_CONNECTOR_IDS: Sequence[str] = tuple(sorted(_HARNESS_TO_CONNECTOR.values()))
