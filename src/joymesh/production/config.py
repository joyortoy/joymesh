"""Explicit production configuration model for JoyMesh delivery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping


class ProductionConfigError(ValueError):
    """Raised when production configuration is invalid or insecure."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True)
class ProductionConfig:
    environment: str = "development"
    organisation_id: str = "local"
    publisher_id: str = "joymesh"
    socket_path: str = ""
    database_path: str = ""
    outbox_path: str = ""
    signing_key_path: str = ""
    signing_key_id: str = ""
    unsigned_mode: bool = False
    maximum_frame_size: int = 262144
    connection_limits: int = 32
    queue_limits: int = 2000
    retry_max_attempts: int = 8
    outbox_retention_seconds: int = 86400
    log_level: str = "INFO"
    health_address: str = "127.0.0.1:9201"
    metrics_address: str = "127.0.0.1:9202"
    shutdown_grace_seconds: float = 15.0
    backup_path: str = ""
    require_absolute_paths: bool = True
    allow_ephemeral_signing_key: bool = False
    max_outbox_entries: int = 2000
    poll_interval_seconds: float = 0.25

    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if redact:
            data["signing_key_path"] = "***" if self.signing_key_path else ""
        return data

    def redacted_json(self) -> str:
        return json.dumps(self.as_dict(redact=True), indent=2, sort_keys=True)


def load_production_config(
    *,
    overrides: Mapping[str, Any] | None = None,
) -> ProductionConfig:
    root = Path(
        _env("JOYMESH_DATA_DIR", str(Path.home() / ".local/share/joymesh"))
        or str(Path.home() / ".local/share/joymesh")
    )
    cfg = ProductionConfig(
        environment=_env("JOYMESH_ENV", "development") or "development",
        organisation_id=_env("JOYMESH_ORGANISATION_ID", "local") or "local",
        publisher_id=_env("JOYMESH_PUBLISHER_ID", "joymesh") or "joymesh",
        socket_path=_env("JOYMESH_DELIVERY_SOCKET", "") or "",
        database_path=_env("JOYMESH_DATABASE_URL", "") or "",
        outbox_path=_env("JOYMESH_OUTBOX_PATH", str(root / "delivery_outbox.sqlite3"))
        or str(root / "delivery_outbox.sqlite3"),
        signing_key_path=_env("JOYMESH_RUNTIME_SIGNING_KEY_PATH", "") or "",
        signing_key_id=_env("JOYMESH_RUNTIME_SIGNING_KEY_ID", "") or "",
        unsigned_mode=_env_bool("JOYMESH_ALLOW_UNSIGNED", False),
        maximum_frame_size=_env_int("JOYMESH_MAX_FRAME_SIZE", 262144),
        connection_limits=_env_int("JOYMESH_CONNECTION_LIMITS", 32),
        queue_limits=_env_int("JOYMESH_QUEUE_LIMITS", 2000),
        retry_max_attempts=_env_int("JOYMESH_RETRY_MAX_ATTEMPTS", 8),
        outbox_retention_seconds=_env_int("JOYMESH_OUTBOX_RETENTION_SECONDS", 86400),
        log_level=(_env("JOYMESH_LOG_LEVEL", "INFO") or "INFO").upper(),
        health_address=_env("JOYMESH_HEALTH_ADDRESS", "127.0.0.1:9201") or "127.0.0.1:9201",
        metrics_address=_env("JOYMESH_METRICS_ADDRESS", "127.0.0.1:9202") or "127.0.0.1:9202",
        shutdown_grace_seconds=_env_float("JOYMESH_SHUTDOWN_GRACE_SECONDS", 15.0),
        backup_path=_env("JOYMESH_BACKUP_PATH", str(root / "backups")) or str(root / "backups"),
        require_absolute_paths=_env_bool("JOYMESH_REQUIRE_ABSOLUTE_PATHS", True),
        allow_ephemeral_signing_key=_env_bool("JOYMESH_ALLOW_EPHEMERAL_SIGNING_KEY", False),
        max_outbox_entries=_env_int("JOYMESH_MAX_OUTBOX_ENTRIES", 2000),
        poll_interval_seconds=_env_float("JOYMESH_POLL_INTERVAL_SECONDS", 0.25),
    )
    if overrides:
        data = asdict(cfg)
        data.update({k: v for k, v in overrides.items() if k in data})
        cfg = ProductionConfig(**data)
    return cfg
