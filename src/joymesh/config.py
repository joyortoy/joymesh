"""JoyMesh user configuration (~/.config/joymesh/config.yaml)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

REMOVED_HARNESS_IDS = frozenset({"fake", "joy"})
LEGACY_JOY_MIGRATION_MESSAGE = (
    'The previously selected “joy” harness has been removed.\n'
    "Choose a supported or custom harness to continue."
)


class MetricsMode(StrEnum):
    ALWAYS = "always"
    ASK = "ask"
    NEVER = "never"


TelemetryMode = MetricsMode


@dataclass(frozen=True)
class MetricsSettings:
    mode: MetricsMode | None = None
    consent_completed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value if self.mode is not None else None,
            "consent_completed": self.consent_completed,
        }


TelemetrySettings = MetricsSettings


@dataclass(frozen=True)
class CustomHarnessConfig:
    harness_id: str
    display_name: str
    executable: str
    args: tuple[str, ...] = ()
    input_mode: str = "stdin"
    output_mode: str = "jsonl"
    timeout_seconds: int = 1800
    working_directory: str = "inherit"
    environment_allowlist: tuple[str, ...] = ("PATH", "HOME")
    # Canonical Capability.value strings; empty = no special capabilities claimed.
    capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "executable": self.executable,
            "args": list(self.args),
            "input_mode": self.input_mode,
            "output_mode": self.output_mode,
            "timeout_seconds": self.timeout_seconds,
            "working_directory": self.working_directory,
            "environment_allowlist": list(self.environment_allowlist),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class HarnessPreferences:
    """Operational harness preferences — separate from anonymous metrics."""

    enabled: tuple[str, ...] = ()
    default: str | None = None  # None => ask each run
    custom: dict[str, CustomHarnessConfig] = field(default_factory=dict)
    selection_required: bool = False
    migration_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": list(self.enabled),
            "default": self.default,
            "selection_required": self.selection_required,
            "migration_message": self.migration_message,
            "custom": {key: value.as_dict() for key, value in sorted(self.custom.items())},
        }


@dataclass(frozen=True)
class UserConfig:
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    harnesses: HarnessPreferences = field(default_factory=HarnessPreferences)

    @property
    def telemetry(self) -> MetricsSettings:
        return self.metrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.as_dict(),
            "harnesses": self.harnesses.as_dict(),
        }


def default_config_dir() -> Path:
    override = os.environ.get("JOYMESH_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/joymesh").expanduser()


def default_config_path() -> Path:
    return default_config_dir() / "config.yaml"


def load_user_config(path: Path | None = None) -> UserConfig:
    config_path = path or default_config_path()
    if not config_path.is_file():
        return UserConfig()
    text = config_path.read_text(encoding="utf-8")
    data = _parse_simple_yaml(text)
    config = user_config_from_mapping(data)
    migrated, changed = migrate_legacy_harness_preferences(config)
    if changed:
        save_user_config(migrated, config_path)
        return migrated
    return config


def save_user_config(config: UserConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_dump_simple_yaml(config.as_dict()), encoding="utf-8")
    return config_path


def _settings_from_raw(raw: dict[str, Any]) -> MetricsSettings:
    mode_raw = raw.get("mode")
    mode: MetricsMode | None = None
    if mode_raw is not None and str(mode_raw).strip():
        try:
            mode = MetricsMode(str(mode_raw).strip().lower())
        except ValueError:
            mode = None
    consent = bool(raw.get("consent_completed", False))
    if mode is not None and "consent_completed" not in raw:
        consent = True
    return MetricsSettings(mode=mode, consent_completed=consent)


def _custom_from_raw(harness_id: str, raw: dict[str, Any]) -> CustomHarnessConfig:
    args_raw = raw.get("args") or []
    if isinstance(args_raw, str):
        args = tuple(part for part in args_raw.split() if part)
    elif isinstance(args_raw, list):
        args = tuple(str(item) for item in args_raw)
    else:
        args = ()
    allow_raw = raw.get("environment_allowlist") or ["PATH", "HOME"]
    if isinstance(allow_raw, str):
        allow = tuple(part for part in allow_raw.replace(",", " ").split() if part)
    elif isinstance(allow_raw, list):
        allow = tuple(str(item) for item in allow_raw)
    else:
        allow = ("PATH", "HOME")
    timeout = raw.get("timeout_seconds", 1800)
    caps_raw = raw.get("capabilities") or ()
    if isinstance(caps_raw, str):
        capabilities = tuple(part for part in caps_raw.replace(",", " ").split() if part)
    elif isinstance(caps_raw, list):
        capabilities = tuple(str(item) for item in caps_raw)
    else:
        capabilities = ()
    return CustomHarnessConfig(
        harness_id=harness_id,
        display_name=str(raw.get("display_name") or harness_id),
        executable=str(raw.get("executable") or ""),
        args=args,
        input_mode=str(raw.get("input_mode") or "stdin"),
        output_mode=str(raw.get("output_mode") or "jsonl"),
        timeout_seconds=int(timeout) if timeout is not None else 1800,
        working_directory=str(raw.get("working_directory") or "inherit"),
        environment_allowlist=allow,
        capabilities=capabilities,
    )


def _harness_prefs_from_raw(data: dict[str, Any]) -> HarnessPreferences:
    harnesses_raw = data.get("harnesses")
    raw: dict[str, Any] = harnesses_raw if isinstance(harnesses_raw, dict) else {}
    # Legacy top-level default_harness / harnesses.default
    default_raw = raw.get("default")
    if default_raw is None and data.get("default_harness") is not None:
        default_raw = data.get("default_harness")
    enabled_raw = raw.get("enabled") or ()
    if isinstance(enabled_raw, str):
        enabled = tuple(part for part in enabled_raw.replace(",", " ").split() if part)
    elif isinstance(enabled_raw, list):
        enabled = tuple(str(item) for item in enabled_raw)
    else:
        enabled = ()
    custom_raw = raw.get("custom") if isinstance(raw.get("custom"), dict) else {}
    custom = {
        str(key): _custom_from_raw(str(key), value if isinstance(value, dict) else {})
        for key, value in (custom_raw or {}).items()
    }
    default = str(default_raw) if default_raw not in (None, "", "null") else None
    return HarnessPreferences(
        enabled=enabled,
        default=default,
        custom=custom,
        selection_required=bool(raw.get("selection_required", False)),
        migration_message=(
            str(raw["migration_message"]) if raw.get("migration_message") is not None else None
        ),
    )


def migrate_legacy_harness_preferences(
    config: UserConfig,
) -> tuple[UserConfig, bool]:
    """Clear removed fake/joy defaults without silently picking another harness."""

    prefs = config.harnesses
    changed = False
    enabled = list(prefs.enabled)
    default = prefs.default
    message = prefs.migration_message
    selection_required = prefs.selection_required

    had_removed = any(item in REMOVED_HARNESS_IDS for item in enabled) or (
        default in REMOVED_HARNESS_IDS
    )
    cleaned_enabled = [item for item in enabled if item not in REMOVED_HARNESS_IDS]
    if cleaned_enabled != list(enabled):
        changed = True
        enabled = cleaned_enabled

    if default in REMOVED_HARNESS_IDS:
        changed = True
        removed_default = default
        default = None
        if removed_default == "joy":
            message = LEGACY_JOY_MIGRATION_MESSAGE
        else:
            message = (
                'The previously selected “fake” harness has been removed.\n'
                "Choose a supported or custom harness to continue."
            )
    elif any(item == "joy" for item in prefs.enabled):
        message = LEGACY_JOY_MIGRATION_MESSAGE
    elif had_removed and message is None:
        message = (
            'The previously selected “fake” harness has been removed.\n'
            "Choose a supported or custom harness to continue."
        )

    if had_removed:
        changed = True
        selection_required = True

    # Also catch legacy default_harness already folded into prefs.default above.
    if not changed:
        return config, False
    updated = replace(
        config,
        harnesses=HarnessPreferences(
            enabled=tuple(enabled),
            default=default,
            custom=dict(prefs.custom),
            selection_required=selection_required,
            migration_message=message,
        ),
    )
    return updated, True


def user_config_from_mapping(data: dict[str, Any]) -> UserConfig:
    metrics_raw = data.get("metrics")
    telemetry_raw = data.get("telemetry")
    if isinstance(metrics_raw, dict):
        metrics = _settings_from_raw(metrics_raw)
    elif isinstance(telemetry_raw, dict):
        metrics = _settings_from_raw(telemetry_raw)
    else:
        metrics = MetricsSettings()
    harnesses = _harness_prefs_from_raw(data)
    return UserConfig(metrics=metrics, harnesses=harnesses)


def set_metrics_mode(
    mode: MetricsMode,
    *,
    path: Path | None = None,
    consent_completed: bool = True,
) -> UserConfig:
    current = load_user_config(path)
    updated = replace(
        current,
        metrics=MetricsSettings(mode=mode, consent_completed=consent_completed),
    )
    save_user_config(updated, path)
    return updated


def set_telemetry_mode(
    mode: MetricsMode,
    *,
    path: Path | None = None,
    consent_completed: bool = True,
) -> UserConfig:
    return set_metrics_mode(mode, path=path, consent_completed=consent_completed)


def save_harness_preferences(
    prefs: HarnessPreferences,
    *,
    path: Path | None = None,
) -> UserConfig:
    current = load_user_config(path)
    updated = replace(current, harnesses=prefs)
    save_user_config(updated, path)
    return updated


def _dump_simple_yaml(data: dict[str, Any], *, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            nested = _dump_simple_yaml(value, indent=indent + 1)
            if nested.strip():
                lines.append(nested.rstrip("\n"))
            continue
        if isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            if not value:
                continue
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{prefix}  -")
                    nested = _dump_simple_yaml(item, indent=indent + 2)
                    if nested.strip():
                        lines.append(nested.rstrip("\n"))
                else:
                    lines.append(f"{prefix}  - {_render_scalar(item)}")
            continue
        lines.append(f"{prefix}{key}: {_render_scalar(value)}")
    return "\n".join(lines) + "\n"


def _render_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


_KEY_VALUE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$")
_LIST_ITEM = re.compile(r"^-\s*(.*?)\s*$")


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    # stack: (indent, container) where container is dict or list
    stack: list[tuple[int, Any]] = [(0, root)]
    pending_list_key: tuple[dict[str, Any], str] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent < stack[-1][0]:
            stack.pop()
            pending_list_key = None
        parent = stack[-1][1]

        list_match = _LIST_ITEM.match(line)
        if list_match:
            item_text = list_match.group(1)
            if not isinstance(parent, list):
                if pending_list_key is not None:
                    owner, key = pending_list_key
                    if not isinstance(owner.get(key), list):
                        owner[key] = []
                    parent = owner[key]
                    stack.append((indent, parent))
                else:
                    continue
            if item_text == "" or item_text.endswith(":"):
                child: dict[str, Any] = {}
                parent.append(child)
                stack.append((indent + 2, child))
            else:
                parent.append(_parse_scalar(item_text))
            continue

        match = _KEY_VALUE.match(line)
        if not match or not isinstance(parent, dict):
            continue
        key, value = match.group(1), match.group(2)
        if value == "":
            # Could be nested mapping or list — peek not available; create dict,
            # promote to list when first "-" arrives via pending_list_key.
            child_map: dict[str, Any] = {}
            parent[key] = child_map
            pending_list_key = (parent, key)
            stack.append((indent + 2, child_map))
            continue
        pending_list_key = None
        parent[key] = _parse_scalar(value)
    return root


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if "." not in value:
            return int(value)
    except ValueError:
        pass
    return value
