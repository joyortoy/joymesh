"""Deterministic harness and subscription routing."""

from __future__ import annotations

from joymesh.models import (
    Capability,
    HarnessAvailability,
    RouteCandidate,
    RoutePreview,
    RoutePreviewRequest,
    SubscriptionProfile,
    SubscriptionState,
)
from joymesh.persistence import Database
from joymesh.registry import AdapterRegistry


class Router:
    def __init__(self, registry: AdapterRegistry, database: Database) -> None:
        self.registry = registry
        self.database = database

    async def preview(self, request: RoutePreviewRequest) -> RoutePreview:
        detected = {item.manifest.harness_id: item for item in await self.registry.detect()}
        subscriptions = await self.database.list_subscriptions()
        by_harness: dict[str, list[SubscriptionProfile]] = {}
        for subscription in subscriptions:
            by_harness.setdefault(subscription.harness_id, []).append(subscription)

        candidates: list[RouteCandidate] = []
        for adapter in self.registry.list():
            harness_id = adapter.manifest.harness_id
            descriptor = detected[harness_id]
            profiles: list[SubscriptionProfile | None] = list(by_harness.get(harness_id, []))
            if not profiles:
                profiles.append(None)
            definition = self.registry.definition(harness_id)
            for profile in profiles:
                candidates.append(
                    await self._candidate(
                        request=request,
                        availability=descriptor.availability,
                        profile=profile,
                        harness_id=harness_id,
                        capabilities=adapter.manifest.capabilities,
                        harness_concurrency=adapter.manifest.max_concurrency,
                        maturity=definition.maturity.value,
                        adapter_certified=adapter.conformance_passed,
                    )
                )

        candidates.sort(
            key=lambda item: (
                not item.eligible,
                -item.score,
                item.harness_id,
                item.subscription_id or "",
            )
        )
        selected = next((item for item in candidates if item.eligible), None)
        return RoutePreview(selected=selected, candidates=tuple(candidates))

    async def _candidate(
        self,
        *,
        request: RoutePreviewRequest,
        availability: HarnessAvailability,
        profile: SubscriptionProfile | None,
        harness_id: str,
        capabilities: frozenset[Capability],
        harness_concurrency: int,
        maturity: str,
        adapter_certified: bool,
    ) -> RouteCandidate:
        reasons: list[str] = []
        eligible = True
        score = 100.0

        if maturity in {"experimental", "discovery_only"}:
            score -= 10
            reasons.append(f"adapter maturity {maturity}")
        else:
            reasons.append(f"adapter maturity {maturity}")
        if adapter_certified:
            reasons.append("adapter conformance passed")
        else:
            score -= 5
            reasons.append("adapter conformance not certified")

        if availability is not HarnessAvailability.AVAILABLE:
            eligible = False
            reasons.append("harness unavailable")

        missing = sorted(
            capability.value for capability in request.required_capabilities - capabilities
        )
        if missing:
            eligible = False
            reasons.append(f"missing capabilities: {', '.join(missing)}")
        else:
            reasons.append("required capabilities satisfied")

        if request.preferred_harness == harness_id:
            score += 25
            reasons.append("user preferred harness")

        if request.allowed_harnesses and harness_id not in request.allowed_harnesses:
            eligible = False
            reasons.append("excluded by harness allowlist")
        if harness_id in request.denied_harnesses:
            eligible = False
            reasons.append("excluded by harness denylist")

        subscription_id = None
        concurrency_limit = harness_concurrency
        if profile is not None:
            subscription_id = profile.id
            concurrency_limit = min(concurrency_limit, profile.max_concurrency)
            if not profile.enabled:
                eligible = False
                reasons.append("subscription disabled")
            if profile.state is SubscriptionState.RATE_LIMITED:
                eligible = False
                reasons.append("subscription rate limited")
            if profile.state is SubscriptionState.EXHAUSTED:
                eligible = False
                reasons.append("subscription exhausted")
            if profile.requires_paid_approval and not request.paid_routes_approved:
                eligible = False
                reasons.append("paid route requires approval")
            elif profile.requires_paid_approval:
                reasons.append("paid route approved")
            remaining = profile.remaining_fraction
            if profile.quota_known and profile.monthly_limit is not None:
                score += (remaining or 0) * 10
                remaining_amount = max(0.0, profile.monthly_limit - profile.used_amount)
                if remaining_amount <= profile.quota_reserve:
                    eligible = False
                    reasons.append("configured quota reserve reached")
                else:
                    reasons.append(f"manual quota {(remaining or 0):.0%} remaining")
            elif not profile.quota_known:
                score -= 15
                reasons.append("unknown quota uncertainty penalty")
            score -= profile.cost_weight
            reasons.append(f"cost weight {profile.cost_weight:g}")
        else:
            eligible = False
            score -= 30
            reasons.append("no funding profile")

        active = await self.database.active_count(
            harness_id=harness_id, subscription_id=subscription_id
        )
        if active >= concurrency_limit:
            eligible = False
            reasons.append(f"concurrency limit reached ({active}/{concurrency_limit})")
        else:
            reasons.append(f"concurrency available ({active}/{concurrency_limit})")

        return RouteCandidate(
            harness_id=harness_id,
            subscription_id=subscription_id,
            score=round(score, 4),
            eligible=eligible,
            reasons=tuple(reasons),
        )
