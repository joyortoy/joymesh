"""Runtime signing key lifecycle helpers for JoyMesh (private keys stay local)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from joymesh.control_plane.security import generate_node_keypair, public_key_from_private, store_private_key


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GeneratedRuntimeKey:
    key_id: str
    public_key: str
    private_key_path: str
    algorithm: str = "ed25519"
    created_at: str = ""

    def as_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        data = {
            "key_id": self.key_id,
            "public_key": self.public_key,
            "private_key_path": self.private_key_path,
            "algorithm": self.algorithm,
            "created_at": self.created_at or _utc_now(),
        }
        if include_private:
            raise ValueError("refusing to include private key material in dict output")
        return data


def generate_runtime_signing_key(
    *,
    destination: Path,
    key_id: str | None = None,
    overwrite: bool = False,
) -> GeneratedRuntimeKey:
    """Generate Ed25519 runtime signing key; write private key with 0600 perms."""

    destination = Path(destination).expanduser()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing key file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    private_key, public_key = generate_node_keypair()
    store_private_key(destination, private_key)
    os.chmod(destination, 0o600)
    derived_id = key_id or f"ed25519:{hashlib.sha256(public_key.encode()).hexdigest()[:16]}"
    meta = {
        "key_id": derived_id,
        "public_key": public_key,
        "algorithm": "ed25519",
        "created_at": _utc_now(),
        "private_key_path": str(destination),
    }
    meta_path = destination.with_suffix(destination.suffix + ".pub.json")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(meta_path, 0o644)
    return GeneratedRuntimeKey(
        key_id=derived_id,
        public_key=public_key,
        private_key_path=str(destination),
        created_at=meta["created_at"],
    )


def inspect_runtime_signing_key(path: Path) -> dict[str, Any]:
    """Inspect a private key file without printing the private material."""

    path = Path(path).expanduser()
    private = path.read_text(encoding="utf-8").strip()
    public = public_key_from_private(private)
    mode = oct(os.stat(path).st_mode & 0o777)
    return {
        "path": str(path),
        "algorithm": "ed25519",
        "public_key": public,
        "key_id_suggestion": f"ed25519:{hashlib.sha256(public.encode()).hexdigest()[:16]}",
        "permissions": mode,
        "private_key_present": True,
        "private_key_redacted": True,
    }
