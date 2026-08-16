"""Delivery transport settings (JoyMesh → JoyCLI)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class DeliveryTransportMode(StrEnum):
    UNIX_SOCKET = "unix_socket"
    MEMORY = "memory"
    DISABLED = "disabled"


class DeliveryConfigError(ValueError):
    """Invalid delivery configuration (path/permissions/mode)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DeliverySettings:
    transport: DeliveryTransportMode
    socket_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport.value,
            "socket_path": str(self.socket_path) if self.socket_path else None,
        }


def default_production_transport_mode() -> DeliveryTransportMode:
    """Production-local default: Unix socket on macOS/Linux."""

    if sys.platform.startswith(("linux", "darwin", "freebsd", "openbsd", "netbsd")):
        return DeliveryTransportMode.UNIX_SOCKET
    return DeliveryTransportMode.DISABLED


def delivery_settings_from_mapping(raw: dict[str, Any] | None) -> DeliverySettings | None:
    if not isinstance(raw, dict) or not raw:
        return None
    transport_raw = raw.get("transport")
    if transport_raw is None or str(transport_raw).strip() == "":
        return None
    try:
        mode = DeliveryTransportMode(str(transport_raw).strip().lower())
    except ValueError as exc:
        raise DeliveryConfigError(
            "invalid_delivery_transport",
            f"unsupported delivery.transport: {transport_raw!r}",
        ) from exc
    socket_raw = raw.get("socket_path")
    socket_path = Path(str(socket_raw)).expanduser() if socket_raw else None
    return DeliverySettings(transport=mode, socket_path=socket_path)


def resolve_delivery_settings(
    *,
    config_delivery: DeliverySettings | None = None,
    environ: dict[str, str] | None = None,
) -> DeliverySettings:
    """Resolve delivery settings.

    Precedence:
    1. ``JOYMESH_DELIVERY_TRANSPORT`` / ``JOYMESH_DELIVERY_SOCKET``
    2. user config ``delivery:``
    3. platform production default (unix_socket on POSIX)
    """

    env = environ if environ is not None else os.environ
    env_transport = (env.get("JOYMESH_DELIVERY_TRANSPORT") or "").strip().lower()
    env_socket = (env.get("JOYMESH_DELIVERY_SOCKET") or "").strip()

    if env_transport:
        try:
            mode = DeliveryTransportMode(env_transport)
        except ValueError as exc:
            raise DeliveryConfigError(
                "invalid_delivery_transport",
                f"unsupported JOYMESH_DELIVERY_TRANSPORT: {env_transport!r}",
            ) from exc
        socket = Path(env_socket).expanduser() if env_socket else (
            config_delivery.socket_path if config_delivery else None
        )
        return DeliverySettings(transport=mode, socket_path=socket)

    if config_delivery is not None:
        if env_socket:
            return DeliverySettings(
                transport=config_delivery.transport,
                socket_path=Path(env_socket).expanduser(),
            )
        return config_delivery

    socket = Path(env_socket).expanduser() if env_socket else None
    return DeliverySettings(
        transport=default_production_transport_mode(),
        socket_path=socket,
    )
