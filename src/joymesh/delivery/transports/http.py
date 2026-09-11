"""HTTP delivery transport: JoyMesh → JoyCTL webhook push."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from joymesh.delivery.contracts import (
    TRANSPORT_VERSION,
    DeliveryAck,
    DeliveryAckStatus,
    DeliveryEnvelope,
)
from joymesh.models import utc_now

DEFAULT_INBOUND_PATH = "/api/v1/mesh/inbound"


class HttpDeliveryTransport:
    """POST signed delivery envelopes to JoyCTL HTTP inbound."""

    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        path: str = DEFAULT_INBOUND_PATH,
        token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.path = path if path.startswith("/") else f"/{path}"
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._connected = False

    @property
    def url(self) -> str:
        return f"{self.base_url}{self.path}"

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def publish(self, envelope: DeliveryEnvelope) -> DeliveryAck:
        if not self._connected:
            await self.connect()
        body = {
            "kind": "delivery_envelope",
            "schema": "joy.delivery_envelope/v1",
            "payload": envelope.as_dict(),
        }
        self._post(body)
        return DeliveryAck(
            envelope_id=envelope.envelope_id,
            status=DeliveryAckStatus.ACKED,
            received_at=utc_now(),
        )

    async def heartbeat(self) -> None:
        if not self._connected:
            await self.connect()
        self._post(
            {
                "kind": "delivery_envelope",
                "schema": "joy.delivery_envelope/v1",
                "payload": {
                    "kind": "heartbeat",
                    "transport_version": TRANSPORT_VERSION,
                    "publisher": {"publisher_id": "joymesh"},
                },
            }
        )

    def negotiated_version(self) -> int:
        return TRANSPORT_VERSION

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "joymesh-http-delivery/1",
        }
        token = (
            self.token if self.token is not None else (os.environ.get("JOYMESH_JOYCTL_TOKEN") or "")
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {"ok": True}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectionError(f"joycli_http_delivery_failed:{exc.code}:{detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"joycli_http_delivery_unreachable:{exc}") from exc
