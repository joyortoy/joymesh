"""Built-in provider-route manager registry."""

from __future__ import annotations

from collections.abc import Mapping

from joymesh.runtime_v1.provider_routes.protocol import ProviderRouteManager

_MANAGERS: dict[str, ProviderRouteManager] | None = None


class ProviderRouteManagerRegistryError(TypeError):
    """Raised when a built-in provider-route manager fails validation."""


def builtin_provider_route_managers() -> dict[str, ProviderRouteManager]:
    """Sole built-in registration point for provider-route managers."""

    global _MANAGERS
    if _MANAGERS is None:
        from joymesh.runtime_v1.provider_routes.fireconnect import (
            FireConnectProviderRouteManager,
        )

        managers: dict[str, ProviderRouteManager] = {
            "fireconnect": FireConnectProviderRouteManager(),
        }
        _MANAGERS = _validate(managers)
    return dict(_MANAGERS)


def get_provider_route_manager(manager_id: str) -> ProviderRouteManager:
    managers = builtin_provider_route_managers()
    try:
        return managers[manager_id]
    except KeyError as exc:
        raise KeyError(f"unknown provider-route manager: {manager_id}") from exc


def reset_provider_route_managers_for_tests() -> None:
    global _MANAGERS
    _MANAGERS = None


def _validate(
    managers: Mapping[str, ProviderRouteManager],
) -> dict[str, ProviderRouteManager]:
    required = (
        "manager_id",
        "display_name",
        "discover",
        "inspect_auth",
        "list_supported_connectors",
        "inspect_route",
        "list_routes",
        "enable_route",
        "disable_route",
        "verify_route",
        "redact_diagnostics",
    )
    validated: dict[str, ProviderRouteManager] = {}
    for key, manager in managers.items():
        for attr in required:
            if not hasattr(manager, attr):
                raise ProviderRouteManagerRegistryError(
                    f"manager {key!r} missing required attribute {attr!r}"
                )
        manager_id = str(getattr(manager, "manager_id", "")).strip()
        display_name = str(getattr(manager, "display_name", "")).strip()
        if not manager_id or not display_name:
            raise ProviderRouteManagerRegistryError(
                f"manager {key!r} requires non-empty manager_id and display_name"
            )
        if key != manager_id:
            raise ProviderRouteManagerRegistryError(
                f"registry key {key!r} does not match manager_id {manager_id!r}"
            )
        if manager_id in validated:
            raise ProviderRouteManagerRegistryError(f"duplicate manager_id: {manager_id!r}")
        validated[manager_id] = manager
    return validated
