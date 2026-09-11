"""Local execution backend — no provider-route mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from joymesh.runtime_v1.execution_routing.capabilities import (
    KNOWN_HARNESSES,
    ExecutionCapability,
)
from joymesh.runtime_v1.execution_routing.failures import ExecutionFailureClass
from joymesh.runtime_v1.execution_routing.harness import HarnessAdapter
from joymesh.runtime_v1.execution_routing.models import (
    BackendHealth,
    ExecutionDecision,
    ExecutionIntent,
    ExecutionResult,
    ExecutionStatus,
)
from joymesh.runtime_v1.execution_routing.process_runner import ProcessRunnerError


class LocalBackend:
    """Runs a harness locally without FireConnect / provider routing."""

    backend_id = "local"
    display_name = "Local Backend"

    def __init__(
        self,
        *,
        harnesses: Mapping[str, HarnessAdapter] | None = None,
        healthy: bool = True,
        supported_harnesses: frozenset[str] | None = None,
    ) -> None:
        self._harnesses = dict(harnesses or {})
        self._healthy = healthy
        if supported_harnesses is not None:
            self._supported = supported_harnesses
        elif self._harnesses:
            self._supported = frozenset(self._harnesses)
        else:
            self._supported = frozenset(KNOWN_HARNESSES)

    def capabilities(self) -> frozenset[ExecutionCapability]:
        return frozenset(
            {
                ExecutionCapability.FILESYSTEM,
                ExecutionCapability.LOCAL_PROCESS,
                ExecutionCapability.STREAMING,
                ExecutionCapability.SANDBOX,
                ExecutionCapability.PERSISTENT_WORKSPACE,
                ExecutionCapability.EPHEMERAL_WORKSPACE,
            }
        )

    def supports(self, intent: ExecutionIntent, *, harness_id: str) -> bool:
        if intent.requires_provider_route:
            return False
        if ExecutionCapability.PROVIDER_ROUTING in intent.required_capabilities:
            return False
        if ExecutionCapability.REMOTE_WORKER in intent.required_capabilities:
            return False
        if intent.locality_preference == "remote":
            return False
        if harness_id not in self._supported and harness_id not in self._harnesses:
            return False
        missing = intent.required_capabilities - self.capabilities()
        return not missing

    async def health(self) -> BackendHealth:
        return BackendHealth(
            healthy=self._healthy,
            backend_id=self.backend_id,
            detail="local backend ready" if self._healthy else "local backend unavailable",
            capabilities=self.capabilities(),
            state="healthy" if self._healthy else "unhealthy",
        )

    async def prepare(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        return {"mode": "local", "harness_id": decision.selected_harness_id}

    async def validate(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> None:
        if not self.supports(intent, harness_id=decision.selected_harness_id):
            raise RuntimeError("local backend does not support this intent/harness")
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
        policy_profile = str(
            (intent.organisation_policy or {}).get("policy_profile") or "read_only"
        )
        read_only = policy_profile in {"read_only", "production_restricted"}
        try:
            output = dict(
                await adapter.run(
                    intent.prompt,
                    {
                        "execution_id": intent.execution_id,
                        "workspace_path": intent.workspace_path,
                        "backend_id": self.backend_id,
                        "provider_routing": False,
                        "timeout_seconds": intent.timeout_seconds,
                        "read_only": read_only,
                        "policy_profile": policy_profile,
                    },
                )
            )
        except ProcessRunnerError as exc:
            failure = (
                ExecutionFailureClass.WORKSPACE_VIOLATION
                if exc.reason_code.startswith("workspace")
                else ExecutionFailureClass.PREPARATION_FAILURE
            )
            return ExecutionResult(
                ok=False,
                execution_id=intent.execution_id,
                backend_id=self.backend_id,
                harness_id=decision.selected_harness_id,
                status=ExecutionStatus.FAILED,
                message=exc.message,
                attempted_backends=(self.backend_id,),
                decision=decision,
                output={"reason_code": exc.reason_code},
                failure_class=failure.value,
            )
        return ExecutionResult(
            ok=bool(output.get("ok", True)),
            execution_id=intent.execution_id,
            backend_id=self.backend_id,
            harness_id=decision.selected_harness_id,
            status=ExecutionStatus.SUCCEEDED if output.get("ok", True) else ExecutionStatus.FAILED,
            message=str(output.get("message") or "local execution completed"),
            attempted_backends=(self.backend_id,),
            decision=decision,
            output=output,
            failure_class=None
            if output.get("ok", True)
            else str(output.get("failure_class") or ExecutionFailureClass.PROCESS_FAILURE.value),
        )

    async def cancel(self, execution_id: str) -> None:
        for adapter in self._harnesses.values():
            cancel = getattr(adapter, "cancel", None)
            if callable(cancel):
                await cancel(execution_id)

    async def cleanup(self, execution_id: str) -> None:
        await self.cancel(execution_id)
