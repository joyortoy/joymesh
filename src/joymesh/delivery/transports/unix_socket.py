"""Unix domain socket delivery transport (production local IPC).

Protocol (newline-delimited JSON):
  client → server: {"type":"hello","transport_version":1}
  server → client: {"type":"hello_ok","transport_version":1}
  client → server: {"type":"publish","envelope":{...}}
  server → client: {"type":"ack","envelope_id":"...","status":"acked"}
  either → either: {"type":"heartbeat"}
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
from pathlib import Path
from typing import Any

from joymesh.delivery.contracts import (
    TRANSPORT_VERSION,
    DeliveryAck,
    DeliveryAckStatus,
    DeliveryEnvelope,
)
from joymesh.delivery.intake import (
    IntakeRejected,
    RuntimeStateIntakeService,
    envelope_from_dict,
)
from joymesh.delivery.transports.protocol import (
    TransportVersionError,
    assert_compatible_version,
)
from joymesh.models import utc_now


def default_socket_path() -> Path:
    """Prefer XDG runtime dir; otherwise a user-private run directory."""

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or (Path.home() / ".local" / "run"))
    prepare_runtime_dir(runtime, require_private=True)
    return runtime / "joymesh-delivery.sock"


def prepare_runtime_dir(path: Path, *, require_private: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    if require_private:
        mode = path.stat().st_mode
        if mode & stat.S_IWOTH:
            raise PermissionError(f"insecure delivery runtime directory: {path}")
    return path


def prepare_socket_parent(
    socket_path: Path, *, require_private: bool = False
) -> Path:
    parent = Path(socket_path).expanduser().parent
    return prepare_runtime_dir(parent, require_private=require_private)


def remove_stale_socket(path: Path) -> bool:
    """Remove a leftover socket file that is not currently accepting connections.

    Returns True when a stale socket file was removed.
    """

    if not path.exists():
        return False
    if not path.is_socket() and not path.is_file():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(str(path))
        # Connected — treat as live; do not unlink.
        return False
    except OSError:
        try:
            path.unlink()
            return True
        except OSError:
            return False
    finally:
        try:
            probe.close()
        except OSError:
            pass


class UnixSocketDeliveryServer:
    """JoyCLI-side Unix listener that delegates to RuntimeStateIntakeService."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        intake: RuntimeStateIntakeService | None = None,
        intake_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path or default_socket_path())
        if intake is not None:
            self.intake = intake
        else:
            store = (
                Path(intake_path)
                if intake_path
                else self.path.with_name(self.path.name + ".intake.sqlite3")
            )
            self.intake = RuntimeStateIntakeService(store)
        self._server: asyncio.AbstractServer | None = None
        self._owns_intake = intake is None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def received(self) -> list[DeliveryEnvelope]:
        return self.intake.received

    async def start(self) -> None:
        prepare_socket_parent(self.path)
        remove_stale_socket(self.path)
        if self.path.exists():
            # Live socket — refuse to clobber another intake process.
            raise FileExistsError(f"delivery socket already in use: {self.path}")
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    async def stop(self) -> None:
        for writer in list(self._writers):
            try:
                writer.close()
            except OSError:
                pass
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass
        if self._owns_intake:
            self.intake.close()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._writers.add(writer)
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                message = json.loads(raw.decode())
                response = await self._dispatch(message)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    async def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        msg_type = message.get("type")
        if msg_type == "hello":
            remote = int(message.get("transport_version", 0))
            try:
                assert_compatible_version(remote)
            except TransportVersionError as exc:
                return {
                    "type": "error",
                    "code": "transport_version_mismatch",
                    "detail": str(exc),
                }
            return {"type": "hello_ok", "transport_version": TRANSPORT_VERSION}
        if msg_type == "heartbeat":
            return {"type": "heartbeat_ok", "transport_version": TRANSPORT_VERSION}
        if msg_type == "publish":
            envelope_data = message.get("envelope") or {}
            try:
                envelope = envelope_from_dict(envelope_data)
                status = self.intake.accept(envelope)
            except IntakeRejected as exc:
                return {
                    "type": "ack",
                    "envelope_id": envelope_data.get("envelope_id"),
                    "status": DeliveryAckStatus.DROPPED.value,
                    "code": exc.code,
                    "detail": exc.detail,
                }
            except (KeyError, TypeError, ValueError) as exc:
                return {
                    "type": "error",
                    "code": "invalid_envelope",
                    "detail": str(exc),
                }
            return {
                "type": "ack",
                "envelope_id": envelope.envelope_id,
                "status": status.value,
            }
        return {"type": "error", "detail": f"unknown message type: {msg_type}"}


class UnixSocketDeliveryTransport:
    """JoyMesh publisher transport over a Unix domain socket."""

    name = "unix_socket"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or default_socket_path())
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._version = TRANSPORT_VERSION
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    async def connect(self) -> None:
        if self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.path)),
                timeout=2.0,
            )
            response = await self._roundtrip(
                {"type": "hello", "transport_version": TRANSPORT_VERSION}
            )
            if response.get("type") == "error":
                await self.close()
                code = response.get("code") or "transport_error"
                raise ConnectionError(f"{code}: {response.get('detail')}")
            self.last_error = None
        except TimeoutError as exc:
            self.last_error = "connect_timeout"
            await self.close()
            raise ConnectionError("unix delivery socket connect timeout") from exc
        except OSError as exc:
            self.last_error = str(exc)
            await self.close()
            raise ConnectionError(f"unix delivery socket unavailable: {exc}") from exc

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    async def publish(self, envelope: DeliveryEnvelope) -> DeliveryAck:
        async with self._lock:
            await self._ensure()
            response = await self._roundtrip(
                {"type": "publish", "envelope": envelope.as_dict()}
            )
            if response.get("type") == "error":
                await self.close()
                raise ConnectionError(response.get("detail") or "transport error")
            status = DeliveryAckStatus(response.get("status", "acked"))
            return DeliveryAck(
                envelope_id=envelope.envelope_id,
                status=status,
                received_at=utc_now(),
                detail=response.get("detail"),
            )

    async def heartbeat(self) -> None:
        async with self._lock:
            await self._ensure()
            await self._roundtrip({"type": "heartbeat"})

    def negotiated_version(self) -> int:
        return self._version

    def health(self) -> dict[str, Any]:
        return {
            "transport": self.name,
            "socket_path": str(self.path),
            "connected": self._writer is not None,
            "last_error": self.last_error,
            "negotiated_version": self._version,
        }

    async def _ensure(self) -> None:
        if self._writer is None:
            await self.connect()

    async def _roundtrip(self, message: dict[str, Any]) -> dict[str, Any]:
        assert self._reader is not None and self._writer is not None
        self._writer.write((json.dumps(message) + "\n").encode())
        await self._writer.drain()
        try:
            raw = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
        except TimeoutError as exc:
            await self.close()
            raise ConnectionError("unix delivery socket read timeout") from exc
        if not raw:
            await self.close()
            raise ConnectionError("unix delivery socket closed")
        response = json.loads(raw.decode())
        if not isinstance(response, dict):
            raise RuntimeError("invalid transport response")
        if response.get("type") == "error" and message.get("type") == "hello":
            return response
        if response.get("type") == "error":
            raise RuntimeError(response.get("detail") or "transport error")
        if "transport_version" in response:
            self._version = int(response["transport_version"])
            assert_compatible_version(self._version)
        return response
