"""Read narrowly attributed native kernel denial records; never parse child output."""
import json
import re
import subprocess
import time
from datetime import datetime

from joymesh.verification import _capture


def parse_denials(text: str, *, pid: int, started_ns: int, ended_ns: int) -> list[dict]:
    events = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
            if (item.get("processID") != 0 or item.get("processImagePath") != "/kernel"
                or item.get("senderImagePath") != "/System/Library/Extensions/Sandbox.kext/Contents/MacOS/Sandbox"):
                continue
            match = re.match(r"^Sandbox: [^\n(]+\((\d+)\) deny\(\d+\) ([a-z0-9-]+)(?: |$)", item["eventMessage"])
            stamp = datetime.fromisoformat(item["timestamp"])
            if stamp.tzinfo is None:
                continue
            ns = int(stamp.timestamp() * 1_000_000_000)
            if not match or int(match[1]) != pid or not started_ns <= ns <= ended_ns:
                continue
            events.append({"pid": pid, "operation": match[2], "observed_ns": ns,
                           "source": "macos-kernel-sandbox", "boot_uuid": item.get("bootUUID")})
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return events[:32]


def collect_denials(receipt: dict) -> dict:
    """Post-stop diagnostic: missing records are explicitly NOT proof of no denial."""
    pid = int(receipt["pid"])
    predicate = f'processID == 0 AND senderImagePath ENDSWITH "/Sandbox" AND eventMessage CONTAINS "({pid}) deny("'
    process = subprocess.Popen(
        ["/usr/bin/log", "show", "--last", "15s", "--style", "ndjson", "--predicate", predicate],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    # _capture retains a bounded tail for receipt data. The predicate is one PID only.
    failure, outputs = _capture(process, time.monotonic() + 5, tail_bytes=4 * 1024 * 1024)
    if failure or process.returncode:
        return {"status": "unavailable", "events": []}
    events = parse_denials(outputs["stdout"]["tail"], pid=pid,
                           started_ns=receipt["started_ns"], ended_ns=receipt["ended_ns"])
    return {"status": "observed" if events else "not_observed", "events": events}
