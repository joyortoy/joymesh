"""Construct the delivery transport for the JoyMesh composition root."""

from __future__ import annotations

from pathlib import Path

from joymesh.delivery.contracts import DeliveryAck, DeliveryEnvelope
from joymesh.delivery.settings import (
    DeliveryConfigError,
    DeliverySettings,
    DeliveryTransportMode,
)
from joymesh.delivery.transports.memory import MemoryDeliveryTransport
from joymesh.delivery.transports.protocol import DeliveryTransport
from joymesh.delivery.transports.unix_socket import (
    UnixSocketDeliveryTransport,
    default_socket_path,
    prepare_socket_parent,
)


class DisabledDeliveryTransport:
    """Explicitly disabled delivery — items remain in the durable outbox."""

    name = "disabled"

    async def connect(self) -> None:
        raise ConnectionError("delivery transport disabled")

    async def close(self) -> None:
        return None

    async def publish(self, envelope: DeliveryEnvelope) -> DeliveryAck:
        raise ConnectionError("delivery transport disabled")

    async def heartbeat(self) -> None:
        raise ConnectionError("delivery transport disabled")

    def negotiated_version(self) -> int:
        return 0


def build_delivery_transport(settings: DeliverySettings) -> DeliveryTransport:
    """Build the transport selected by settings.

    Never silently substitutes ``memory`` when ``unix_socket`` is selected.
    """

    if settings.transport is DeliveryTransportMode.MEMORY:
        return MemoryDeliveryTransport()
    if settings.transport is DeliveryTransportMode.DISABLED:
        return DisabledDeliveryTransport()
    if settings.transport is DeliveryTransportMode.UNIX_SOCKET:
        explicit = settings.socket_path is not None
        path = settings.socket_path or default_socket_path()
        try:
            # Default runtime dir must be private; explicit overrides (tests) may
            # live under shared temp roots.
            prepare_socket_parent(path, require_private=not explicit)
        except OSError as exc:
            raise DeliveryConfigError(
                "delivery_socket_permission_denied",
                f"cannot prepare socket parent for {path}: {exc}",
            ) from exc
        if path.exists() and path.is_dir():
            raise DeliveryConfigError(
                "delivery_socket_invalid_path",
                f"socket path is a directory: {path}",
            )
        return UnixSocketDeliveryTransport(path)
    raise DeliveryConfigError(
        "invalid_delivery_transport",
        f"unsupported delivery transport: {settings.transport!r}",
    )


def resolve_socket_path(settings: DeliverySettings) -> Path:
    return settings.socket_path or default_socket_path()
