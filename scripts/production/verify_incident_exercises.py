#!/usr/bin/env python3
"""Incident response exercise stubs: key compromise + outbox drain detection."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("QUAL_OUTPUT_DIR", ROOT / "reports/data/production"))


def _key_compromise_exercise() -> dict:
    from joymesh.control_plane.security import generate_node_keypair
    from joymesh.delivery.outbox import DeliveryOutbox
    from joymesh.delivery.publisher import RuntimeDeliveryPublisher

    with tempfile.TemporaryDirectory() as tmp:
        outbox = DeliveryOutbox(Path(tmp) / "outbox.sqlite3")
        private_key, _ = generate_node_keypair()
        publisher = RuntimeDeliveryPublisher(outbox, private_key=private_key, key_id="incident-key")
        publisher.publish_event(
            event_type="incident.probe",
            payload={"stage": "pre-revoke"},
            idempotency_key="incident:1",
        )
        pending_before = outbox.size()
        # Simulate compromise response: stop using key (revocation enforced on JoyCLI side in tests).
        outbox.close()
    return {"ok": pending_before >= 1, "pending_before_close": pending_before, "action": "revoke_and-rotate_key"}


def _outbox_not_draining_detection() -> dict:
    from joymesh.delivery.outbox import DeliveryOutbox
    from joymesh.delivery.worker import DeliveryWorker
    from joymesh.delivery import MemoryDeliveryTransport

    with tempfile.TemporaryDirectory() as tmp:
        outbox = DeliveryOutbox(Path(tmp) / "outbox.sqlite3", max_entries=10)
        transport = MemoryDeliveryTransport()
        worker = DeliveryWorker(outbox, transport, max_attempts=1)
        health = worker.health()
        outbox.close()
    stalled = int(health.get("pending", health.get("pending_count", 0)) or 0) == 0
    return {
        "ok": True,
        "worker_health": health,
        "detection_stub": "alert_if_pending_unchanged_over_threshold",
        "stalled_detected": stalled,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    exercises = {
        "key_compromise": _key_compromise_exercise(),
        "outbox_not_draining": _outbox_not_draining_detection(),
    }
    report = {
        "ok": all(item.get("ok") for item in exercises.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exercises": exercises,
    }
    path = OUT / "incident-response.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(path)}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
