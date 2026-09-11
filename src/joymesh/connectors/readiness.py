"""Authoritative node connector readiness derivation from catalogue and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from joymesh.connectors import ConnectorCatalogue, ConnectorDefinition
from joymesh.connectors.lifecycle_models import (
    ACTIVE_TASK_STATUSES,
    ConnectorEvidenceType,
    ConnectorExecutionOrigin,
    ConnectorReadiness,
    ConnectorTaskStatus,
    EvidenceTrustLevel,
    NodeConnectorState,
    RecommendedConnectorAction,
)
from joymesh.connectors.models import ConnectorExecutionMode, ConnectorMaturity, ConnectorTier
from joymesh.control_plane.security import production_mode
from joymesh.models import utc_now


@dataclass(frozen=True)
class _NodeConnectorSnapshot:
    discovery_executable: str | None
    discovery_version: str | None
    discovery_revision: str | None
    discovery_environment: str | None
    installation_executable: str | None
    installation_version: str | None
    installation_revision: str | None
    installation_routing_enabled: bool
    auth_status: str | None
    auth_verified_at: datetime | None
    auth_method_id: str | None
    certification_valid: bool
    certification_expires_at: datetime | None
    evidence_by_type: dict[ConnectorEvidenceType, dict[str, Any]]
    active_task_status: ConnectorTaskStatus | None
    active_task_id: str | None
    active_task_action: str | None
    latest_evidence_id: str | None
    executable_fingerprint: str | None
    platform: str
    node_online: bool
    node_revoked: bool = False


class ConnectorReadinessService:
    def __init__(self, catalogue: ConnectorCatalogue | None = None) -> None:
        self.catalogue = catalogue or ConnectorCatalogue.builtins()

    def derive_from_snapshot(
        self,
        *,
        node_id: str,
        connector_id: str,
        snapshot: _NodeConnectorSnapshot,
    ) -> ConnectorReadiness:
        definition = self.catalogue.get(connector_id)
        now = utc_now()
        state, action, blocking = self._derive_state(definition, snapshot, now)
        routing = state is NodeConnectorState.READY
        installed_version = (
            snapshot.installation_version
            or snapshot.discovery_version
            or _evidence_version(snapshot, ConnectorEvidenceType.VERSION)
        )
        executable = snapshot.installation_executable or snapshot.discovery_executable
        cert = snapshot.evidence_by_type.get(ConnectorEvidenceType.CERTIFICATION) or {}
        trust = _parse_trust(cert.get("trust_level"))
        origin = _parse_origin(cert.get("execution_origin"))
        raw_profile = cert.get("routing_profile")
        profile = raw_profile if isinstance(raw_profile, str) else None
        if state is NodeConnectorState.READY and profile is None:
            profile = "read_only_repository"
        return ConnectorReadiness(
            node_id=node_id,
            connector_id=connector_id,
            state=state,
            recommended_action=action,
            blocking_reason=blocking,
            active_task_id=snapshot.active_task_id,
            latest_evidence_id=snapshot.latest_evidence_id,
            routing_eligible=routing,
            catalogue_maturity=definition.maturity.value,
            installed_version=installed_version,
            executable_path=executable,
            routing_profile=profile if routing else None,
            evidence_trust_level=trust,
            execution_origin=origin,
            updated_at=now,
        )

    def _derive_state(
        self,
        definition: ConnectorDefinition,
        snapshot: _NodeConnectorSnapshot,
        now: datetime,
    ) -> tuple[NodeConnectorState, RecommendedConnectorAction | None, str | None]:
        if definition.maturity is ConnectorMaturity.BLOCKED:
            return (
                NodeConnectorState.BLOCKED,
                RecommendedConnectorAction.NONE,
                definition.blocked_reason or "Connector is blocked in the catalogue",
            )
        if snapshot.platform not in definition.supported_platforms:
            return (
                NodeConnectorState.UNSUPPORTED_PLATFORM,
                RecommendedConnectorAction.NONE,
                f"Platform {snapshot.platform} is not supported",
            )
        if (
            definition.tier is ConnectorTier.IDE
            or definition.execution.mode is ConnectorExecutionMode.IDE_ONLY
        ):
            return (NodeConnectorState.IDE_ONLY, RecommendedConnectorAction.NONE, None)
        if snapshot.node_revoked:
            return (
                NodeConnectorState.BLOCKED,
                RecommendedConnectorAction.NONE,
                "Node registration is revoked",
            )
        if not snapshot.node_online:
            # Keep evidence-derived state but block readiness for routing.
            pass

        active = snapshot.active_task_status
        active_action = snapshot.active_task_action or ""

        if active in ACTIVE_TASK_STATUSES and "install" in active_action:
            return (NodeConnectorState.INSTALLING, None, None)
        if active in {
            ConnectorTaskStatus.WAITING_FOR_USER,
            ConnectorTaskStatus.WAITING_FOR_AUTH_CALLBACK,
        }:
            return (NodeConnectorState.AUTHENTICATION_IN_PROGRESS, None, None)
        if active in ACTIVE_TASK_STATUSES and active_action == "authenticate":
            return (NodeConnectorState.AUTHENTICATION_IN_PROGRESS, None, None)
        if active in ACTIVE_TASK_STATUSES and active_action in {
            "verify_authentication",
            "verify_adapter",
        }:
            return (NodeConnectorState.VERIFICATION_IN_PROGRESS, None, None)
        if active in ACTIVE_TASK_STATUSES and active_action == "certify":
            return (NodeConnectorState.CERTIFICATION_IN_PROGRESS, None, None)
        if active in ACTIVE_TASK_STATUSES and active_action == "repair":
            return (NodeConnectorState.NEEDS_REPAIR, RecommendedConnectorAction.REPAIR, None)
        if active is ConnectorTaskStatus.FAILED and active_action == "authenticate":
            return (
                NodeConnectorState.AUTHENTICATION_FAILED,
                RecommendedConnectorAction.RETRY,
                snapshot.active_task_id and "Authentication task failed",
            )
        if active is ConnectorTaskStatus.FAILED and active_action == "certify":
            return (
                NodeConnectorState.CERTIFICATION_FAILED,
                RecommendedConnectorAction.RETRY,
                "Certification task failed",
            )

        broken = _broken_executable_evidence(snapshot)
        if broken:
            return (
                NodeConnectorState.NEEDS_REPAIR,
                RecommendedConnectorAction.REPAIR,
                broken,
            )

        executable = snapshot.installation_executable or snapshot.discovery_executable
        has_install_methods = bool(definition.installation_options)

        if not executable and has_install_methods:
            return (
                NodeConnectorState.AVAILABLE_TO_INSTALL,
                RecommendedConnectorAction.INSTALL,
                None,
            )
        if not executable:
            return (
                NodeConnectorState.NOT_AVAILABLE,
                RecommendedConnectorAction.NONE,
                "Executable not discovered on this node",
            )

        if _stale_revision(snapshot, definition.revision):
            return (
                NodeConnectorState.VERIFICATION_REQUIRED,
                RecommendedConnectorAction.VERIFY_ADAPTER,
                "Connector revision changed; evidence must be refreshed",
            )

        auth_required = bool(definition.authentication_methods) and not _auth_not_required(
            definition
        )
        auth_ok = _authentication_verified(snapshot)

        if auth_required and not auth_ok:
            return (
                NodeConnectorState.AUTHENTICATION_REQUIRED,
                RecommendedConnectorAction.AUTHENTICATE,
                None,
            )

        adapter_ok = _adapter_conformance_passed(snapshot)
        if not adapter_ok:
            return (
                NodeConnectorState.VERIFICATION_REQUIRED,
                RecommendedConnectorAction.VERIFY_ADAPTER,
                None,
            )

        cert_ok = _certification_accepted(snapshot, now)
        if not cert_ok:
            return (
                NodeConnectorState.CERTIFICATION_REQUIRED,
                RecommendedConnectorAction.CERTIFY,
                _certification_block_reason(snapshot),
            )

        if not snapshot.installation_routing_enabled:
            return (
                NodeConnectorState.ROUTING_DISABLED,
                RecommendedConnectorAction.ENABLE_ROUTING,
                None,
            )

        if not snapshot.node_online:
            return (
                NodeConnectorState.ROUTING_DISABLED,
                RecommendedConnectorAction.NONE,
                "Node is offline; routing is ineligible",
            )

        return (NodeConnectorState.READY, RecommendedConnectorAction.NONE, None)


def _auth_not_required(definition: ConnectorDefinition) -> bool:
    return not definition.authentication_methods


def _authentication_verified(snapshot: _NodeConnectorSnapshot) -> bool:
    if snapshot.auth_status == "authenticated" and snapshot.auth_verified_at is not None:
        auth_evidence = snapshot.evidence_by_type.get(ConnectorEvidenceType.AUTHENTICATION)
        if production_mode() and auth_evidence and not _production_trust_ok(auth_evidence):
            return False
        return True
    auth_evidence = snapshot.evidence_by_type.get(ConnectorEvidenceType.AUTHENTICATION)
    if not (auth_evidence and auth_evidence.get("status") == "authenticated"):
        return False
    if production_mode() and not _production_trust_ok(auth_evidence):
        return False
    return True


def _adapter_conformance_passed(snapshot: _NodeConnectorSnapshot) -> bool:
    evidence = snapshot.evidence_by_type.get(ConnectorEvidenceType.ADAPTER_CONFORMANCE)
    if not (evidence and evidence.get("status") == "passed"):
        return False
    if production_mode() and not _production_trust_ok(evidence):
        return False
    return True


def _certification_accepted(snapshot: _NodeConnectorSnapshot, now: datetime) -> bool:
    cert = snapshot.evidence_by_type.get(ConnectorEvidenceType.CERTIFICATION)
    table_ok = snapshot.certification_valid and not _certification_expired(snapshot, now)
    evidence_ok = bool(cert and cert.get("status") == "certified")
    if not (table_ok or evidence_ok):
        return False
    if production_mode():
        if not cert or not _production_trust_ok(cert):
            return False
    return True


def _certification_block_reason(snapshot: _NodeConnectorSnapshot) -> str | None:
    cert = snapshot.evidence_by_type.get(ConnectorEvidenceType.CERTIFICATION)
    if not cert:
        return None
    if production_mode() and not _production_trust_ok(cert):
        trust = cert.get("trust_level")
        origin = cert.get("execution_origin")
        return (
            "Production routing requires node-attested remote-node evidence "
            f"(trust={trust}, origin={origin})"
        )
    return None


def _production_trust_ok(evidence: dict[str, Any]) -> bool:
    trust = str(evidence.get("trust_level") or "")
    origin = str(evidence.get("execution_origin") or "")
    if trust in {EvidenceTrustLevel.MOCK.value, EvidenceTrustLevel.DEVELOPMENT.value}:
        return False
    if origin in {
        ConnectorExecutionOrigin.MOCK_TEST.value,
        ConnectorExecutionOrigin.INLINE_DEVELOPMENT.value,
    }:
        return False
    return (
        trust == EvidenceTrustLevel.NODE_ATTESTED.value
        and origin == ConnectorExecutionOrigin.REMOTE_NODE.value
    )


def _parse_trust(value: object) -> EvidenceTrustLevel | None:
    if not isinstance(value, str):
        return None
    try:
        return EvidenceTrustLevel(value)
    except ValueError:
        return None


def _parse_origin(value: object) -> ConnectorExecutionOrigin | None:
    if not isinstance(value, str):
        return None
    try:
        return ConnectorExecutionOrigin(value)
    except ValueError:
        return None


def _broken_executable_evidence(snapshot: _NodeConnectorSnapshot) -> str | None:
    failure = snapshot.evidence_by_type.get(ConnectorEvidenceType.FAILURE)
    version = snapshot.evidence_by_type.get(ConnectorEvidenceType.VERSION)
    for item in (failure, version):
        if item and item.get("status") == "broken_executable":
            detail = item.get("detail") or item.get("message")
            return str(detail) if detail else "Executable failed version or launch checks"
    return None


def _stale_revision(snapshot: _NodeConnectorSnapshot, current_revision: str) -> bool:
    revisions = [
        snapshot.discovery_revision,
        snapshot.installation_revision,
    ]
    for evidence in snapshot.evidence_by_type.values():
        rev = evidence.get("connector_revision")
        if isinstance(rev, str):
            revisions.append(rev)
    observed = [item for item in revisions if item]
    if not observed:
        return False
    return any(item != current_revision for item in observed)


def _certification_expired(snapshot: _NodeConnectorSnapshot, now: datetime) -> bool:
    if snapshot.certification_expires_at is None:
        return False
    return snapshot.certification_expires_at <= now


def _evidence_version(snapshot: _NodeConnectorSnapshot, kind: ConnectorEvidenceType) -> str | None:
    evidence = snapshot.evidence_by_type.get(kind)
    if not evidence:
        return None
    version = evidence.get("harness_version") or evidence.get("version")
    return str(version) if version else None


__all__ = [
    "ConnectorReadinessService",
    "_NodeConnectorSnapshot",
]
