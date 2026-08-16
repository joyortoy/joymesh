#!/usr/bin/env python3
"""Signed JoyMesh publish through JoyCLI projection, route, and directive."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile


def main() -> int:
    joycli_src = Path("/Users/joytan/intexta-buildweek/joycli/src")
    sys.path.insert(0, str(joycli_src))

    from joycli.provider_capabilities import ProviderCapabilityCertificationRegistry
    from joycli.provider_routing import route_provider, route_request_from_dict
    from joycli.provider_sessions import ProviderCliSessionSnapshot
    from joycli.providers import GenericProvider, ProviderRegistry
    from joycli.runtime.intake import (
        PublisherKey,
        PublisherKeyRegistry,
        PublisherKeyStatus,
        RuntimeHarnessProjectionSnapshot,
        RuntimeIntakeStore,
        RuntimeStateIntakeService,
        build_execution_directive,
        envelope_from_dict,
    )
    from joymesh.control_plane.security import generate_node_keypair
    from joymesh.delivery import DeliveryOutbox, RuntimeDeliveryPublisher

    private_key, public_key = generate_node_keypair()
    key_id = "runtime-routing-e2e"
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
    intake = RuntimeStateIntakeService(RuntimeIntakeStore(), key_registry=keys)
    outbox = DeliveryOutbox(Path(tempfile.mkdtemp()) / "outbox.sqlite3")
    publisher = RuntimeDeliveryPublisher(outbox, private_key=private_key, key_id=key_id)
    published = publisher.publish_event(
        event_type="runtime.snapshot",
        payload={
            "harness_id": "opencode",
            "availability": "ready",
            "authenticated": True,
            "configured": True,
            "quota": {"state": "available"},
            "capabilities": ["shell"],
            "execution_state": "idle",
        },
        idempotency_key="runtime-routing-e2e",
    )
    envelope = envelope_from_dict(published.as_dict())
    accepted = intake.ingest_envelope(envelope=envelope)
    assert accepted.code == "ok"

    bad = replace(
        envelope,
        envelope_id="bad-signature",
        sequence=2,
        idempotency_key="bad-signature",
        signature="invalid",
    )
    assert intake.ingest_envelope(envelope=bad).code == "invalid_signature"

    current = RuntimeHarnessProjectionSnapshot.from_intake(intake)
    facts = {
        **current.facts,
        "stale": {
            **current.facts["opencode"],
            "harness_id": "stale",
            "freshness": "stale",
        },
        "recon": {
            **current.facts["opencode"],
            "harness_id": "recon",
            "reconciliation_required": True,
        },
    }
    projection = RuntimeHarnessProjectionSnapshot(facts=facts, revision=current.revision)
    registry = ProviderRegistry()
    for harness_id in facts:
        registry.register(GenericProvider(provider_id=harness_id))
    request = route_request_from_dict(
        {
            "route_request_id": "runtime-routing-e2e",
            "allowed_provider_ids": sorted(facts),
            "provider_preference": ["stale", "recon", "opencode"],
            "authority_binding_ref": "authorization:e2e",
        }
    )
    decision = route_provider(
        request,
        registry.snapshot(),
        ProviderCapabilityCertificationRegistry().snapshot(),
        ProviderCliSessionSnapshot((), "e2e", "1970-01-01T00:00:00+00:00"),
        projection,
    )
    assert decision.selected_provider_id == "opencode"
    directive = build_execution_directive(decision, request, projection)
    assert directive["selected_harness"] == decision.selected_provider_id
    assert directive["runtime_projection_revision"] == projection.revision
    outbox.close()
    intake.close()
    print("runtime routing e2e: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
