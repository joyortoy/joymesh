"""Schema compatibility between JoyMesh wire contracts and JoyCLI mirrored contracts."""

from __future__ import annotations

import json
from pathlib import Path

from joymesh.delivery.contracts import (
    SCHEMA_VERSION,
    TRANSPORT_VERSION,
    DeliveryEnvelope,
    DeliveryKind,
    PublisherIdentity,
)


def test_wire_contract_field_parity_with_joycli_fixture():
    # Fixture generated from JoyCLI contract field names (no JoyCLI import required).
    fixture = {
        "schema_version": 1,
        "transport_version": 1,
        "kinds": [
            "runtime_snapshot",
            "runtime_event",
            "approval_request",
            "heartbeat",
        ],
        "envelope_fields": [
            "envelope_id",
            "kind",
            "sequence",
            "observed_at",
            "publisher",
            "payload",
            "payload_hash",
            "schema_version",
            "transport_version",
            "signature",
                "key_id",
                "signature_algorithm",
            "idempotency_key",
        ],
            "publisher_fields": [
                "publisher_id",
                "public_key",
                "instance_id",
                "organisation_id",
            ],
    }
    assert SCHEMA_VERSION == fixture["schema_version"]
    assert TRANSPORT_VERSION == fixture["transport_version"]
    assert {k.value for k in DeliveryKind} == set(fixture["kinds"])
    env = DeliveryEnvelope.build(
        kind=DeliveryKind.RUNTIME_SNAPSHOT,
        sequence=1,
        publisher=PublisherIdentity(publisher_id="joymesh"),
        payload={"harnesses": []},
        idempotency_key="compat",
    )
    assert set(env.as_dict()) == set(fixture["envelope_fields"])
    assert set(env.publisher.as_dict()) >= set(fixture["publisher_fields"])
    # Hash algorithm compatibility: sha256 of canonical JSON
    assert len(env.payload_hash) == 64


def test_joymesh_does_not_own_canonical_intake_in_production_docs():
    intake = Path(__file__).resolve().parents[1] / "src" / "joymesh" / "delivery" / "intake.py"
    text = intake.read_text(encoding="utf-8")
    assert "DEPRECATED" in text
    assert "joycli.runtime.intake" in text
