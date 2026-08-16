"""Delivery transport protocol."""

from __future__ import annotations

from typing import Protocol

from joymesh.delivery.contracts import TRANSPORT_VERSION, DeliveryAck, DeliveryEnvelope


class DeliveryTransport(Protocol):
    name: str

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, envelope: DeliveryEnvelope) -> DeliveryAck: ...

    async def heartbeat(self) -> None: ...

    def negotiated_version(self) -> int: ...


class TransportVersionError(RuntimeError):
    def __init__(self, local: int, remote: int) -> None:
        super().__init__(
            f"transport version mismatch: local={local} remote={remote}"
        )
        self.local = local
        self.remote = remote


def assert_compatible_version(remote: int, *, local: int = TRANSPORT_VERSION) -> None:
    if remote != local:
        raise TransportVersionError(local, remote)
