"""Outbound-only JoyMesh Node transport and remote task validation."""

from __future__ import annotations

import asyncio
import random
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from joymesh.control_plane.contracts import (
    NodeProtocolMessageType,
    ProtocolMessage,
    RemoteTaskEnvelope,
    WorkspaceGrant,
)
from joymesh.control_plane.security import NonceStore, resolve_workspace_path, verify_envelope


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
        bearer_token: str,
        heartbeat_seconds: float = 20,
        max_backoff_seconds: float = 30,
    ) -> None:
        if not gateway_url.startswith("wss://") and "localhost" not in gateway_url:
            raise ValueError("JoyMesh Node requires wss:// outside local development")
        self.node_id = node_id
        self.gateway_url = gateway_url
        self.bearer_token = bearer_token
        self.heartbeat_seconds = heartbeat_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.sequence = 0
        self.replay = ReplayBuffer()
        self._stopping = asyncio.Event()

    async def run(
        self,
        on_message: Callable[[ProtocolMessage], Awaitable[None]],
    ) -> None:
        attempt = 0
        while not self._stopping.is_set():
            try:
                ssl_context = (
                    ssl.create_default_context() if self.gateway_url.startswith("wss://") else None
                )
                async with connect(
                    self.gateway_url,
                    ssl=ssl_context,
                    additional_headers={"Authorization": f"Bearer {self.bearer_token}"},
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

    async def _connected(
        self,
        socket: ClientConnection,
        on_message: Callable[[ProtocolMessage], Awaitable[None]],
    ) -> None:
        await self._send(
            socket,
            NodeProtocolMessageType.HELLO,
            {"last_sequence": self.sequence},
        )
        heartbeat = asyncio.create_task(self._heartbeat(socket))
        try:
            async for raw in socket:
                if not isinstance(raw, str):
                    raise NodeProtocolError("binary protocol frames are unsupported")
                message = ProtocolMessage.model_validate_json(raw)
                if message.node_id != self.node_id:
                    raise NodeProtocolError("received message for a different node")
                await on_message(message)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, socket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._send(
                socket,
                NodeProtocolMessageType.HEARTBEAT,
                {"status": "online"},
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
        )
        self.replay.append(message)
        await socket.send(message.model_dump_json())
        return message
