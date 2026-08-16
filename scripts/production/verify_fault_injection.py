#!/usr/bin/env python3
"""Run production fault-injection matrix (25 cases) and write JSON report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JOYCLI_ROOT = Path(os.environ.get("JOYCLI_ROOT", Path.home() / "intexta-buildweek/joycli"))
OUT = Path(os.environ.get("QUAL_OUTPUT_DIR", ROOT / "reports/data/production"))

# Canonical 25-case production fault matrix.
CASES: list[dict] = [
    {"id": "FI-01", "title": "Production missing signing key fails closed", "nodeid": "tests/test_fault_injection_production.py::test_production_missing_signing_key_fails", "repo": "joymesh"},
    {"id": "FI-02", "title": "Invalid signature rejected", "nodeid": "tests/test_fault_injection_production.py::test_invalid_signature_rejected_by_verifier", "repo": "joymesh"},
    {"id": "FI-03", "title": "Outbox restore checksum mismatch", "nodeid": "tests/test_fault_injection_production.py::test_outbox_restore_checksum_mismatch", "repo": "joymesh"},
    {"id": "FI-04", "title": "Backup interrupt corrupt manifest", "nodeid": "tests/test_fault_injection_production.py::test_backup_interrupt_corrupt_manifest", "repo": "joymesh"},
    {"id": "FI-05", "title": "Outbox max entries from production config", "nodeid": "tests/test_fault_injection_production.py::test_outbox_max_entries_from_production_config", "repo": "joymesh"},
    {"id": "FI-06", "title": "Revoked key rejected on JoyCLI side", "nodeid": "tests/test_fault_injection_production.py::test_revoked_key_rejected_on_joycli_side", "repo": "joymesh"},
    {"id": "FI-07", "title": "Commit durable before ack semantics", "nodeid": "tests/test_fault_injection_intake.py::test_commit_durable_before_ack_semantics", "repo": "joycli"},
    {"id": "FI-08", "title": "Receive without commit not visible", "nodeid": "tests/test_fault_injection_intake.py::test_receive_without_commit_not_visible", "repo": "joycli"},
    {"id": "FI-09", "title": "Listener rejects oversized frame", "nodeid": "tests/test_fault_injection_intake.py::test_listener_rejects_oversized_frame", "repo": "joycli"},
    {"id": "FI-10", "title": "Restore rejects future schema version", "nodeid": "tests/test_fault_injection_intake.py::test_restore_rejects_future_schema_version", "repo": "joycli"},
    {"id": "FI-11", "title": "Restore refuses overwrite without force", "nodeid": "tests/test_fault_injection_intake.py::test_restore_refuses_overwrite_without_force", "repo": "joycli"},
    {"id": "FI-12", "title": "Revoked publisher key rejected", "nodeid": "tests/test_fault_injection_intake.py::test_revoked_publisher_key_rejected", "repo": "joycli"},
    {"id": "FI-13", "title": "Production validate requires signing key", "nodeid": "tests/test_production_readiness.py::test_production_validate_requires_signing_key", "repo": "joymesh"},
    {"id": "FI-14", "title": "Publisher fails closed in production", "nodeid": "tests/test_production_readiness.py::test_publisher_fails_closed_in_production", "repo": "joymesh"},
    {"id": "FI-15", "title": "Key generate never returns private material", "nodeid": "tests/test_production_readiness.py::test_key_generate_never_returns_private", "repo": "joymesh"},
    {"id": "FI-16", "title": "Delivery backup restore roundtrip", "nodeid": "tests/test_production_readiness.py::test_delivery_backup_restore", "repo": "joymesh"},
    {"id": "FI-17", "title": "Node challenge signature roundtrip", "nodeid": "tests/test_node_production_path.py::test_challenge_signature_roundtrip", "repo": "joymesh"},
    {"id": "FI-18", "title": "Inline transport refused in production", "nodeid": "tests/test_node_production_path.py::test_inline_refused_in_production", "repo": "joymesh"},
    {"id": "FI-19", "title": "Node journal survives restart; blocks duplicate", "nodeid": "tests/test_node_production_path.py::test_node_journal_survives_restart_and_blocks_duplicate", "repo": "joymesh"},
    {"id": "FI-20", "title": "Connector envelope signing", "nodeid": "tests/test_node_production_path.py::test_connector_envelope_signing", "repo": "joymesh"},
    {"id": "FI-21", "title": "Foreign org cannot use wrong tenant key", "nodeid": "tests/test_multitenancy_negatives.py::test_foreign_org_cannot_use_wrong_tenant_key", "repo": "joycli"},
    {"id": "FI-22", "title": "Cross-tenant publish rejected", "nodeid": "tests/test_multitenancy_negatives.py::test_cross_tenant_publish_rejected", "repo": "joycli"},
    {"id": "FI-23", "title": "Harness list org-scoped projection filter", "nodeid": "tests/test_multitenancy_negatives.py::test_list_harnesses_org_scoped_projection_filter", "repo": "joycli"},
    {"id": "FI-24", "title": "Key id alone does not authorize without org", "nodeid": "tests/test_multitenancy_negatives.py::test_key_id_alone_does_not_authorize_without_matching_org", "repo": "joycli"},
    {"id": "FI-25", "title": "SIGKILL mid-commit on live Linux intake", "status": "skip", "evidence": "docs/production-deployment.md#walkthrough; requires live systemd intake — not executed on prod-qual"},
]


def _run_pytest(nodeid: str, repo: str) -> dict:
    cwd = ROOT if repo == "joymesh" else JOYCLI_ROOT
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "--tb=no"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-3:],
        "stderr_tail": proc.stderr.splitlines()[-3:],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for case in CASES:
        if case.get("status") == "skip":
            results.append({**case, "status": "skip"})
            continue
        run = _run_pytest(case["nodeid"], case["repo"])
        results.append({**case, **run, "evidence": case["nodeid"]})

    executed = [r for r in results if r["status"] != "skip"]
    ok = all(r["status"] == "pass" for r in executed)
    report = {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(results),
            "pass": sum(1 for r in results if r["status"] == "pass"),
            "fail": sum(1 for r in results if r["status"] == "fail"),
            "skip": sum(1 for r in results if r["status"] == "skip"),
        },
        "cases": results,
    }
    path = OUT / "fault-injection.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "report": str(path), "summary": report["summary"]}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
