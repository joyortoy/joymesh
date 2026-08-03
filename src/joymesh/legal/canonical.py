"""Deterministic JSON hashing compatible with JoyLegal canonicalization."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any


def _default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_default,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def content_hash(payload: dict[str, Any], *, drop: tuple[str, ...] = ()) -> str:
    data = dict(payload)
    for key in drop:
        data.pop(key, None)
    return sha256_hex(data)


def deterministic_id(namespace: str, payload: object, *, length: int = 24) -> str:
    return f"{namespace}_{sha256_hex(payload)[:length]}"
