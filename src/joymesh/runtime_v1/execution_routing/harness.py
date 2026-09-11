"""Harness adapter boundary — harness identity is independent of backend."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from joymesh.runtime_v1.connector_protocol import ConnectorExecutionContext
from joymesh.runtime_v1.connectors import builtin_connectors
from joymesh.runtime_v1.execution_routing.capabilities import KNOWN_HARNESSES
from joymesh.runtime_v1.execution_routing.process_runner import (
    ProcessRunnerError,
    ProcessRunRequest,
    SafeProcessRunner,
)

HarnessExecutor = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


def _parent_dir(path: str) -> str:
    import os

    return os.path.dirname(os.path.abspath(path))


@runtime_checkable
class HarnessAdapterProtocol(Protocol):
    harness_id: str
    display_name: str

    async def detect(self) -> Mapping[str, Any]: ...

    def capabilities(self) -> frozenset[str]: ...

    async def validate(self, context: Mapping[str, Any]) -> None: ...

    async def prepare(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def execute(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def stream(
        self, prompt: str, context: Mapping[str, Any]
    ) -> AsyncIterator[Mapping[str, Any]]: ...

    async def cancel(self, execution_id: str) -> Mapping[str, Any]: ...

    async def cleanup(self, execution_id: str) -> None: ...

    async def health(self) -> Mapping[str, Any]: ...


@dataclass
class HarnessAdapter:
    """Thin harness handle; optional execute_fn for tests, full protocol for production."""

    harness_id: str
    display_name: str
    execute_fn: HarnessExecutor | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    supported: bool = True
    unsupported_reason: str | None = None

    async def detect(self) -> Mapping[str, Any]:
        return {
            "harness_id": self.harness_id,
            "installed": False,
            "usable": False,
            "supported": self.supported,
            "reason": self.unsupported_reason or "stub_adapter",
        }

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    async def validate(self, context: Mapping[str, Any]) -> None:
        del context
        if not self.supported:
            raise RuntimeError(self.unsupported_reason or f"{self.harness_id} unsupported")

    async def prepare(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        del prompt
        return {"harness_id": self.harness_id, "context_keys": sorted(context)}

    async def run(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self.execute(prompt, context)

    async def execute(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        await self.validate(context)
        if self.execute_fn is None:
            if not self.supported:
                return {
                    "ok": False,
                    "harness_id": self.harness_id,
                    "message": self.unsupported_reason or "unsupported harness",
                    "failure_class": "unsupported_feature",
                }
            return {
                "ok": True,
                "harness_id": self.harness_id,
                "message": "harness adapter stub completed",
                "prompt_digest": str(len(prompt)),
                "stub": True,
            }
        return await self.execute_fn(prompt, context)

    async def stream(
        self, prompt: str, context: Mapping[str, Any]
    ) -> AsyncIterator[Mapping[str, Any]]:
        result = await self.execute(prompt, context)
        yield {"event_type": "execution.completed", "sequence": 1, "result": result}

    async def cancel(self, execution_id: str) -> Mapping[str, Any]:
        del execution_id
        return {"cancelled": True, "lingering": False, "detail": "stub"}

    async def cleanup(self, execution_id: str) -> None:
        del execution_id

    async def health(self) -> Mapping[str, Any]:
        return await self.detect()


@dataclass
class ConnectorHarnessAdapter(HarnessAdapter):
    """Production harness adapter backed by ConnectorRuntime argv contracts + SafeProcessRunner."""

    connector: Any = None
    runner: SafeProcessRunner = field(default_factory=SafeProcessRunner)
    allowed_roots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.connector is None:
            raise ValueError("connector is required")
        self.supported = True
        self.unsupported_reason = None

    async def detect(self) -> Mapping[str, Any]:
        context = ConnectorExecutionContext(node_id="local")
        discovery = await self.connector.discover(context)
        return {
            "harness_id": self.harness_id,
            "installed": discovery.installed,
            "usable": discovery.usable,
            "version": discovery.version,
            "executable_path": discovery.executable_path,
            "fingerprint": discovery.fingerprint,
            "reason_code": discovery.reason_code,
            "supported": True,
        }

    def capabilities(self) -> frozenset[str]:
        declared = getattr(self.connector, "declared_capabilities", None)
        if callable(declared):
            return frozenset(declared())
        return frozenset()

    async def validate(self, context: Mapping[str, Any]) -> None:
        workspace = str(context.get("workspace_path") or "")
        if not workspace:
            raise ProcessRunnerError("workspace_unavailable", "workspace_path required")
        roots = self.allowed_roots or (_parent_dir(workspace),)
        self.runner.validate_cwd(workspace, allowed_roots=roots)
        detection = await self.detect()
        if not detection.get("usable"):
            raise RuntimeError(
                f"{self.harness_id} not usable: {detection.get('reason_code') or 'unavailable'}"
            )

    async def prepare(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        await self.validate(context)
        detection = await self.detect()
        executable = detection.get("executable_path") or shutil.which(self.harness_id)
        if not executable:
            raise RuntimeError(f"{self.harness_id} executable not found")
        argv = list(
            self.connector.build_exec_argv(
                executable=executable,
                prompt=prompt,
                workspace_path=str(context["workspace_path"]),
                read_only=bool(context.get("read_only", True)),
            )
        )
        return {
            "executable": executable,
            "argv_redacted": [*argv[:-1], "<PROMPT>"] if argv else [],
            "argv": argv,
            "version": detection.get("version"),
            "fingerprint": detection.get("fingerprint"),
        }

    async def execute(self, prompt: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        prepared = await self.prepare(prompt, context)
        execution_id = str(context.get("execution_id") or uuid4())
        extra = dict(
            getattr(self.connector, "execution_environment", lambda **_: {})(read_only=True)
        )
        result = await self.runner.run(
            ProcessRunRequest(
                argv=tuple(prepared["argv"]),
                cwd=str(context["workspace_path"]),
                timeout_seconds=float(context.get("timeout_seconds") or 300),
                extra_env_keys=frozenset(extra),
                extra_env=extra,
                run_id=execution_id,
            )
        )
        events: Sequence[Mapping[str, Any]] = ()
        parse = getattr(self.connector, "parse_events", None)
        if callable(parse) and result.stdout:
            try:
                events = tuple(parse(result.stdout))
            except Exception:
                events = ()
        return {
            "ok": result.ok,
            "harness_id": self.harness_id,
            "message": "harness execution completed" if result.ok else result.classification,
            "failure_class": None if result.ok else result.classification,
            "process": result.as_dict(),
            "events": list(events),
            "version": prepared.get("version"),
            "stub": False,
        }

    async def stream(
        self, prompt: str, context: Mapping[str, Any]
    ) -> AsyncIterator[Mapping[str, Any]]:
        prepared = await self.prepare(prompt, context)
        execution_id = str(context.get("execution_id") or uuid4())
        extra = dict(
            getattr(self.connector, "execution_environment", lambda **_: {})(read_only=True)
        )
        async for item in self.runner.stream_lines(
            ProcessRunRequest(
                argv=tuple(prepared["argv"]),
                cwd=str(context["workspace_path"]),
                timeout_seconds=float(context.get("timeout_seconds") or 300),
                extra_env_keys=frozenset(extra),
                extra_env=extra,
                run_id=execution_id,
            )
        ):
            yield item

    async def cancel(self, execution_id: str) -> Mapping[str, Any]:
        return dict(await self.runner.cancel(execution_id))

    async def cleanup(self, execution_id: str) -> None:
        await self.cancel(execution_id)

    async def health(self) -> Mapping[str, Any]:
        return await self.detect()


# Harnesses with documented non-interactive Runtime v1 connector contracts.
_PROCESS_BACKED_HARNESSES: frozenset[str] = frozenset(
    {"codex", "opencode", "claude", "cursor", "grok"}
)


def builtin_harness_adapters(
    *,
    executors: Mapping[str, HarnessExecutor] | None = None,
    connectors: Mapping[str, Any] | None = None,
    runner: SafeProcessRunner | None = None,
    allowed_roots: Sequence[str] | None = None,
    use_real_adapters: bool = False,
) -> dict[str, HarnessAdapter]:
    executors = executors or {}
    connectors = connectors or (builtin_connectors() if use_real_adapters else {})
    shared_runner = runner or SafeProcessRunner()
    roots = tuple(allowed_roots or ())
    out: dict[str, HarnessAdapter] = {}
    for harness_id in sorted(KNOWN_HARNESSES):
        if harness_id in executors:
            out[harness_id] = HarnessAdapter(
                harness_id=harness_id,
                display_name=harness_id.replace("-", " ").title(),
                execute_fn=executors[harness_id],
            )
            continue
        connector = connectors.get(harness_id)
        if use_real_adapters and connector is not None and harness_id in _PROCESS_BACKED_HARNESSES:
            out[harness_id] = ConnectorHarnessAdapter(
                harness_id=harness_id,
                display_name=getattr(connector, "display_name", harness_id),
                connector=connector,
                runner=shared_runner,
                allowed_roots=roots,
            )
            continue
        unsupported = harness_id == "vscode" or (
            use_real_adapters and connector is None and harness_id not in _PROCESS_BACKED_HARNESSES
        )
        out[harness_id] = HarnessAdapter(
            harness_id=harness_id,
            display_name=harness_id.replace("-", " ").title(),
            supported=not unsupported and harness_id != "vscode",
            unsupported_reason=(
                "vscode has no Runtime v1 non-interactive connector contract"
                if harness_id == "vscode"
                else (
                    f"{harness_id} has no Runtime v1 connector"
                    if use_real_adapters and connector is None
                    else None
                )
            ),
        )
    return out
