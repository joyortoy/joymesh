#!/usr/bin/env bash
# Clean-environment validation using packaged wheels only (no PYTHONPATH / editable).
set -euo pipefail

MESH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOYCLI_ROOT="${JOYCLI_ROOT:-/Users/joytan/intexta-buildweek/joycli}"
WORKDIR="$(mktemp -d -t joymux-clean-XXXXXX)"
WORKDIR="$(cd "${WORKDIR}" && pwd -P)"
cleanup() {
  if [[ -n "${INTAKE_PID:-}" ]]; then kill "${INTAKE_PID}" 2>/dev/null || true; fi
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

STAGE() { printf 'STAGE %s: %s\n' "$1" "$2"; }
FAIL() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

STAGE 0 "workdir ${WORKDIR}"

STAGE 1 "rebuild wheels"
rm -rf "${MESH_ROOT}/dist" "${JOYCLI_ROOT}/dist"
mkdir -p "${JOYCLI_ROOT}/dist"
"${MESH_ROOT}/.venv/bin/python" -m build "${MESH_ROOT}" >/dev/null
(
  cd "${JOYCLI_ROOT}"
  "${MESH_ROOT}/.venv/bin/python" -c "from joycli_build_backend import build_wheel; print(build_wheel('dist'))"
)

MESH_WHEEL="$(ls -1 "${MESH_ROOT}/dist"/joymesh-*.whl | head -1)"
JOYCLI_WHEEL="$(ls -1 "${JOYCLI_ROOT}/dist"/joycli-*.whl | head -1)"
[[ -f "${MESH_WHEEL}" ]] || FAIL "missing JoyMesh wheel"
[[ -f "${JOYCLI_WHEEL}" ]] || FAIL "missing JoyCLI wheel"

STAGE 2 "inspect Requires-Dist"
python3 - <<PY
import zipfile
for label, wheel in (("joycli", "${JOYCLI_WHEEL}"), ("joymesh", "${MESH_WHEEL}")):
    with zipfile.ZipFile(wheel) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        text = zf.read(meta).decode()
    reqs = [ln.split(":",1)[1].strip() for ln in text.splitlines() if ln.startswith("Requires-Dist:")]
    print(label, "requires", reqs)
    if not any(r.startswith("cryptography") for r in reqs):
        raise SystemExit(f"{label} wheel missing cryptography Requires-Dist")
PY

STAGE 3 "clean venv install"
PYTHON_BIN="${MESH_ROOT}/.venv/bin/python"
"${PYTHON_BIN}" -m venv "${WORKDIR}/venv"
unset PYTHONPATH || true
export PATH="${WORKDIR}/venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
hash -r
python -m pip install --upgrade pip >/dev/null
python -m pip install "${JOYCLI_WHEEL}" "${MESH_WHEEL}" >/dev/null

STAGE 4 "assert no source imports"
python - <<PY
import joycli, joymesh, pathlib, sys, cryptography
jc = pathlib.Path(joycli.__file__).resolve()
jm = pathlib.Path(joymesh.__file__).resolve()
print("joycli", jc)
print("joymesh", jm)
print("cryptography", cryptography.__version__)
assert "${JOYCLI_ROOT}/src" not in sys.path
assert "${MESH_ROOT}/src" not in sys.path
assert "site-packages" in str(jc)
assert "site-packages" in str(jm)
PY

SOCK="/tmp/jm-clean-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])').sock"
STATE="${WORKDIR}/state"
JOYCLI_STATE="${WORKDIR}/joycli-state"
mkdir -p "${STATE}" "${JOYCLI_STATE}" "${WORKDIR}/workspace" "${WORKDIR}/keys"
printf 'clean install\n' > "${WORKDIR}/workspace/README.md"

STAGE 5 "provision signing keys"
python - <<PY
from pathlib import Path
from joymesh.control_plane.security import generate_node_keypair
priv, pub = generate_node_keypair()
Path("${WORKDIR}/keys/private.key").write_text(priv)
Path("${WORKDIR}/keys/public.key").write_text(pub)
print("key_material_written")
PY
PRIV="$(cat "${WORKDIR}/keys/private.key")"
PUB="$(cat "${WORKDIR}/keys/public.key")"
KEY_ID="clean-install-ed25519"
chmod 600 "${WORKDIR}/keys/private.key"

export JOYMESH_RUNTIME_SIGNING_KEY="${PRIV}"
export JOYMESH_RUNTIME_SIGNING_KEY_ID="${KEY_ID}"
export JOYCLI_RUNTIME_PUBLISHER_PUBLIC_KEY="${PUB}"
export JOYCLI_RUNTIME_PUBLISHER_KEY_ID="${KEY_ID}"
export JOYCLI_RUNTIME_ALLOW_UNSIGNED=0
export JOYMESH_DELIVERY_TRANSPORT=unix_socket
export JOYMESH_DELIVERY_SOCKET="${SOCK}"
export JOYMESH_DATABASE_URL="sqlite+aiosqlite:///${STATE}/mesh.db"

STAGE 6 "start JoyCLI intake"
joyctl --repo "${WORKDIR}" --state "${JOYCLI_STATE}" --mode durable-local \
  runtime intake-serve --socket "${SOCK}" >"${WORKDIR}/intake.log" 2>&1 &
INTAKE_PID=$!
for _ in $(seq 1 50); do
  [[ -S "${SOCK}" ]] && break
  sleep 0.1
done
[[ -S "${SOCK}" ]] || { cat "${WORKDIR}/intake.log"; FAIL "intake socket missing"; }

STAGE 7 "signed publish ACK projection route directive replay"
python - <<PY
import asyncio, json, os
from datetime import datetime, timezone
from pathlib import Path
from joymesh.service import JoyMesh
from joymesh.delivery import RuntimeDeliveryPublisher, DeliveryOutbox, build_delivery_transport
from joymesh.delivery.contracts import DeliveryKind
from joymesh.delivery.worker import DeliveryWorker
from joymesh.delivery.settings import DeliverySettings, DeliveryTransportMode
from joycli.runtime.intake import (
    SqliteRuntimeIntakeStore, RuntimeStateIntakeService,
    PublisherKey, PublisherKeyRegistry, PublisherKeyStatus,
)
from joycli.runtime.intake.routing_bridge import RuntimeHarnessProjectionSnapshot
from joycli.runtime.intake.directive import build_execution_directive
from joycli.provider_routing import (
    ProviderRouteRequest, ProviderSessionRequirement, route_provider,
    PROVIDER_SELECTION_POLICY_REVISION,
)
from joycli.provider_capabilities import (
    CertifiedProviderCapability, ProviderCapabilityCertificationRegistry,
    ProviderCapabilityCertificationState, ProviderCompatibilityConstraints,
    issue_capability_certification,
)
from joycli.provider_sessions import ProviderCliSessionSnapshot
from joycli.providers import ProviderRegistry, GenericProvider

async def main():
    settings = DeliverySettings(
        transport=DeliveryTransportMode.UNIX_SOCKET,
        socket_path=Path(os.environ["JOYMESH_DELIVERY_SOCKET"]),
    )
    mesh = JoyMesh(database_url=os.environ["JOYMESH_DATABASE_URL"], delivery_settings=settings)
    await mesh.initialize()
    snap = await mesh.get_runtime_snapshot(refresh=True)
    mesh.delivery_publisher.publish_snapshot(snap)
    drained = await mesh.delivery_worker.flush_once()
    health = mesh.delivery_health()
    print(json.dumps({"mesh_snapshot_drained": drained, "health": health}, default=str))
    assert drained >= 1, health
    assert int(health.get("outbox_size", 1)) == 0
    await mesh.close()

    # Eligible synthetic snapshot for packaging routing proof (facts only).
    outbox = DeliveryOutbox(Path("${STATE}") / "route.outbox.sqlite3")
    publisher = RuntimeDeliveryPublisher(
        outbox,
        private_key=os.environ["JOYMESH_RUNTIME_SIGNING_KEY"],
        key_id=os.environ["JOYMESH_RUNTIME_SIGNING_KEY_ID"],
        sign=True,
    )
    now = datetime.now(timezone.utc).isoformat()
    publisher._append(
        kind=DeliveryKind.RUNTIME_SNAPSHOT,
        payload={
            "snapshot_id": "clean-eligible",
            "harnesses": [
                {
                    "harness_id": "opencode",
                    "availability": "available",
                    "authenticated": True,
                    "configured": True,
                    "quota": {"state": "ok"},
                    "capabilities": ["text_generation"],
                    "execution_state": "idle",
                    "observed_at": now,
                },
                {
                    "harness_id": "codex",
                    "availability": "quota_exhausted",
                    "authenticated": True,
                    "configured": True,
                    "quota": {"state": "exhausted"},
                    "capabilities": ["text_generation"],
                    "execution_state": "idle",
                    "observed_at": now,
                },
            ],
        },
        idempotency_key="clean-eligible-1",
    )
    worker = DeliveryWorker(outbox, build_delivery_transport(settings))
    assert await worker.flush_once() == 1, worker.health()
    assert outbox.size() == 0
    await worker.stop()
    outbox.close()

    store_path = Path("${JOYCLI_STATE}") / "runtime_intake.sqlite3"
    assert store_path.exists(), store_path
    keys = PublisherKeyRegistry((
        PublisherKey(
            os.environ["JOYCLI_RUNTIME_PUBLISHER_KEY_ID"],
            "ed25519",
            os.environ["JOYCLI_RUNTIME_PUBLISHER_PUBLIC_KEY"],
            PublisherKeyStatus.ACTIVE,
            "joymesh",
            "local",
        ),
    ))
    intake = RuntimeStateIntakeService(SqliteRuntimeIntakeStore(store_path), key_registry=keys)
    harnesses = list(intake.list_harnesses())
    print("harnesses", sorted(h["harness_id"] for h in harnesses))
    assert any(h["harness_id"] == "opencode" for h in harnesses), harnesses
    proj = RuntimeHarnessProjectionSnapshot.from_intake(intake)
    registry = ProviderRegistry()
    for provider_id in ("opencode", "codex"):
        registry.register(GenericProvider(provider_id=provider_id))
    certs = []
    for provider_id in ("opencode", "codex"):
        certs.append(issue_capability_certification(
            provider_id=provider_id,
            provider_implementation_ref=f"clean:{provider_id}",
            provider_snapshot_revision=registry.snapshot().revision_id,
            capability_id=CertifiedProviderCapability.TEXT_GENERATION,
            issuer_ref="issuer:clean",
            evidence_refs=("evidence-sha256:clean",),
            compatibility_constraints=ProviderCompatibilityConstraints(
                minimum_adapter_contract_version="model.provider.m19.v1",
                supported_provider_ids=(provider_id,),
                supported_model_ids=("local-model",),
                supported_operating_systems=("darwin", "linux"),
                required_execution_mode="sandbox_required",
                maximum_certified_context_class="local_context_snapshot",
                allowed_data_handling_classification="non_secret_artifact_references_only",
            ),
            certification_state=ProviderCapabilityCertificationState.CERTIFIED,
        ))
    req = ProviderRouteRequest(
        route_request_id="clean-route",
        required_capabilities=(CertifiedProviderCapability.TEXT_GENERATION,),
        allowed_provider_ids=("opencode", "codex"),
        allowed_model_ids=("local-model",),
        required_execution_mode="sandbox_required",
        operating_system_constraint="darwin",
        context_class_requirement="local_context_snapshot",
        data_handling_classification="non_secret_artifact_references_only",
        session_requirement=ProviderSessionRequirement.NONE,
        deterministic_policy_revision=PROVIDER_SELECTION_POLICY_REVISION,
        authority_binding_ref="authority:clean-install",
    )
    decision = route_provider(
        req,
        registry.snapshot(),
        ProviderCapabilityCertificationRegistry(tuple(certs)).snapshot(),
        ProviderCliSessionSnapshot((), "sess", "1970-01-01T00:00:00+00:00"),
        runtime_projection=proj,
    )
    assert decision.selected_provider_id == "opencode", decision.to_dict()
    directive = build_execution_directive(decision, req, proj)
    assert directive["runtime_projection_revision"] == decision.input_snapshot_revisions["runtime_projection"]
    intake2 = RuntimeStateIntakeService(SqliteRuntimeIntakeStore(store_path), key_registry=keys)
    assert list(intake2.list_harnesses()), "projection lost after reopen"
    print(json.dumps({"ok": True, "selected": decision.selected_provider_id, "directive": directive["selected_harness"]}, sort_keys=True))
    intake.close(); intake2.close()

asyncio.run(main())
PY

STAGE 8 "artifact hashes"
python3 - <<PY
import hashlib, pathlib
from datetime import datetime, timezone
for path in ["${JOYCLI_WHEEL}", "${MESH_WHEEL}"]:
    p = pathlib.Path(path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    print(f"filename={p.name}")
    print(f"bytes={p.stat().st_size}")
    print(f"sha256={digest}")
    print(f"mtime_utc={datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()}")
PY

STAGE 9 "clean wheel install validation complete"
