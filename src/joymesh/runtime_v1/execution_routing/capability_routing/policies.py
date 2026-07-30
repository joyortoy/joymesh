"""User and organisation routing policies — influence score, never bypass capability needs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class RoutingPolicyPreset(StrEnum):
    PREFER_LOCAL = "prefer_local"
    PREFER_CHEAPEST = "prefer_cheapest"
    PREFER_FASTEST = "prefer_fastest"
    PREFER_STRONGEST_REASONING = "prefer_strongest_reasoning"
    PREFER_OPEN_MODELS = "prefer_open_models"
    AVOID_PAID_APIS = "avoid_paid_apis"
    MAXIMIZE_QUALITY = "maximize_quality"
    BALANCED = "balanced"


@dataclass(frozen=True)
class RoutingPolicy:
    preset: RoutingPolicyPreset = RoutingPolicyPreset.BALANCED
    prefer_local: bool = False
    prefer_cheapest: bool = False
    prefer_fastest: bool = False
    prefer_strongest_reasoning: bool = False
    prefer_open_models: bool = False
    avoid_paid_apis: bool = False
    maximize_quality: bool = False
    max_cost: float | None = None
    denied_harnesses: tuple[str, ...] = ()
    denied_connectors: tuple[str, ...] = ()
    denied_models: tuple[str, ...] = ()
    preferred_harnesses: tuple[str, ...] = ()
    preferred_connectors: tuple[str, ...] = ()
    preferred_models: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> RoutingPolicy:
        data = dict(raw or {})
        preset_raw = str(data.get("preset") or data.get("policy") or "balanced")
        try:
            preset = RoutingPolicyPreset(preset_raw)
        except ValueError:
            # Map MissionSpec cost_preference / locality into presets.
            if preset_raw in {"cheapest", "prefer_cheapest"}:
                preset = RoutingPolicyPreset.PREFER_CHEAPEST
            elif preset_raw in {"fastest", "prefer_fastest"}:
                preset = RoutingPolicyPreset.PREFER_FASTEST
            elif preset_raw in {"local", "prefer_local"}:
                preset = RoutingPolicyPreset.PREFER_LOCAL
            elif preset_raw in {"quality", "maximize_quality"}:
                preset = RoutingPolicyPreset.MAXIMIZE_QUALITY
            else:
                preset = RoutingPolicyPreset.BALANCED

        prefer_local = bool(data.get("prefer_local")) or preset is RoutingPolicyPreset.PREFER_LOCAL
        prefer_cheapest = (
            bool(data.get("prefer_cheapest")) or preset is RoutingPolicyPreset.PREFER_CHEAPEST
        )
        prefer_fastest = (
            bool(data.get("prefer_fastest")) or preset is RoutingPolicyPreset.PREFER_FASTEST
        )
        prefer_strongest = (
            bool(data.get("prefer_strongest_reasoning"))
            or preset is RoutingPolicyPreset.PREFER_STRONGEST_REASONING
        )
        prefer_open = (
            bool(data.get("prefer_open_models"))
            or preset is RoutingPolicyPreset.PREFER_OPEN_MODELS
        )
        avoid_paid = (
            bool(data.get("avoid_paid_apis")) or preset is RoutingPolicyPreset.AVOID_PAID_APIS
        )
        maximize_quality = (
            bool(data.get("maximize_quality")) or preset is RoutingPolicyPreset.MAXIMIZE_QUALITY
        )
        return cls(
            preset=preset,
            prefer_local=prefer_local,
            prefer_cheapest=prefer_cheapest,
            prefer_fastest=prefer_fastest,
            prefer_strongest_reasoning=prefer_strongest,
            prefer_open_models=prefer_open,
            avoid_paid_apis=avoid_paid,
            maximize_quality=maximize_quality,
            max_cost=float(data["max_cost"]) if data.get("max_cost") is not None else None,
            denied_harnesses=tuple(data.get("denied_harnesses") or ()),
            denied_connectors=tuple(data.get("denied_connectors") or ()),
            denied_models=tuple(data.get("denied_models") or ()),
            preferred_harnesses=tuple(data.get("preferred_harnesses") or ()),
            preferred_connectors=tuple(data.get("preferred_connectors") or ()),
            preferred_models=tuple(data.get("preferred_models") or ()),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset.value,
            "prefer_local": self.prefer_local,
            "prefer_cheapest": self.prefer_cheapest,
            "prefer_fastest": self.prefer_fastest,
            "prefer_strongest_reasoning": self.prefer_strongest_reasoning,
            "prefer_open_models": self.prefer_open_models,
            "avoid_paid_apis": self.avoid_paid_apis,
            "maximize_quality": self.maximize_quality,
            "max_cost": self.max_cost,
            "denied_harnesses": list(self.denied_harnesses),
            "denied_connectors": list(self.denied_connectors),
            "denied_models": list(self.denied_models),
            "preferred_harnesses": list(self.preferred_harnesses),
            "preferred_connectors": list(self.preferred_connectors),
            "preferred_models": list(self.preferred_models),
        }
