from __future__ import annotations

import base64
import hashlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from joymesh.delivery.joyctl_device import (
    MAX_WEBSOCKET_FRAME_BYTES,
    WEBSOCKET_GUID,
    DeviceAgentError,
    _hosted_endpoint,
    _read_private_json,
    _write_private_json,
    _ws_recv,
    connect_protocol,
    http_json,
)


def test_plaintext_token_transport_requires_ip_loopback() -> None:
    with pytest.raises(DeviceAgentError, match="loopback"):
        _hosted_endpoint("http://100.109.234.47:8766")
    with pytest.raises(DeviceAgentError, match="origin"):
        _hosted_endpoint("https://joy.example/api/agent/connect")
    assert _hosted_endpoint("http://127.0.0.1:8766") == ("http", "127.0.0.1", 8766)
    assert _hosted_endpoint("https://joy.example") == ("https", "joy.example", 443)


@pytest.mark.parametrize("valid", [True, False])
def test_websocket_handshake_authenticates_server(valid: bool) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    observed: list[str] = []

    def respond() -> None:
        connection, _ = listener.accept()
        with connection:
            raw = b""
            while not raw.endswith(b"\r\n\r\n"):
                raw += connection.recv(1)
            headers = raw.decode().split("\r\n")
            key = next(line.split(": ", 1)[1] for line in headers if line.startswith("Sec-WebSocket-Key:"))
            observed.append(key)
            accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode()).digest()).decode()
            if not valid:
                accept = "forged"
            connection.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode() + b"\x81\x14" + b'{"type":"connected"}')

    worker = threading.Thread(target=respond, daemon=True)
    worker.start()
    try:
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        if valid:
            protocol = connect_protocol(address, "test-token", timeout=2)
            protocol.connection.close()
        else:
            with pytest.raises(DeviceAgentError, match="handshake"):
                connect_protocol(address, "test-token", timeout=2)
        assert len(base64.b64decode(observed[0])) == 16
    finally:
        listener.close()
        worker.join(timeout=2)


def test_websocket_rejects_oversized_frame_before_body_read() -> None:
    client, server = socket.socketpair()
    try:
        server.sendall(b"\x81\x7f" + (MAX_WEBSOCKET_FRAME_BYTES + 1).to_bytes(8, "big"))
        with pytest.raises(DeviceAgentError, match="size limit"):
            _ws_recv(client)
    finally:
        client.close()
        server.close()


def test_credential_request_does_not_follow_redirect() -> None:
    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(307)
            self.send_header("Location", "http://127.0.0.1:9/steal")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/agent/authenticate"
        with pytest.raises(DeviceAgentError, match="redirected"):
            http_json("POST", url, {"device_credential": "secret"})
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_device_credential_store_rejects_world_readable_and_symlink(tmp_path: Path) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    state = directory / "device.json"
    _write_private_json(state, {"device_credential": "secret"})
    assert _read_private_json(state)["device_credential"] == "secret"
    assert state.stat().st_mode & 0o777 == 0o600
    state.chmod(0o644)
    with pytest.raises(DeviceAgentError, match="owner-only"):
        _read_private_json(state)
    state.unlink()
    state.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        _read_private_json(state)
