"""Environment filtering and secret redaction shared by all adapters."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

SAFE_ENVIRONMENT_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
}

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+"),
)


def filter_environment(
    source: Mapping[str, str] | None = None,
    *,
    extra_keys: frozenset[str] = frozenset(),
) -> dict[str, str]:
    environment = source or os.environ
    allowed = SAFE_ENVIRONMENT_KEYS | set(extra_keys)
    return {key: value for key, value in environment.items() if key in allowed}


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
