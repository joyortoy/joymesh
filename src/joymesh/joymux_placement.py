"""Fetch JoyMux context placement for JoyMesh CLI runs.

OWNER: JoyMesh (validation/consumer only). JoyMux owns placement selection.
Production ``joymesh run`` must attach a JoyMux ``joy.context_placement_decision/v1``
before execution; this helper speaks UNIX JSON-RPC to the local daemon.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

EXECUTION_PROTOCOL_V1 = "joymux.execution.v1"
DEFAULT_SOCKET = Path.home() / ".joymux" / "runtime.sock"


class JoyMuxPlacementError(RuntimeError):
    """Raised when JoyMux placement cannot be obtained."""

    def __init__(self, message: str, *, code: str = "joymux_placement_failed") -> None:
        super().__init__(message)
        self.code = code


def resolve_joymux_socket() -> Path:
    for candidate in (
        os.environ.get("JOYCLI_JOYMUX_PLACEMENT_SOCKET"),
        os.environ.get("JOYMUX_SOCKET"),
        os.environ.get("JOYMUX_RUNTIME_SOCKET"),
    ):
        if candidate:
            return Path(candidate).expanduser()
    data_dir = os.environ.get("JOYMUX_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "runtime.sock"
    return DEFAULT_SOCKET


class JoyMuxUnixRpc:
    """Connection-bound JSON-RPC client (register sticks for subsequent methods)."""

    def __init__(self, socket_path: Path | None = None, *, timeout: float = 5.0) -> None:
        self.socket_path = socket_path or resolve_joymux_socket()
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        if self._sock is not None:
            return
        if not self.socket_path.exists():
            raise JoyMuxPlacementError(
                f"JoyMux socket missing at {self.socket_path}; run `joymux daemon start`",
                code="joymux_socket_missing",
            )
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise JoyMuxPlacementError(
                f"cannot connect to JoyMux at {self.socket_path}: {exc}",
                code="joymux_connect_failed",
            ) from exc
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> JoyMuxUnixRpc:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.connect()
        assert self._sock is not None
        req_id = str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params if params is not None else {},
        }
        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self._sock.sendall(raw)
        buf = b""
        while b"\n" not in buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise JoyMuxPlacementError(
                    "JoyMux closed the connection",
                    code="joymux_connection_closed",
                )
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        resp = json.loads(line.decode("utf-8"))
        if resp.get("error") is not None:
            err = resp["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            code = "joymux_rpc_error"
            if isinstance(err, dict):
                data = err.get("data") or {}
                if isinstance(data, dict) and data.get("code"):
                    code = str(data["code"])
            raise JoyMuxPlacementError(str(message), code=code)
        return resp.get("result")


def build_place_params(
    *,
    harness: str,
    workspace: str | Path,
    task: str,
    organisation_id: str = "joyui",
    requirements_id: str | None = None,
) -> dict[str, Any]:
    """Build JoyMux ``placement/place`` params for a local harness run."""

    harness_id = (harness or "grok").strip().lower()
    if harness_id in {"", "auto"}:
        harness_id = "grok"
    req_id = requirements_id or f"req_{uuid4().hex[:12]}"
    corr = f"corr_{uuid4().hex[:12]}"
    revision = f"rsnap_{uuid4().hex[:12]}"
    worker = f"{harness_id}_local"
    return {
        "requirements": {
            "requirements_id": req_id,
            "organisation_id": organisation_id,
            "mission_id": "joymesh_cli_run",
            "objective_id": "harness_execution",
            "correlation_id": corr,
            "required_capabilities": ["local_command_execution"],
            "eligible_harnesses": [harness_id],
            "historical_preferences": {"preferred_harness": harness_id},
            "local_only": True,
            "fresh_reasoning_required": True,
            "independent_verifier_required": False,
            "workspace": str(workspace),
            "task_preview": str(task)[:240],
        },
        "runtime_facts": {
            "runtime_snapshot_revision": revision,
            "candidates": [
                {
                    "worker_id": worker,
                    "agent_id": worker,
                    "runtime_id": f"rt_{worker}",
                    "harness": harness_id,
                    "harnesses": [harness_id],
                    "eligible": True,
                    "health": "healthy",
                    "available_capacity": 1,
                }
            ],
        },
    }


def fetch_context_placement(
    *,
    harness: str,
    workspace: str | Path,
    task: str,
    client_name: str = "joymesh-cli",
    socket_path: Path | None = None,
) -> dict[str, Any]:
    """Register with JoyMux and return an executable context placement decision."""

    params = build_place_params(harness=harness, workspace=workspace, task=task)
    with JoyMuxUnixRpc(socket_path) as rpc:
        rpc.call(
            "client/register",
            {
                "protocol_version": EXECUTION_PROTOCOL_V1,
                "client_name": client_name,
                "requested_capabilities": [],
            },
        )
        decision = rpc.call("placement/place", params)
    if not isinstance(decision, dict):
        raise JoyMuxPlacementError(
            "JoyMux placement/place returned a non-object",
            code="invalid_placement",
        )
    if not decision.get("executable"):
        raise JoyMuxPlacementError(
            f"JoyMux placement is not executable: {decision.get('selection_reason_codes')}",
            code="placement_not_executable",
        )
    if not decision.get("selected_harness"):
        raise JoyMuxPlacementError(
            "JoyMux placement missing selected_harness",
            code="placement_missing_harness",
        )
    # Align runtime revision facts for JoyMesh enforce_placement when present.
    return decision
