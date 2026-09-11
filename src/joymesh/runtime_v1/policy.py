"""Connector-independent policy profiles and evaluation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from joymesh.runtime_v1.capabilities import (
    MUTATING_CAPABILITIES,
    READ_ONLY_CAPABILITIES,
    CapabilityRegistry,
    expand_capabilities,
)
from joymesh.runtime_v1.models import PolicyDecision, RouteCandidate, RuntimeTaskRequest


@dataclass(frozen=True)
class PolicyProfile:
    profile_id: str
    description: str
    allowed: frozenset[str]
    denied: frozenset[str]
    require_node_attested: bool
    require_noninteractive_auth: bool
    enabled: bool
    approval_for: frozenset[str] = frozenset()


_PROFILES: dict[str, PolicyProfile] = {
    "read_only": PolicyProfile(
        profile_id="read_only",
        description="Read-only repository analysis without mutation or shell",
        allowed=READ_ONLY_CAPABILITIES,
        denied=MUTATING_CAPABILITIES | frozenset({"session.resume", "session.fork"}),
        require_node_attested=True,
        require_noninteractive_auth=False,
        enabled=True,
    ),
    "developer": PolicyProfile(
        profile_id="developer",
        description="Local developer work with explicit approvals for mutation",
        allowed=READ_ONLY_CAPABILITIES
        | frozenset(
            {
                "repository.write",
                "repository.patch",
                "filesystem.write",
                "shell.execute",
                "git.read",
                "git.diff",
                "test.execute",
                "build.execute",
            }
        ),
        denied=frozenset({"git.push", "network.write"}),
        require_node_attested=True,
        require_noninteractive_auth=False,
        enabled=True,
        approval_for=frozenset(
            {
                "repository.write",
                "repository.patch",
                "filesystem.write",
                "shell.execute",
                "test.execute",
                "build.execute",
            }
        ),
    ),
    "ci": PolicyProfile(
        profile_id="ci",
        description="Deterministic CI with non-interactive authentication",
        allowed=READ_ONLY_CAPABILITIES
        | frozenset({"test.execute", "build.execute", "shell.execute", "git.read", "git.diff"}),
        denied=frozenset({"git.push", "git.commit", "network.write", "session.resume"}),
        require_node_attested=True,
        require_noninteractive_auth=True,
        enabled=True,
        approval_for=frozenset({"test.execute", "build.execute", "shell.execute"}),
    ),
    "production_restricted": PolicyProfile(
        profile_id="production_restricted",
        description="Production-safe restricted capabilities with node-attested evidence",
        allowed=READ_ONLY_CAPABILITIES,
        denied=MUTATING_CAPABILITIES | frozenset({"session.resume", "browser.use"}),
        require_node_attested=True,
        require_noninteractive_auth=False,
        enabled=True,
    ),
    "autonomous": PolicyProfile(
        profile_id="autonomous",
        description="Autonomous multi-step work (disabled by default)",
        allowed=frozenset(),
        denied=frozenset(),
        require_node_attested=True,
        require_noninteractive_auth=False,
        enabled=False,
    ),
}


class PolicyEngine:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        profiles: dict[str, PolicyProfile] | None = None,
        autonomous_enabled: bool | None = None,
    ) -> None:
        self.registry = registry or CapabilityRegistry()
        self.profiles = profiles or dict(_PROFILES)
        if autonomous_enabled is None:
            autonomous_enabled = os.environ.get("JOYMESH_AUTONOMOUS_POLICY", "0") == "1"
        if autonomous_enabled and "autonomous" in self.profiles:
            profile = self.profiles["autonomous"]
            self.profiles["autonomous"] = PolicyProfile(
                profile_id=profile.profile_id,
                description=profile.description,
                allowed=frozenset(item.capability_id for item in self.registry.all()),
                denied=frozenset(),
                require_node_attested=True,
                require_noninteractive_auth=False,
                enabled=True,
            )

    def list_profiles(self) -> tuple[PolicyProfile, ...]:
        return tuple(self.profiles[key] for key in sorted(self.profiles))

    def get(self, profile_id: str) -> PolicyProfile:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"unknown policy profile: {profile_id}") from exc

    def evaluate(
        self,
        request: RuntimeTaskRequest,
        candidate: RouteCandidate | None = None,
    ) -> PolicyDecision:
        profile = self.get(request.policy_profile)
        reasons: list[str] = []
        if not profile.enabled:
            return PolicyDecision(
                allowed=False,
                granted_capabilities=frozenset(),
                denied_capabilities=request.requested_capabilities,
                approval_requirements=(),
                reasons=(f"policy {profile.profile_id} is disabled",),
            )
        try:
            expanded = expand_capabilities(
                request.requested_capabilities,
                prohibited=request.prohibited_capabilities,
                registry=self.registry,
            )
        except ValueError as exc:
            return PolicyDecision(
                allowed=False,
                granted_capabilities=frozenset(),
                denied_capabilities=request.requested_capabilities,
                approval_requirements=(),
                reasons=(str(exc),),
            )

        denied = frozenset(
            item for item in expanded if item in profile.denied or item not in profile.allowed
        )
        if denied:
            reasons.append("policy rejected capabilities: " + ", ".join(sorted(denied)))
            return PolicyDecision(
                allowed=False,
                granted_capabilities=frozenset(),
                denied_capabilities=denied,
                approval_requirements=(),
                reasons=tuple(reasons),
            )

        if candidate is not None:
            missing = expanded - candidate.certified_capabilities
            if missing:
                reasons.append("capabilities not certified: " + ", ".join(sorted(missing)))
                return PolicyDecision(
                    allowed=False,
                    granted_capabilities=frozenset(),
                    denied_capabilities=missing,
                    approval_requirements=(),
                    reasons=tuple(reasons),
                )

        approvals = tuple(sorted(profile.approval_for & expanded))
        if approvals:
            reasons.append("explicit approval required for: " + ", ".join(approvals))
        reasons.append(f"policy {profile.profile_id} allowed expanded capabilities")
        return PolicyDecision(
            allowed=True,
            granted_capabilities=expanded,
            denied_capabilities=frozenset(),
            approval_requirements=approvals,
            reasons=tuple(reasons),
        )
