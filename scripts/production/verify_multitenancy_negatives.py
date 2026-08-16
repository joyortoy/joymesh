#!/usr/bin/env python3
"""Run JoyCLI multitenancy negative checks (via sibling repo) and write report."""

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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    target = JOYCLI / "tests/test_multitenancy_negatives.py"
    ok = False
    detail: dict = {"target": str(target), "joycli_repo": str(JOYCLI)}
    if target.exists():
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-q", "--tb=no"],
            cwd=JOYCLI,
            capture_output=True,
            text=True,
        )
        ok = proc.returncode == 0
        detail.update(
            {
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout.splitlines()[-5:],
                "stderr_tail": proc.stderr.splitlines()[-5:],
            }
        )
    else:
        detail["error"] = "joycli multitenancy test file not found"

    report = {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
    path = OUT / "multitenancy-negative.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "report": str(path)}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
