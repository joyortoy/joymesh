"""In-memory delivery transport for tests and local sinks."""

from __future__ import annotations

from joymesh.delivery.contracts import (
    TRANSPORT_VERSION,
    DeliveryAck,
    DeliveryAckStatus,
    DeliveryEnvelope,
)
from joymesh.models import utc_now


class MemoryDeliveryTransport:
    name = "memory"

    def __init__(self) -> None:
        self.published: list[DeliveryEnvelope] = []
        self.connected = False
        self._version = TRANSPORT_VERSION

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def publish(self, envelope: DeliveryEnvelope) -> DeliveryAck:
        if not self.connected:
            await self.connect()
        self.published.append(envelope)
        return DeliveryAck(
            envelope_id=envelope.envelope_id,
            status=DeliveryAckStatus.ACKED,
            received_at=utc_now(),
        )

    async def heartbeat(self) -> None:
        if not self.connected:
            await self.connect()

    def negotiated_version(self) -> int:
        return self._version
