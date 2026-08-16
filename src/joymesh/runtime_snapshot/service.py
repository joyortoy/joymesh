"""Collect, validate, freeze, publish, and cache runtime snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from joymesh.models import Capability, FailureKind, utc_now
from joymesh.quota.contracts import (
    AUTO_BLOCKED_AVAILABILITIES,
    HarnessAvailability,
    QuotaSnapshot,
)
from joymesh.quota.service import QuotaService
from joymesh.registry import AdapterRegistry
from joymesh.runtime_snapshot.cache import RuntimeSnapshotCache
from joymesh.runtime_snapshot.contracts import (
    SCHEMA_VERSION,
    ExecutionState,
    HarnessRuntimeSnapshot,
    RuntimeSnapshot,
    RuntimeValidationCode,
)
from joymesh.runtime_snapshot.observations import ObservationStore
from joymesh.runtime_snapshot.publisher import RuntimeSnapshotPublisher
from joymesh.runtime_snapshot.validators import (
    RuntimeSnapshotValidationError,
    sanitize_provider_metadata,
    validate_snapshot,
)


class RuntimeLaunchError(RuntimeError):
    """Structured launch-time rejection (JoyMesh validates; does not route)."""

    def __init__(
        self,
        message: str,
        *,
        code: RuntimeValidationCode,
        remediation: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.details = details or {}


class RuntimeSnapshotService:
    """Authoritative producer of factual harness runtime state for JoyCLI."""

    def __init__(
        self,
        *,
        quota: QuotaService,
        registry: AdapterRegistry,
        observations: ObservationStore | None = None,
        cache: RuntimeSnapshotCache | None = None,
        publisher: RuntimeSnapshotPublisher | None = None,
        harness_ids: Iterable[str] | None = None,
    ) -> None:
        self.quota = quota
        self.registry = registry
        self.observations = observations or ObservationStore()
        self.cache = cache or RuntimeSnapshotCache()
        self.publisher = publisher or RuntimeSnapshotPublisher()
        self._extra_ids = tuple(harness_ids or ())
        self._lock = asyncio.Lock()
        self._prior_availability: dict[str, HarnessAvailability] = {}

    def known_harness_ids(self) -> tuple[str, ...]:
        ids = {
            *self._extra_ids,
            *self.quota.known_harness_ids(),
            *(item.manifest.harness_id for item in self.registry.list()),
        }
        return tuple(sorted(ids))

    async def snapshot(
        self,
        *,
        harness_ids: Iterable[str] | None = None,
        refresh: bool = False,
    ) -> RuntimeSnapshot:
        if not refresh:
            cached = self.cache.get()
            if cached is not None:
                if harness_ids is None:
                    return cached
                wanted = set(harness_ids)
                filtered = tuple(
                    item for item in cached.harnesses if item.harness_id in wanted
                )
                if len(filtered) == len(wanted):
                    return RuntimeSnapshot(
                        snapshot_id=cached.snapshot_id,
                        observed_at=cached.observed_at,
                        harnesses=filtered,
                        schema_version=cached.schema_version,
                    )
        async with self._lock:
            if not refresh:
                cached = self.cache.get()
                if cached is not None and harness_ids is None:
                    return cached
            ids = tuple(harness_ids) if harness_ids is not None else self.known_harness_ids()
            entries: list[HarnessRuntimeSnapshot] = []
            for harness_id in ids:
                entries.append(await self._build_harness(harness_id, refresh=refresh))
            snapshot = RuntimeSnapshot(
                snapshot_id=str(uuid4()),
                observed_at=utc_now(),
                harnesses=tuple(entries),
                schema_version=SCHEMA_VERSION,
            )
            validate_snapshot(snapshot)
            for entry in snapshot.harnesses:
                self._prior_availability[entry.harness_id] = entry.availability
            self.cache.put(snapshot)
            self.publisher.publish(snapshot)
            return snapshot

    async def harness_snapshot(
        self,
        harness_id: str,
        *,
        refresh: bool = False,
    ) -> HarnessRuntimeSnapshot:
        full = await self.snapshot(harness_ids=(harness_id,), refresh=refresh)
        entry = full.harness(harness_id)
        if entry is None:
            raise KeyError(f"unknown harness: {harness_id}")
        return entry

    async def refresh(self, harness_id: str | None = None) -> RuntimeSnapshot:
        self.cache.invalidate()
        if harness_id:
            return await self.snapshot(harness_ids=(harness_id,), refresh=True)
        return await self.snapshot(refresh=True)

    async def observe_execution(
        self,
        harness_id: str,
        *,
        success: bool,
        failure_kind: FailureKind | None = None,
        detail: str | None = None,
        duration_ms: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> RuntimeSnapshot:
        self.observations.record_execution(
            harness_id,
            success=success,
            failure_kind=failure_kind,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self.observations.mark_running(harness_id, delta=-1)
        # Keep quota subsystem authoritative for capacity; then republish snapshot.
        await self.quota.observe_execution(
            harness_id,
            success=success,
            failure_kind=failure_kind,
            detail=detail,
            usage_tokens=(input_tokens + output_tokens) or None,
        )
        # Use cached quota observation — do not re-probe providers and wipe facts.
        self.cache.invalidate()
        return await self.snapshot(refresh=False)

    def mark_run_started(self, harness_id: str) -> None:
        self.observations.mark_running(harness_id, delta=1)
        self.cache.invalidate()

    async def revalidate_for_launch(
        self,
        harness_id: str,
        *,
        required_capabilities: frozenset[Capability] | frozenset[str] | None = None,
        prior_availability: HarnessAvailability | None = None,
    ) -> HarnessRuntimeSnapshot:
        """Re-probe facts at launch. Reject invalid state; never silently execute."""

        entry = await self.harness_snapshot(harness_id, refresh=True)
        prior = prior_availability or self._prior_availability.get(harness_id)

        if required_capabilities:
            required = {
                item.value if isinstance(item, Capability) else str(item)
                for item in required_capabilities
            }
            missing = sorted(required - set(entry.capabilities))
            if missing:
                raise RuntimeLaunchError(
                    f"capability mismatch for {harness_id}: {', '.join(missing)}",
                    code=RuntimeValidationCode.CAPABILITY_MISMATCH,
                    remediation="Choose a harness that declares the required capabilities.",
                    details={"harness_id": harness_id, "missing_capabilities": missing},
                )

        code = _availability_to_code(entry.availability)
        if code is not None:
            if (
                prior is not None
                and prior is HarnessAvailability.READY
                and entry.availability in AUTO_BLOCKED_AVAILABILITIES
            ):
                raise RuntimeLaunchError(
                    f"runtime changed for {harness_id}: now {entry.availability.value}",
                    code=RuntimeValidationCode.RUNTIME_CHANGED,
                    remediation="Refresh the JoyCLI routing decision and retry.",
                    details={
                        "harness_id": harness_id,
                        "prior_availability": prior.value,
                        "availability": entry.availability.value,
                        "entry": entry.as_dict(),
                    },
                )
            raise RuntimeLaunchError(
                f"harness {harness_id} unavailable: {entry.availability.value}",
                code=code,
                remediation=_remediation_for(code),
                details={"harness_id": harness_id, "entry": entry.as_dict()},
            )
        return entry

    def format_table(self, snapshot: RuntimeSnapshot) -> str:
        if not snapshot.harnesses:
            return "No harness runtime data.\n"
        lines = ["Harness", ""]
        for item in snapshot.harnesses:
            lines.append(_display_name(item.harness_id))
            lines.append(item.display_status)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def as_json(self, snapshot: RuntimeSnapshot) -> dict[str, Any]:
        return self.publisher.as_json(snapshot)

    async def _build_harness(
        self,
        harness_id: str,
        *,
        refresh: bool,
    ) -> HarnessRuntimeSnapshot:
        quota = await self.quota.snapshot(harness_id, refresh=refresh)
        capabilities: frozenset[str] = frozenset()
        try:
            adapter = self.registry.get(harness_id)
            capabilities = frozenset(
                capability.value for capability in adapter.manifest.capabilities
            )
        except KeyError:
            capabilities = frozenset()

        active = self.observations.active_runs(harness_id)
        execution_state = (
            ExecutionState.RUNNING if active > 0 else ExecutionState.IDLE
        )
        metadata = sanitize_provider_metadata(
            {
                "reset_at": quota.reset_at.isoformat() if quota.reset_at else None,
                "rate_limit": quota.availability is HarnessAvailability.RATE_LIMITED,
                "quota_visibility": quota.quota_visibility.value,
                "quota_state": quota.state.value,
                "quota_source": quota.source.value,
                **{
                    key: value
                    for key, value in dict(quota.raw_metadata).items()
                    if key
                    in {
                        "login_status_code",
                        "auth_status_code",
                        "models_code",
                        "version_code",
                        "version",
                        "api_key_env_present",
                        "vertex",
                        "gca",
                        "settings_configured",
                        "detail",
                    }
                },
            }
        )
        safe_quota = QuotaSnapshot(
            harness_id=quota.harness_id,
            availability=quota.availability,
            quota_visibility=quota.quota_visibility,
            state=quota.state,
            authenticated=quota.authenticated,
            configured=quota.configured,
            credits_remaining=quota.credits_remaining,
            requests_remaining=quota.requests_remaining,
            tokens_remaining=quota.tokens_remaining,
            reset_at=quota.reset_at,
            observed_at=quota.observed_at,
            source=quota.source,
            raw_metadata=sanitize_provider_metadata(quota.raw_metadata),
        )
        return HarnessRuntimeSnapshot(
            harness_id=harness_id,
            availability=quota.availability,
            authenticated=quota.authenticated,
            configured=quota.configured,
            quota=safe_quota,
            capabilities=capabilities,
            execution_state=execution_state,
            recent_usage=self.observations.usage(harness_id),
            recent_quality=self.observations.quality(harness_id),
            latency=self.observations.latency(harness_id),
            provider_metadata=metadata,
        )


def _availability_to_code(
    availability: HarnessAvailability,
) -> RuntimeValidationCode | None:
    mapping = {
        HarnessAvailability.AUTHENTICATION_REQUIRED: (
            RuntimeValidationCode.AUTHENTICATION_REQUIRED
        ),
        HarnessAvailability.CONFIGURATION_REQUIRED: (
            RuntimeValidationCode.CONFIGURATION_REQUIRED
        ),
        HarnessAvailability.QUOTA_EXHAUSTED: RuntimeValidationCode.QUOTA_EXHAUSTED,
        HarnessAvailability.RATE_LIMITED: RuntimeValidationCode.RATE_LIMITED,
        HarnessAvailability.OFFLINE: RuntimeValidationCode.OFFLINE,
        HarnessAvailability.PROVIDER_UNAVAILABLE: (
            RuntimeValidationCode.PROVIDER_UNAVAILABLE
        ),
    }
    return mapping.get(availability)


def _remediation_for(code: RuntimeValidationCode) -> str:
    return {
        RuntimeValidationCode.QUOTA_EXHAUSTED: "Wait for quota reset or choose another harness.",
        RuntimeValidationCode.AUTHENTICATION_REQUIRED: "Authenticate the harness, then refresh.",
        RuntimeValidationCode.CONFIGURATION_REQUIRED: (
            "Configure the required API key or auth method."
        ),
        RuntimeValidationCode.CAPABILITY_MISMATCH: "Select a capability-compatible harness.",
        RuntimeValidationCode.PROVIDER_UNAVAILABLE: "Retry when the provider is available.",
        RuntimeValidationCode.RUNTIME_CHANGED: "Refresh routing and select again.",
        RuntimeValidationCode.RATE_LIMITED: "Wait for the rate limit window to reset.",
        RuntimeValidationCode.OFFLINE: "Install or start the harness executable.",
    }.get(code, "Refresh runtime snapshot and retry.")


def _display_name(harness_id: str) -> str:
    mapping = {
        "opencode": "OpenCode",
        "claude-code": "Claude",
        "codex": "Codex",
        "gemini-cli": "Gemini",
        "grok": "Grok",
    }
    return mapping.get(harness_id, harness_id)


# Re-export for callers that expect ValidationError alias.
__all__ = [
    "RuntimeLaunchError",
    "RuntimeSnapshotService",
    "RuntimeSnapshotValidationError",
]
