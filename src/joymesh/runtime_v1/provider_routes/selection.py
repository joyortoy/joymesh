"""Deterministic two-stage provider-route selection (connector-neutral)."""

from __future__ import annotations

from collections.abc import Sequence

from joymesh.runtime_v1.provider_routes.protocol import (
    ProviderRoute,
    ProviderRouteSelectionResult,
)


def select_provider_route(
    *,
    connector_id: str,
    routes: Sequence[ProviderRoute],
    preferred_providers: Sequence[str] = (),
    required_provider: str | None = None,
    allowed_providers: Sequence[str] | None = None,
    allow_disabled: bool = False,
) -> ProviderRouteSelectionResult:
    """Choose a provider route for an already-selected connector.

    Never enables or disables routes. Only already-discovered snapshots are
    considered. Tie-break is deterministic: preference index, then provider_id,
    then route_id.
    """

    rejected: list[dict[str, object]] = []
    eligible: list[ProviderRoute] = []
    for route in routes:
        if route.connector_id != connector_id:
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": "connector_mismatch",
                }
            )
            continue
        if allowed_providers is not None and route.provider_id not in allowed_providers:
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": "provider_not_allowed",
                }
            )
            continue
        if required_provider and route.provider_id != required_provider:
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": "required_provider_mismatch",
                }
            )
            continue
        if not route.available:
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": route.reason_code or "provider_unavailable",
                }
            )
            continue
        if not allow_disabled and route.provider_id != "native" and not route.enabled:
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": "route_not_enabled",
                }
            )
            continue
        if route.provider_id != "native" and not route.authenticated and route.enabled:
            # Enabled but manager reports unauthenticated — exclude.
            rejected.append(
                {
                    "route_id": route.route_id,
                    "reason_code": "authentication_required",
                }
            )
            continue
        eligible.append(route)

    if not eligible:
        return ProviderRouteSelectionResult(
            connector_id=connector_id,
            connector_candidates=(connector_id,),
            selected_connector=connector_id,
            connector_selection_reason="provided",
            provider_route_candidates=tuple(routes),
            selected_provider_route=None,
            provider_selection_reason="no_eligible_provider_route",
            selected_model=None,
            rejected_candidates=tuple(rejected),
        )

    def sort_key(route: ProviderRoute) -> tuple[float, str, str]:
        if preferred_providers and route.provider_id in preferred_providers:
            preference = float(preferred_providers.index(route.provider_id))
        elif preferred_providers:
            preference = 100.0
        else:
            # Prefer native when no preference (stable default; no silent Fireworks).
            preference = 0.0 if route.provider_id == "native" else 10.0
        return (preference, route.provider_id, route.route_id)

    selected = sorted(eligible, key=sort_key)[0]
    reason = (
        "preferred_provider"
        if (preferred_providers and selected.provider_id in preferred_providers)
        else ("required_provider" if required_provider else "deterministic_default")
    )
    return ProviderRouteSelectionResult(
        connector_id=connector_id,
        connector_candidates=(connector_id,),
        selected_connector=connector_id,
        connector_selection_reason="provided",
        provider_route_candidates=tuple(routes),
        selected_provider_route=selected,
        provider_selection_reason=reason,
        selected_model=selected.model_id,
        rejected_candidates=tuple(rejected),
    )


def native_route_for(connector_id: str, *, authenticated: bool = True) -> ProviderRoute:
    """Synthesise the always-present native provider route for a connector."""

    return ProviderRoute(
        route_id=f"{connector_id}:native",
        display_name=f"{connector_id} native provider",
        manager_id=None,
        connector_id=connector_id,
        provider_id="native",
        model_id=None,
        enabled=True,
        available=True,
        authenticated=authenticated,
        configuration_status="valid",
        credential_source="harness_native",
        supports_enable=False,
        supports_disable=False,
        supports_status=True,
        supports_model_selection=False,
        supports_usage_status=False,
        reason_code=None,
        details={"synthetic": True},
    )
