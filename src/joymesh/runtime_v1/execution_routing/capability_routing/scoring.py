"""Multi-factor scoring for Route = Harness + Connector + Model (+ backend)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from joymesh.runtime_v1.execution_routing.capability_routing.policies import RoutingPolicy
from joymesh.runtime_v1.execution_routing.capability_routing.profiles import (
    CapabilityProfiles,
    ConnectorProfile,
    HarnessProfile,
    ModelProfile,
)
from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import (
    SemanticCapability,
    TaskAnalysis,
)


@dataclass(frozen=True)
class ScoredRoute:
    """A candidate execution route with explainable score breakdown."""

    harness_id: str
    backend_id: str
    connector_id: str | None
    model_id: str | None
    score: float
    breakdown: dict[str, float]
    reasons: tuple[str, ...] = ()
    eligible: bool = True
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "backend_id": self.backend_id,
            "connector_id": self.connector_id,
            "model_id": self.model_id,
            "score": self.score,
            "breakdown": dict(self.breakdown),
            "reasons": list(self.reasons),
            "eligible": self.eligible,
            "rejection_reason": self.rejection_reason,
            "metadata": dict(self.metadata),
        }


_LATENCY_SCORE = {"low": 1.0, "medium": 0.6, "high": 0.3}
_RELIABILITY_SCORE = {"low": 0.4, "medium": 0.7, "high": 1.0}
_PRICING_COST = {"free": 0.0, "cheap": 0.25, "standard": 0.55, "premium": 0.9}


def harness_capability_match(
    profile: HarnessProfile,
    required: frozenset[SemanticCapability],
) -> tuple[float, frozenset[SemanticCapability]]:
    if not required:
        return 1.0, frozenset()
    missing = required - profile.capabilities
    covered = len(required) - len(missing)
    return covered / len(required), missing


def score_route(
    *,
    harness: HarnessProfile,
    backend_id: str,
    backend_base_score: float,
    task: TaskAnalysis,
    policy: RoutingPolicy,
    connector: ConnectorProfile | None = None,
    model: ModelProfile | None = None,
    subscription_ok: bool = True,
    quota_ok: bool = True,
    backend_priority_boost: float = 0.0,
) -> ScoredRoute:
    """Score one Route candidate. Capability gaps hard-fail (eligible=False)."""

    match, missing = harness_capability_match(harness, task.required_semantic)
    reasons: list[str] = []
    breakdown: dict[str, float] = {}

    soft_classes = {"unknown", "quick_question", "planning", "documentation"}
    if missing:
        # Hard capability requirements must be fully covered except for soft task classes,
        # which allow partial matches when at least half the required set is present.
        allow_partial = task.task_class.value in soft_classes and match >= 0.5
        if not allow_partial and match < 1.0:
            return ScoredRoute(
                harness_id=harness.harness_id,
                backend_id=backend_id,
                connector_id=connector.connector_id if connector else None,
                model_id=model.model_id if model else None,
                score=-1.0,
                breakdown={"capability_match": match},
                reasons=(f"missing_capabilities:{','.join(sorted(m.value for m in missing))}",),
                eligible=False,
                rejection_reason="capability_mismatch",
            )

    if harness.harness_id in policy.denied_harnesses:
        return _reject(harness, backend_id, connector, model, "harness_denied")
    if connector and connector.connector_id in policy.denied_connectors:
        return _reject(harness, backend_id, connector, model, "connector_denied")
    if model and model.model_id in policy.denied_models:
        return _reject(harness, backend_id, connector, model, "model_denied")

    if not subscription_ok:
        return _reject(harness, backend_id, connector, model, "subscription_unavailable")
    if not quota_ok:
        return _reject(harness, backend_id, connector, model, "quota_exhausted")

    if task.privacy_required or policy.prefer_local or SemanticCapability.LOCAL_ONLY in task.required_semantic:
        if connector and not connector.local and connector.privacy != "local":
            return _reject(harness, backend_id, connector, model, "privacy_requires_local")
        if backend_id not in {"local", "joymesh"} and policy.prefer_local:
            # Prefer local backends but do not hard-fail remote if no local connector yet.
            reasons.append("privacy_prefers_local_backend")

    if policy.avoid_paid_apis and connector and connector.pricing_tier in {"standard", "premium"}:
        return _reject(harness, backend_id, connector, model, "avoid_paid_apis")

    if policy.max_cost is not None and model is not None and model.cost > policy.max_cost:
        return _reject(harness, backend_id, connector, model, "max_cost_exceeded")

    # --- scoring (weights sum roughly to ~100 base) ---
    capability = match * 40.0
    breakdown["capability_match"] = capability
    reasons.append(f"capability_match={match:.2f}")

    sub_score = 10.0 if subscription_ok else 0.0
    breakdown["subscription"] = sub_score

    quota_score = 10.0
    if connector and connector.quota_remaining_fraction is not None:
        quota_score = 10.0 * float(connector.quota_remaining_fraction)
    breakdown["quota"] = quota_score if quota_ok else 0.0

    quality = harness.quality * 15.0
    if model is not None:
        quality += (
            model.coding * 8.0
            + model.reasoning * 8.0
            + model.tool_use * 4.0
        )
        if SemanticCapability.VISION in task.required_semantic:
            quality += model.vision * 6.0
        if SemanticCapability.REASONING in task.required_semantic:
            quality += model.reasoning * 5.0
    breakdown["model_quality"] = quality

    health = 5.0
    if connector is not None:
        health = 10.0 * float(connector.health)
        if connector.rate_limited:
            health -= 5.0
    breakdown["connector_health"] = health

    latency_component = _LATENCY_SCORE.get(harness.latency, 0.5) * 8.0
    if connector is not None:
        latency_component += _LATENCY_SCORE.get(connector.latency, 0.5) * 4.0
    if model is not None:
        latency_component += (1.0 - model.latency) * 6.0
    breakdown["latency"] = latency_component

    cost_penalty = harness.cost_bias * 8.0
    if connector is not None:
        cost_penalty += _PRICING_COST.get(connector.pricing_tier, 0.5) * 8.0
    if model is not None:
        cost_penalty += model.cost * 10.0
    breakdown["cost_penalty"] = -cost_penalty

    reliability = _RELIABILITY_SCORE.get(harness.reliability, 0.7) * 8.0
    breakdown["reliability"] = reliability

    backend_score = backend_base_score * 0.15 + backend_priority_boost
    breakdown["backend"] = backend_score

    # Policy adjustments (never override capability eligibility).
    policy_bonus = 0.0
    if policy.prefer_local:
        if connector and connector.local:
            policy_bonus += 20.0
            reasons.append("policy:prefer_local")
        elif backend_id == "local":
            policy_bonus += 10.0
    if policy.prefer_cheapest or SemanticCapability.COST_SENSITIVE in task.required_semantic:
        policy_bonus += max(0.0, 15.0 - cost_penalty)
        reasons.append("policy:prefer_cheapest")
    if policy.prefer_fastest:
        policy_bonus += latency_component * 0.5
        reasons.append("policy:prefer_fastest")
    if policy.prefer_strongest_reasoning and model is not None:
        policy_bonus += model.reasoning * 20.0
        reasons.append("policy:prefer_strongest_reasoning")
    if policy.prefer_open_models and model is not None and model.open_weights:
        policy_bonus += 15.0
        reasons.append("policy:prefer_open_models")
    if policy.maximize_quality:
        policy_bonus += quality * 0.35
        reasons.append("policy:maximize_quality")
    if harness.harness_id in policy.preferred_harnesses:
        policy_bonus += 12.0
    if connector and connector.connector_id in policy.preferred_connectors:
        policy_bonus += 10.0
    if model and model.model_id in policy.preferred_models:
        policy_bonus += 10.0
    breakdown["policy"] = policy_bonus

    if SemanticCapability.OPEN_MODEL_SUPPORT in task.required_semantic:
        if model and model.open_weights:
            policy_bonus += 8.0
        elif connector and connector.pricing_tier in {"free", "cheap"}:
            policy_bonus += 4.0

    total = (
        capability
        + sub_score
        + (quota_score if quota_ok else 0.0)
        + quality
        + health
        + latency_component
        - cost_penalty
        + reliability
        + backend_score
        + policy_bonus
    )
    breakdown["total"] = total

    return ScoredRoute(
        harness_id=harness.harness_id,
        backend_id=backend_id,
        connector_id=connector.connector_id if connector else None,
        model_id=model.model_id if model else None,
        score=total,
        breakdown=breakdown,
        reasons=tuple(reasons),
        eligible=True,
    )


def _reject(
    harness: HarnessProfile,
    backend_id: str,
    connector: ConnectorProfile | None,
    model: ModelProfile | None,
    reason: str,
) -> ScoredRoute:
    return ScoredRoute(
        harness_id=harness.harness_id,
        backend_id=backend_id,
        connector_id=connector.connector_id if connector else None,
        model_id=model.model_id if model else None,
        score=-1.0,
        breakdown={},
        reasons=(reason,),
        eligible=False,
        rejection_reason=reason,
    )


def rank_routes(routes: list[ScoredRoute]) -> list[ScoredRoute]:
    eligible = [r for r in routes if r.eligible]
    ineligible = [r for r in routes if not r.eligible]
    eligible.sort(
        key=lambda r: (
            -r.score,
            r.harness_id,
            r.backend_id,
            r.connector_id or "",
            r.model_id or "",
        )
    )
    return eligible + ineligible


def score_harness_only(
    *,
    harness: HarnessProfile,
    task: TaskAnalysis,
    policy: RoutingPolicy,
    profiles: CapabilityProfiles | None = None,
) -> ScoredRoute:
    """Score harness fitness before backend expansion (capability gate)."""
    _ = profiles
    return score_route(
        harness=harness,
        backend_id="*",
        backend_base_score=50.0,
        task=task,
        policy=policy,
        connector=None,
        model=None,
    )


_BACKEND_CONNECTOR_AFFINITY: dict[str, tuple[str, ...]] = {
    "fireconnect": ("fireconnect", "openrouter", "openai", "anthropic"),
    "local": ("lmstudio", "ollama", "openai", "anthropic", "openrouter"),
    "joymesh": ("openai", "anthropic", "fireconnect", "openrouter"),
}


def best_connector_model_for_harness(
    *,
    harness: HarnessProfile,
    task: TaskAnalysis,
    policy: RoutingPolicy,
    profiles: CapabilityProfiles,
    backend_id: str,
    backend_base_score: float,
    available_connectors: tuple[str, ...] | None = None,
) -> ScoredRoute | None:
    """Pick best connector+model pair for a harness/backend, or harness-only if none."""
    connectors = list(profiles.connectors.values())
    if available_connectors is not None:
        allowed = set(available_connectors)
        connectors = [c for c in connectors if c.connector_id in allowed]
    affinity = _BACKEND_CONNECTOR_AFFINITY.get(backend_id)
    if affinity is not None:
        preferred = {cid for cid in affinity}
        # Soft filter: keep affinity connectors first; if none available keep all.
        affinity_connectors = [c for c in connectors if c.connector_id in preferred]
        if affinity_connectors:
            connectors = affinity_connectors

    candidates: list[ScoredRoute] = []
    if not connectors:
        return score_route(
            harness=harness,
            backend_id=backend_id,
            backend_base_score=backend_base_score,
            task=task,
            policy=policy,
        )

    for connector in connectors:
        models = connector.available_models or ("",)
        for model_id in models:
            model = profiles.models.get(model_id) if model_id else None
            scored = score_route(
                harness=harness,
                backend_id=backend_id,
                backend_base_score=backend_base_score,
                task=task,
                policy=policy,
                connector=connector,
                model=model,
            )
            # Affinity boost for preferred connector ordering.
            if affinity and connector.connector_id in affinity:
                boost = 5.0 * (len(affinity) - affinity.index(connector.connector_id))
                if scored.eligible:
                    scored = ScoredRoute(
                        harness_id=scored.harness_id,
                        backend_id=scored.backend_id,
                        connector_id=scored.connector_id,
                        model_id=scored.model_id,
                        score=scored.score + boost,
                        breakdown={**scored.breakdown, "backend_affinity": boost},
                        reasons=(*scored.reasons, "backend_connector_affinity"),
                        eligible=True,
                    )
            candidates.append(scored)
    ranked = rank_routes(candidates)
    return ranked[0] if ranked and ranked[0].eligible else None


def explain_top(routes: list[ScoredRoute], *, limit: int = 5) -> list[Mapping[str, Any]]:
    return [r.as_dict() for r in rank_routes(routes)[:limit]]
