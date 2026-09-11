import json

import pytest

from joymesh.denial_evidence import parse_denials


def record():
    return {
        "processID": 0,
        "processImagePath": "/kernel",
        "senderImagePath": "/System/Library/Extensions/Sandbox.kext/Contents/MacOS/Sandbox",
        "eventMessage": "Sandbox: python(123) deny(1) network-outbound secret-target",
        "timestamp": "2026-09-06 00:00:00+0000",
        "bootUUID": "test",
    }


def test_attributed_kernel_record_drops_target():
    result = parse_denials(
        json.dumps(record()), pid=123, started_ns=1788652799000000000, ended_ns=1788652801000000000
    )
    assert len(result) == 1
    assert result[0]["operation"] == "network-outbound"
    assert "secret-target" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("processID", 123),
        ("processImagePath", "/bin/echo"),
        ("senderImagePath", "/tmp/Sandbox"),
        ("eventMessage", "Sandbox: python(456) deny(1) network-outbound"),
        ("timestamp", "2026-09-05 00:00:00+0000"),
    ],
)
def test_spoof_wrong_pid_and_stale_rejected(field, value):
    item = record()
    item[field] = value
    assert (
        parse_denials(
            json.dumps(item), pid=123, started_ns=1788652799000000000, ended_ns=1788652801000000000
        )
        == []
    )
