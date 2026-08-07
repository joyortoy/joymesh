"""Local OS Keychain credential vault for harness/provider API keys.

JoyMesh never stores provider secrets in SQLite or config.yaml.
Secrets live in the macOS Keychain (or a plaintext fallback file that is
chmod 600, for non-macOS / CI only when JOYMESH_SECRETS_ALLOW_FILE=1).

OpenCode still reads ~/.local/share/opencode/auth.json; use
``joymesh secrets sync-opencode`` after storing keys so auth.json is
rebuilt from Keychain across restarts without re-typing keys.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVICE_NAME = "joymesh.secrets"
ACCOUNT_PREFIX = "provider:"

# Map vault names → OpenCode auth.json provider ids (type=api).
OPENCODE_PROVIDER_IDS: frozenset[str] = frozenset(
    {
        "opencode-go",
        "opencode",
        "openrouter",
        "anthropic",
        "openai",
        "google",
        "groq",
        "xai",
        "azure",
        "bedrock",
    }
)

# Map vault names → shell env vars (for tools that read env, not auth.json).
ENV_ALIASES: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "grok": "XAI_API_KEY",
    "cursor": "CURSOR_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class SecretsError(RuntimeError):
    """Credential vault failure."""


@dataclass(frozen=True)
class SecretMeta:
    name: str
    backend: str
    present: bool

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "backend": self.backend, "present": self.present}


def _account(name: str) -> str:
    cleaned = name.strip().lower()
    if not cleaned or "/" in cleaned or "\\" in cleaned:
        raise SecretsError(f"invalid secret name: {name!r}")
    return f"{ACCOUNT_PREFIX}{cleaned}"


def _config_dir() -> Path:
    return Path(os.environ.get("JOYMESH_CONFIG_DIR", Path.home() / ".config" / "joymesh")).expanduser()


def _file_store_path() -> Path:
    return _config_dir() / "secrets.local.json"


def _index_path() -> Path:
    return _config_dir() / "secrets.index.json"


def keychain_available() -> bool:
    if platform.system() != "Darwin":
        return False
    return subprocess.run(["which", "security"], capture_output=True, check=False).returncode == 0


def _keychain_set(name: str, value: str) -> None:
    account = _account(name)
    # Prefer generic password in login keychain; -U updates existing.
    proc = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s",
            SERVICE_NAME,
            "-a",
            account,
            "-w",
            value,
            "-U",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SecretsError(proc.stderr.strip() or "security add-generic-password failed")


def _keychain_get(name: str) -> str | None:
    account = _account(name)
    proc = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            SERVICE_NAME,
            "-a",
            account,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.rstrip("\n")


def _keychain_delete(name: str) -> bool:
    account = _account(name)
    proc = subprocess.run(
        [
            "security",
            "delete-generic-password",
            "-s",
            SERVICE_NAME,
            "-a",
            account,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _keychain_list_names() -> list[str]:
    # security dump-keychain is heavy; track an index file of names only (no values).
    index = _index_path()
    if not index.exists():
        return []
    try:
        payload = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = payload.get("names", [])
    return sorted({str(n).lower() for n in names if str(n).strip()})


def _index_add(name: str) -> None:
    index = _index_path()
    index.parent.mkdir(parents=True, exist_ok=True)
    names = set(_keychain_list_names())
    names.add(name.strip().lower())
    index.write_text(
        json.dumps({"names": sorted(names)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(index, 0o600)


def _index_remove(name: str) -> None:
    index = _index_path()
    if not index.exists():
        return
    names = set(_keychain_list_names())
    names.discard(name.strip().lower())
    index.write_text(
        json.dumps({"names": sorted(names)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_load() -> dict[str, str]:
    path = _file_store_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecretsError(f"secrets file unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise SecretsError("secrets file must be a JSON object")
    return {str(k).lower(): str(v) for k, v in payload.items() if str(v)}


def _file_save(data: dict[str, str]) -> None:
    if os.environ.get("JOYMESH_SECRETS_ALLOW_FILE", "").strip() not in {"1", "true", "yes"}:
        raise SecretsError(
            "file backend disabled; set JOYMESH_SECRETS_ALLOW_FILE=1 or use macOS Keychain"
        )
    path = _file_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def backend_name() -> str:
    if keychain_available():
        return "macos-keychain"
    return "file"


def set_secret(name: str, value: str) -> SecretMeta:
    cleaned = name.strip().lower()
    secret = value.strip()
    if not secret:
        raise SecretsError("refusing empty secret")
    if keychain_available():
        _keychain_set(cleaned, secret)
        _index_add(cleaned)
        return SecretMeta(name=cleaned, backend="macos-keychain", present=True)
    data = _file_load()
    data[cleaned] = secret
    _file_save(data)
    return SecretMeta(name=cleaned, backend="file", present=True)


def get_secret(name: str) -> str | None:
    cleaned = name.strip().lower()
    if keychain_available():
        return _keychain_get(cleaned)
    return _file_load().get(cleaned)


def delete_secret(name: str) -> bool:
    cleaned = name.strip().lower()
    if keychain_available():
        deleted = _keychain_delete(cleaned)
        _index_remove(cleaned)
        return deleted
    data = _file_load()
    if cleaned not in data:
        return False
    del data[cleaned]
    _file_save(data)
    return True


def list_secrets() -> list[SecretMeta]:
    backend = backend_name()
    if keychain_available():
        names = _keychain_list_names()
        return [
            SecretMeta(name=n, backend=backend, present=_keychain_get(n) is not None) for n in names
        ]
    return [SecretMeta(name=n, backend=backend, present=True) for n in sorted(_file_load())]


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]} (len={len(value)})"


def default_opencode_auth_path() -> Path:
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def import_opencode_auth(path: Path | None = None) -> list[str]:
    """Import OpenCode auth.json API keys into the vault (does not print values)."""

    auth_path = path or default_opencode_auth_path()
    if not auth_path.exists():
        raise SecretsError(f"OpenCode auth file missing: {auth_path}")
    payload = json.loads(auth_path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for provider, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type", "")).lower() != "api":
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        set_secret(str(provider), key)
        imported.append(str(provider).lower())
    return sorted(set(imported))


def sync_opencode_auth(path: Path | None = None, *, providers: list[str] | None = None) -> Path:
    """Rebuild OpenCode auth.json from Keychain for listed (or known) providers."""

    auth_path = path or default_opencode_auth_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if auth_path.exists():
        try:
            loaded = json.loads(auth_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}

    names = providers or [
        meta.name for meta in list_secrets() if meta.name in OPENCODE_PROVIDER_IDS or meta.present
    ]
    # Always try known Opencode providers from index + aliases.
    for candidate in sorted(OPENCODE_PROVIDER_IDS):
        if candidate not in names:
            names.append(candidate)

    written = 0
    for name in names:
        value = get_secret(name)
        if not value:
            continue
        existing[name] = {"type": "api", "key": value}
        written += 1
    if written == 0:
        raise SecretsError("no OpenCode-compatible secrets found in vault to sync")

    tmp = auth_path.with_suffix(auth_path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(auth_path)
    os.chmod(auth_path, 0o600)
    return auth_path


def export_env_lines(*, names: list[str] | None = None) -> list[str]:
    """Return shell export lines for env-backed providers (values included for eval)."""

    lines: list[str] = []
    metas = {m.name: m for m in list_secrets()}
    targets = names or sorted(set(ENV_ALIASES) | set(metas))
    for name in targets:
        env_name = ENV_ALIASES.get(name.strip().lower())
        if not env_name:
            continue
        value = get_secret(name)
        if not value:
            continue
        # Single-quote value safely for POSIX shells.
        escaped = value.replace("'", "'\"'\"'")
        lines.append(f"export {env_name}='{escaped}'")
    return lines
