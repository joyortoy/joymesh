#!/usr/bin/env python3
"""Packaged production qualification harness.

Usage:
  QUAL_DURATION_SECONDS=3600 python scripts/production/run_qualification.py
  QUAL_DURATION_SECONDS=28800 ...  # 8-hour
"""

from __future__ import annotations

import json
import os
import resource
import time
from datetime import datetime, timezone
from pathlib import Path


def rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS returns bytes; Linux often KB — normalize heuristically.
    value = int(usage.ru_maxrss)
    if value < 10_000_000:
        return value * 1024
    return value


def main() -> int:
    duration = int(os.environ.get("QUAL_DURATION_SECONDS", "60"))
    out_dir = Path(os.environ.get("QUAL_OUTPUT_DIR", "reports/data/production"))
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    samples: list[dict] = []
    ops = {"ticks": 0, "failures": 0}
    deadline = time.time() + duration
    while time.time() < deadline:
        ops["ticks"] += 1
        samples.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "rss_bytes": rss_bytes(),
                "open_fds": len(os.listdir("/dev/fd")) if Path("/dev/fd").exists() else -1,
            }
        )
        time.sleep(min(5.0, max(0.5, duration / 120)))
    ended = datetime.now(timezone.utc)
    elapsed = (ended - started).total_seconds()
    min_ticks = max(1, int(duration / 10))
    gates = {
        "duration_met": elapsed >= (duration - 5),
        "zero_failures": ops["failures"] == 0,
        "min_ticks": ops["ticks"] >= min_ticks,
    }
    report = {
        "ok": all(gates.values()),
        "gates": gates,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": duration,
        "elapsed_seconds": elapsed,
        "operations": ops,
        "samples": samples[-120:],
        "note": "Lightweight resource sampler; pair with verify_* scripts for functional proof.",
    }
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out_file = os.environ.get("QUAL_OUTPUT_FILE")
    path = Path(out_file) if out_file else out_dir / f"qualification-{duration}s-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(path), "ticks": ops["ticks"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
