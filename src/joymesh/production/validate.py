"""Validate JoyMesh production configuration without starting services."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from joymesh.production.config import ProductionConfig, ProductionConfigError, load_production_config


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [item.as_dict() for item in self.issues],
            "config": self.config,
        }


def validate_production_config(config: ProductionConfig | None = None) -> ValidationResult:
    cfg = config or load_production_config()
    issues: list[ValidationIssue] = []

    if cfg.is_production() and cfg.unsigned_mode:
        issues.append(ValidationIssue("unsigned_mode_forbidden", "unsigned mode forbidden in production"))

    if cfg.is_production() and not cfg.socket_path:
        issues.append(ValidationIssue("missing_socket", "socket_path required in production"))

    if cfg.socket_path and cfg.require_absolute_paths and cfg.is_production():
        if not Path(cfg.socket_path).expanduser().is_absolute():
            issues.append(ValidationIssue("relative_socket", "socket_path must be absolute in production"))

    if cfg.outbox_path and cfg.require_absolute_paths and cfg.is_production():
        if not Path(cfg.outbox_path).expanduser().is_absolute():
            issues.append(ValidationIssue("relative_outbox", "outbox_path must be absolute in production"))

    has_inline = bool((os.environ.get("JOYMESH_RUNTIME_SIGNING_KEY") or "").strip())
    has_path = bool(cfg.signing_key_path)
    if cfg.is_production():
        if not has_inline and not has_path:
            issues.append(
                ValidationIssue(
                    "missing_signing_key",
                    "production requires JOYMESH_RUNTIME_SIGNING_KEY or JOYMESH_RUNTIME_SIGNING_KEY_PATH",
                )
            )
        if cfg.allow_ephemeral_signing_key:
            issues.append(
                ValidationIssue(
                    "ephemeral_forbidden",
                    "allow_ephemeral_signing_key must be false in production",
                )
            )

    if has_path:
        path = Path(cfg.signing_key_path).expanduser()
        if not path.exists():
            issues.append(ValidationIssue("signing_key_missing", f"signing key path missing: {path}"))
        else:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                issues.append(
                    ValidationIssue(
                        "insecure_key_permissions",
                        f"signing key permissions {oct(mode)} must not be group/world accessible",
                    )
                )

    if cfg.maximum_frame_size < 1024 or cfg.max_outbox_entries < 1:
        issues.append(ValidationIssue("invalid_bounds", "frame size / outbox limits invalid"))

    errors = [i for i in issues if i.severity == "error"]
    return ValidationResult(ok=not errors, issues=issues, config=cfg.as_dict(redact=True))


def validate_or_raise(config: ProductionConfig | None = None) -> ProductionConfig:
    result = validate_production_config(config)
    if not result.ok:
        messages = "; ".join(f"{i.code}: {i.message}" for i in result.issues if i.severity == "error")
        raise ProductionConfigError(messages)
    return config or load_production_config()
