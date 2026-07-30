"""Deterministic multi-node route scheduler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from joymesh.connectors.lifecycle_models import (
    ConnectorExecutionOrigin,
    EvidenceTrustLevel,
    NodeConnectorState,
)
from joymesh.control_plane.security import production_mode
from joymesh.runtime_v1.models import (
    CertifiedCapability,
    RouteCandidate,
    RuntimeTaskRequest,
    WorkspacePlacement,
)
from joymesh.runtime_v1.policy import PolicyEngine


@dataclass(frozen=True)
class SchedulerNodeSnapshot:
    node_id: str
    online: bool
    revoked: bool
    session_authenticated: bool
    connectors: Mapping[str, SchedulerConnectorSnapshot]
    placements: tuple[WorkspacePlacement, ...]
    concurrency: int = 0
    queue_depth: int = 0
    recent_failures: int = 0


@dataclass(frozen=True)
class SchedulerConnectorSnapshot:
    connector_id: str
    installed: bool
    readiness: NodeConnectorState
    authenticated: bool
    routing_enabled: bool
    certified_capabilities: frozenset[str]
    trust_level: EvidenceTrustLevel | None
    execution_origin: ConnectorExecutionOrigin | None
    certified: tuple[CertifiedCapability, ...] = ()


class RuntimeScheduler:
    def __init__(self, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()

    def rank_candidates(
        self,
        request: RuntimeTaskRequest,
        nodes: Sequence[SchedulerNodeSnapshot],
    ) -> list[RouteCandidate]:
        base = self.policy.evaluate(request)
        if not base.allowed:
            return [
                RouteCandidate(
                    node_id="*",
                    connector_id="*",
                    policy_profile=request.policy_profile,
                    certified_capabilities=frozenset(),
                    score=0.0,
                    eligible=False,
                    rejection_reasons=base.reasons,
                    scoring_factors={},
                )
            ]

        required = base.granted_capabilities
        candidates: list[RouteCandidate] = []
        for node in sorted(nodes, key=lambda item: item.node_id):
            for connector_id in sorted(node.connectors):
                connector = node.connectors[connector_id]
                reasons: list[str] = []
                factors: dict[str, float] = {}
                if request.required_node and node.node_id != request.required_node:
                    reasons.append("required node mismatch")
                if request.required_connector and connector_id != request.required_connector:
                    reasons.append("required connector mismatch")
                if node.revoked:
                    reasons.append("node revoked")
                if not node.online:
                    reasons.append("node offline")
                if not node.session_authenticated:
                    reasons.append("node session not authenticated")
                if not connector.installed:
                    reasons.append("connector not installed")
                if connector.readiness is not NodeConnectorState.READY:
                    reasons.append(f"connector readiness is {connector.readiness.value}")
                if not connector.authenticated:
                    reasons.append("connector authentication invalid")
                if not connector.routing_enabled:
                    reasons.append("routing disabled")
                missing = required - connector.certified_capabilities
                if missing:
                    reasons.append("capabilities not certified: " + ", ".join(sorted(missing)))
                if production_mode():
                    if connector.trust_level is not EvidenceTrustLevel.NODE_ATTESTED:
                        reasons.append("production requires node-attested evidence")
                    if connector.execution_origin is not ConnectorExecutionOrigin.REMOTE_NODE:
                        reasons.append("production requires remote_node execution origin")
                placement = _placement_for(node, request.workspace_id)
                if placement is None:
                    reasons.append("workspace placement missing")
                else:
                    if not placement.writable and _needs_write(required):
                        reasons.append("read-only workspace rejects write capabilities")
                decision = self.policy.evaluate(
                    request,
                    RouteCandidate(
                        node_id=node.node_id,
                        connector_id=connector_id,
                        policy_profile=request.policy_profile,
                        certified_capabilities=connector.certified_capabilities,
                        score=0.0,
                        eligible=False,
                        rejection_reasons=(),
                        scoring_factors={},
                    ),
                )
                if not decision.allowed:
                    reasons.extend(decision.reasons)

                score = 100.0
                factors["base"] = 100.0
                factors["concurrency"] = -float(node.concurrency) * 2.0
                factors["queue_depth"] = -float(node.queue_depth)
                factors["recent_failures"] = -float(node.recent_failures) * 3.0
                if request.preferred_connectors and connector_id in request.preferred_connectors:
                    preference = 10.0 - request.preferred_connectors.index(connector_id)
                    factors["preferred_connector"] = preference
                if request.preferred_nodes and node.node_id in request.preferred_nodes:
                    preference = 8.0 - request.preferred_nodes.index(node.node_id)
                    factors["preferred_node"] = preference
                if placement is not None:
                    factors["workspace_locality"] = 15.0
                score += sum(factors.values()) - factors["base"]
                eligible = not reasons
                candidates.append(
                    RouteCandidate(
                        node_id=node.node_id,
                        connector_id=connector_id,
                        policy_profile=request.policy_profile,
                        certified_capabilities=connector.certified_capabilities,
                        score=score if eligible else 0.0,
                        eligible=eligible,
                        rejection_reasons=tuple(reasons),
                        scoring_factors=factors if eligible else {},
                    )
                )

        candidates.sort(
            key=lambda item: (
                0 if item.eligible else 1,
                -item.score,
                item.node_id,
                item.connector_id,
            )
        )
        return candidates


def _placement_for(node: SchedulerNodeSnapshot, workspace_id: str) -> WorkspacePlacement | None:
    for placement in node.placements:
        if placement.workspace_id == workspace_id:
            return placement
    return None


def _needs_write(capabilities: frozenset[str]) -> bool:
    return bool(
        capabilities
        & {
            "repository.write",
            "repository.patch",
            "filesystem.write",
            "git.commit",
            "dependency.install",
        }
    )
