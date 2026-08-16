"""Backup/restore for JoyMesh durable delivery outbox."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any


class DeliveryBackupError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DeliveryBackupManifest:
    created_at: str
    outbox_sha256: str
    source_outbox: str
    include_private_key: bool
    files: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def backup_delivery_outbox(
    *,
    outbox_path: Path,
    destination: Path,
    signing_key_path: Path | None = None,
    include_private_key: bool = False,
) -> DeliveryBackupManifest:
    outbox_path = Path(outbox_path).expanduser()
    destination = Path(destination).expanduser()
    if not outbox_path.exists():
        raise DeliveryBackupError(f"outbox missing: {outbox_path}")
    if destination.exists() and any(destination.iterdir()):
        raise DeliveryBackupError(f"destination not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    dst = destination / "delivery_outbox.sqlite3"
    src = sqlite3.connect(str(outbox_path))
    try:
        conn = sqlite3.connect(str(dst))
        try:
            src.backup(conn)
        finally:
            conn.close()
    finally:
        src.close()
    os.chmod(dst, 0o600)
    files = {"delivery_outbox.sqlite3": _sha256_file(dst)}

    if include_private_key:
        if signing_key_path is None or not Path(signing_key_path).expanduser().exists():
            raise DeliveryBackupError("include_private_key requested but signing key missing")
        key_dst = destination / "signing.key"
        shutil.copy2(Path(signing_key_path).expanduser(), key_dst)
        os.chmod(key_dst, 0o600)
        files["signing.key"] = _sha256_file(key_dst)

    manifest = DeliveryBackupManifest(
        created_at=_utc_now(),
        outbox_sha256=files["delivery_outbox.sqlite3"],
        source_outbox=str(outbox_path),
        include_private_key=include_private_key,
        files=files,
    )
    (destination / "manifest.json").write_text(
        json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def restore_delivery_outbox(
    *,
    backup_dir: Path,
    outbox_path: Path,
    force: bool = False,
) -> DeliveryBackupManifest:
    backup_dir = Path(backup_dir).expanduser()
    outbox_path = Path(outbox_path).expanduser()
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise DeliveryBackupError("manifest.json missing")
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    src = backup_dir / "delivery_outbox.sqlite3"
    if not src.exists():
        raise DeliveryBackupError("delivery_outbox.sqlite3 missing")
    expected = manifest_data["files"].get("delivery_outbox.sqlite3")
    actual = _sha256_file(src)
    if expected and actual != expected:
        raise DeliveryBackupError("outbox checksum mismatch")
    if outbox_path.exists() and not force:
        raise DeliveryBackupError("refusing to overwrite live outbox without --force")
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = outbox_path.with_suffix(outbox_path.suffix + ".restore-tmp")
    shutil.copy2(src, tmp)
    os.chmod(tmp, 0o600)
    tmp.replace(outbox_path)
    os.chmod(outbox_path, 0o600)
    return DeliveryBackupManifest(
        created_at=str(manifest_data["created_at"]),
        outbox_sha256=actual,
        source_outbox=str(manifest_data.get("source_outbox", "")),
        include_private_key=bool(manifest_data.get("include_private_key")),
        files=dict(manifest_data.get("files", {})),
    )
