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

# Keys matching these patterns are never inherited, even via extra_keys.
SECRET_ENVIRONMENT_KEY_DENYLIST = (
    re.compile(r"(?i)(^|_)(API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE[_-]?KEY)(_|$)"),
    re.compile(r"(?i)^(AWS_|AZURE_|GOOGLE_|OPENAI_|ANTHROPIC_|XAI_|FIREWORKS_|GITHUB_)"),
)

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+"),
)


def _is_denied_secret_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in SECRET_ENVIRONMENT_KEY_DENYLIST)


def filter_environment(
    source: Mapping[str, str] | None = None,
    *,
    extra_keys: frozenset[str] = frozenset(),
) -> dict[str, str]:
    environment = source or os.environ
    allowed = SAFE_ENVIRONMENT_KEYS | set(extra_keys)
    return {
        key: value
        for key, value in environment.items()
        if (key in allowed or key.startswith("JOYMESH_")) and not _is_denied_secret_key(key)
    }


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
