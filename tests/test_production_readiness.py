"""Production readiness tests for JoyMesh config, keys, and delivery backup."""

from __future__ import annotations

from pathlib import Path

import pytest

from joymesh.delivery.backup import backup_delivery_outbox, restore_delivery_outbox
from joymesh.delivery.key_lifecycle import generate_runtime_signing_key, inspect_runtime_signing_key
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.delivery.publisher import RuntimeDeliveryPublisher
from joymesh.production.validate import validate_production_config


def test_production_validate_requires_signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY", raising=False)
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY_PATH", raising=False)
    monkeypatch.setenv("JOYMESH_DELIVERY_SOCKET", str(tmp_path / "sock"))
    monkeypatch.setenv("JOYMESH_OUTBOX_PATH", str(tmp_path / "outbox.sqlite3"))
    monkeypatch.setenv("JOYMESH_BACKUP_PATH", str(tmp_path / "backups"))
    result = validate_production_config()
    assert result.ok is False
    assert any(i.code == "missing_signing_key" for i in result.issues)


def test_publisher_fails_closed_in_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_ENV", "production")
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY", raising=False)
    monkeypatch.delenv("JOYMESH_RUNTIME_SIGNING_KEY_PATH", raising=False)
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    with pytest.raises(RuntimeError, match="production signing key required"):
        RuntimeDeliveryPublisher(outbox)


def test_key_generate_never_returns_private(tmp_path: Path) -> None:
    dest = tmp_path / "signing.key"
    generated = generate_runtime_signing_key(destination=dest, key_id="test-key")
    payload = generated.as_dict(include_private=False)
    assert "private_key" not in payload
    assert dest.exists()
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    inspected = inspect_runtime_signing_key(dest)
    assert inspected["private_key_redacted"] is True
    assert inspected["public_key"]


def test_delivery_backup_restore(tmp_path: Path) -> None:
    outbox_path = tmp_path / "outbox.sqlite3"
    outbox = DeliveryOutbox(outbox_path)
    outbox.close()
    assert outbox_path.exists()
    backup_dir = tmp_path / "backup"
    manifest = backup_delivery_outbox(outbox_path=outbox_path, destination=backup_dir)
    assert manifest.outbox_sha256
    restored = tmp_path / "restored.sqlite3"
    restore_delivery_outbox(backup_dir=backup_dir, outbox_path=restored)
    assert restored.exists()
