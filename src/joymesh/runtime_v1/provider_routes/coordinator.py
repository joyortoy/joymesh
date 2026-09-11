"""Provider-route mutation coordinator: lease + enable + execute + restore."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.runtime_v1.provider_routes.authority import mutation_authority
from joymesh.runtime_v1.provider_routes.lease_store import (
    ProviderRouteLease,
    ProviderRouteLeaseError,
    ProviderRouteLeaseStore,
    sanitise_route_state,
)
from joymesh.runtime_v1.provider_routes.protocol import (
    ProviderRoute,
    ProviderRouteManager,
    ProviderRouteMutationApproval,
)
from joymesh.security import redact_secrets

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderRouteAuditEvent:
    event_type: str
    manager_id: str
    connector_id: str
    owner_execution_id: str | None
    reason_code: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: utc_now().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "manager_id": self.manager_id,
            "connector_id": self.connector_id,
            "owner_execution_id": self.owner_execution_id,
            "reason_code": self.reason_code,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ProviderRouteLifecycleResult:
    ok: bool
    lease: ProviderRouteLease | None
    original_state: Mapping[str, Any]
    restored: bool
    restoration_verified: bool
    execution_result: Any | None
    reason_code: str | None
    message: str
    audits: tuple[ProviderRouteAuditEvent, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lease": self.lease.as_dict() if self.lease else None,
            "original_state": dict(self.original_state),
            "restored": self.restored,
            "restoration_verified": self.restoration_verified,
            "execution_result": self.execution_result,
            "reason_code": self.reason_code,
            "message": self.message,
            "audits": [item.as_dict() for item in self.audits],
        }


class ProviderRouteMutationCoordinator:
    """Serialises provider-route mutation+execution per manager+connector.

    Cross-process safety comes from ``ProviderRouteLeaseStore`` (SQL unique
    active lease). Process-local ``asyncio.Lock`` reduces thundering-herd
    contention within one worker.
    """

    def __init__(self, store: ProviderRouteLeaseStore | None = None) -> None:
        self.store = store or ProviderRouteLeaseStore()
        self._audits: list[ProviderRouteAuditEvent] = []

    @property
    def audits(self) -> Sequence[ProviderRouteAuditEvent]:
        return tuple(self._audits)

    def clear_audits(self) -> None:
        self._audits.clear()

    async def run_lifecycle(
        self,
        *,
        manager: ProviderRouteManager,
        connector_id: str,
        approval: ProviderRouteMutationApproval,
        execute: Callable[[], Awaitable[T]],
        owner_execution_id: str | None = None,
        model_id: str | None = None,
        target_provider_id: str = "fireworks",
        acquire_timeout_seconds: float = 30.0,
        lease_ttl_seconds: float = 120.0,
        poll_interval_seconds: float = 0.05,
    ) -> ProviderRouteLifecycleResult:
        """Capture → enable → verify → execute → restore under a per-connector lease."""

        owner = owner_execution_id or str(uuid4())
        manager_id = manager.manager_id
        audits: list[ProviderRouteAuditEvent] = []

        blocked = await self.store.is_blocked(manager_id, connector_id)
        if blocked:
            event = self._audit(
                "provider_route.mutation_blocked",
                manager_id,
                connector_id,
                owner,
                reason_code="recovery_failed",
                payload={"detail": blocked},
            )
            return ProviderRouteLifecycleResult(
                ok=False,
                lease=None,
                original_state={},
                restored=False,
                restoration_verified=False,
                execution_result=None,
                reason_code="recovery_failed",
                message=f"provider-route mutations blocked: {blocked}",
                audits=(event,),
            )

        # Recover expired leases before attempting acquisition.
        await self.recover_expired_leases(manager, connector_ids=(connector_id,))

        lease: ProviderRouteLease | None = None
        original: dict[str, Any] = {}
        execution_result: Any | None = None
        restored = False
        restoration_verified = False
        reason_code: str | None = None
        message = ""
        ok = False

        local = self.store.local_lock(manager_id, connector_id)
        waited = False
        try:
            try:
                await asyncio.wait_for(local.acquire(), timeout=acquire_timeout_seconds)
            except TimeoutError:
                audits.append(
                    self._audit(
                        "provider_route.lock_timeout",
                        manager_id,
                        connector_id,
                        owner,
                        reason_code="lock_acquisition_timeout",
                    )
                )
                return ProviderRouteLifecycleResult(
                    ok=False,
                    lease=None,
                    original_state={},
                    restored=False,
                    restoration_verified=False,
                    execution_result=None,
                    reason_code="lock_acquisition_timeout",
                    message="timed out waiting for process-local provider-route lock",
                    audits=tuple(audits),
                )
            except asyncio.CancelledError:
                audits.append(
                    self._audit(
                        "provider_route.lock_wait_cancelled",
                        manager_id,
                        connector_id,
                        owner,
                        reason_code="lock_wait_cancelled",
                    )
                )
                raise

            # DB lease acquisition with timeout (cross-process).
            deadline = asyncio.get_running_loop().time() + acquire_timeout_seconds
            current = await manager.inspect_route(connector_id)
            original = sanitise_route_state(
                {
                    "connector_id": connector_id,
                    "provider_id": current.provider_id,
                    "enabled": current.enabled,
                    "model_id": current.model_id,
                    "configuration_status": current.configuration_status,
                    "available": current.available,
                    "authenticated": current.authenticated,
                }
            )
            while True:
                lease = await self.store.try_acquire(
                    manager_id=manager_id,
                    connector_id=connector_id,
                    owner_execution_id=owner,
                    ttl_seconds=lease_ttl_seconds,
                    original_state=original,
                    target_provider_id=target_provider_id,
                    target_model_id=model_id,
                    details={"phase": "acquired"},
                )
                if lease is not None:
                    break
                waited = True
                if asyncio.get_running_loop().time() >= deadline:
                    audits.append(
                        self._audit(
                            "provider_route.lease_timeout",
                            manager_id,
                            connector_id,
                            owner,
                            reason_code="lock_acquisition_timeout",
                        )
                    )
                    return ProviderRouteLifecycleResult(
                        ok=False,
                        lease=None,
                        original_state=original,
                        restored=False,
                        restoration_verified=False,
                        execution_result=None,
                        reason_code="lock_acquisition_timeout",
                        message="timed out waiting for provider-route lease",
                        audits=tuple(audits),
                    )
                await asyncio.sleep(poll_interval_seconds)

            assert lease is not None
            audits.append(
                self._audit(
                    "provider_route.lease_acquired",
                    manager_id,
                    connector_id,
                    owner,
                    payload={
                        "lease_id": lease.lease_id,
                        "waited": waited,
                        "original_enabled": original.get("enabled"),
                    },
                )
            )

            with mutation_authority(
                manager_id=manager_id,
                connector_id=connector_id,
                purpose="lifecycle",
                lease_id=lease.lease_id,
            ):
                try:
                    # Enable target route when needed.
                    need_enable = not (
                        current.enabled
                        and current.provider_id == target_provider_id
                        and (
                            model_id is None
                            or current.model_id == model_id
                            or _model_matches(current.model_id, model_id)
                        )
                    )
                    if need_enable:
                        enable_result = await manager.enable_route(
                            connector_id,
                            approval=approval,
                            model_id=model_id,
                        )
                        if not enable_result.ok:
                            if enable_result.reason_code == "configuration_invalid":
                                reason_code = "provider_status_verification_failed"
                            else:
                                reason_code = enable_result.reason_code or "provider_enable_failed"
                            message = enable_result.message
                            audits.append(
                                self._audit(
                                    "provider_route.enable_failed"
                                    if reason_code != "provider_status_verification_failed"
                                    else "provider_route.verify_failed",
                                    manager_id,
                                    connector_id,
                                    owner,
                                    reason_code=reason_code,
                                    payload={"message": redact_secrets(message)[:300]},
                                )
                            )
                        else:
                            verified = await manager.verify_route(connector_id)
                            if not verified.enabled:
                                reason_code = "provider_status_verification_failed"
                                message = "route enable did not verify as enabled"
                                audits.append(
                                    self._audit(
                                        "provider_route.verify_failed",
                                        manager_id,
                                        connector_id,
                                        owner,
                                        reason_code=reason_code,
                                    )
                                )
                            else:
                                audits.append(
                                    self._audit(
                                        "provider_route.enabled",
                                        manager_id,
                                        connector_id,
                                        owner,
                                        payload={"model_id": verified.model_id},
                                    )
                                )
                    else:
                        audits.append(
                            self._audit(
                                "provider_route.already_enabled",
                                manager_id,
                                connector_id,
                                owner,
                                payload={"model_id": current.model_id},
                            )
                        )

                    if reason_code is None:
                        try:
                            await self.store.renew(
                                lease_id=lease.lease_id,
                                lease_token=lease.lease_token,
                                owner_execution_id=owner,
                                ttl_seconds=lease_ttl_seconds,
                            )
                            execution_result = await execute()
                            ok = True
                            message = "lifecycle completed"
                            audits.append(
                                self._audit(
                                    "provider_route.execution_completed",
                                    manager_id,
                                    connector_id,
                                    owner,
                                )
                            )
                        except asyncio.CancelledError:
                            reason_code = "holder_cancelled"
                            message = "execution cancelled while holding provider-route lease"
                            audits.append(
                                self._audit(
                                    "provider_route.execution_cancelled",
                                    manager_id,
                                    connector_id,
                                    owner,
                                    reason_code=reason_code,
                                )
                            )
                            raise
                        except TimeoutError:
                            reason_code = "holder_timeout"
                            message = "execution timed out while holding provider-route lease"
                            audits.append(
                                self._audit(
                                    "provider_route.execution_timeout",
                                    manager_id,
                                    connector_id,
                                    owner,
                                    reason_code=reason_code,
                                )
                            )
                        except Exception as exc:
                            reason_code = "holder_execution_failed"
                            message = redact_secrets(str(exc))[:300]
                            audits.append(
                                self._audit(
                                    "provider_route.execution_failed",
                                    manager_id,
                                    connector_id,
                                    owner,
                                    reason_code=reason_code,
                                    payload={"error_type": type(exc).__name__},
                                )
                            )
                finally:

                    async def _cleanup() -> None:
                        nonlocal restored, restoration_verified, reason_code, message
                        try:
                            (
                                restored,
                                restoration_verified,
                                restore_code,
                                restore_msg,
                            ) = await self._restore(
                                manager=manager,
                                connector_id=connector_id,
                                original=original,
                                owner=owner,
                                lease=lease,
                            )
                            if restore_code and reason_code is None and not ok:
                                reason_code = restore_code
                            if restore_msg and not message:
                                message = restore_msg
                            audits.append(
                                self._audit(
                                    "provider_route.restored"
                                    if restored and restoration_verified
                                    else "provider_route.restore_failed",
                                    manager_id,
                                    connector_id,
                                    owner,
                                    reason_code=None
                                    if restored and restoration_verified
                                    else (restore_code or "restoration_failed"),
                                    payload={
                                        "restored": restored,
                                        "verified": restoration_verified,
                                    },
                                )
                            )
                        finally:
                            try:
                                await self.store.release(
                                    lease_id=lease.lease_id,
                                    lease_token=lease.lease_token,
                                    owner_execution_id=owner,
                                )
                                audits.append(
                                    self._audit(
                                        "provider_route.lease_released",
                                        manager_id,
                                        connector_id,
                                        owner,
                                        payload={"lease_id": lease.lease_id},
                                    )
                                )
                            except ProviderRouteLeaseError as exc:
                                audits.append(
                                    self._audit(
                                        "provider_route.lease_release_failed",
                                        manager_id,
                                        connector_id,
                                        owner,
                                        reason_code=exc.reason_code,
                                        payload={"message": exc.message},
                                    )
                                )

                    cleanup_task = asyncio.create_task(_cleanup())
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError:
                        await asyncio.shield(cleanup_task)
                        raise
        finally:
            if local.locked():
                local.release()

        if ok and restored and restoration_verified:
            ok = True
        elif ok and not restoration_verified:
            ok = False
            reason_code = reason_code or "restoration_failed"
            message = message or "execution succeeded but restoration failed"

        result = ProviderRouteLifecycleResult(
            ok=ok and restoration_verified,
            lease=lease,
            original_state=original,
            restored=restored,
            restoration_verified=restoration_verified,
            execution_result=execution_result,
            reason_code=reason_code,
            message=message,
            audits=tuple(audits),
        )
        self._audits.extend(audits)
        return result

    async def recover_expired_leases(
        self,
        manager: ProviderRouteManager,
        *,
        connector_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Restore original state for expired active leases; block on failure."""

        reports: list[dict[str, Any]] = []
        expired = await self.store.list_expired_active()
        for lease in expired:
            if connector_ids is not None and lease.connector_id not in connector_ids:
                continue
            if lease.manager_id != manager.manager_id:
                continue
            if lease.details.get("restore_on_expiry") is False:
                await self.store.mark_recovery(
                    lease_id=lease.lease_id,
                    lease_token=lease.lease_token,
                    recovery_status="expired_no_restore",
                    details={"reason": "cli_mutation"},
                )
                reports.append(
                    {
                        "lease_id": lease.lease_id,
                        "connector_id": lease.connector_id,
                        "status": "expired_no_restore",
                        "verified": True,
                    }
                )
                continue
            report = await self._recover_one(manager, lease)
            reports.append(report)
        return reports

    async def run_serialised_mutation(
        self,
        *,
        manager: ProviderRouteManager,
        connector_id: str,
        mutate: Callable[[], Awaitable[T]],
        owner_execution_id: str | None = None,
        acquire_timeout_seconds: float = 30.0,
        lease_ttl_seconds: float = 60.0,
    ) -> T:
        """Serialise a one-shot mutation without automatic restore (CLI enable/disable)."""

        owner = owner_execution_id or str(uuid4())
        manager_id = manager.manager_id
        blocked = await self.store.is_blocked(manager_id, connector_id)
        if blocked:
            raise ProviderRouteLeaseError("recovery_failed", f"mutations blocked: {blocked}")
        await self.recover_expired_leases(manager, connector_ids=(connector_id,))
        local = self.store.local_lock(manager_id, connector_id)
        await asyncio.wait_for(local.acquire(), timeout=acquire_timeout_seconds)
        lease: ProviderRouteLease | None = None
        try:
            current = await manager.inspect_route(connector_id)
            original = sanitise_route_state(
                {
                    "connector_id": connector_id,
                    "provider_id": current.provider_id,
                    "enabled": current.enabled,
                    "model_id": current.model_id,
                    "configuration_status": current.configuration_status,
                    "available": current.available,
                    "authenticated": current.authenticated,
                }
            )
            deadline = asyncio.get_running_loop().time() + acquire_timeout_seconds
            while True:
                lease = await self.store.try_acquire(
                    manager_id=manager_id,
                    connector_id=connector_id,
                    owner_execution_id=owner,
                    ttl_seconds=lease_ttl_seconds,
                    original_state=original,
                    details={"restore_on_expiry": False, "phase": "cli_mutation"},
                )
                if lease is not None:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise ProviderRouteLeaseError(
                        "lock_acquisition_timeout",
                        "timed out waiting for provider-route lease",
                    )
                await asyncio.sleep(0.05)
            assert lease is not None
            self._audit(
                "provider_route.cli_mutation_acquired",
                manager_id,
                connector_id,
                owner,
                payload={"lease_id": lease.lease_id},
            )
            with mutation_authority(
                manager_id=manager_id,
                connector_id=connector_id,
                purpose="serialised",
                lease_id=lease.lease_id,
            ):
                return await mutate()
        finally:
            if lease is not None:
                try:
                    await self.store.release(
                        lease_id=lease.lease_id,
                        lease_token=lease.lease_token,
                        owner_execution_id=owner,
                    )
                except ProviderRouteLeaseError:
                    pass
            if local.locked():
                local.release()

    async def _recover_one(
        self,
        manager: ProviderRouteManager,
        lease: ProviderRouteLease,
    ) -> dict[str, Any]:
        """Restore using recovery claim — never acquires a new lease (no deadlock)."""

        recovery_owner = f"recovery:{uuid4().hex}"
        original = dict(lease.original_state)
        local = self.store.local_lock(lease.manager_id, lease.connector_id)
        try:
            claimed = await self.store.claim_recovery(
                lease_id=lease.lease_id,
                lease_token=lease.lease_token,
                recovery_owner_id=recovery_owner,
            )
        except ProviderRouteLeaseError as exc:
            if exc.reason_code == "recovery_claimed":
                return {
                    "lease_id": lease.lease_id,
                    "connector_id": lease.connector_id,
                    "status": "recovery_in_progress",
                    "verified": False,
                }
            raise

        await local.acquire()
        try:
            with mutation_authority(
                manager_id=claimed.manager_id,
                connector_id=claimed.connector_id,
                purpose="recovery",
                lease_id=claimed.lease_id,
            ):
                restored, verified, code, msg = await self._restore(
                    manager=manager,
                    connector_id=claimed.connector_id,
                    original=original,
                    owner=claimed.owner_execution_id,
                    lease=claimed,
                    for_recovery=True,
                )
            status = "restored_verified" if restored and verified else "recovery_failed"
            await self.store.mark_recovery(
                lease_id=claimed.lease_id,
                lease_token=claimed.lease_token,
                recovery_status=status,
                details={
                    "message": msg,
                    "code": code,
                    "recovery_owner_id": recovery_owner,
                },
            )
            if status == "recovery_failed":
                await self.store.block_connector(
                    manager_id=claimed.manager_id,
                    connector_id=claimed.connector_id,
                    reason=msg or code or "recovery_failed",
                )
            self._audit(
                "provider_route.crash_recovery",
                claimed.manager_id,
                claimed.connector_id,
                claimed.owner_execution_id,
                reason_code=status,
                payload={"verified": verified},
            )
            return {
                "lease_id": claimed.lease_id,
                "connector_id": claimed.connector_id,
                "status": status,
                "verified": verified,
            }
        except Exception as exc:
            await self.store.block_connector(
                manager_id=lease.manager_id,
                connector_id=lease.connector_id,
                reason=redact_secrets(str(exc))[:200],
            )
            try:
                await self.store.mark_recovery(
                    lease_id=lease.lease_id,
                    lease_token=lease.lease_token,
                    recovery_status="recovery_failed",
                    details={"error_type": type(exc).__name__},
                )
            except ProviderRouteLeaseError:
                pass
            return {
                "lease_id": lease.lease_id,
                "connector_id": lease.connector_id,
                "status": "recovery_failed",
                "verified": False,
            }
        finally:
            if local.locked():
                local.release()

    async def _restore(
        self,
        *,
        manager: ProviderRouteManager,
        connector_id: str,
        original: Mapping[str, Any],
        owner: str,
        lease: ProviderRouteLease,
        for_recovery: bool = False,
    ) -> tuple[bool, bool, str | None, str]:
        del owner, lease, for_recovery
        desired_enabled = bool(original.get("enabled"))
        desired_model = original.get("model_id")
        try:
            from joymesh.runtime_v1.provider_routes.protocol import (
                ProviderRouteMutationApproval,
            )

            if desired_enabled:
                approval = ProviderRouteMutationApproval(
                    approved=True,
                    action="enable",
                    manager_id=manager.manager_id,
                    connector_id=connector_id,
                    nonce=uuid4().hex,
                    model_id=str(desired_model) if desired_model else None,
                )
                result = await manager.enable_route(
                    connector_id,
                    approval=approval,
                    model_id=str(desired_model) if desired_model else None,
                )
                if not result.ok:
                    return False, False, "restoration_failed", result.message
            else:
                approval = ProviderRouteMutationApproval(
                    approved=True,
                    action="disable",
                    manager_id=manager.manager_id,
                    connector_id=connector_id,
                    nonce=uuid4().hex,
                )
                result = await manager.disable_route(connector_id, approval=approval)
                if not result.ok:
                    return False, False, "restoration_failed", result.message

            verified_route = await manager.verify_route(connector_id)
            matches = _state_matches(verified_route, original)
            if not matches:
                return (
                    True,
                    False,
                    "restoration_failed",
                    "restored route does not match original sanitised state",
                )
            return True, True, None, "exact original state restored"
        except Exception as exc:
            return False, False, "restoration_failed", redact_secrets(str(exc))[:300]

    def _audit(
        self,
        event_type: str,
        manager_id: str,
        connector_id: str,
        owner: str | None,
        *,
        reason_code: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> ProviderRouteAuditEvent:
        event = ProviderRouteAuditEvent(
            event_type=event_type,
            manager_id=manager_id,
            connector_id=connector_id,
            owner_execution_id=owner,
            reason_code=reason_code,
            payload=dict(payload or {}),
        )
        self._audits.append(event)
        return event


def _model_matches(observed: str | None, requested: str | None) -> bool:
    if requested is None:
        return True
    if observed is None:
        return False
    if observed == requested:
        return True
    # FireConnect often reports short ids.
    return observed.endswith(requested) or requested.endswith(observed)


def _state_matches(route: ProviderRoute, original: Mapping[str, Any]) -> bool:
    if bool(route.enabled) != bool(original.get("enabled")):
        return False
    if original.get("enabled"):
        desired_model = original.get("model_id")
        if desired_model and not _model_matches(route.model_id, str(desired_model)):
            return False
        desired_provider = original.get("provider_id")
        if desired_provider and route.provider_id != desired_provider:
            return False
    return True
