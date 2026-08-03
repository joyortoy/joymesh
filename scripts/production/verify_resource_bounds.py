#!/usr/bin/env python3
"""Run resource bounds checks across JoyCLI + JoyMesh and write report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JOYCLI = Path(os.environ.get("JOYCLI_REPO", Path.home() / "intexta-buildweek/joycli"))
OUT = Path(os.environ.get("QUAL_OUTPUT_DIR", ROOT / "reports/data/production"))


def _pytest(cwd: Path, target: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--tb=no"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return {
        "cwd": str(cwd),
        "target": target,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-5:],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = [
        _pytest(ROOT, "tests/test_fault_injection_production.py::test_outbox_max_entries_from_production_config"),
        _pytest(ROOT, "tests/test_production_readiness.py"),
    ]
    joycli_target = JOYCLI / "tests/test_resource_bounds.py"
    if joycli_target.exists():
        cases.append(_pytest(JOYCLI, "tests/test_resource_bounds.py"))
    else:
        cases.append({"ok": False, "error": f"missing {joycli_target}"})

    report = {
        "ok": all(item.get("ok") for item in cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
    }
    path = OUT / "resource-bounds.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(path)}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
