"""Capability-aware route selection: Task → Analysis → scored Route candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from joymesh.runtime_v1.execution_routing.capability_routing.policies import RoutingPolicy
from joymesh.runtime_v1.execution_routing.capability_routing.profiles import (
    CapabilityProfiles,
    HarnessProfile,
    builtin_capability_profiles,
)
from joymesh.runtime_v1.execution_routing.capability_routing.scoring import (
    ScoredRoute,
    best_connector_model_for_harness,
    rank_routes,
    score_harness_only,
)
from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import (
    SemanticCapability,
    TaskAnalysis,
    TaskAnalyzer,
)


@dataclass(frozen=True)
class RouteSelection:
    analysis: TaskAnalysis
    policy: RoutingPolicy
    selected: ScoredRoute | None
    candidates: tuple[ScoredRoute, ...]
    harness_order: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis.as_dict(),
            "policy": self.policy.as_dict(),
            "selected": self.selected.as_dict() if self.selected else None,
            "candidates": [c.as_dict() for c in self.candidates[:20]],
            "harness_order": list(self.harness_order),
        }


@dataclass
class CapabilityAwareRouteSelector:
    """Selects Route = Harness + Connector + Model given backends already ranked."""

    profiles: CapabilityProfiles = field(default_factory=builtin_capability_profiles)
    analyzer: TaskAnalyzer = field(default_factory=TaskAnalyzer)

    def analyse_prompt(
        self, prompt: str, *, metadata: Mapping[str, Any] | None = None
    ) -> TaskAnalysis:
        return self.analyzer.analyse(prompt, metadata=dict(metadata or {}))

    def order_harnesses(
        self,
        *,
        available_harnesses: Sequence[str],
        analysis: TaskAnalysis,
        policy: RoutingPolicy,
        preferred_harness: str | None = None,
    ) -> list[str]:
        """Capability-first harness ranking; preferred harness wins only if capable."""
        available = [h for h in available_harnesses if h]
        if preferred_harness and preferred_harness in available:
            profile = self.profiles.harnesses.get(preferred_harness)
            if profile is not None:
                scored = score_harness_only(harness=profile, task=analysis, policy=policy)
                if scored.eligible:
                    rest = [h for h in available if h != preferred_harness]
                    return [preferred_harness, *self._rank_harness_ids(rest, analysis, policy)]
            rest = [h for h in available if h != preferred_harness]
            return [preferred_harness, *self._rank_harness_ids(rest, analysis, policy)]
        return self._rank_harness_ids(available, analysis, policy)

    def _rank_harness_ids(
        self,
        harness_ids: Sequence[str],
        analysis: TaskAnalysis,
        policy: RoutingPolicy,
    ) -> list[str]:
        scored: list[tuple[float, str]] = []
        for harness_id in harness_ids:
            profile = self.profiles.harnesses.get(harness_id)
            if profile is None:
                scored.append((50.0, harness_id))
                continue
            result = score_harness_only(harness=profile, task=analysis, policy=policy)
            if not result.eligible:
                continue
            scored.append((result.score, harness_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        ordered = [h for _, h in scored]
        missing = [h for h in harness_ids if h not in ordered]
        return ordered + missing

    def select(
        self,
        *,
        analysis: TaskAnalysis,
        policy: RoutingPolicy,
        available_harnesses: Sequence[str],
        ranked_backends: Sequence[tuple[str, float, str]],
        preferred_harness: str | None = None,
        available_connectors: Sequence[str] | None = None,
        subscription_by_backend: Mapping[str, bool] | None = None,
        quota_by_backend: Mapping[str, bool] | None = None,
    ) -> RouteSelection:
        harness_order = self.order_harnesses(
            available_harnesses=available_harnesses,
            analysis=analysis,
            policy=policy,
            preferred_harness=preferred_harness,
        )
        candidates: list[ScoredRoute] = []
        connectors = tuple(available_connectors) if available_connectors is not None else None

        for harness_id in harness_order:
            profile = self.profiles.harnesses.get(harness_id)
            if profile is None:
                profile = HarnessProfile(
                    harness_id=harness_id,
                    capabilities=frozenset(SemanticCapability),
                    quality=0.5,
                )
            for backend_id, backend_score, _reason in ranked_backends:
                sub_ok = True
                if subscription_by_backend and backend_id in subscription_by_backend:
                    sub_ok = bool(subscription_by_backend[backend_id])
                quota_ok = True
                if quota_by_backend and backend_id in quota_by_backend:
                    quota_ok = bool(quota_by_backend[backend_id])
                if not sub_ok or not quota_ok:
                    continue
                best = best_connector_model_for_harness(
                    harness=profile,
                    task=analysis,
                    policy=policy,
                    profiles=self.profiles,
                    backend_id=backend_id,
                    backend_base_score=backend_score,
                    available_connectors=connectors,
                )
                if best is not None:
                    candidates.append(best)

        ranked = rank_routes(candidates)
        selected = ranked[0] if ranked and ranked[0].eligible else None
        return RouteSelection(
            analysis=analysis,
            policy=policy,
            selected=selected,
            candidates=tuple(ranked),
            harness_order=tuple(harness_order),
        )
