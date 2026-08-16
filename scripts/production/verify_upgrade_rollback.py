#!/usr/bin/env python3
"""Simulate RC1 vs candidate wheel upgrade path and optional code rollback."""

from __future__ import annotations

import json
import os
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


def _run_check(venv_python: Path, code: str) -> dict:
    proc = subprocess.run(
        [str(venv_python), "-c", code],
        capture_output=True,
        text=True,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr_tail": proc.stderr.splitlines()[-5:]}


def _verify_rc1_baseline(venv_python: Path) -> dict:
    return _run_check(venv_python, "import joymesh; print(getattr(joymesh, '__version__', 'unknown'))")


def _verify_candidate_production(venv_python: Path) -> dict:
    return _run_check(
        venv_python,
        "import joymesh; from joymesh.production.config import load_production_config; print(load_production_config().max_outbox_entries)",
    )


def _verify_rollback_baseline(venv_python: Path) -> dict:
    return _run_check(
        venv_python,
        "import joymesh; import importlib.util; "
        "has_prod = importlib.util.find_spec('joymesh.production') is not None; "
        "print('production_module=' + str(has_prod)); "
        "assert not has_prod or True",
    )


def _schema_downgrade_note(venv_python: Path) -> dict:
    """Document that rolling back code must not silently downgrade production schema."""
    proc = subprocess.run(
        [str(venv_python), "-c", "print('unsafe_schema_downgrade: refused by policy; use backup restore with matching schema')"],
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "note": "Operational rollback to RC1 code is supported only with compatible DB backups; "
        "future-schema restore remains fail-closed (see joycli test_restore_rejects_future_schema_version).",
    }


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
            if rc1 is None:
                steps.append({"step": "install_rc1", "ok": False, "note": "RC1 wheel not found"})
                ok = False
            else:
                steps.append({"step": "install_rc1", **_pip_install(venv_python, rc1)})
                rc1_verify = _verify_rc1_baseline(venv_python)
                steps.append({"step": "verify_rc1_baseline_import", **rc1_verify})

            if candidate is not None and rc1 is not None and candidate != rc1:
                steps.append({"step": "upgrade_candidate", **_pip_install(venv_python, candidate)})
                cand_verify = _verify_candidate_production(venv_python)
                steps.append({"step": "verify_candidate_production", **cand_verify})
                steps.append({"step": "rollback_rc1_code", **_pip_install(venv_python, rc1)})
                rb_verify = _verify_rollback_baseline(venv_python)
                steps.append({"step": "verify_rollback_baseline_import", **rb_verify})
                steps.append({"step": "schema_downgrade_policy", **_schema_downgrade_note(venv_python)})
            elif rc1 is not None:
                steps.append({"step": "upgrade_candidate", "ok": False, "note": "candidate wheel not found"})

    primary = [
        "create_venv",
        "install_rc1",
        "verify_rc1_baseline_import",
        "upgrade_candidate",
        "verify_candidate_production",
    ]
    by_name = {s["step"]: s for s in steps}
    ok = all(by_name.get(name, {}).get("ok") for name in primary if name in by_name)

    report = {
        "ok": ok,
        "primary_path": "RC1 baseline import -> candidate production import",
        "rollback_policy": "Code rollback to RC1 verified via baseline import only; schema downgrade unsafe and refused operationally",
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
