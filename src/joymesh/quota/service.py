"""Quota service: probe, cache, observe, and format status."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from joymesh.models import FailureKind
from joymesh.quota.cache import QuotaCache
from joymesh.quota.contracts import (
    AUTO_BLOCKED_AVAILABILITIES,
    AVAILABILITY_SCORE,
    QUOTA_STATE_SCORE,
    HarnessAvailability,
    QuotaSnapshot,
    QuotaSource,
    QuotaState,
    QuotaVisibility,
)
from joymesh.quota.providers import (
    BaseQuotaProvider,
    UnknownQuotaProvider,
    async_quota_snapshot,
    builtin_quota_providers,
    utc_now,
)


class QuotaService:
    def __init__(
        self,
        *,
        providers: dict[str, BaseQuotaProvider] | None = None,
        cache: QuotaCache | None = None,
        harness_ids: Iterable[str] | None = None,
    ) -> None:
        self._providers = dict(providers or builtin_quota_providers())
        self._cache = cache or QuotaCache()
        self._extra_ids = tuple(harness_ids or ())
        self._lock = asyncio.Lock()

    @property
    def cache(self) -> QuotaCache:
        return self._cache

    def known_harness_ids(self) -> tuple[str, ...]:
        ids = set(self._providers) | set(self._extra_ids)
        return tuple(sorted(ids))

    def provider_for(self, harness_id: str) -> BaseQuotaProvider:
        return self._providers.get(harness_id) or UnknownQuotaProvider(harness_id)

    async def snapshot(
        self,
        harness_id: str,
        *,
        refresh: bool = False,
    ) -> QuotaSnapshot:
        if not refresh:
            cached = self._cache.get(harness_id)
            if cached is not None:
                return cached
        async with self._lock:
            if not refresh:
                cached = self._cache.get(harness_id)
                if cached is not None:
                    return cached
            provider = self.provider_for(harness_id)
            try:
                snapshot = await async_quota_snapshot(provider)
            except Exception as exc:
                snapshot = QuotaSnapshot(
                    harness_id=harness_id,
                    availability=HarnessAvailability.UNKNOWN,
                    quota_visibility=QuotaVisibility.UNKNOWN,
                    state=QuotaState.UNKNOWN,
                    authenticated=False,
                    configured=False,
                    credits_remaining=None,
                    requests_remaining=None,
                    tokens_remaining=None,
                    reset_at=None,
                    observed_at=utc_now(),
                    source=QuotaSource.NONE,
                    raw_metadata={"probe_error": type(exc).__name__},
                )
            self._cache.put(snapshot)
            return snapshot

    async def list_snapshots(
        self,
        *,
        harness_ids: Iterable[str] | None = None,
        refresh: bool = False,
    ) -> tuple[QuotaSnapshot, ...]:
        ids = tuple(harness_ids) if harness_ids is not None else self.known_harness_ids()
        return tuple([await self.snapshot(item, refresh=refresh) for item in ids])

    async def refresh(self, harness_id: str | None = None) -> tuple[QuotaSnapshot, ...]:
        if harness_id:
            self._cache.invalidate(harness_id)
            return (await self.snapshot(harness_id, refresh=True),)
        self._cache.invalidate()
        return await self.list_snapshots(refresh=True)

    async def observe_execution(
        self,
        harness_id: str,
        *,
        success: bool,
        failure_kind: FailureKind | None = None,
        detail: str | None = None,
        usage_tokens: int | None = None,
    ) -> QuotaSnapshot:
        """Update quota from an execution outcome.

        Successful executions refresh the cache with a normal TTL.
        Failures refresh the cache immediately with the observed failure state.
        """
        provider = self.provider_for(harness_id)
        prior = self._cache.get(harness_id)
        if prior is None:
            try:
                prior = await async_quota_snapshot(provider)
            except Exception:
                prior = QuotaSnapshot(
                    harness_id=harness_id,
                    availability=HarnessAvailability.UNKNOWN,
                    quota_visibility=QuotaVisibility.UNKNOWN,
                    state=QuotaState.UNKNOWN,
                    authenticated=False,
                    configured=False,
                    credits_remaining=None,
                    requests_remaining=None,
                    tokens_remaining=None,
                    reset_at=None,
                    observed_at=utc_now(),
                    source=QuotaSource.NONE,
                    raw_metadata={},
                )
        updated = provider.apply_observation(
            prior,
            success=success,
            failure_kind=failure_kind,
            detail=detail,
            usage_tokens=usage_tokens,
        )
        if success:
            self._cache.put(updated)
        else:
            # Failure refreshes immediately: replace any stale READY entry now.
            self._cache.invalidate(harness_id)
            self._cache.put(updated)
        return updated

    def routing_adjustment(
        self,
        snapshot: QuotaSnapshot,
        *,
        explicit_request: bool,
    ) -> tuple[bool, float, str]:
        """Return (hard_block, score_delta, reason)."""
        score = AVAILABILITY_SCORE.get(snapshot.availability, 0.0)
        score += QUOTA_STATE_SCORE.get(snapshot.state, 0.0)
        blocked = snapshot.availability in AUTO_BLOCKED_AVAILABILITIES
        if blocked and not explicit_request:
            return True, score, f"quota {snapshot.availability.value}"
        if blocked and explicit_request:
            return False, score, f"quota {snapshot.availability.value} (explicit override)"
        return False, score, f"quota {snapshot.availability.value}"

    def format_table(self, snapshots: Iterable[QuotaSnapshot]) -> str:
        rows = list(snapshots)
        if not rows:
            return "No harness quota data."
        name_width = max(len("Harness"), *(len(_display_name(item.harness_id)) for item in rows))
        lines = [f"{'Harness'.ljust(name_width)}  Status", "-" * (name_width + 2 + 24)]
        for item in rows:
            status = f"{item.display_mark} {item.display_status}"
            lines.append(f"{_display_name(item.harness_id).ljust(name_width)}  {status}")
        return "\n".join(lines)

    def as_json(self, snapshots: Iterable[QuotaSnapshot]) -> list[dict[str, Any]]:
        return [item.as_dict() for item in snapshots]


def _display_name(harness_id: str) -> str:
    mapping = {
        "opencode": "OpenCode",
        "claude-code": "Claude",
        "codex": "Codex",
        "gemini-cli": "Gemini",
        "grok": "Grok",
    }
    return mapping.get(harness_id, harness_id)
