#!/usr/bin/env bash
# Backup/restore E2E for JoyCTL intake + JoyMesh outbox.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="$(mktemp -d -t jm-backup-XXXXXX)"
WORKDIR="$(cd "${WORKDIR}" && pwd -P)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

PYTHON="${PYTHON:-python3}"
JOYCTL_ROOT="${JOYCTL_ROOT:-${HOME}/joycli}"
export JOYCTL_STATE="${WORKDIR}/joycli-state"
mkdir -p "${JOYCTL_STATE}" "${WORKDIR}/outbox" "${WORKDIR}/backup-cli" "${WORKDIR}/backup-mesh"

# Create intake DB
(
  cd "${JOYCTL_ROOT}"
  "${PYTHON}" - <<PY
from pathlib import Path
from joyctl.runtime.intake.store import SqliteRuntimeIntakeStore
from joyctl.runtime.intake.key_store import DurablePublisherKeyStore
state = Path("${JOYCTL_STATE}")
SqliteRuntimeIntakeStore(state / "runtime_intake.sqlite3").close()
DurablePublisherKeyStore(state / "publisher_keys.json").add(
    key_id="bk", public_key="pub", publisher_id="joymesh", organisation_id="local"
)
PY
  "${PYTHON}" -m joycli.cli --state "${JOYCTL_STATE}" runtime backup --destination "${WORKDIR}/backup-cli"
  rm -f "${JOYCTL_STATE}/runtime_intake.sqlite3" "${JOYCTL_STATE}/publisher_keys.json"
  "${PYTHON}" -m joycli.cli --state "${JOYCTL_STATE}" runtime restore --source "${WORKDIR}/backup-cli" --force
)

# Create outbox and backup
"${PYTHON}" - <<PY
from pathlib import Path
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.delivery.backup import backup_delivery_outbox, restore_delivery_outbox
outbox = Path("${WORKDIR}/outbox/delivery_outbox.sqlite3")
DeliveryOutbox(outbox).close()
backup_delivery_outbox(outbox_path=outbox, destination=Path("${WORKDIR}/backup-mesh"))
outbox.unlink()
restore_delivery_outbox(backup_dir=Path("${WORKDIR}/backup-mesh"), outbox_path=outbox)
assert outbox.exists()
print("backup_restore_e2e: ok")
PY
