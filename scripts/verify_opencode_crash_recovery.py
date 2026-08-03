#!/usr/bin/env python3
"""Live OpenCode crash-recovery proof (real process kill).

Gated by JOYMESH_LIVE_OPENCODE_CRASH=1.

Flow:
  JoyCLI intake (parent)
  → JoyMesh child (unix socket mode) starts real OpenCode
  → parent SIGKILLs JoyMesh child
  → parent restarts JoyMesh on same durable dirs
  → resume or clean retry
  → cancel + orphan check

Exit: 0 ok, 2 skip, 1 fail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

GATE = "JOYMESH_LIVE_OPENCODE_CRASH"


def log(stage: str, **fields: object) -> None:
    print(json.dumps({"stage": stage, **fields}, sort_keys=True, default=str), flush=True)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def pgrep_opencode() -> list[int]:
    try:
        out = subprocess.check_output(["pgrep", "-f", "opencode run"], text=True)
    except subprocess.CalledProcessError:
        return []
    return [int(x) for x in out.splitlines() if x.strip().isdigit()]


CHILD_RUNNER = r"""
import asyncio, json, os, sys
from pathlib import Path

async def main() -> None:
    root = Path(os.environ["CRASH_ROOT"])
    sock = Path(os.environ["CRASH_SOCK"])
    db = Path(os.environ["CRASH_DB"])
    workspace = Path(os.environ["CRASH_WORKSPACE"])
    exe = os.environ["CRASH_OPENCODE"]
    marker = Path(os.environ["CRASH_MARKER"])

    from joymesh.adapters.opencode import OpenCodeAdapter
    from joymesh.delivery import MemoryDeliveryTransport
    from joymesh.delivery.settings import DeliverySettings, DeliveryTransportMode
    from joymesh.models import BillingRoute, PermissionMode, RunRequest, RunStatus, SubscriptionCreate
    from joymesh.registry import AdapterRegistry
    from joymesh.service import JoyMesh

    settings = DeliverySettings(transport=DeliveryTransportMode.UNIX_SOCKET, socket_path=sock)
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{db}",
        registry=AdapterRegistry(adapters=[OpenCodeAdapter(exe, conformance_passed=True)]),
        delivery_settings=settings,
    )
    if isinstance(mesh.delivery_transport, MemoryDeliveryTransport):
        raise SystemExit("memory transport selected in production unix mode")
    await mesh.initialize()
    await mesh.create_subscription(SubscriptionCreate(
        harness_id="opencode", name="live", billing_route=BillingRoute.API, quota_known=False
    ))
    snap = await mesh.get_runtime_snapshot(refresh=True)
    mesh.delivery_publisher.publish_snapshot(snap)
    await mesh.delivery_worker.flush_once()

    req = RunRequest(
        task=("Create progress.txt and append one line every 3 seconds for at least "
              "five minutes. Do not finish early."),
        workspace=str(workspace),
        timeout_seconds=300,
        permission_mode=PermissionMode.AUTO_APPROVE,
    )
    route = await mesh.resolve_route(request=req, preferred_harness="opencode")
    run = await mesh.start_run(request=req, route=route)

    # Wait until running/process visible, then signal readiness and sleep forever
    # until SIGKILL.
    for _ in range(200):
        live = await mesh.inspect_run(run.id)
        if live and (live.process_id or live.status is RunStatus.RUNNING):
            marker.write_text(json.dumps({
                "run_id": run.id,
                "status": live.status.value,
                "pid": live.process_id,
                "native_session_id": live.native_session_id,
                "task_context_id": live.task_context_id,
                "outbox": str(mesh._delivery_outbox.path),
                "checkpoints": str(mesh.checkpoints.path),
            }), encoding="utf-8")
            break
        if live and live.status in {RunStatus.FAILED, RunStatus.COMPLETED, RunStatus.CANCELLED}:
            marker.write_text(json.dumps({
                "run_id": run.id,
                "status": live.status.value,
                "pid": live.process_id,
                "native_session_id": live.native_session_id,
                "task_context_id": live.task_context_id,
                "error": live.error,
                "outbox": str(mesh._delivery_outbox.path),
                "checkpoints": str(mesh.checkpoints.path),
            }), encoding="utf-8")
            break
        await asyncio.sleep(0.1)
    else:
        raise SystemExit("execution never started")

    while True:
        await asyncio.sleep(30)

asyncio.run(main())
"""


async def recover_and_cancel(root: Path, sock: Path, db: Path, workspace: Path, exe: str, marker: dict) -> dict:
    from joymesh.adapters.opencode import OpenCodeAdapter
    from joymesh.delivery import MemoryDeliveryTransport
    from joymesh.delivery.settings import DeliverySettings, DeliveryTransportMode
    from joymesh.execution import ExecutionCheckpoint
    from joymesh.models import (
        BillingRoute,
        Capability,
        PermissionMode,
        RunRequest,
        RunStatus,
        SubscriptionCreate,
        utc_now,
    )
    from joymesh.registry import AdapterRegistry
    from joymesh.service import JoyMesh

    settings = DeliverySettings(transport=DeliveryTransportMode.UNIX_SOCKET, socket_path=sock)
    registry = AdapterRegistry(adapters=[OpenCodeAdapter(exe, conformance_passed=True)])
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{db}",
        registry=registry,
        delivery_settings=settings,
    )
    if isinstance(mesh.delivery_transport, MemoryDeliveryTransport):
        fail("restart selected MemoryDeliveryTransport")
    await mesh.initialize()

    run_id = marker["run_id"]
    # Crash left the harness process orphaned — terminate it before clean retry.
    orphan_pid = marker.get("pid")
    if orphan_pid:
        try:
            os.kill(int(orphan_pid), 0)
            os.kill(int(orphan_pid), signal.SIGKILL)
            log("terminated_orphaned_original_harness", pid=orphan_pid)
        except OSError:
            log("original_harness_already_gone", pid=orphan_pid)
    # Ensure interrupted checkpoint exists (crash may have skipped graceful mark).
    existing = mesh.checkpoints.get(run_id)
    if existing is None or existing.status not in {"interrupted", "cancelled", "failed", "completed"}:
        mesh.checkpoints.save(
            ExecutionCheckpoint(
                execution_id=run_id,
                attempt_id=run_id,
                harness_id="opencode",
                native_session_id=marker.get("native_session_id"),
                status="interrupted",
                directive_json=None,
                updated_at=utc_now(),
            )
        )
    # initialize() also calls mark_interrupted for non-terminal rows.
    restored = mesh.checkpoints.get(run_id)
    if restored is None:
        fail("checkpoint missing after restart")
    log(
        "checkpoint_restored",
        status=restored.status,
        native_session_id=restored.native_session_id,
        outbox_size=mesh._delivery_outbox.size(),
    )

    snap = await mesh.get_runtime_snapshot(refresh=True)
    mesh.delivery_publisher.publish_snapshot(snap)
    drained = await mesh.delivery_worker.flush_once()
    log("snapshot_republished", drained=drained, health=mesh.delivery_health())

    supports_resume = Capability.SESSION_RESUME in OpenCodeAdapter().manifest.capabilities
    session = restored.native_session_id
    if supports_resume and session:
        mode = "session_resume"
        req = RunRequest(
            task="Continue briefly, then stop.",
            workspace=str(workspace),
            timeout_seconds=90,
            permission_mode=PermissionMode.AUTO_APPROVE,
            resume_session_id=session,
        )
    else:
        mode = "clean_retry"
        req = RunRequest(
            task="Say exactly: recovered",
            workspace=str(workspace),
            timeout_seconds=90,
            permission_mode=PermissionMode.AUTO_APPROVE,
        )
        if restored.status != "interrupted":
            # mark_interrupted may have rewritten status
            pass

    # Ensure subscription exists after restart (same db should already have it).
    try:
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id="opencode",
                name="live",
                billing_route=BillingRoute.API,
                quota_known=False,
            )
        )
    except Exception:
        pass

    original = await mesh.inspect_run(run_id)
    route = await mesh.resolve_route(request=req, preferred_harness="opencode")
    continuation = await mesh.start_run(
        request=req,
        route=route,
        task_context_id=(original.task_context_id if original else marker.get("task_context_id")),
        continuation_of_run_id=run_id,
    )
    if continuation.id == run_id:
        fail("recovery reused original attempt id")
    if mode == "clean_retry" and req.resume_session_id:
        fail("clean retry incorrectly set resume_session_id")
    log(
        "recovery_attempt",
        mode=mode,
        continuation_id=continuation.id,
        continuation_of=continuation.continuation_of_run_id,
        resume_session_id=req.resume_session_id,
    )

    await asyncio.sleep(2.0)
    before = set(pgrep_opencode())
    await mesh.cancel(continuation.id)
    final = await asyncio.wait_for(mesh.wait_for_run(continuation.id), timeout=45)
    await mesh.delivery_worker.flush_once()
    events = [e.type.value for e in await mesh.events(continuation.id)]
    if final.status is not RunStatus.CANCELLED and "run.cancelled" not in events:
        fail(f"cancellation failed status={final.status} events={events}")
    cp = mesh.checkpoints.get(continuation.id) or mesh.checkpoints.get(run_id)
    log(
        "cancel_after_recovery",
        status=final.status.value,
        events=events,
        checkpoint=None if cp is None else cp.as_dict(),
    )

    await asyncio.sleep(1.0)
    after = set(pgrep_opencode())
    orphans: list[int] = []
    if final.process_id:
        try:
            os.kill(final.process_id, 0)
            orphans.append(final.process_id)
        except OSError:
            pass
    if orphan_pid:
        try:
            os.kill(int(orphan_pid), 0)
            orphans.append(int(orphan_pid))
        except OSError:
            pass
    # Any opencode still running under our workspace is an orphan.
    for pid in sorted(after):
        try:
            cmdline = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            cmdline = ""
        if str(workspace) in cmdline:
            orphans.append(pid)
    orphans = sorted(set(orphans))
    log("orphan_check", orphans=orphans, pgrep_after=sorted(after), pgrep_before=sorted(before))
    for pid in orphans:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if orphans:
        fail(f"orphan opencode processes: {orphans}")

    await mesh.close()
    return {"mode": mode, "continuation_id": continuation.id, "final_status": final.status.value}


async def async_main(exe: str) -> int:
    if os.environ.get(GATE) != "1":
        print(f"SKIP: set {GATE}=1 to run")
        return 2
    joycli_src = Path("/Users/joytan/intexta-buildweek/joycli/src")
    if joycli_src.is_dir():
        sys.path.insert(0, str(joycli_src))
    from shutil import which

    resolved = which(exe)
    if not resolved:
        print(f"SKIP: opencode not found ({exe})")
        return 2

    root = Path(tempfile.mkdtemp(prefix="joymesh-opencode-crash-"))
    sock = Path("/tmp") / f"joymesh-delivery-{uuid4().hex}.sock"
    db = root / "mesh.db"
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("crash probe\n", encoding="utf-8")
    intake_store = root / "intake.sqlite3"
    marker_path = root / "marker.json"
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "delivery:\n"
        "  transport: unix_socket\n"
        f"  socket_path: {sock}\n"
        "harnesses:\n"
        "  enabled: [opencode]\n"
        "  default: opencode\n",
        encoding="utf-8",
    )

    from joycli.runtime.intake import (
        PublisherKey,
        PublisherKeyRegistry,
        PublisherKeyStatus,
        RuntimeStateIntakeService,
        SqliteRuntimeIntakeStore,
        UnixSocketRuntimeListener,
    )
    from joymesh.control_plane.security import generate_node_keypair

    private_key, public_key = generate_node_keypair()
    key_id = "crash-recovery-ed25519"
    keys = PublisherKeyRegistry(
        (
            PublisherKey(
                key_id,
                "ed25519",
                public_key,
                PublisherKeyStatus.ACTIVE,
                "joymesh",
                "local",
            ),
        )
    )
    store = SqliteRuntimeIntakeStore(intake_store)
    intake = RuntimeStateIntakeService(store, key_registry=keys)
    server = UnixSocketRuntimeListener(intake, path=sock)
    server.start_background()
    log("intake_started", socket=str(sock), root=str(root), owner="joycli")

    # Parent recovery mesh must reuse the same signing key JoyCLI trusts.
    os.environ["JOYMESH_RUNTIME_SIGNING_KEY"] = private_key
    os.environ["JOYMESH_RUNTIME_SIGNING_KEY_ID"] = key_id

    env = os.environ.copy()
    env.update(
        {
            "CRASH_ROOT": str(root),
            "CRASH_SOCK": str(sock),
            "CRASH_DB": str(db),
            "CRASH_WORKSPACE": str(workspace),
            "CRASH_OPENCODE": resolved,
            "CRASH_MARKER": str(marker_path),
            "JOYMESH_CONFIG_DIR": str(config_dir),
            "JOYMESH_DELIVERY_TRANSPORT": "unix_socket",
            "JOYMESH_DELIVERY_SOCKET": str(sock),
            "JOYMESH_RUNTIME_SIGNING_KEY": private_key,
            "JOYMESH_RUNTIME_SIGNING_KEY_ID": key_id,
            "PYTHONUNBUFFERED": "1",
        }
    )
    # Ensure child imports the same source tree when run from checkout.
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_RUNNER],
        env=env,
        cwd=str(root),
    )
    log("joymesh_child_started", pid=child.pid)

    deadline = time.time() + 90
    while time.time() < deadline and not marker_path.exists():
        if child.poll() is not None:
            fail(f"joymesh child exited early code={child.returncode}")
        await asyncio.sleep(0.2)
    if not marker_path.exists():
        child.kill()
        fail("timed out waiting for execution marker")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    log("execution_started", **marker)
    intake_before = store.accepted if hasattr(store, "accepted") else 0
    # Prefer health counter after child publishes.
    intake_before = int(server.health().get("accepted_count") or 0)
    log("pre_crash_intake_size", size=intake_before)

    # Forcibly terminate JoyMesh process (leave intake running).
    child.kill()  # SIGKILL
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        fail("child did not die after SIGKILL")
    log("joymesh_sigkilled", pid=child.pid, intake_still_up=True)

    result = await recover_and_cancel(root, sock, db, workspace, resolved, marker)
    intake_after = int(server.health().get("accepted_count") or 0)
    log("post_recovery_intake_size", before=intake_before, after=intake_after)
    if intake_after < 1:
        fail("JoyCLI intake received no runtime updates")
    server.stop_background()
    intake.close()
    log("complete", **result, root=str(root))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode", default=os.environ.get("OPENCODE_BIN", "opencode"))
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(async_main(args.opencode)))
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
