#!/usr/bin/env bash
# Fresh-install release ritual for JoyMesh.
# Builds (or accepts) a wheel, installs into an isolated venv (no repo PYTHONPATH),
# runs Unix-socket intake + snapshot + OpenCode detect/execute/cancel + restart recover.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE() { printf 'STAGE %s: %s\n' "$1" "$2"; }
FAIL() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

WHEEL_ARG="${1:-}"
JOYCLI_ROOT="${JOYCLI_ROOT:-/Users/joytan/intexta-buildweek/joycli}"
JOYCLI_WHEEL_ARG="${JOYCLI_WHEEL:-}"
# Resolve to a physical path so JoyCLI state validation does not reject macOS
# /var -> /private/var (and /tmp -> /private/tmp) symlink hops.
WORKDIR="$(mktemp -d -t joymesh-fresh-XXXXXX)"
WORKDIR="$(cd "${WORKDIR}" && pwd -P)"
cleanup() {
  if [[ -n "${INTAKE_PID:-}" ]]; then kill "${INTAKE_PID}" 2>/dev/null || true; fi
  if [[ -n "${MESH_PID:-}" ]]; then kill "${MESH_PID}" 2>/dev/null || true; fi
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

STAGE 0 "workdir ${WORKDIR}"
cd "${ROOT}"

if [[ -n "${WHEEL_ARG}" ]]; then
  WHEEL="$(cd "$(dirname "${WHEEL_ARG}")" && pwd)/$(basename "${WHEEL_ARG}")"
  [[ -f "${WHEEL}" ]] || FAIL "wheel not found: ${WHEEL}"
else
  STAGE 1 "python -m build"
  rm -rf "${ROOT}/dist"
  "${ROOT}/.venv/bin/python" -m build >/dev/null
  WHEEL="$(ls -1 "${ROOT}/dist"/joymesh-*.whl | head -1)"
  [[ -n "${WHEEL}" ]] || FAIL "no wheel produced"
fi

if [[ -n "${JOYCLI_WHEEL_ARG}" ]]; then
  JOYCLI_WHEEL="$(cd "$(dirname "${JOYCLI_WHEEL_ARG}")" && pwd)/$(basename "${JOYCLI_WHEEL_ARG}")"
else
  STAGE 1b "build JoyCLI package"
  [[ -d "${JOYCLI_ROOT}" ]] || FAIL "JOYCLI_ROOT missing: ${JOYCLI_ROOT}"
  rm -rf "${JOYCLI_ROOT}/dist"
  "${ROOT}/.venv/bin/python" -m build "${JOYCLI_ROOT}" >/dev/null
  JOYCLI_WHEEL="$(ls -1 "${JOYCLI_ROOT}/dist"/joycli-*.whl | head -1)"
  [[ -n "${JOYCLI_WHEEL}" ]] || FAIL "no JoyCLI wheel produced"
fi

STAGE 2 "wheel ${WHEEL}"
BYTES="$(wc -c < "${WHEEL}" | tr -d ' ')"
SHA="$(shasum -a 256 "${WHEEL}" | awk '{print $1}')"
STAGE 2 "bytes=${BYTES} sha256=${SHA}"
JOYCLI_BYTES="$(wc -c < "${JOYCLI_WHEEL}" | tr -d ' ')"
JOYCLI_SHA="$(shasum -a 256 "${JOYCLI_WHEEL}" | awk '{print $1}')"
STAGE 2b "joycli_wheel=${JOYCLI_WHEEL} bytes=${JOYCLI_BYTES} sha256=${JOYCLI_SHA}"

# Wheel content inspection
STAGE 3 "inspect wheel contents"
python3 - <<PY
import sys, zipfile
wheel = "${WHEEL}"
need = [
  "joymesh/delivery/",
  "joymesh/delivery/transports/unix_socket.py",
  "joymesh/execution/",
  "joymesh/diagnostics/",
  "joymesh/quota/",
  "joymesh/runtime_snapshot/",
]
with zipfile.ZipFile(wheel) as zf:
    names = zf.namelist()
missing = [n for n in need if not any(x.startswith(n) or x == n for x in names)]
if missing:
    print("MISSING", missing)
    sys.exit(1)
# entry points record
eps = [n for n in names if n.endswith("entry_points.txt")]
print("entry_points_files", eps)
print("has_delivery", any("joymesh/delivery/" in n for n in names))
print("has_checkpoint_module", any("joymesh/execution/checkpoint.py" in n for n in names))
print("has_outbox_module", any("joymesh/delivery/outbox.py" in n for n in names))
PY

STAGE 4 "create isolated venv (no repo PYTHONPATH)"
# Use the same Python major/minor as the build environment (>=3.12).
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${ROOT}/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3.12 || command -v python3)"
  fi
fi
"${PYTHON_BIN}" -c 'import sys; assert sys.version_info >= (3, 12), sys.version' \
  || FAIL "need Python >=3.12 to create fresh venv (got ${PYTHON_BIN})"
"${PYTHON_BIN}" -m venv "${WORKDIR}/venv"
# Critical: do not export PYTHONPATH to the repository.
unset PYTHONPATH || true
export PATH="${WORKDIR}/venv/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"
hash -r
python -m pip install --upgrade pip >/dev/null
python -m pip install "${WHEEL}" "${JOYCLI_WHEEL}" >/dev/null
python -c 'import sys; print("fresh_python", sys.version.split()[0], sys.executable)'
command -v joyctl >/dev/null || FAIL "joyctl entrypoint missing after JoyCLI install"
command -v joymesh >/dev/null || FAIL "joymesh entrypoint missing after JoyMesh install"

STAGE 5 "verify import root is the venv, not the checkout"
python - <<PY
import joymesh, pathlib, sys
root = pathlib.Path(joymesh.__file__).resolve()
print("joymesh_file", root)
print("sys_path0", sys.path[0])
if "${ROOT}/src" in sys.path or str(root).startswith("${ROOT}/src"):
    raise SystemExit("fresh install imported from source tree")
print("version", getattr(joymesh, "__version__", "unknown"))
PY

STAGE 6 "assert MemoryDeliveryTransport is not production default"
python - <<'PY'
from joymesh.delivery import (
    MemoryDeliveryTransport,
    UnixSocketDeliveryTransport,
    build_delivery_transport,
    resolve_delivery_settings,
)
settings = resolve_delivery_settings(environ={})
transport = build_delivery_transport(settings)
assert isinstance(transport, UnixSocketDeliveryTransport), type(transport)
assert not isinstance(transport, MemoryDeliveryTransport)
print("transport", transport.name, transport.path)
PY

# AF_UNIX paths are short on macOS; keep the socket under /tmp while state stays physical.
SOCK="/tmp/jm-fresh-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])').sock"
STATE="${WORKDIR}/state"
mkdir -p "${STATE}" "${WORKDIR}/config" "${WORKDIR}/workspace"
rm -f "${SOCK}"
printf 'fresh install ritual\n' > "${WORKDIR}/workspace/README.md"
cat > "${WORKDIR}/config/config.yaml" <<EOF
delivery:
  transport: unix_socket
  socket_path: ${SOCK}
harnesses:
  enabled:
    - opencode
  default: opencode
EOF

export JOYMESH_CONFIG_DIR="${WORKDIR}/config"
export JOYMESH_DELIVERY_TRANSPORT=unix_socket
export JOYMESH_DELIVERY_SOCKET="${SOCK}"
export JOYMESH_DATABASE_URL="sqlite+aiosqlite:///${STATE}/mesh.db"
RUNTIME_KEY_MATERIAL="$(python - <<'PY'
from joymesh.control_plane.security import generate_node_keypair
private_key, public_key = generate_node_keypair()
print(private_key, public_key)
PY
)"
read -r JOYMESH_RUNTIME_SIGNING_KEY JOYCLI_RUNTIME_PUBLISHER_PUBLIC_KEY <<<"${RUNTIME_KEY_MATERIAL}"
export JOYMESH_RUNTIME_SIGNING_KEY
export JOYMESH_RUNTIME_SIGNING_KEY_ID="fresh-install-ed25519"
export JOYCLI_RUNTIME_PUBLISHER_PUBLIC_KEY
export JOYCLI_RUNTIME_PUBLISHER_KEY_ID="${JOYMESH_RUNTIME_SIGNING_KEY_ID}"
unset PYTHONPATH || true

STAGE 7 "start JoyCLI unix intake (canonical)"
JOYCLI_STATE="${WORKDIR}/joycli-state"
mkdir -p "${JOYCLI_STATE}"
joyctl --repo "${WORKDIR}" --state "${JOYCLI_STATE}" --mode durable-local \
  runtime intake-serve --socket "${SOCK}" >"${WORKDIR}/joycli-intake.log" 2>&1 &
INTAKE_PID=$!
for i in $(seq 1 50); do
  if [[ -S "${SOCK}" ]]; then break; fi
  sleep 0.1
done
[[ -S "${SOCK}" ]] || { cat "${WORKDIR}/joycli-intake.log" || true; FAIL "JoyCLI intake socket not created"; }

STAGE 8 "doctor / detect OpenCode"
python - <<'PY'
import asyncio, json, shutil
from joymesh.service import JoyMesh
from joymesh.delivery import MemoryDeliveryTransport
from joymesh.delivery.settings import DeliverySettings, DeliveryTransportMode
import os
from pathlib import Path

async def main():
    settings = DeliverySettings(
        transport=DeliveryTransportMode.UNIX_SOCKET,
        socket_path=Path(os.environ["JOYMESH_DELIVERY_SOCKET"]),
    )
    mesh = JoyMesh(
        database_url=os.environ["JOYMESH_DATABASE_URL"],
        delivery_settings=settings,
    )
    if isinstance(mesh.delivery_transport, MemoryDeliveryTransport):
        raise SystemExit("MemoryDeliveryTransport selected")
    await mesh.initialize()
    detected = await mesh.detect_harnesses()
    opencode = [d for d in detected if d.manifest.harness_id == "opencode"]
    print(json.dumps({
        "transport": mesh.delivery_transport.name,
        "opencode_path": shutil.which("opencode"),
        "detected": [
            {"id": d.manifest.harness_id, "availability": d.availability.value, "executable": d.executable}
            for d in detected if d.manifest.harness_id == "opencode"
        ],
        "health": mesh.delivery_health(),
    }, indent=2))
    snap = await mesh.get_runtime_snapshot(refresh=True)
    mesh.delivery_publisher.publish_snapshot(snap)
    drained = await mesh.delivery_worker.flush_once()
    assert drained == 1, mesh.delivery_health()
    print("snapshot_drained", drained, "outbox", mesh._delivery_outbox.size())
    await mesh.close()
asyncio.run(main())
PY

STAGE 9 "small OpenCode execution + cancel + restart recover"
python - <<'PY'
import asyncio, json, os, shutil
from pathlib import Path
from joymesh.adapters.opencode import OpenCodeAdapter
from joymesh.delivery import MemoryDeliveryTransport
from joymesh.delivery.settings import DeliverySettings, DeliveryTransportMode
from joymesh.models import BillingRoute, PermissionMode, RunRequest, RunStatus, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh

async def main():
    exe = shutil.which("opencode")
    if not exe:
        print("OPENCODE_MISSING")
        return
    settings = DeliverySettings(
        transport=DeliveryTransportMode.UNIX_SOCKET,
        socket_path=Path(os.environ["JOYMESH_DELIVERY_SOCKET"]),
    )
    mesh = JoyMesh(
        database_url=os.environ["JOYMESH_DATABASE_URL"],
        registry=AdapterRegistry(adapters=[OpenCodeAdapter(exe, conformance_passed=True)]),
        delivery_settings=settings,
    )
    assert not isinstance(mesh.delivery_transport, MemoryDeliveryTransport)
    await mesh.initialize()
    await mesh.create_subscription(SubscriptionCreate(
        harness_id="opencode", name="fresh", billing_route=BillingRoute.API, quota_known=False
    ))
    req = RunRequest(
        task="Say exactly: hello-fresh-install",
        workspace=os.environ.get("WORKSPACE") or str(Path(os.environ["JOYMESH_CONFIG_DIR"]).parent / "workspace"),
        timeout_seconds=90,
        permission_mode=PermissionMode.AUTO_APPROVE,
    )
    route = await mesh.resolve_route(request=req, preferred_harness="opencode")
    run = await mesh.start_run(request=req, route=route)
    await asyncio.sleep(1.5)
    cancelled = await mesh.cancel(run.id)
    final = await asyncio.wait_for(mesh.wait_for_run(run.id), timeout=60)
    await mesh.delivery_worker.flush_once()
    print(json.dumps({
        "run_id": run.id,
        "final": final.status.value,
        "events": [e.type.value for e in await mesh.events(run.id)],
        "outbox": str(mesh._delivery_outbox.path),
        "checkpoints": str(mesh.checkpoints.path),
    }))
    outbox = mesh._delivery_outbox.path
    checkpoints = mesh.checkpoints.path
    db_url = mesh.database.url if hasattr(mesh.database, "url") else os.environ["JOYMESH_DATABASE_URL"]
    await mesh.close()

    # Restart recover
    mesh2 = JoyMesh(
        database_url=os.environ["JOYMESH_DATABASE_URL"],
        registry=AdapterRegistry(adapters=[OpenCodeAdapter(exe, conformance_passed=True)]),
        delivery_settings=settings,
    )
    await mesh2.initialize()
    snap = await mesh2.get_runtime_snapshot(refresh=True)
    mesh2.delivery_publisher.publish_snapshot(snap)
    drained = await mesh2.delivery_worker.flush_once()
    print(json.dumps({
        "restart_transport": mesh2.delivery_transport.name,
        "restart_drained": drained,
        "checkpoint": None if mesh2.checkpoints.get(run.id) is None else mesh2.checkpoints.get(run.id).as_dict(),
        "health": mesh2.delivery_health(),
    }))
    await mesh2.close()

asyncio.run(main())
PY

STAGE 10 "artifact report"
python3 - <<PY
import platform, sys, importlib.metadata
print("wheel_filename=$(basename "${WHEEL}")")
print("wheel_bytes=${BYTES}")
print("wheel_sha256=${SHA}")
print("joycli_wheel_filename=$(basename "${JOYCLI_WHEEL}")")
print("joycli_wheel_bytes=${JOYCLI_BYTES}")
print("joycli_wheel_sha256=${JOYCLI_SHA}")
print("installed_version", importlib.metadata.version("joymesh"))
print("joycli_installed_version", importlib.metadata.version("joycli"))
print("python", sys.version.split()[0])
print("platform", platform.platform())
print("machine", platform.machine())
PY

STAGE 11 "fresh-install ritual complete"
