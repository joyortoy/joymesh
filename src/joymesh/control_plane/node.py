"""Outbound-only JoyMesh Node transport, authentication, and connector execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from joymesh.connectors.lifecycle_models import ConnectorTaskStatus
from joymesh.connectors.node_runner import ConnectorNodeRunner
from joymesh.connectors.planning import ConnectorAction, ConnectorTaskPlan
from joymesh.control_plane.contracts import (
    ConnectorTaskEnvelope,
    NodeProtocolMessageType,
    ProtocolMessage,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.journal import NodeTaskJournal
from joymesh.control_plane.security import (
    NonceStore,
    load_private_key,
    resolve_workspace_path,
    sign_bytes,
    verify_connector_envelope,
    verify_envelope,
)
from joymesh.models import utc_now


class NodeProtocolError(RuntimeError):
    pass


@dataclass
class ReplayBuffer:
    max_messages: int = 1_000
    _messages: list[ProtocolMessage] = field(default_factory=list)

    def append(self, message: ProtocolMessage) -> None:
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]

    def after(self, sequence: int) -> tuple[ProtocolMessage, ...]:
        return tuple(message for message in self._messages if message.sequence > sequence)


class NodeTaskValidator:
    def __init__(self, *, control_plane_public_key: str) -> None:
        self.control_plane_public_key = control_plane_public_key
        self.nonces = NonceStore()

    def validate(
        self,
        envelope: RemoteTaskEnvelope,
        *,
        node_id: str,
        grants: tuple[WorkspaceGrant, ...],
        requested_path: str = ".",
    ) -> Path:
        verify_envelope(envelope, self.control_plane_public_key)
        self.nonces.consume(envelope.nonce, envelope.expires_at)
        if envelope.node_id != node_id:
            raise PermissionError("task is addressed to another node")
        grant = next(
            (
                item
                for item in grants
                if item.node_id == node_id
                and item.workspace_id == envelope.workspace_id
                and item.revoked_at is None
            ),
            None,
        )
        if grant is None:
            raise PermissionError("workspace permission is missing or revoked")
        return resolve_workspace_path(grant.root_path, requested_path)


class JoyMeshNode:
    """Maintains a resilient TLS WebSocket initiated by the local node."""

    def __init__(
        self,
        *,
        node_id: str,
        gateway_url: str,
        private_key: str,
        bearer_token: str | None = None,
        heartbeat_seconds: float = 20,
        max_backoff_seconds: float = 30,
        runtime_version: str = "0.1.0",
        journal: NodeTaskJournal | None = None,
        control_plane_public_key: str | None = None,
    ) -> None:
        if not gateway_url.startswith("wss://") and "localhost" not in gateway_url:
            raise ValueError("JoyMesh Node requires wss:// outside local development")
        self.node_id = node_id
        self.gateway_url = gateway_url
        self.private_key = private_key
        self.bearer_token = bearer_token
        self.heartbeat_seconds = heartbeat_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.runtime_version = runtime_version
        self.sequence = 0
        self.session_id: str | None = None
        self.control_plane_public_key = control_plane_public_key
        self.journal = journal or NodeTaskJournal()
        self.runner = ConnectorNodeRunner(node_id=node_id)
        self.replay = ReplayBuffer()
        self._stopping = asyncio.Event()
        self._revoked = False
        self._socket: ClientConnection | None = None
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    @classmethod
    def from_key_path(
        cls,
        *,
        node_id: str,
        gateway_url: str,
        private_key_path: Path,
        bearer_token: str | None = None,
    ) -> JoyMeshNode:
        return cls(
            node_id=node_id,
            gateway_url=gateway_url,
            private_key=load_private_key(private_key_path),
            bearer_token=bearer_token,
        )

    async def run(
        self,
        on_message: Callable[[ProtocolMessage], Awaitable[None]] | None = None,
    ) -> None:
        attempt = 0
        while not self._stopping.is_set() and not self._revoked:
            try:
                ssl_context = (
                    ssl.create_default_context() if self.gateway_url.startswith("wss://") else None
                )
                headers = {}
                if self.bearer_token:
                    headers["Authorization"] = f"Bearer {self.bearer_token}"
                async with connect(
                    self.gateway_url,
                    ssl=ssl_context,
                    additional_headers=headers or None,
                    max_size=2**20,
                    ping_interval=None,
                ) as socket:
                    attempt = 0
                    await self._connected(socket, on_message)
            except (OSError, TimeoutError, NodeProtocolError):
                attempt += 1
                ceiling = min(self.max_backoff_seconds, 2 ** min(attempt, 8))
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=random.uniform(0, ceiling),
                    )
                except TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stopping.set()
        for task in list(self._active_tasks.values()):
            task.cancel()

    async def _connected(
        self,
        socket: ClientConnection,
        on_message: Callable[[ProtocolMessage], Awaitable[None]] | None,
    ) -> None:
        self._socket = socket
        await self._send(
            socket,
            NodeProtocolMessageType.HELLO,
            {
                "last_sequence": self.sequence,
                "runtime_version": self.runtime_version,
                "journal": self.journal.summary(),
            },
        )
        heartbeat = asyncio.create_task(self._heartbeat(socket))
        try:
            async for raw in socket:
                if not isinstance(raw, str):
                    raise NodeProtocolError("binary protocol frames are unsupported")
                message = ProtocolMessage.model_validate_json(raw)
                if message.node_id not in {self.node_id, ""}:
                    raise NodeProtocolError("received message for a different node")
                await self._handle(socket, message, on_message)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._socket = None

    async def _handle(
        self,
        socket: ClientConnection,
        message: ProtocolMessage,
        on_message: Callable[[ProtocolMessage], Awaitable[None]] | None,
    ) -> None:
        if message.type is NodeProtocolMessageType.CHALLENGE:
            challenge = str(message.payload["challenge"])
            signature = sign_bytes(challenge.encode(), self.private_key)
            await self._send(
                socket,
                NodeProtocolMessageType.AUTHENTICATE,
                {
                    "challenge": challenge,
                    "signature": signature,
                    "runtime_version": self.runtime_version,
                },
            )
            return
        if message.type is NodeProtocolMessageType.SESSION_ESTABLISHED:
            self.session_id = str(message.payload.get("session_id"))
            self.control_plane_public_key = str(
                message.payload.get("control_plane_public_key")
                or self.control_plane_public_key
                or ""
            )
            await self._send(
                socket,
                NodeProtocolMessageType.READY,
                {"session_id": self.session_id, "journal": self.journal.summary()},
            )
            return
        if message.type is NodeProtocolMessageType.NODE_REVOKED:
            self._revoked = True
            await self.stop()
            return
        if message.type is NodeProtocolMessageType.TASK_RECONCILE:
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_RECONCILE_RESPONSE,
                self.journal.summary(),
            )
            return
        if message.type is NodeProtocolMessageType.TASK_OFFER:
            await self._accept_connector_offer(socket, message)
            return
        if message.type is NodeProtocolMessageType.TASK_CANCEL:
            task_id = str(message.payload.get("task_id", ""))
            await self.runner.cancel_task(task_id)
            handle = self._active_tasks.get(task_id)
            if handle is not None:
                handle.cancel()
            return
        if message.type is NodeProtocolMessageType.HEARTBEAT_ACK:
            return
        if on_message is not None:
            await on_message(message)

    async def _accept_connector_offer(
        self, socket: ClientConnection, message: ProtocolMessage
    ) -> None:
        if self._revoked:
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_REJECTED,
                {"reason": "node revoked", **message.payload},
            )
            return
        envelope = ConnectorTaskEnvelope.model_validate(message.payload)
        if not self.control_plane_public_key:
            raise NodeProtocolError("control plane public key unknown")
        verify_connector_envelope(envelope, self.control_plane_public_key)
        if envelope.node_id != self.node_id:
            raise PermissionError("task is addressed to another node")
        existing = self.journal.get(envelope.task_id, envelope.plan_hash)
        if existing is not None and existing.terminal_at is not None:
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_SUCCEEDED
                if existing.status == "succeeded"
                else NodeProtocolMessageType.TASK_FAILED,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": existing.last_sequence_number,
                    "detail": "replayed terminal result",
                    "terminal_result_digest": existing.terminal_result_digest,
                },
            )
            return
        if existing is not None and existing.started_at is not None:
            # Non-terminal journal entry: resume without relaunching Cursor.
            active = self._active_tasks.get(envelope.task_id)
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_ACCEPTED,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": existing.last_sequence_number or 1,
                    "detail": "resumed without duplicate execution",
                    "process_active": active is not None and not active.done(),
                    "journal_status": existing.status,
                },
            )
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_WAITING_FOR_USER
                if envelope.action == "authenticate"
                else NodeProtocolMessageType.TASK_PROGRESS,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": max(existing.last_sequence_number, 1) + 1,
                    "detail": "reconciled active journal entry; Cursor not relaunched",
                },
            )
            return
        self.journal.accept(
            task_id=envelope.task_id,
            plan_hash=envelope.plan_hash,
            connector_id=envelope.connector_id,
            connector_revision=envelope.connector_revision,
        )
        await self._send(
            socket,
            NodeProtocolMessageType.TASK_ACCEPTED,
            {
                "task_id": envelope.task_id,
                "node_id": self.node_id,
                "connector_id": envelope.connector_id,
                "connector_revision": envelope.connector_revision,
                "plan_hash": envelope.plan_hash,
                "sequence_number": 1,
            },
        )
        handle = asyncio.create_task(self._execute_connector_task(socket, envelope))
        self._active_tasks[envelope.task_id] = handle
        handle.add_done_callback(lambda _task: self._active_tasks.pop(envelope.task_id, None))

    async def _execute_connector_task(
        self, socket: ClientConnection, envelope: ConnectorTaskEnvelope
    ) -> None:
        self.journal.mark_started(envelope.task_id, envelope.plan_hash)
        sequence = 1

        async def emit_event(event: Any) -> None:
            nonlocal sequence
            sequence = max(sequence, event.sequence)
            self.journal.update_sequence(envelope.task_id, envelope.plan_hash, sequence)
            event_type = {
                "task.started": NodeProtocolMessageType.TASK_STARTED,
                "task.waiting_for_user": NodeProtocolMessageType.TASK_WAITING_FOR_USER,
                "task.succeeded": NodeProtocolMessageType.TASK_SUCCEEDED,
                "task.failed": NodeProtocolMessageType.TASK_FAILED,
                "task.cancelled": NodeProtocolMessageType.TASK_CANCELLED,
                "task.interrupted": NodeProtocolMessageType.TASK_INTERRUPTED,
            }.get(event.event_type, NodeProtocolMessageType.TASK_PROGRESS)
            await self._send(
                socket,
                event_type,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": event.sequence,
                    **event.payload,
                },
            )

        async def record_evidence(evidence: Any) -> None:
            nonlocal sequence
            sequence += 1
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_EVIDENCE,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": sequence,
                    "evidence": {
                        "evidence_id": evidence.evidence_id,
                        "evidence_type": evidence.evidence_type.value,
                        "status": evidence.status,
                        "executable_path": evidence.executable_path,
                        "executable_fingerprint": evidence.executable_fingerprint,
                        "harness_version": evidence.harness_version,
                        "provider_mode": evidence.provider_mode,
                        "connector_revision": evidence.connector_revision,
                        "trust_level": evidence.trust_level.value,
                        "execution_origin": evidence.execution_origin.value,
                        "details": dict(evidence.details),
                    },
                },
            )

        plan = ConnectorTaskPlan(
            plan_id=envelope.plan_id,
            node_id=envelope.node_id,
            connector_id=envelope.connector_id,
            connector_revision=envelope.connector_revision,
            action=ConnectorAction(envelope.action),
            method_id=envelope.method_id,
            executable=envelope.executable,
            arguments=envelope.arguments,
            package_source=envelope.package_source,
            expected_executables=envelope.expected_executables,
            download_digest=envelope.download_digest,
            risk_level=envelope.risk_level,
            expires_at=envelope.expires_at,
            plan_hash=envelope.plan_hash,
        )
        try:
            status = await self.runner.execute(
                task_id=envelope.task_id,
                plan=plan,
                emit_event=emit_event,
                record_evidence=record_evidence,
                sequence_start=1,
            )
        except asyncio.CancelledError:
            status = ConnectorTaskStatus.CANCELLED
            await self._send(
                socket,
                NodeProtocolMessageType.TASK_CANCELLED,
                {
                    "task_id": envelope.task_id,
                    "node_id": self.node_id,
                    "connector_id": envelope.connector_id,
                    "connector_revision": envelope.connector_revision,
                    "plan_hash": envelope.plan_hash,
                    "sequence_number": sequence + 1,
                },
            )
            raise
        if status is ConnectorTaskStatus.WAITING_FOR_USER:
            return
        digest = hashlib.sha256(
            json.dumps(
                {"task_id": envelope.task_id, "status": status.value},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.journal.mark_terminal(
            envelope.task_id,
            envelope.plan_hash,
            status=status.value,
            result_digest=digest,
            sequence=sequence,
        )

    async def _heartbeat(self, socket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._send(
                socket,
                NodeProtocolMessageType.HEARTBEAT,
                {
                    "status": "online",
                    "session_id": self.session_id,
                    "active_task_ids": list(self._active_tasks),
                    "journal": self.journal.summary(),
                },
            )

    async def _send(
        self,
        socket: ClientConnection,
        message_type: NodeProtocolMessageType,
        payload: dict[str, Any],
    ) -> ProtocolMessage:
        self.sequence += 1
        message = ProtocolMessage(
            type=message_type,
            node_id=self.node_id,
            sequence=self.sequence,
            payload=payload,
            timestamp=utc_now(),
        )
        self.replay.append(message)
        await socket.send(message.model_dump_json())
        return message
