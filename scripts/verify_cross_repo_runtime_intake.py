#!/usr/bin/env python3
"""Cross-repository JoyMesh publisher → JoyCLI intake integration proof.

Exit 0 on success, 1 on failure, 2 if JoyCLI unavailable.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def main() -> int:
    joycli_src = Path("/Users/joytan/intexta-buildweek/joycli/src")
    if joycli_src.is_dir() and str(joycli_src) not in sys.path:
        sys.path.insert(0, str(joycli_src))
    try:
        from joycli.runtime.intake import (
            PublisherKey,
            PublisherKeyRegistry,
            PublisherKeyStatus,
            RuntimeStateIntakeService,
            SqliteRuntimeIntakeStore,
            UnixSocketRuntimeListener,
            evaluate_harness_eligibility,
            select_eligible_harness,
        )
    except ImportError:
        print("SKIP: joycli.runtime.intake not importable")
        return 2

    from joymesh.delivery import (
        DeliveryOutbox,
        DeliverySettings,
        DeliveryTransportMode,
        DeliveryWorker,
        MemoryDeliveryTransport,
        RuntimeDeliveryPublisher,
        build_delivery_transport,
    )
    from joymesh.control_plane.security import generate_node_keypair
    from joymesh.models import utc_now
    from joymesh.quota.contracts import (
        HarnessAvailability,
        QuotaSnapshot,
        QuotaSource,
        QuotaState,
        QuotaVisibility,
    )
    from joymesh.runtime_snapshot.contracts import (
        ExecutionState,
        HarnessRuntimeSnapshot,
        LatencySnapshot,
        QualitySnapshot,
        RuntimeSnapshot,
        UsageSnapshot,
    )

    root = Path(tempfile.mkdtemp(prefix="cross-runtime-"))
    sock = Path("/tmp") / f"joymesh-delivery-{uuid4().hex}.sock"
    intake_db = root / "intake.sqlite3"
    outbox_path = root / "outbox.sqlite3"

    private_key, public_key = generate_node_keypair()
    key_id = "cross-repo-ed25519"
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
    store = SqliteRuntimeIntakeStore(intake_db)
    intake = RuntimeStateIntakeService(store, key_registry=keys)
    listener = UnixSocketRuntimeListener(intake, path=sock)
    listener.start_background()

    settings = DeliverySettings(
        transport=DeliveryTransportMode.UNIX_SOCKET, socket_path=sock
    )
    transport = build_delivery_transport(settings)
    assert not isinstance(transport, MemoryDeliveryTransport)
    outbox = DeliveryOutbox(outbox_path)
    publisher = RuntimeDeliveryPublisher(outbox, private_key=private_key, key_id=key_id)
    worker = DeliveryWorker(outbox, transport)

    def quota(hid: str, availability: HarnessAvailability, state: QuotaState) -> QuotaSnapshot:
        return QuotaSnapshot(
            harness_id=hid,
            availability=availability,
            quota_visibility=QuotaVisibility.OBSERVED,
            state=state,
            authenticated=availability
            not in {
                HarnessAvailability.AUTHENTICATION_REQUIRED,
            },
            configured=availability
            not in {
                HarnessAvailability.CONFIGURATION_REQUIRED,
            },
            credits_remaining=None,
            requests_remaining=None,
            tokens_remaining=None,
            reset_at=None,
            observed_at=utc_now(),
            source=QuotaSource.NONE,
        )

    def harness(hid: str, availability: HarnessAvailability, state: QuotaState) -> HarnessRuntimeSnapshot:
        q = quota(hid, availability, state)
        return HarnessRuntimeSnapshot(
            harness_id=hid,
            availability=availability,
            authenticated=q.authenticated,
            configured=q.configured,
            quota=q,
            capabilities=frozenset(),
            execution_state=ExecutionState.IDLE,
            recent_usage=UsageSnapshot(),
            recent_quality=QualitySnapshot(),
            latency=LatencySnapshot(),
        )

    async def _run() -> None:
        snap = RuntimeSnapshot(
            snapshot_id=str(uuid4()),
            observed_at=utc_now(),
            harnesses=(
                harness("codex", HarnessAvailability.QUOTA_EXHAUSTED, QuotaState.EXHAUSTED),
                harness(
                    "claude-code",
                    HarnessAvailability.AUTHENTICATION_REQUIRED,
                    QuotaState.UNKNOWN,
                ),
                harness(
                    "gemini-cli",
                    HarnessAvailability.CONFIGURATION_REQUIRED,
                    QuotaState.UNKNOWN,
                ),
                harness("opencode", HarnessAvailability.READY, QuotaState.AVAILABLE),
                harness("grok", HarnessAvailability.READY, QuotaState.AVAILABLE),
            ),
        )
        publisher.publish_snapshot(snap)
        assert outbox.size() == 1
        drained = await worker.flush_once()
        assert drained == 1, worker.health()
        assert outbox.size() == 0

        harnesses = {h["harness_id"]: h for h in intake.list_harnesses()}
        assert harnesses["opencode"]["availability"] == "ready"
        assert harnesses["codex"]["availability"] == "quota_exhausted"
        evaluations = {e.harness_id: e for e in evaluate_harness_eligibility(intake)}
        assert evaluations["codex"].eligible is False
        assert evaluations["claude-code"].eligible is False
        assert evaluations["gemini-cli"].eligible is False
        assert evaluations["opencode"].eligible is True
        selected = select_eligible_harness(intake, preference=("opencode", "grok"))
        assert selected == "opencode"

        publisher.publish_event(
            event_type="runtime.probe",
            payload={"ok": True},
            idempotency_key="probe-1",
        )
        assert await worker.flush_once() == 1
        publisher.publish_event(
            event_type="runtime.probe",
            payload={"ok": True},
            idempotency_key="probe-1",
        )
        # Resend after ACK deletion creates new outbox row; JoyCLI idempotently ACKs.
        assert await worker.flush_once() == 1

        listener.stop_background()
        intake.close()
        await transport.close()
        store2 = SqliteRuntimeIntakeStore(intake_db)
        intake2 = RuntimeStateIntakeService(store2, key_registry=keys)
        assert store2.get_projection("opencode") is not None
        listener2 = UnixSocketRuntimeListener(intake2, path=sock)
        listener2.start_background()
        transport2 = build_delivery_transport(settings)
        worker2 = DeliveryWorker(outbox, transport2)
        publisher.publish_event(
            event_type="runtime.reconcile",
            payload={"n": 1},
            idempotency_key="recon-1",
        )
        assert await worker2.flush_once() == 1, worker2.health()
        listener2.stop_background()
        intake2.close()
        await worker2.stop()
        await worker.stop()
        outbox.close()
        print(json.dumps({"ok": True, "selected": selected, "root": str(root)}))

    try:
        asyncio.run(_run())
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            listener.stop_background()
            intake.close()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
