"""Built-in connector registry with fail-fast conformance validation."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from joymesh.runtime_v1.capabilities import CapabilityRegistry
from joymesh.runtime_v1.connector_protocol import ConnectorRuntime


class ConnectorRegistryError(TypeError):
    """Raised when a built-in connector fails protocol or registry validation."""


_REQUIRED_ATTRS = (
    "connector_id",
    "display_name",
    "connector_revision",
    "declared_capabilities",
    "certification_profiles",
    "discover",
    "classify_auth_status",
    "inspect_authentication",
    "build_authentication_plan",
    "verify_authentication",
    "verify_adapter",
    "adapter_verification_notice",
    "execution_environment",
    "build_exec_argv",
    "build_read_only_cert_argv",
    "parse_events",
    "execute",
    "cancel",
)

_CONNECTORS: dict[str, ConnectorRuntime] | None = None


def builtin_connectors() -> dict[str, ConnectorRuntime]:
    """Sole built-in registration point for Runtime v1 connectors."""

    global _CONNECTORS
    if _CONNECTORS is None:
        from joymesh.runtime_v1.connectors.claude import ClaudeConnectorRuntime
        from joymesh.runtime_v1.connectors.codex import CodexConnectorRuntime
        from joymesh.runtime_v1.connectors.cursor import CursorConnectorRuntime
        from joymesh.runtime_v1.connectors.grok import GrokConnectorRuntime
        from joymesh.runtime_v1.connectors.opencode import OpenCodeConnectorRuntime

        _CONNECTORS = validate_builtin_connectors(
            {
                "cursor": CursorConnectorRuntime(),
                "codex": CodexConnectorRuntime(),
                "opencode": OpenCodeConnectorRuntime(),
                "claude": ClaudeConnectorRuntime(),
                "grok": GrokConnectorRuntime(),
            }
        )
    return dict(_CONNECTORS)


def get_connector(connector_id: str) -> ConnectorRuntime:
    connectors = builtin_connectors()
    try:
        return connectors[connector_id]
    except KeyError as exc:
        raise KeyError(f"unknown connector runtime: {connector_id}") from exc


def reset_builtin_connectors_for_tests() -> None:
    """Clear the cached registry (test-only)."""

    global _CONNECTORS
    _CONNECTORS = None


def validate_builtin_connectors(
    connectors: Mapping[str, ConnectorRuntime],
    *,
    capability_registry: CapabilityRegistry | None = None,
) -> dict[str, ConnectorRuntime]:
    """Validate uniqueness, required methods, capabilities, and cert argv signature."""

    caps = capability_registry or CapabilityRegistry()
    validated: dict[str, ConnectorRuntime] = {}
    for registry_key, connector in connectors.items():
        _validate_one(registry_key, connector, caps)
        if connector.connector_id in validated:
            raise ConnectorRegistryError(f"duplicate connector_id: {connector.connector_id!r}")
        if registry_key != connector.connector_id:
            raise ConnectorRegistryError(
                f"registry key {registry_key!r} does not match connector_id "
                f"{connector.connector_id!r}"
            )
        validated[connector.connector_id] = connector
    return validated


def _validate_one(registry_key: str, connector: ConnectorRuntime, caps: CapabilityRegistry) -> None:
    for attr in _REQUIRED_ATTRS:
        if not hasattr(connector, attr):
            raise ConnectorRegistryError(
                f"connector {registry_key!r} missing required attribute {attr!r}"
            )

    connector_id = str(getattr(connector, "connector_id", "")).strip()
    display_name = str(getattr(connector, "display_name", "")).strip()
    if not connector_id:
        raise ConnectorRegistryError(f"connector {registry_key!r} has empty connector_id")
    if not display_name:
        raise ConnectorRegistryError(f"connector {connector_id!r} has empty display_name")

    declared = connector.declared_capabilities()
    if not isinstance(declared, frozenset):
        raise ConnectorRegistryError(
            f"connector {connector_id!r} declared_capabilities must return frozenset"
        )
    unknown = sorted(item for item in declared if not caps.known(item))
    if unknown:
        raise ConnectorRegistryError(
            f"connector {connector_id!r} declares unknown capabilities: " + ", ".join(unknown)
        )

    _require_cert_argv_signature(connector)
    _require_callable(connector, "classify_auth_status")
    _require_callable(connector, "parse_events")
    _require_callable(connector, "build_exec_argv")
    _require_callable(connector, "adapter_verification_notice")
    _require_callable(connector, "execution_environment")
    env = connector.execution_environment(read_only=True)
    if not isinstance(env, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} execution_environment "
            "must return Mapping[str, str]"
        )


def _require_callable(connector: ConnectorRuntime, name: str) -> None:
    attr = getattr(connector, name, None)
    if not callable(attr):
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} {name} must be callable"
        )


def _require_cert_argv_signature(connector: ConnectorRuntime) -> None:
    method = connector.build_read_only_cert_argv
    if not callable(method):
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} build_read_only_cert_argv must be callable"
        )
    signature = inspect.signature(method)
    params = signature.parameters
    required = ("executable", "prompt", "workspace")
    for name in required:
        if name not in params:
            raise ConnectorRegistryError(
                f"connector {connector.connector_id!r} build_read_only_cert_argv "
                f"missing required parameter {name!r}"
            )
        param = params[name]
        if param.kind not in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise ConnectorRegistryError(
                f"connector {connector.connector_id!r} build_read_only_cert_argv "
                f"parameter {name!r} must be keyword-capable"
            )
        if param.default is not inspect.Parameter.empty:
            raise ConnectorRegistryError(
                f"connector {connector.connector_id!r} build_read_only_cert_argv "
                f"parameter {name!r} must be required (no default)"
            )
    workspace = params["workspace"]
    annotation = workspace.annotation
    if annotation is inspect.Parameter.empty:
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} build_read_only_cert_argv "
            "workspace parameter must be annotated as Path"
        )
    if annotation is not Path and annotation != "Path":
        origin = getattr(annotation, "__origin__", None)
        if annotation is not Path and origin is not Path:
            # Accept string annotations resolved later; reject obvious mismatches.
            if annotation not in {Path, "Path", "pathlib.Path"}:
                raise ConnectorRegistryError(
                    f"connector {connector.connector_id!r} build_read_only_cert_argv "
                    f"workspace must be Path, got {annotation!r}"
                )

    # Smoke-call with dummy args to ensure Sequence[str] return (no TypeError fallback).
    try:
        argv = method(
            executable="/usr/bin/false",
            prompt="prompt",
            workspace=Path("/tmp"),
        )
    except TypeError as exc:
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} build_read_only_cert_argv "
            f"rejected formal signature: {exc}"
        ) from exc
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} build_read_only_cert_argv "
            "must return a Sequence[str]"
        )
    if not all(isinstance(item, str) for item in argv):
        raise ConnectorRegistryError(
            f"connector {connector.connector_id!r} build_read_only_cert_argv "
            "must return only strings"
        )


def assert_connector_conforms(connector: ConnectorRuntime) -> None:
    """Public helper for tests: validate a single connector instance."""

    validate_builtin_connectors({connector.connector_id: connector})


# Re-export for type checkers / tests that monkeypatch registration.
ConnectorFactory = Callable[[], ConnectorRuntime]
