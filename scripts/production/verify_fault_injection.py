#!/usr/bin/env python3
"""Run production fault-injection checks and write JSON report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("QUAL_OUTPUT_DIR", ROOT / "reports/data/production"))


def _run_pytest(target: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return {
        "target": target,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-5:],
        "stderr_tail": proc.stderr.splitlines()[-5:],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = [
        _run_pytest("tests/test_fault_injection_production.py"),
        _run_pytest("tests/test_production_readiness.py"),
    ]
    report = {
        "ok": all(item["ok"] for item in cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
    }
    path = OUT / "fault-injection.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(path)}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
