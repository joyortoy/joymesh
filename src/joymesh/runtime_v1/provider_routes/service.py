"""Public provider-route mutation service — sole production entry for mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from joymesh.runtime_v1.provider_routes.coordinator import (
    ProviderRouteLifecycleResult,
    ProviderRouteMutationCoordinator,
)
from joymesh.runtime_v1.provider_routes.fireconnect import make_approval
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.protocol import (
    ProviderRouteManager,
    ProviderRouteMutationResult,
)
from joymesh.runtime_v1.provider_routes.registry import get_provider_route_manager

T = TypeVar("T")


class ProviderRouteService:
    """Lease-coordinated mutations for CLI, API, legacy façades, and execution."""

    def __init__(
        self,
        *,
        store: ProviderRouteLeaseStore | None = None,
        coordinator: ProviderRouteMutationCoordinator | None = None,
    ) -> None:
        self.coordinator = coordinator or ProviderRouteMutationCoordinator(store)

    def manager(self, manager_id: str) -> ProviderRouteManager:
        return get_provider_route_manager(manager_id)

    async def enable_permanently(
        self,
        manager_id: str,
        connector_id: str,
        *,
        model_id: str | None = None,
        owner_execution_id: str | None = None,
        acquire_timeout_seconds: float = 30.0,
    ) -> ProviderRouteMutationResult:
        """Permanent enable via ``run_serialised_mutation`` (no auto-restore)."""

        manager = self.manager(manager_id)

        async def mutate() -> ProviderRouteMutationResult:
            return await manager.enable_route(
                connector_id,
                approval=make_approval(
                    action="enable",
                    connector_id=connector_id,
                    model_id=model_id,
                ),
                model_id=model_id,
            )

        return await self.coordinator.run_serialised_mutation(
            manager=manager,
            connector_id=connector_id,
            mutate=mutate,
            owner_execution_id=owner_execution_id,
            acquire_timeout_seconds=acquire_timeout_seconds,
        )

    async def disable_permanently(
        self,
        manager_id: str,
        connector_id: str,
        *,
        owner_execution_id: str | None = None,
        acquire_timeout_seconds: float = 30.0,
    ) -> ProviderRouteMutationResult:
        """Permanent disable via ``run_serialised_mutation`` (no auto-restore)."""

        manager = self.manager(manager_id)

        async def mutate() -> ProviderRouteMutationResult:
            return await manager.disable_route(
                connector_id,
                approval=make_approval(action="disable", connector_id=connector_id),
            )

        return await self.coordinator.run_serialised_mutation(
            manager=manager,
            connector_id=connector_id,
            mutate=mutate,
            owner_execution_id=owner_execution_id,
            acquire_timeout_seconds=acquire_timeout_seconds,
        )

    async def run_temporary(
        self,
        manager_id: str,
        connector_id: str,
        *,
        execute: Callable[[], Awaitable[T]],
        model_id: str | None = None,
        owner_execution_id: str | None = None,
        target_provider_id: str = "fireworks",
        acquire_timeout_seconds: float = 30.0,
        lease_ttl_seconds: float = 120.0,
    ) -> ProviderRouteLifecycleResult:
        """Temporary execution route: enable → execute → restore exact prior state."""

        manager = self.manager(manager_id)
        return await self.coordinator.run_lifecycle(
            manager=manager,
            connector_id=connector_id,
            approval=make_approval(
                action="enable",
                connector_id=connector_id,
                model_id=model_id,
            ),
            execute=execute,
            owner_execution_id=owner_execution_id,
            model_id=model_id,
            target_provider_id=target_provider_id,
            acquire_timeout_seconds=acquire_timeout_seconds,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    async def recover_expired(
        self,
        manager_id: str,
        *,
        connector_ids: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        manager = self.manager(manager_id)
        return await self.coordinator.recover_expired_leases(
            manager,
            connector_ids=connector_ids,
        )

    async def enable_permanently_as_dict(
        self,
        manager_id: str,
        connector_id: str,
        *,
        model_id: str | None = None,
        owner_execution_id: str | None = None,
    ) -> dict[str, object]:
        result = await self.enable_permanently(
            manager_id,
            connector_id,
            model_id=model_id,
            owner_execution_id=owner_execution_id,
        )
        return result.as_dict()

    async def disable_permanently_as_dict(
        self,
        manager_id: str,
        connector_id: str,
        *,
        owner_execution_id: str | None = None,
    ) -> dict[str, object]:
        result = await self.disable_permanently(
            manager_id,
            connector_id,
            owner_execution_id=owner_execution_id,
        )
        return result.as_dict()
