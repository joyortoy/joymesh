"""Unit tests for Keychain/file secrets vault (no real provider keys)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joymesh import secrets as secrets_mod


def test_file_backend_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("JOYMESH_SECRETS_ALLOW_FILE", "1")
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)

    meta = secrets_mod.set_secret("openai", "sk-test-openai-key-value")
    assert meta.present is True
    assert secrets_mod.get_secret("openai") == "sk-test-openai-key-value"
    listed = secrets_mod.list_secrets()
    assert any(item.name == "openai" and item.present for item in listed)
    assert secrets_mod.delete_secret("openai") is True
    assert secrets_mod.get_secret("openai") is None


def test_import_and_sync_opencode_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("JOYMESH_SECRETS_ALLOW_FILE", "1")
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)
    # Isolate index path under tmp home
    monkeypatch.setenv("HOME", str(tmp_path))

    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "opencode-go": {"type": "api", "key": "sk-go-test"},
                "openrouter": {"type": "api", "key": "sk-or-test"},
                "oauth-provider": {"type": "oauth", "refresh": "x"},
            }
        ),
        encoding="utf-8",
    )
    imported = secrets_mod.import_opencode_auth(auth)
    assert set(imported) == {"opencode-go", "openrouter"}

    out = tmp_path / "auth-out.json"
    path = secrets_mod.sync_opencode_auth(out)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["opencode-go"]["key"] == "sk-go-test"
    assert payload["openrouter"]["key"] == "sk-or-test"
    assert "oauth-provider" not in payload or payload.get("oauth-provider", {}).get("type") != "api"


def test_mask_secret() -> None:
    assert "***" == secrets_mod.mask_secret("short")
    masked = secrets_mod.mask_secret("sk-abcdefghijklmnop")
    assert "sk-a" in masked
    assert "mnop" in masked
