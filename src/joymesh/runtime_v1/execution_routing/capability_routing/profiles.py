"""Static capability profiles for harnesses, connectors, and models.

These are routing hints — catalogue YAML and certification remain authoritative
for install/runtime readiness. Profiles must not invent a third catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import SemanticCapability


@dataclass(frozen=True)
class HarnessProfile:
    harness_id: str
    capabilities: frozenset[SemanticCapability]
    latency: str = "medium"  # low | medium | high
    reliability: str = "high"  # low | medium | high
    quality: float = 0.7  # 0..1
    cost_bias: float = 0.5  # higher = more expensive tendency
    local_capable: bool = True
    remote_capable: bool = True
    notes: str = ""


@dataclass(frozen=True)
class ConnectorProfile:
    connector_id: str
    provider_family: str
    available_models: tuple[str, ...] = ()
    authenticated: bool = True
    quota_remaining_fraction: float | None = None  # None = unknown
    pricing_tier: str = "standard"  # free | cheap | standard | premium
    latency: str = "medium"
    privacy: str = "remote"  # local | remote | hybrid
    health: float = 1.0  # 0..1
    local: bool = False
    rate_limited: bool = False


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    coding: float = 0.5
    reasoning: float = 0.5
    vision: float = 0.0
    tool_use: float = 0.5
    context_length: int = 128_000
    cost: float = 0.5  # relative 0..1
    latency: float = 0.5  # relative 0..1 (higher = slower)
    multilingual: float = 0.5
    open_weights: bool = False
    local: bool = False


@dataclass(frozen=True)
class CapabilityProfiles:
    harnesses: dict[str, HarnessProfile] = field(default_factory=dict)
    connectors: dict[str, ConnectorProfile] = field(default_factory=dict)
    models: dict[str, ModelProfile] = field(default_factory=dict)


def builtin_capability_profiles() -> CapabilityProfiles:
    harnesses = {
        "codex": HarnessProfile(
            harness_id="codex",
            capabilities=frozenset(
                {
                    SemanticCapability.REPOSITORY_EDITING,
                    SemanticCapability.AUTONOMOUS_CODING,
                    SemanticCapability.TERMINAL,
                    SemanticCapability.TESTING,
                    SemanticCapability.LONG_RUNNING,
                    SemanticCapability.PATCH_GENERATION,
                    SemanticCapability.LONG_CONTEXT,
                }
            ),
            latency="medium",
            reliability="high",
            quality=0.9,
            cost_bias=0.7,
            notes="Strong autonomous repository editing",
        ),
        "cursor": HarnessProfile(
            harness_id="cursor",
            capabilities=frozenset(
                {
                    SemanticCapability.INTERACTIVE_EDITING,
                    SemanticCapability.IDE_WORKFLOW,
                    SemanticCapability.PAIR_PROGRAMMING,
                    SemanticCapability.REPOSITORY_EDITING,
                    SemanticCapability.LOW_LATENCY,
                }
            ),
            latency="low",
            reliability="high",
            quality=0.85,
            cost_bias=0.6,
            notes="Interactive IDE workflow",
        ),
        "opencode": HarnessProfile(
            harness_id="opencode",
            capabilities=frozenset(
                {
                    SemanticCapability.PROVIDER_FLEXIBLE,
                    SemanticCapability.OPEN_MODEL_SUPPORT,
                    SemanticCapability.SCRIPTING,
                    SemanticCapability.AUTONOMOUS_CODING,
                    SemanticCapability.TERMINAL,
                    SemanticCapability.REPOSITORY_EDITING,
                    SemanticCapability.COST_SENSITIVE,
                }
            ),
            latency="medium",
            reliability="medium",
            quality=0.75,
            cost_bias=0.3,
            notes="Provider-flexible open-model friendly",
        ),
        "claude": HarnessProfile(
            harness_id="claude",
            capabilities=frozenset(
                {
                    SemanticCapability.REPOSITORY_EDITING,
                    SemanticCapability.AUTONOMOUS_CODING,
                    SemanticCapability.REASONING,
                    SemanticCapability.LONG_CONTEXT,
                    SemanticCapability.TERMINAL,
                }
            ),
            latency="medium",
            reliability="high",
            quality=0.92,
            cost_bias=0.75,
        ),
        "grok": HarnessProfile(
            harness_id="grok",
            capabilities=frozenset(
                {
                    SemanticCapability.AUTONOMOUS_CODING,
                    SemanticCapability.REASONING,
                    SemanticCapability.PROVIDER_FLEXIBLE,
                }
            ),
            latency="medium",
            reliability="medium",
            quality=0.7,
            cost_bias=0.5,
        ),
        "vscode": HarnessProfile(
            harness_id="vscode",
            capabilities=frozenset(
                {
                    SemanticCapability.IDE_WORKFLOW,
                    SemanticCapability.INTERACTIVE_EDITING,
                }
            ),
            latency="low",
            reliability="medium",
            quality=0.6,
            cost_bias=0.2,
            remote_capable=False,
        ),
    }
    connectors = {
        "openai": ConnectorProfile(
            connector_id="openai",
            provider_family="openai",
            available_models=("gpt-5", "gpt-4.1", "o3"),
            pricing_tier="premium",
            latency="medium",
            privacy="remote",
            health=1.0,
            quota_remaining_fraction=0.8,
        ),
        "anthropic": ConnectorProfile(
            connector_id="anthropic",
            provider_family="anthropic",
            available_models=("claude-sonnet", "claude-opus"),
            pricing_tier="premium",
            latency="medium",
            privacy="remote",
            health=1.0,
            quota_remaining_fraction=0.8,
        ),
        "fireconnect": ConnectorProfile(
            connector_id="fireconnect",
            provider_family="fireconnect",
            available_models=("qwen", "gpt-4.1", "deepseek"),
            pricing_tier="cheap",
            latency="medium",
            privacy="remote",
            health=0.95,
            quota_remaining_fraction=0.9,
        ),
        "openrouter": ConnectorProfile(
            connector_id="openrouter",
            provider_family="openrouter",
            available_models=("qwen", "deepseek", "llama"),
            pricing_tier="cheap",
            latency="medium",
            privacy="remote",
            health=0.9,
        ),
        "lmstudio": ConnectorProfile(
            connector_id="lmstudio",
            provider_family="local",
            available_models=("local-default",),
            pricing_tier="free",
            latency="low",
            privacy="local",
            health=0.85,
            local=True,
            quota_remaining_fraction=1.0,
        ),
        "ollama": ConnectorProfile(
            connector_id="ollama",
            provider_family="local",
            available_models=("local-default", "qwen-local"),
            pricing_tier="free",
            latency="low",
            privacy="local",
            health=0.9,
            local=True,
            quota_remaining_fraction=1.0,
        ),
    }
    models = {
        "gpt-5": ModelProfile(
            model_id="gpt-5",
            coding=0.95,
            reasoning=0.95,
            vision=0.8,
            tool_use=0.95,
            context_length=400_000,
            cost=0.9,
            latency=0.6,
        ),
        "gpt-4.1": ModelProfile(
            model_id="gpt-4.1",
            coding=0.85,
            reasoning=0.8,
            vision=0.7,
            tool_use=0.85,
            cost=0.7,
            latency=0.5,
        ),
        "o3": ModelProfile(
            model_id="o3",
            coding=0.8,
            reasoning=0.98,
            tool_use=0.8,
            cost=0.95,
            latency=0.8,
        ),
        "claude-sonnet": ModelProfile(
            model_id="claude-sonnet",
            coding=0.9,
            reasoning=0.9,
            tool_use=0.9,
            cost=0.75,
            latency=0.5,
        ),
        "claude-opus": ModelProfile(
            model_id="claude-opus",
            coding=0.92,
            reasoning=0.97,
            tool_use=0.9,
            cost=0.95,
            latency=0.7,
        ),
        "qwen": ModelProfile(
            model_id="qwen",
            coding=0.8,
            reasoning=0.75,
            tool_use=0.7,
            cost=0.25,
            latency=0.4,
            open_weights=True,
        ),
        "deepseek": ModelProfile(
            model_id="deepseek",
            coding=0.82,
            reasoning=0.8,
            tool_use=0.7,
            cost=0.2,
            latency=0.45,
            open_weights=True,
        ),
        "local-default": ModelProfile(
            model_id="local-default",
            coding=0.65,
            reasoning=0.6,
            tool_use=0.5,
            cost=0.0,
            latency=0.3,
            open_weights=True,
            local=True,
        ),
        "qwen-local": ModelProfile(
            model_id="qwen-local",
            coding=0.7,
            reasoning=0.65,
            tool_use=0.55,
            cost=0.0,
            latency=0.35,
            open_weights=True,
            local=True,
        ),
        "llama": ModelProfile(
            model_id="llama",
            coding=0.7,
            reasoning=0.7,
            tool_use=0.6,
            cost=0.2,
            latency=0.4,
            open_weights=True,
        ),
    }
    return CapabilityProfiles(harnesses=harnesses, connectors=connectors, models=models)
