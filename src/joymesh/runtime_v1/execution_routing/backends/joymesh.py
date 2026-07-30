"""JoyMesh remote-worker backend — maps to hosted node lease lifecycle."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability
from joymesh.runtime_v1.execution_routing.models import (
    BackendHealth,
    ExecutionDecision,
    ExecutionIntent,
    ExecutionResult,
    ExecutionStatus,
)

# Optional bridge into RuntimeService node scheduling (injected at runtime).
RemoteSubmitFn = Callable[[ExecutionIntent, ExecutionDecision], Awaitable[Mapping[str, Any]]]


class JoyMeshBackend:
    """Remote JoyMesh workers: submit / claim / stream / cancel via injected transport."""

    backend_id = "joymesh"
    display_name = "JoyMesh Remote Backend"

    def __init__(
        self,
        *,
        healthy: bool = True,
        submissions: dict[str, Mapping[str, Any]] | None = None,
        submit_fn: RemoteSubmitFn | None = None,
    ) -> None:
        self._healthy = healthy
        self._submissions: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in (submissions or {}).items()
        }
        self._submit_fn = submit_fn
        self._events: dict[str, list[dict[str, Any]]] = {}

    def capabilities(self) -> frozenset[ExecutionCapability]:
        return frozenset(
            {
                ExecutionCapability.REMOTE_WORKER,
                ExecutionCapability.STREAMING,
                ExecutionCapability.LONG_RUNNING,
                ExecutionCapability.INTERNET,
                ExecutionCapability.EPHEMERAL_WORKSPACE,
                ExecutionCapability.FILESYSTEM,
                ExecutionCapability.PERSISTENT_WORKSPACE,
            }
        )

    def supports(self, intent: ExecutionIntent, *, harness_id: str) -> bool:
        del harness_id
        if intent.requires_provider_route:
            return False
        if intent.locality_preference == "local":
            return False
        missing = intent.required_capabilities - self.capabilities()
        return not missing

    async def health(self) -> BackendHealth:
        return BackendHealth(
            healthy=self._healthy,
            backend_id=self.backend_id,
            detail="joymesh workers reachable" if self._healthy else "joymesh workers unavailable",
            capabilities=self.capabilities(),
            state="healthy" if self._healthy else "unhealthy",
        )

    async def prepare(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        return await self.submit(intent, decision)

    async def validate(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> None:
        if not self.supports(intent, harness_id=decision.selected_harness_id):
            raise RuntimeError("joymesh backend does not support this intent")
        health = await self.health()
        if not health.healthy:
            raise RuntimeError(health.detail)

    async def submit(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        if self._submit_fn is not None:
            payload = dict(await self._submit_fn(intent, decision))
            self._submissions[intent.execution_id] = dict(payload)
            return payload
        payload = {
            "execution_id": intent.execution_id,
            "mission_id": intent.mission_id,
            "harness_id": decision.selected_harness_id,
            "status": "submitted",
            "lease_id": f"lease_{uuid4().hex[:12]}",
            "attempt_id": f"execution_attempt_{uuid4().hex}",
        }
        self._submissions[intent.execution_id] = dict(payload)
        return payload

    async def claim(self, execution_id: str, *, worker_id: str) -> Mapping[str, Any]:
        row = self._submissions.get(execution_id)
        if row is None:
            raise KeyError(f"unknown execution {execution_id}")
        row = {**row, "status": "claimed", "worker_id": worker_id}
        self._submissions[execution_id] = row
        self._append_event(execution_id, "execution.started", {"worker_id": worker_id})
        return row

    async def renew_lease(self, execution_id: str) -> Mapping[str, Any]:
        row = self._submissions.get(execution_id)
        if row is None:
            raise KeyError(f"unknown execution {execution_id}")
        row = {**row, "lease_renewed": True}
        self._submissions[execution_id] = row
        return row

    async def stream_output(self, execution_id: str) -> Mapping[str, Any]:
        row = self._submissions.get(execution_id) or {"execution_id": execution_id}
        return {
            "execution_id": execution_id,
            "chunks": tuple(self._events.get(execution_id, ())),
            "status": row.get("status"),
        }

    async def verify_completion(self, execution_id: str) -> bool:
        row = self._submissions.get(execution_id)
        return bool(row and row.get("status") in {"completed", "claimed", "submitted", "verified"})

    async def execute(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
        *,
        prepared: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        await self.validate(intent, decision)
        submission = dict(prepared or await self.submit(intent, decision))
        if self._submit_fn is not None:
            # Live RuntimeService path: submission already leased a remote node.
            status = str(submission.get("status") or "leased")
            ok = status in {"leased", "submitted", "claimed", "completed", "verified"}
            if status == "queued":
                exec_status = ExecutionStatus.BLOCKED
            elif ok:
                exec_status = ExecutionStatus.SUCCEEDED
            else:
                exec_status = ExecutionStatus.FAILED
            return ExecutionResult(
                ok=ok,
                execution_id=intent.execution_id,
                backend_id=self.backend_id,
                harness_id=decision.selected_harness_id,
                status=exec_status,
                message=str(submission.get("message") or "joymesh remote execution leased"),
                attempted_backends=(self.backend_id,),
                decision=decision,
                output={
                    "submission": submission,
                    "remote": True,
                    "completion_verified": False,
                    "awaiting_node_events": ok,
                },
                failure_class=None if ok else "backend_unavailable",
            )
        claimed = await self.claim(intent.execution_id, worker_id="foundation-worker")
        self._submissions[intent.execution_id] = {
            **claimed,
            "status": "completed",
            "message": "joymesh foundation execution recorded",
        }
        self._append_event(intent.execution_id, "execution.completed", {})
        return ExecutionResult(
            ok=True,
            execution_id=intent.execution_id,
            backend_id=self.backend_id,
            harness_id=decision.selected_harness_id,
            status=ExecutionStatus.SUCCEEDED,
            message="joymesh foundation execution completed",
            attempted_backends=(self.backend_id,),
            decision=decision,
            output={
                "submission": submission,
                "claim": claimed,
                "completion_verified": await self.verify_completion(intent.execution_id),
            },
            verification={"completed": True, "mode": "foundation"},
        )

    async def cancel(self, execution_id: str) -> None:
        if execution_id in self._submissions:
            self._submissions[execution_id] = {
                **self._submissions[execution_id],
                "status": "cancelled",
            }
            self._append_event(execution_id, "backend.cancelled", {})

    async def cleanup(self, execution_id: str) -> None:
        self._submissions.pop(execution_id, None)
        self._events.pop(execution_id, None)

    def ingest_event(self, execution_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        """Idempotent event ingest for remote workers."""

        existing = self._events.setdefault(execution_id, [])
        fingerprint = (event_type, str(payload.get("sequence")), str(payload.get("event_id")))
        for item in existing:
            if (
                item.get("event_type") == fingerprint[0]
                and str(item.get("payload", {}).get("sequence")) == fingerprint[1]
                and str(item.get("payload", {}).get("event_id")) == fingerprint[2]
            ):
                return
        sequences = [
            int(item.get("payload", {}).get("sequence") or 0)
            for item in existing
            if isinstance(item.get("payload", {}).get("sequence"), int)
            or str(item.get("payload", {}).get("sequence") or "").isdigit()
        ]
        incoming_seq = payload.get("sequence")
        if incoming_seq is not None and sequences:
            try:
                if int(incoming_seq) < max(sequences):
                    raise ValueError("out-of-order remote event")
            except (TypeError, ValueError):
                if isinstance(incoming_seq, int) and incoming_seq < max(sequences):
                    raise
        self._append_event(execution_id, event_type, dict(payload))
        if event_type in {"execution.completed", "task.succeeded"}:
            row = self._submissions.get(execution_id, {"execution_id": execution_id})
            self._submissions[execution_id] = {**row, "status": "completed"}

    def _append_event(self, execution_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        self._events.setdefault(execution_id, []).append(
            {"event_type": event_type, "payload": dict(payload)}
        )


class HostedBackend:
    """Stub hosted provider — disabled by default; never reports healthy for selection."""

    backend_id = "hosted"
    display_name = "Hosted Backend"

    def __init__(self, *, enabled: bool = False, healthy: bool = False) -> None:
        self._enabled = enabled
        # Even if healthy=True is passed, stub remains non-selectable unless enabled
        # with a real implementation flag.
        self._healthy = bool(enabled and healthy)
        self._real_implementation = False

    def capabilities(self) -> frozenset[ExecutionCapability]:
        return frozenset(
            {
                ExecutionCapability.INTERNET,
                ExecutionCapability.STREAMING,
                ExecutionCapability.LONG_RUNNING,
                ExecutionCapability.SANDBOX,
            }
        )

    def supports(self, intent: ExecutionIntent, *, harness_id: str) -> bool:
        del intent, harness_id
        return False  # stub never eligible

    async def health(self) -> BackendHealth:
        state = "disabled" if not self._enabled else "unsupported"
        return BackendHealth(
            healthy=False,
            backend_id=self.backend_id,
            detail=f"hosted backend {state}",
            capabilities=self.capabilities(),
            state=state,
        )

    async def prepare(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> Mapping[str, Any]:
        del intent, decision
        raise RuntimeError("hosted backend stub cannot prepare execution")

    async def validate(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
    ) -> None:
        del intent, decision
        raise RuntimeError("hosted backend stub cannot validate execution")

    async def execute(
        self,
        intent: ExecutionIntent,
        decision: ExecutionDecision,
        *,
        prepared: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        del prepared
        return ExecutionResult(
            ok=False,
            execution_id=intent.execution_id,
            backend_id=self.backend_id,
            harness_id=decision.selected_harness_id,
            status=ExecutionStatus.BLOCKED,
            message="hosted backend stub is disabled",
            attempted_backends=(self.backend_id,),
            decision=decision,
            output={"mode": "hosted", "stub": True},
            failure_class="backend_unavailable",
        )

    async def cancel(self, execution_id: str) -> None:
        del execution_id

    async def cleanup(self, execution_id: str) -> None:
        del execution_id
