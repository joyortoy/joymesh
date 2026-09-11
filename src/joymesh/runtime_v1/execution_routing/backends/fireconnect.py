"""FireConnect execution backend — sole owner of provider-route mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.failures import ExecutionFailureClass
from joymesh.runtime_v1.execution_routing.harness import HarnessAdapter
from joymesh.runtime_v1.execution_routing.models import (
    BackendHealth,
    ExecutionDecision,
    ExecutionIntent,
    ExecutionResult,
    ExecutionStatus,
)
from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

HealthProbe = Callable[[], Awaitable[BackendHealth]]


class FireConnectBackend:
    """Execution backend that may temporarily mutate provider routes via ProviderRouteService."""

    backend_id = "fireconnect"
    display_name = "FireConnect Backend"

    def __init__(
        self,
        *,
        provider_routes: ProviderRouteService | None = None,
        harnesses: Mapping[str, HarnessAdapter] | None = None,
        healthy: bool = True,
        manager_id: str = "fireconnect",
        supported_harnesses: frozenset[str] | None = None,
        health_probe: HealthProbe | None = None,
        skip_live_probe: bool = True,
    ) -> None:
        self.provider_routes = provider_routes or ProviderRouteService()
        self._harnesses = dict(harnesses or {})
        self._healthy = healthy
        self._manager_id = manager_id
        self._supported = supported_harnesses or frozenset(
            {"codex", "claude", "opencode", "cursor"}
        )
        self._health_probe = health_probe
        self._skip_live_probe = skip_live_probe

    def capabilities(self) -> frozenset[ExecutionCapability]:
        return frozenset(
            {
                ExecutionCapability.INTERNET,
                ExecutionCapability.FILESYSTEM,
                ExecutionCapability.PROVIDER_ROUTING,
                ExecutionCapability.STREAMING,
                ExecutionCapability.SANDBOX,
                ExecutionCapability.LOCAL_PROCESS,
                ExecutionCapability.PERSISTENT_WORKSPACE,
                ExecutionCapability.EPHEMERAL_WORKSPACE,
            }
        )

    def supports(self, intent: ExecutionIntent, *, harness_id: str) -> bool:
        if harness_id not in self._supported:
            return False
        missing = intent.required_capabilities - self.capabilities()
        return not missing

    async def health(self) -> BackendHealth:
        if not self._healthy:
            return BackendHealth(
                healthy=False,
                backend_id=self.backend_id,
                detail="fireconnect backend marked unavailable",
                capabilities=self.capabilities(),
                state="unhealthy",
            )
        if self._health_probe is not None:
            return await self._health_probe()
        if self._skip_live_probe:
            # Cached/configured unknown — never spawn FireConnect on default construction.
            return BackendHealth(
                healthy=True,
                backend_id=self.backend_id,
                detail="fireconnect health assumed (no live probe)",
                capabilities=self.capabilities(),
                state="unknown",
            )
        try:
            manager = self.provider_routes.manager(self._manager_id)
            discovery = await manager.discover()
            healthy = bool(discovery.installed and discovery.usable)
            detail = (
                "fireconnect provider-route manager usable"
                if healthy
                else f"fireconnect unavailable: {discovery.reason_code or 'not usable'}"
            )
            return BackendHealth(
                healthy=healthy,
                backend_id=self.backend_id,
                detail=detail,
                capabilities=self.capabilities(),
                state="healthy" if healthy else "unhealthy",
            )
        except Exception as exc:
            return BackendHealth(
                healthy=False,
                backend_id=self.backend_id,
                detail=f"fireconnect health probe failed: {type(exc).__name__}",
                capabilities=self.capabilities(),
                state="unhealthy",
            )

    async def prepare(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        return {
            "mode": "fireconnect",
            "harness_id": decision.selected_harness_id,
            "provider_routing_required": decision.provider_routing_required,
            "manager_id": self._manager_id,
        }

    async def validate(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> None:
        if not self.supports(intent, harness_id=decision.selected_harness_id):
            raise RuntimeError("fireconnect backend does not support this intent/harness")
        health = await self.health()
        if not health.healthy:
            raise RuntimeError(health.detail)

    async def execute(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
        *,
        prepared: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        del prepared
        await self.validate(intent, decision)
        adapter = self._harnesses.get(decision.selected_harness_id) or HarnessAdapter(
            harness_id=decision.selected_harness_id,
            display_name=decision.selected_harness_id,
        )

        async def _run_harness() -> Mapping[str, Any]:
            return await adapter.run(
                intent.prompt,
                {
                    "execution_id": intent.execution_id,
                    "workspace_path": intent.workspace_path,
                    "backend_id": self.backend_id,
                    "provider_routing": decision.provider_routing_required,
                    "model": intent.preferred_model,
                    "timeout_seconds": intent.timeout_seconds,
                    "read_only": True,
                },
            )

        if decision.provider_routing_required or intent.requires_provider_route:
            lifecycle = await self.provider_routes.run_temporary(
                self._manager_id,
                decision.selected_harness_id,
                execute=_run_harness,
                model_id=intent.preferred_model,
                owner_execution_id=intent.execution_id,
            )
            raw = lifecycle.execution_result
            if isinstance(raw, Mapping):
                output = dict(raw)
            else:
                output = {
                    "ok": lifecycle.ok,
                    "message": lifecycle.message,
                    "restored": lifecycle.restoration_verified,
                    "result": raw,
                }
            restore_failed = not bool(getattr(lifecycle, "restoration_verified", True))
            if restore_failed:
                return ExecutionResult(
                    ok=False,
                    execution_id=intent.execution_id,
                    backend_id=self.backend_id,
                    harness_id=decision.selected_harness_id,
                    status=ExecutionStatus.BLOCKED,
                    message=lifecycle.message or "provider route restoration failed",
                    attempted_backends=(self.backend_id,),
                    decision=decision,
                    output=output,
                    failure_class=ExecutionFailureClass.PROVIDER_RESTORE_FAILURE.value,
                )
            reason = getattr(lifecycle, "reason_code", None)
            if reason == "connector_blocked":
                return ExecutionResult(
                    ok=False,
                    execution_id=intent.execution_id,
                    backend_id=self.backend_id,
                    harness_id=decision.selected_harness_id,
                    status=ExecutionStatus.FAILED,
                    message=lifecycle.message or "connector blocked",
                    attempted_backends=(self.backend_id,),
                    decision=decision,
                    output=output,
                    failure_class=ExecutionFailureClass.CONNECTOR_BLOCKED.value,
                )
            ok = lifecycle.ok and bool(output.get("ok", True))
            return ExecutionResult(
                ok=ok,
                execution_id=intent.execution_id,
                backend_id=self.backend_id,
                harness_id=decision.selected_harness_id,
                status=ExecutionStatus.SUCCEEDED if ok else ExecutionStatus.FAILED,
                message=lifecycle.message,
                attempted_backends=(self.backend_id,),
                decision=decision,
                output=output,
                failure_class=None if ok else ExecutionFailureClass.PROCESS_FAILURE.value,
            )

        output = dict(await _run_harness())
        return ExecutionResult(
            ok=bool(output.get("ok", True)),
            execution_id=intent.execution_id,
            backend_id=self.backend_id,
            harness_id=decision.selected_harness_id,
            status=ExecutionStatus.SUCCEEDED if output.get("ok", True) else ExecutionStatus.FAILED,
            message=str(output.get("message") or "fireconnect execution completed"),
            attempted_backends=(self.backend_id,),
            decision=decision,
            output=output,
        )

    async def cancel(self, execution_id: str) -> None:
        adapter_ids = list(self._harnesses)
        for harness_id in adapter_ids:
            adapter = self._harnesses[harness_id]
            cancel = getattr(adapter, "cancel", None)
            if callable(cancel):
                await cancel(execution_id)

    async def cleanup(self, execution_id: str) -> None:
        await self.cancel(execution_id)
