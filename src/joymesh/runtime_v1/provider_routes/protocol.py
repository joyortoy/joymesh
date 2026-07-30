"""Connector-neutral provider-route types and manager protocol.

Provider routes are distinct from coding-harness connectors:

* ``connector_id`` — which harness executes the task
* ``provider_id`` — which model provider serves inference (``native`` / ``fireworks``)
* ``manager_id`` — which tool configures the route (``fireconnect``), or None for native
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from joymesh.models import utc_now

ProviderAuthStatus = Literal[
    "authenticated",
    "unauthenticated",
    "expired",
    "misconfigured",
    "unavailable",
    "unknown",
]

MutationAction = Literal["enable", "disable", "set_model"]


@dataclass(frozen=True)
class ProviderRoute:
    """Snapshot of a connector ↔ provider binding."""

    route_id: str
    display_name: str
    manager_id: str | None
    connector_id: str
    provider_id: str
    model_id: str | None
    enabled: bool
    available: bool
    authenticated: bool
    configuration_status: str
    credential_source: str | None = None
    supports_enable: bool = False
    supports_disable: bool = False
    supports_status: bool = True
    supports_model_selection: bool = False
    supports_usage_status: bool = False
    last_checked_at: datetime = field(default_factory=utc_now)
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "display_name": self.display_name,
            "manager_id": self.manager_id,
            "connector_id": self.connector_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "enabled": self.enabled,
            "available": self.available,
            "authenticated": self.authenticated,
            "configuration_status": self.configuration_status,
            "credential_source": self.credential_source,
            "supports_enable": self.supports_enable,
            "supports_disable": self.supports_disable,
            "supports_status": self.supports_status,
            "supports_model_selection": self.supports_model_selection,
            "supports_usage_status": self.supports_usage_status,
            "last_checked_at": self.last_checked_at.isoformat(),
            "reason_code": self.reason_code,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProviderRouteManagerDiscovery:
    executable_path: str | None
    version: str | None
    fingerprint: str | None
    installed: bool
    usable: bool
    supported_harnesses: tuple[str, ...]
    status_capability: str
    configuration_locations: tuple[str, ...]
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "executable_path": self.executable_path,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "installed": self.installed,
            "usable": self.usable,
            "supported_harnesses": list(self.supported_harnesses),
            "status_capability": self.status_capability,
            "configuration_locations": list(self.configuration_locations),
            "reason_code": self.reason_code,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProviderRouteAuthEvidence:
    status: ProviderAuthStatus
    detail: str
    signed_in: bool = False
    credential_source: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "signed_in": self.signed_in,
            "credential_source": self.credential_source,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProviderRouteMutationApproval:
    """Explicit approval required before mutating harness provider configuration."""

    approved: bool
    action: MutationAction
    manager_id: str
    connector_id: str
    nonce: str
    model_id: str | None = None


@dataclass(frozen=True)
class ProviderRouteMutationResult:
    ok: bool
    action: MutationAction
    route: ProviderRoute | None
    previous_snapshot: Mapping[str, Any]
    restored: bool = False
    reason_code: str | None = None
    message: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "route": self.route.as_dict() if self.route else None,
            "previous_snapshot": dict(self.previous_snapshot),
            "restored": self.restored,
            "reason_code": self.reason_code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProviderRouteSelectionResult:
    connector_id: str
    connector_candidates: tuple[str, ...]
    selected_connector: str | None
    connector_selection_reason: str
    provider_route_candidates: tuple[ProviderRoute, ...]
    selected_provider_route: ProviderRoute | None
    provider_selection_reason: str
    selected_model: str | None
    rejected_candidates: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "connector_candidates": list(self.connector_candidates),
            "selected_connector": self.selected_connector,
            "connector_selection_reason": self.connector_selection_reason,
            "provider_route_candidates": [
                item.as_dict() for item in self.provider_route_candidates
            ],
            "selected_provider_route": (
                self.selected_provider_route.as_dict() if self.selected_provider_route else None
            ),
            "provider_selection_reason": self.provider_selection_reason,
            "selected_model": self.selected_model,
            "rejected_candidates": [dict(item) for item in self.rejected_candidates],
        }


@runtime_checkable
class ProviderRouteManager(Protocol):
    """Protocol for provider-route configuration managers (not harnesses)."""

    manager_id: str
    display_name: str

    async def discover(self) -> ProviderRouteManagerDiscovery: ...

    async def inspect_auth(self) -> ProviderRouteAuthEvidence: ...

    async def list_supported_connectors(self) -> tuple[str, ...]: ...

    async def inspect_route(self, connector_id: str) -> ProviderRoute: ...

    async def list_routes(self, connector_id: str | None = None) -> tuple[ProviderRoute, ...]: ...

    async def enable_route(
        self,
        connector_id: str,
        *,
        approval: ProviderRouteMutationApproval,
        model_id: str | None = None,
    ) -> ProviderRouteMutationResult:
        """Raw mutation primitive. Callers must hold coordinator mutation authority."""
        ...

    async def disable_route(
        self,
        connector_id: str,
        *,
        approval: ProviderRouteMutationApproval,
    ) -> ProviderRouteMutationResult:
        """Raw mutation primitive. Callers must hold coordinator mutation authority."""
        ...

    async def verify_route(self, connector_id: str) -> ProviderRoute: ...

    def redact_diagnostics(self, text: str) -> str: ...
