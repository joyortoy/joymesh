"""Provider-route managers — configuration layers distinct from connectors."""

from __future__ import annotations

from joymesh.runtime_v1.provider_routes.authority import (
    MutationAuthority,
    MutationAuthorityError,
    current_mutation_authority,
    mutation_authority,
    mutation_authority_for_tests,
    require_mutation_authority,
)
from joymesh.runtime_v1.provider_routes.coordinator import (
    ProviderRouteLifecycleResult,
    ProviderRouteMutationCoordinator,
)
from joymesh.runtime_v1.provider_routes.lease_store import (
    ProviderRouteLease,
    ProviderRouteLeaseError,
    ProviderRouteLeaseStore,
)
from joymesh.runtime_v1.provider_routes.protocol import (
    ProviderRoute,
    ProviderRouteAuthEvidence,
    ProviderRouteManager,
    ProviderRouteManagerDiscovery,
    ProviderRouteMutationApproval,
    ProviderRouteMutationResult,
    ProviderRouteSelectionResult,
)
from joymesh.runtime_v1.provider_routes.registry import (
    builtin_provider_route_managers,
    get_provider_route_manager,
    reset_provider_route_managers_for_tests,
)
from joymesh.runtime_v1.provider_routes.selection import (
    native_route_for,
    select_provider_route,
)
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

__all__ = [
    "MutationAuthority",
    "MutationAuthorityError",
    "ProviderRoute",
    "ProviderRouteAuthEvidence",
    "ProviderRouteLease",
    "ProviderRouteLeaseError",
    "ProviderRouteLeaseStore",
    "ProviderRouteLifecycleResult",
    "ProviderRouteManager",
    "ProviderRouteManagerDiscovery",
    "ProviderRouteMutationApproval",
    "ProviderRouteMutationCoordinator",
    "ProviderRouteMutationResult",
    "ProviderRouteSelectionResult",
    "ProviderRouteService",
    "builtin_provider_route_managers",
    "current_mutation_authority",
    "get_provider_route_manager",
    "mutation_authority",
    "mutation_authority_for_tests",
    "native_route_for",
    "require_mutation_authority",
    "reset_provider_route_managers_for_tests",
    "select_provider_route",
]
