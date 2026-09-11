"""Backend registry — configuration isolated from planner decisions."""

from __future__ import annotations

from collections.abc import Mapping

from joymesh.runtime_v1.execution_routing.backends.fireconnect import FireConnectBackend
from joymesh.runtime_v1.execution_routing.backends.joymesh import HostedBackend, JoyMeshBackend
from joymesh.runtime_v1.execution_routing.backends.local import LocalBackend
from joymesh.runtime_v1.execution_routing.backends.protocol import ExecutionBackend
from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.harness import builtin_harness_adapters
from joymesh.runtime_v1.execution_routing.models import BackendRegistryConfig
from joymesh.runtime_v1.provider_routes.lease_store import ProviderRouteLeaseStore
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService


class BackendRegistryError(KeyError):
    pass


class BackendRegistry:
    def __init__(
        self,
        backends: Mapping[str, ExecutionBackend] | None = None,
        *,
        config: BackendRegistryConfig | None = None,
    ) -> None:
        self.config = config or BackendRegistryConfig()
        if backends is not None:
            self._backends = dict(backends)
        else:
            # Default construction must NOT probe live FireConnect / network / credentials.
            harnesses = builtin_harness_adapters(use_real_adapters=True)
            provider_routes = ProviderRouteService(store=ProviderRouteLeaseStore())
            self._backends = {
                "local": LocalBackend(harnesses=harnesses, healthy=True),
                "fireconnect": FireConnectBackend(
                    harnesses=harnesses,
                    healthy=True,
                    provider_routes=provider_routes,
                    # Explicit cached/unknown health — no subprocess on construct or health().
                    health_probe=None,
                    skip_live_probe=True,
                ),
                "joymesh": JoyMeshBackend(healthy=True),
                "hosted": HostedBackend(enabled=False),
            }
        self._apply_capability_overrides()

    def _apply_capability_overrides(self) -> None:
        self._overrides: dict[str, frozenset[ExecutionCapability]] = {}
        for backend_id, values in self.config.capability_overrides.items():
            self._overrides[backend_id] = frozenset(ExecutionCapability(item) for item in values)

    def get(self, backend_id: str) -> ExecutionBackend:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise BackendRegistryError(f"unknown execution backend: {backend_id}") from exc

    def enabled(self) -> list[ExecutionBackend]:
        out: list[ExecutionBackend] = []
        for backend_id in self.config.enabled_backends:
            backend = self._backends.get(backend_id)
            if backend is None:
                continue
            if backend_id == "hosted" and not self.config.allow_stub_backends:
                continue
            out.append(backend)
        return out

    def priority_order(self) -> tuple[str, ...]:
        return self.config.priority

    def fallback_order(self) -> tuple[str, ...]:
        return self.config.fallback_order

    def override_capabilities(self, backend_id: str) -> frozenset[ExecutionCapability] | None:
        return self._overrides.get(backend_id)

    def list_ids(self) -> tuple[str, ...]:
        return tuple(self._backends)

    @property
    def revision(self) -> str:
        return self.config.registry_revision
