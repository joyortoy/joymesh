"""Protocol decoders shared by subprocess adapters."""

from __future__ import annotations

import json
from typing import Any

from joymesh.harnesses.contracts import ProtocolKind


class ProtocolDecodeError(ValueError):
    pass


def decode_record(protocol: ProtocolKind, value: str) -> dict[str, Any]:
    """Decode one complete native record without assuming all CLIs are JSONL."""

    if protocol in {ProtocolKind.JSONL, ProtocolKind.STREAM_JSON, ProtocolKind.JSON}:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProtocolDecodeError("invalid JSON record") from exc
        if isinstance(decoded, dict):
            return decoded
        return {"type": "output", "message": decoded}
    if protocol is ProtocolKind.TEXT:
        return {"type": "output", "message": value}
    if protocol in {ProtocolKind.SDK, ProtocolKind.ACP, ProtocolKind.SERVER}:
        raise ProtocolDecodeError(f"{protocol.value} records require a protocol-specific client")
    raise ProtocolDecodeError("harness has no executable protocol")


def decode_record_lenient(protocol: ProtocolKind, value: str) -> dict[str, Any]:
    """Preserve malformed native output as redacted plain text for diagnostics."""

    try:
        return decode_record(protocol, value)
    except ProtocolDecodeError:
        return {"type": "invalid_native_output", "message": value}
