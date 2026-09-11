"""Outbound JoyCTL device loop with real JoyMesh harness execution."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import shlex
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from joymesh.adapters.base import HarnessAdapter
from joymesh.harnesses.registry import HarnessRegistry
from joymesh.models import PermissionMode, RunRequest
from joymesh.runtime import HarnessRuntime
from joymesh.security import filter_environment, redact_secrets

PROTOCOL_VERSION = "1"
MAX_OUTPUT_EVENTS = 100
MAX_OUTPUT_CHARACTERS = 8_000


class DeviceAgentError(RuntimeError):
    """A bounded device-agent failure safe to report to JoyCTL."""


def _job_harness_id(job: Mapping[str, Any]) -> str:
    """Honor an explicit authorized harness; keep Codex for legacy jobs."""
    constraints = job.get("constraints") or {}
    value = (
        constraints.get("harness_id")
        or constraints.get("harness")
        or job.get("harness_id")
        or job.get("harness")
    )
    if value is None:
        return "codex"
    if not isinstance(value, str):
        raise DeviceAgentError("harness id must be a string")
    harness = {"openai": "codex"}.get(value.strip().lower(), value.strip().lower())
    if harness not in {"codex", "opencode", "grok"}:
        raise DeviceAgentError(f"unsupported hosted harness: {harness}")
    capabilities = (job.get("grant") or {}).get("capabilities", [])
    if harness not in capabilities:
        raise DeviceAgentError(f"harness is not authorized by job grant: {harness}")
    return harness


def _adapter_for(harness_id: str) -> HarnessAdapter:
    try:
        return HarnessRegistry().get(harness_id)
    except KeyError as exc:
        raise DeviceAgentError(f"no executable adapter for harness {harness_id}") from exc


def _is_private_harness_record(line: str) -> bool:
    """Keep native private reasoning out of hosted telemetry."""
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    private = {
        "thought",
        "thinking",
        "reasoning",
        "reasoning_delta",
        "thinking_delta",
        "agent_thought_chunk",
    }
    records = [value]
    for key in ("item", "part", "delta", "update", "params"):
        nested = value.get(key)
        if isinstance(nested, dict):
            records.append(nested)
            if isinstance(nested.get("update"), dict):
                records.append(nested["update"])
    return any(
        record.get("type") in private or record.get("sessionUpdate") in private
        for record in records
    )


class ProtocolClient(Protocol):
    def request(self, message: Mapping[str, Any]) -> Any: ...


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode())
    if not isinstance(value, dict):
        raise DeviceAgentError("JoyCTL returned a non-object response")
    return value


def claim_and_auth(
    base: str,
    pairing_code: str,
    device_name: str,
    capabilities: list[str],
    routes: list[dict[str, Any]],
) -> dict[str, Any]:
    claimed = http_json(
        "POST",
        f"{base.rstrip('/')}/api/device-pairings/claim",
        {
            "pairing_code": pairing_code,
            "device_name": device_name,
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": capabilities,
            "routes": routes,
        },
    )
    authenticated = http_json(
        "POST",
        f"{base.rstrip('/')}/api/agent/authenticate",
        {"device_credential": claimed["device_credential"]},
    )
    return {
        "device_id": claimed["device_id"],
        "device_name": device_name,
        "device_credential": claimed["device_credential"],
        "access_token": authenticated["access_token"],
        "capabilities": capabilities,
        "routes": routes,
    }


def refresh_access_token(base: str, session: dict[str, Any]) -> dict[str, Any]:
    authenticated = http_json(
        "POST",
        f"{base.rstrip('/')}/api/agent/authenticate",
        {"device_credential": session["device_credential"]},
    )
    session["access_token"] = authenticated["access_token"]
    return session


def _ws_mask(payload: bytes) -> bytes:
    mask = os.urandom(4)
    return mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def _ws_send(connection: socket.socket, payload: Mapping[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode()
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65_536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    connection.sendall(bytes(header) + _ws_mask(data))


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < length:
        chunk = connection.recv(length - len(buffer))
        if not chunk:
            raise ConnectionError("JoyCTL WebSocket closed")
        buffer.extend(chunk)
    return bytes(buffer)


def _ws_recv(connection: socket.socket) -> dict[str, Any]:
    header = _recv_exact(connection, 2)
    length = header[1] & 0x7F
    masked = bool(header[1] & 0x80)
    if length == 126:
        length = struct.unpack("!H", _recv_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    mask = _recv_exact(connection, 4) if masked else b""
    data = _recv_exact(connection, length)
    if masked:
        data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    value = json.loads(data.decode())
    if not isinstance(value, dict):
        raise DeviceAgentError("JoyCTL WebSocket returned a non-object message")
    return value


class WebSocketProtocolClient:
    def __init__(self, connection: socket.socket):
        self.connection = connection

    def request(self, message: Mapping[str, Any]) -> Any:
        _ws_send(self.connection, message)
        response = _ws_recv(self.connection)
        if response.get("type") == "error":
            raise DeviceAgentError(
                f"JoyCTL rejected {message.get('type')}: {response.get('error_code', 'error')}"
            )
        if response.get("type") != "ack" or response.get("request_type") != message.get("type"):
            raise DeviceAgentError("JoyCTL returned an unexpected acknowledgement")
        return response.get("result")


class WorkspaceResolver:
    def __init__(self, mappings: Mapping[str, str | Path]):
        self._mappings = {key: Path(value).resolve() for key, value in mappings.items()}
        if not self._mappings:
            raise DeviceAgentError("at least one workspace mapping is required")
        for reference, path in self._mappings.items():
            if not reference or "/" in reference or "\\" in reference or ".." in reference:
                raise DeviceAgentError("workspace references must be opaque names")
            if not path.is_dir() or not (path / ".git").exists():
                raise DeviceAgentError(f"mapped workspace is not a Git repository: {reference}")

    def resolve(self, reference: str) -> Path:
        try:
            return self._mappings[reference]
        except KeyError as exc:
            raise DeviceAgentError(f"workspace reference is not mapped: {reference}") from exc


def parse_workspace_mappings(values: Sequence[str]) -> dict[str, Path]:
    mappings: dict[str, Path] = {}
    for value in values:
        reference, separator, raw_path = value.partition("=")
        if not separator or not reference.strip() or not raw_path.strip():
            raise DeviceAgentError("workspace mappings must use REF=/absolute/path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise DeviceAgentError("workspace mapping paths must be absolute")
        mappings[reference.strip()] = path
    return mappings


def _git(workspace: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        env=filter_environment(),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise DeviceAgentError(f"Git inspection failed: {' '.join(arguments)}")
    return completed.stdout


def changed_paths(workspace: Path) -> tuple[str, ...]:
    raw = _git(workspace, "status", "--porcelain=v1", "-z")
    paths: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        text = entry.decode(errors="replace")
        value = text[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise DeviceAgentError("Git reported an unsafe changed path")
        paths.add(value)
    return tuple(sorted(paths))


def workspace_evidence_hash(workspace: Path, paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        digest.update(relative.encode())
        path = workspace / relative
        if path.is_file():
            digest.update(path.read_bytes())
        elif not path.exists():
            digest.update(b"<deleted>")
    return digest.hexdigest()


def event_id(execution_id: str, sequence: int) -> str:
    basis = f"{execution_id}:{sequence}".encode()
    return f"event_{hashlib.sha256(basis).hexdigest()[:32]}"


class JobEventStream:
    def __init__(self, protocol: ProtocolClient, job: Mapping[str, Any]):
        self.protocol = protocol
        self.job = job
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def send(self, event_type: str, payload: Mapping[str, Any]) -> Any:
        async with self.lock:
            self.sequence += 1
            event = {
                "protocol_version": PROTOCOL_VERSION,
                "event_id": event_id(str(self.job["execution_id"]), self.sequence),
                "job_id": self.job["job_id"],
                "execution_id": self.job["execution_id"],
                "sequence": self.sequence,
                "timestamp": datetime.now(UTC).isoformat(),
                "type": event_type,
                "payload": dict(payload),
            }
            return self.protocol.request(
                {
                    "type": "event",
                    "lease_id": self.job["lease_id"],
                    "lease_token": self.job["lease_token"],
                    "event": event,
                }
            )

    async def renew(self, lease_seconds: int = 120) -> Any:
        async with self.lock:
            return self.protocol.request(
                {
                    "type": "renew",
                    "lease_id": self.job["lease_id"],
                    "lease_token": self.job["lease_token"],
                    "lease_seconds": lease_seconds,
                }
            )


async def _renew_until_done(stream: JobEventStream, done: asyncio.Event) -> None:
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=30)
        except TimeoutError:
            await stream.renew()


def _run_verification(workspace: Path, commands: Sequence[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        argv = shlex.split(command)
        if not argv or any(token in {";", "&&", "||", "|", ">", "<"} for token in argv):
            raise DeviceAgentError("verification commands must be argv-only")
        completed = subprocess.run(
            argv,
            cwd=workspace,
            env=filter_environment(),
            capture_output=True,
            check=False,
            timeout=900,
        )
        results.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "output_sha256": hashlib.sha256(completed.stdout + completed.stderr).hexdigest(),
            }
        )
    return results


async def execute_claimed_job(
    job: Mapping[str, Any],
    *,
    resolver: WorkspaceResolver,
    protocol: ProtocolClient,
    adapter: HarnessAdapter | None = None,
    runtime: HarnessRuntime | None = None,
) -> dict[str, Any]:
    workspace = resolver.resolve(str(job.get("workspace_ref", "")))
    if changed_paths(workspace):
        raise DeviceAgentError("refusing to execute in a dirty mapped workspace")
    grant = job.get("grant")
    if not isinstance(grant, dict) or grant.get("production_mutation") is not False:
        raise DeviceAgentError("job grant does not explicitly prohibit production mutation")
    constraints = job.get("constraints")
    if not isinstance(constraints, dict):
        raise DeviceAgentError("job constraints are missing")
    if constraints.get("workspace_ref") != job.get("workspace_ref"):
        raise DeviceAgentError("job workspace binding does not match its constraints")

    harness_id = _job_harness_id(job)
    selected_adapter = adapter or _adapter_for(harness_id)
    selected_runtime = runtime or HarnessRuntime()
    stream = JobEventStream(protocol, job)
    done = asyncio.Event()
    renew_task = asyncio.create_task(_renew_until_done(stream, done))
    output_events = 0
    output_hash = hashlib.sha256()

    await stream.send(
        "accepted",
        {"harness_id": harness_id, "workspace_ref": job["workspace_ref"]},
    )
    await stream.send("started", {"harness_id": harness_id})
    selected_model = constraints.get("model")
    if selected_model is None and harness_id == "opencode":
        selected_model = os.environ.get("JOYMESH_OPENCODE_MODEL")
    if selected_model is not None and not isinstance(selected_model, str):
        raise DeviceAgentError("model id must be a string")
    request = RunRequest(
        model=selected_model,
        permission_mode=(
            PermissionMode.AUTO_APPROVE
            if harness_id == "opencode" and "write_repository" in grant.get("capabilities", [])
            else PermissionMode.DEFAULT
        ),
        task=str(job["instruction"]),
        workspace=str(workspace),
        timeout_seconds=float(constraints.get("timeout_seconds", 3600)),
        preferred_harness=harness_id,
        mission_id=str(job.get("mission_id") or ""),
        execution_id=str(job["execution_id"]),
    )
    launch = selected_adapter.build_launch_spec(request)
    if harness_id == "grok" and "write_repository" in grant.get("capabilities", []):
        launch = launch.model_copy(
            update={"argv": (*launch.argv, "--permission-mode", "auto")}
        )
    if harness_id == "grok" and os.environ.get("JOYMESH_GROK_SANDBOX_PROFILE"):
        argv = list(launch.argv)
        if "--sandbox" not in argv:
            raise DeviceAgentError("Grok launch must retain an explicit sandbox")
        argv[argv.index("--sandbox") + 1] = os.environ["JOYMESH_GROK_SANDBOX_PROFILE"]
        env = dict(launch.env)
        env["JOYMUX_DATA_DIR"] = str(Path.home() / ".grok" / "joymesh-route-state")
        env["JOYMUX_SOCKET"] = str(Path.home() / ".joymux" / "runtime.sock")
        launch = launch.model_copy(update={"argv": tuple(argv), "env": env})

    async def on_line(channel: str, line: str) -> None:
        nonlocal output_events
        if _is_private_harness_record(line):
            return
        safe_line = redact_secrets(line)[:MAX_OUTPUT_CHARACTERS]
        output_hash.update(channel.encode() + b"\0" + safe_line.encode() + b"\n")
        if output_events < MAX_OUTPUT_EVENTS:
            output_events += 1
            await stream.send(
                "output",
                {"stream": channel, "message": safe_line, "harness_id": harness_id},
            )

    try:
        exit_code = await selected_runtime.execute(
            run_id=str(job["execution_id"]),
            launch=launch,
            on_line=on_line,
        )
        commands = constraints.get("required_commands", [])
        if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
            raise DeviceAgentError("required_commands must be a string list")
        verification = _run_verification(workspace, commands) if exit_code == 0 else []
        paths = changed_paths(workspace)
        for path in paths:
            await stream.send("file_changed", {"path": path, "operation": "modified"})
        evidence_hash = workspace_evidence_hash(workspace, paths)
        await stream.send(
            "evidence_produced",
            {
                "evidence_type": "repository_execution",
                "reference": f"joymesh://job/{job['job_id']}/workspace-evidence",
                "content_hash": evidence_hash,
                "metadata": {
                    "changed_file_count": len(paths),
                    "harness_exit_code": exit_code,
                    "output_sha256": output_hash.hexdigest(),
                    "verification": verification,
                },
            },
        )
        verification_passed = all(item["exit_code"] == 0 for item in verification)
        successful = exit_code == 0 and verification_passed and bool(paths)
        terminal_type = "completed" if successful else "failed"
        terminal = await stream.send(
            terminal_type,
            {
                "summary": (
                    f"{harness_id} produced repository changes and mandatory verification passed"
                    if successful
                    else "Harness, evidence, or mandatory verification did not satisfy completion"
                ),
                "exit_code": exit_code if exit_code != 0 else (0 if successful else 1),
                "changed_file_count": len(paths),
                "verification_passed": verification_passed,
            },
        )
        return {
            "success": successful,
            "exit_code": exit_code,
            "changed_paths": list(paths),
            "verification": verification,
            "joycli_completion": terminal,
        }
    except Exception as error:
        try:
            await stream.send(
                "failed",
                {
                    "summary": redact_secrets(str(error))[:1_000],
                    "exit_code": 1,
                    "error_type": error.__class__.__name__,
                },
            )
        except Exception:
            pass
        raise
    finally:
        done.set()
        await asyncio.gather(renew_task, return_exceptions=True)


def connect_protocol(base: str, access_token: str, timeout: float) -> WebSocketProtocolClient:
    from urllib.parse import urlparse

    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        raise DeviceAgentError("TLS WebSocket transport is not configured in this device client")
    connection = socket.create_connection((host, port), timeout=30)
    connection.settimeout(timeout)
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    connection.sendall(
        (
            "GET /api/agent/connect HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {access_token}\r\n\r\n"
        ).encode()
    )
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        buffer.extend(connection.recv(1))
    if b"101" not in buffer.split(b"\r\n", 1)[0]:
        raise DeviceAgentError("JoyCTL WebSocket upgrade failed")
    hello = _ws_recv(connection)
    if hello.get("type") != "connected":
        raise DeviceAgentError("JoyCTL did not confirm the device connection")
    return WebSocketProtocolClient(connection)


def run_loop(
    base: str,
    access_token: str,
    capabilities: list[str],
    routes: list[dict[str, Any]],
    interval: float,
    resolver: WorkspaceResolver,
) -> None:
    protocol = connect_protocol(base, access_token, timeout=180)
    print(json.dumps({"event": "connected"}), flush=True)
    while True:
        heartbeat = protocol.request(
            {
                "type": "heartbeat",
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": capabilities,
                "routes": routes,
            }
        )
        print(json.dumps({"event": "heartbeat_ack", "result": heartbeat}), flush=True)
        job = protocol.request({"type": "claim", "lease_seconds": 120})
        if isinstance(job, dict):
            print(
                json.dumps(
                    {
                        "event": "job_claimed",
                        "job_id": job.get("job_id"),
                        "mission_id": job.get("mission_id"),
                    }
                ),
                flush=True,
            )
            result = asyncio.run(execute_claimed_job(job, resolver=resolver, protocol=protocol))
            print(
                json.dumps(
                    {
                        "event": "job_result_reported",
                        "job_id": job.get("job_id"),
                        "success": result["success"],
                    }
                ),
                flush=True,
            )
        time.sleep(interval + random.uniform(0, 1.0))


def load_or_pair(bootstrap: dict[str, Any], device_name: str, state_path: Path) -> dict[str, Any]:
    pairing = next(item for item in bootstrap["pairings"] if item["device_name"] == device_name)
    base = bootstrap["hosted_base_url"]
    if state_path.exists():
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(stored, dict) and "device_credential" in stored:
            return {str(key): value for key, value in stored.items()}
    session = claim_and_auth(
        base,
        pairing["pairing_code"],
        pairing["device_name"],
        pairing["capabilities"],
        pairing["routes"],
    )
    state_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    os.chmod(state_path, 0o600)
    print(
        json.dumps(
            {"event": "paired", "device_id": session["device_id"], "device_name": device_name}
        ),
        flush=True,
    )
    return session


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bootstrap",
        default=os.path.expanduser("~/.local/share/joy-multihost/bootstrap.json"),
    )
    parser.add_argument("--device-name", required=True)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument(
        "--state-dir",
        default=os.path.expanduser("~/.local/share/joy-multihost"),
    )
    parser.add_argument(
        "--workspace-map",
        action="append",
        default=[],
        metavar="REF=/ABSOLUTE/PATH",
    )
    arguments = parser.parse_args()
    bootstrap = json.loads(Path(arguments.bootstrap).read_text(encoding="utf-8"))
    base = bootstrap["hosted_base_url"]
    state_path = Path(arguments.state_dir) / f"{arguments.device_name}.json"
    resolver = WorkspaceResolver(parse_workspace_mappings(arguments.workspace_map))
    session = load_or_pair(bootstrap, arguments.device_name, state_path)
    backoff = 2.0
    while True:
        try:
            session = refresh_access_token(base, session)
            state_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
            os.chmod(state_path, 0o600)
            run_loop(
                base,
                session["access_token"],
                session["capabilities"],
                session["routes"],
                arguments.interval,
                resolver,
            )
            backoff = 2.0
        except Exception as error:
            print(
                json.dumps({"event": "reconnect", "error": redact_secrets(str(error))}),
                flush=True,
            )
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps({"event": "fatal", "error": redact_secrets(str(error))}),
            flush=True,
        )
        sys.exit(1)
