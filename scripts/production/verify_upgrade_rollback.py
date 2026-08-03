#!/usr/bin/env python3
"""Simulate RC1 vs candidate wheel upgrade/rollback path (macOS-friendly)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(os.environ.get("RC1_ARTIFACTS", Path.home() / "Documents/joymesh-rc1-verify/artifacts"))
OUT = Path(os.environ.get("QUAL_OUTPUT_DIR", ROOT / "reports/data/production"))


def _joymesh_wheel(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    wheels = sorted(directory.glob("joymesh-*.whl"))
    return wheels[0] if wheels else None


def _find_wheels() -> tuple[Path | None, Path | None]:
    rc1_dir = os.environ.get("RC1_WHEEL_DIR")
    cand_dir = os.environ.get("CANDIDATE_WHEEL_DIR")
    if rc1_dir and cand_dir:
        return _joymesh_wheel(Path(rc1_dir)), _joymesh_wheel(Path(cand_dir))
    if not ARTIFACTS.exists():
        return None, None
    wheels = sorted(ARTIFACTS.glob("joymesh-*.whl"))
    if not wheels:
        return None, None
    rc1 = next((w for w in wheels if "rc1" in w.name.lower()), wheels[0])
    candidate = wheels[-1] if len(wheels) > 1 else None
    return rc1, candidate


def _pip_install(venv_python: Path, wheel: Path) -> dict:
    proc = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--force-reinstall", str(wheel)],
        capture_output=True,
        text=True,
    )
    return {"wheel": str(wheel), "ok": proc.returncode == 0, "stderr_tail": proc.stderr.splitlines()[-3:]}


def _import_check(venv_python: Path) -> dict:
    proc = subprocess.run(
        [str(venv_python), "-c", "import joymesh; from joymesh.production.config import load_production_config; print(load_production_config().max_outbox_entries)"],
        capture_output=True,
        text=True,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr_tail": proc.stderr.splitlines()[-3:]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rc1, candidate = _find_wheels()
    steps: list[dict] = []
    ok = True

    with tempfile.TemporaryDirectory(prefix="joymesh-upgrade-") as tmp:
        venv_dir = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=False)
        venv_python = venv_dir / "bin" / "python"
        if not venv_python.exists():
            ok = False
            steps.append({"step": "create_venv", "ok": False})
        else:
            steps.append({"step": "create_venv", "ok": True})
            if rc1 is not None:
                steps.append({"step": "install_rc1", **_pip_install(venv_python, rc1)})
                steps.append({"step": "verify_rc1", **_import_check(venv_python)})
            else:
                steps.append({"step": "install_rc1", "ok": False, "note": "RC1 wheel not found; skipped"})
                ok = False

            if candidate is not None and rc1 is not None and candidate != rc1:
                steps.append({"step": "upgrade_candidate", **_pip_install(venv_python, candidate)})
                steps.append({"step": "verify_candidate", **_import_check(venv_python)})
                steps.append({"step": "rollback_rc1", **_pip_install(venv_python, rc1)})
                steps.append({"step": "verify_rollback", **_import_check(venv_python)})
            elif rc1 is None:
                pass
            else:
                steps.append({"step": "upgrade_candidate", "ok": False, "note": "candidate wheel not found; skipped"})

    if steps:
        ok = ok and all(item.get("ok", False) for item in steps if item["step"].startswith(("install", "verify", "upgrade", "rollback")))

    report = {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts_dir": str(ARTIFACTS),
        "rc1_wheel": str(rc1) if rc1 else None,
        "candidate_wheel": str(candidate) if candidate else None,
        "steps": steps,
    }
    path = OUT / "upgrade-rollback.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "report": str(path)}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
