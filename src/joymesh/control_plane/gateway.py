"""Authenticated outbound node gateway sessions and connector task offering."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from fastapi import WebSocket

from joymesh.connectors.lifecycle_models import (
    TERMINAL_TASK_STATUSES,
    ConnectorEvidence,
    ConnectorEvidenceType,
    ConnectorTaskEvent,
    ConnectorTaskRecord,
    ConnectorTaskStatus,
)
from joymesh.connectors.planning import ConnectorTaskPlan
from joymesh.connectors.store import ConnectorLifecycleStore
from joymesh.control_plane.contracts import (
    ConnectorTaskEnvelope,
    NodeProtocolMessageType,
    NodeSession,
    ProtocolMessage,
)
from joymesh.control_plane.security import (
    NonceStore,
    sign_connector_envelope,
    verify_bytes,
)
from joymesh.models import utc_now


@dataclass
class BoundNodeConnection:
    session: NodeSession
    websocket: WebSocket
    node_public_key: str
    organisation_id: str


@dataclass
class NodeGateway:
    signing_private_key: str
    signing_public_key: str
    key_id: str = "ephemeral-reference"
    connections: dict[str, BoundNodeConnection] = field(default_factory=dict)
    sessions: dict[str, NodeSession] = field(default_factory=dict)
    challenges: dict[str, tuple[str, float]] = field(default_factory=dict)
    challenge_nonces: NonceStore = field(default_factory=NonceStore)
    offer_timeout_seconds: float = 30.0

    def online_node_ids(self) -> set[str]:
        return {
            node_id
            for node_id, connection in self.connections.items()
            if connection.session.status == "online"
        }

    async def authenticate(
        self,
        websocket: WebSocket,
        *,
        node_id: str,
        organisation_id: str,
        public_key: str,
        runtime_version: str = "0.1.0",
        remote_address: str | None = None,
    ) -> NodeSession:
        challenge = secrets.token_urlsafe(32)
        expires_at = utc_now().timestamp() + 60
        self.challenges[node_id] = (challenge, expires_at)
        await websocket.send_json(
            ProtocolMessage(
                type=NodeProtocolMessageType.CHALLENGE,
                node_id=node_id,
                sequence=0,
                payload={"challenge": challenge, "expires_in_seconds": 60},
            ).model_dump(mode="json")
        )
        raw = await websocket.receive_text()
        message = ProtocolMessage.model_validate_json(raw)
        if message.type is not NodeProtocolMessageType.AUTHENTICATE:
            raise PermissionError("expected node.authenticate")
        if message.node_id != node_id:
            raise PermissionError("node identity mismatch")
        stored = self.challenges.pop(node_id, None)
        if stored is None or stored[1] <= utc_now().timestamp():
            raise PermissionError("challenge expired")
        expected_challenge, _ = stored
        supplied_challenge = str(message.payload.get("challenge", ""))
        signature = str(message.payload.get("signature", ""))
        if supplied_challenge != expected_challenge:
            raise PermissionError("challenge mismatch")
        self.challenge_nonces.consume(expected_challenge, utc_now() + timedelta(minutes=5))
        verify_bytes(expected_challenge.encode(), signature, public_key)
        session = NodeSession(
            node_id=node_id,
            organisation_id=organisation_id,
            runtime_version=runtime_version,
            remote_address=remote_address,
            challenge_nonce=expected_challenge,
        )
        self.sessions[session.session_id] = session
        self.connections[node_id] = BoundNodeConnection(
            session=session,
            websocket=websocket,
            node_public_key=public_key,
            organisation_id=organisation_id,
        )
        await websocket.send_json(
            ProtocolMessage(
                type=NodeProtocolMessageType.SESSION_ESTABLISHED,
                node_id=node_id,
                sequence=0,
                reply_to=message.message_id,
                payload={
                    "session_id": session.session_id,
                    "heartbeat_seconds": 20,
                    "control_plane_public_key": self.signing_public_key,
                },
            ).model_dump(mode="json")
        )
        return session

    def disconnect(self, node_id: str, websocket: WebSocket | None = None) -> None:
        current = self.connections.get(node_id)
        if current is None:
            return
        if websocket is not None and current.websocket is not websocket:
            return
        session = current.session.model_copy(
            update={"status": "offline", "last_seen_at": utc_now()}
        )
        self.sessions[session.session_id] = session
        self.connections.pop(node_id, None)

    async def offer_connector_task(
        self,
        task: ConnectorTaskRecord,
        plan: ConnectorTaskPlan,
    ) -> bool:
        connection = self.connections.get(task.node_id)
        if connection is None:
            return False
        envelope = sign_connector_envelope(
            ConnectorTaskEnvelope(
                task_id=task.task_id,
                plan_id=plan.plan_id,
                node_id=plan.node_id,
                connector_id=plan.connector_id,
                connector_revision=plan.connector_revision,
                action=plan.action.value,
                plan_hash=plan.plan_hash,
                executable=plan.executable,
                arguments=plan.arguments,
                method_id=plan.method_id,
                package_source=plan.package_source,
                expected_executables=plan.expected_executables,
                download_digest=plan.download_digest,
                risk_level=plan.risk_level,
                key_id=self.key_id,
                idempotency_key=task.idempotency_key,
            ),
            self.signing_private_key,
        )
        await connection.websocket.send_json(
            ProtocolMessage(
                type=NodeProtocolMessageType.TASK_OFFER,
                node_id=task.node_id,
                sequence=0,
                payload=envelope.model_dump(mode="json"),
            ).model_dump(mode="json")
        )
        return True

    async def revoke_node(self, node_id: str) -> None:
        connection = self.connections.get(node_id)
        if connection is None:
            return
        await connection.websocket.send_json(
            ProtocolMessage(
                type=NodeProtocolMessageType.NODE_REVOKED,
                node_id=node_id,
                sequence=0,
                payload={"reason": "node revoked"},
            ).model_dump(mode="json")
        )
        await connection.websocket.close(code=4403, reason="node revoked")
        self.disconnect(node_id, connection.websocket)


@dataclass
class ConnectorTaskEventIngestor:
    """Validates and persists inbound connector task events from a bound node."""

    store: ConnectorLifecycleStore
    seen_terminals: set[str] = field(default_factory=set)

    async def ingest(self, message: ProtocolMessage) -> ConnectorTaskRecord | None:
        payload = message.payload
        task_id = str(payload.get("task_id", ""))
        if not task_id:
            raise ValueError("task_id required")
        task = await self.store.get_task(task_id)
        if task.node_id != message.node_id:
            raise PermissionError("event for wrong node")
        if task.connector_id != str(payload.get("connector_id", task.connector_id)):
            raise PermissionError("event for wrong connector")
        plan_hash = str(payload.get("plan_hash", ""))
        if plan_hash and plan_hash != task.plan_hash:
            raise PermissionError("stale plan hash")
        if task.status in TERMINAL_TASK_STATUSES:
            raise ValueError("events after terminal completion are rejected")
        sequence = int(payload.get("sequence_number", payload.get("sequence", 0)))
        event_type = message.type.value
        await self.store.append_task_event(
            ConnectorTaskEvent(
                event_id=str(uuid4()),
                task_id=task_id,
                node_id=message.node_id,
                connector_id=task.connector_id,
                event_type=event_type,
                sequence=sequence,
                payload=payload,
            )
        )
        if message.type is NodeProtocolMessageType.TASK_ACCEPTED:
            if task.status is ConnectorTaskStatus.ACCEPTED_BY_NODE:
                return task
            if task.status is ConnectorTaskStatus.RUNNING:
                return task
            return await self.store.transition_task(
                task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.ACCEPTED_BY_NODE,
                started_at=utc_now(),
            )
        if message.type is NodeProtocolMessageType.TASK_STARTED:
            if task.status is ConnectorTaskStatus.RUNNING:
                return task
            return await self.store.transition_task(
                task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.RUNNING,
            )
        if message.type is NodeProtocolMessageType.TASK_WAITING_FOR_USER:
            return await self.store.transition_task(
                task_id,
                expected_version=task.version,
                status=ConnectorTaskStatus.WAITING_FOR_USER,
            )
        if message.type is NodeProtocolMessageType.TASK_EVIDENCE:
            evidence_payload = payload.get("evidence", {})
            if isinstance(evidence_payload, dict):
                from joymesh.connectors.lifecycle_models import (
                    ConnectorExecutionOrigin,
                    EvidenceTrustLevel,
                )

                trust_raw = str(
                    evidence_payload.get("trust_level")
                    or (evidence_payload.get("details") or {}).get("trust_level")
                    or EvidenceTrustLevel.NODE_ATTESTED.value
                )
                origin_raw = str(
                    evidence_payload.get("execution_origin")
                    or (evidence_payload.get("details") or {}).get("execution_origin")
                    or ConnectorExecutionOrigin.REMOTE_NODE.value
                )
                try:
                    trust = EvidenceTrustLevel(trust_raw)
                except ValueError:
                    trust = EvidenceTrustLevel.NODE_ATTESTED
                try:
                    origin = ConnectorExecutionOrigin(origin_raw)
                except ValueError:
                    origin = ConnectorExecutionOrigin.REMOTE_NODE
                await self.store.record_evidence(
                    ConnectorEvidence(
                        evidence_id=str(evidence_payload.get("evidence_id", uuid4())),
                        node_id=task.node_id,
                        connector_id=task.connector_id,
                        connector_revision=str(
                            evidence_payload.get("connector_revision", task.connector_revision)
                        ),
                        task_id=task_id,
                        evidence_type=ConnectorEvidenceType(
                            str(evidence_payload.get("evidence_type", "failure"))
                        ),
                        status=str(evidence_payload.get("status", "unknown")),
                        executable_path=evidence_payload.get("executable_path"),
                        executable_fingerprint=evidence_payload.get("executable_fingerprint"),
                        harness_version=evidence_payload.get("harness_version"),
                        provider_mode=evidence_payload.get("provider_mode"),
                        details=dict(evidence_payload.get("details") or {}),
                        created_at=utc_now(),
                        expires_at=None,
                        trust_level=trust,
                        execution_origin=origin,
                    )
                )
            return task
        terminal_map = {
            NodeProtocolMessageType.TASK_SUCCEEDED: ConnectorTaskStatus.SUCCEEDED,
            NodeProtocolMessageType.TASK_FAILED: ConnectorTaskStatus.FAILED,
            NodeProtocolMessageType.TASK_CANCELLED: ConnectorTaskStatus.CANCELLED,
            NodeProtocolMessageType.TASK_INTERRUPTED: ConnectorTaskStatus.INTERRUPTED,
        }
        if message.type in terminal_map:
            if task_id in self.seen_terminals:
                raise ValueError("duplicate terminal event rejected")
            self.seen_terminals.add(task_id)
            updated = await self.store.transition_task(
                task_id,
                expected_version=task.version,
                status=terminal_map[message.type],
                detail=str(payload.get("detail", "")) or None,
                finished_at=utc_now(),
            )
            await self.store.recompute(node_id=task.node_id, connector_id=task.connector_id)
            return updated
        return task
