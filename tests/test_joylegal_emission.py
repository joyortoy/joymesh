"""JoyLegal claim/bundle emission — producer boundary tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from joymesh.cli import app
from joymesh.legal import (
    build_bundle_v2,
    build_certification_observation_v2,
    build_claim_v2,
    build_connector_evidence,
    build_soak_evidence,
    collect_source_identity,
    create_bundle_directory,
    export_evidence,
    validate_against_schema,
    verify_bundle,
)
from joymesh.legal.identity import package_version

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()


def _identity():
    return collect_source_identity(ROOT, producer_system="joymesh", package_version_value=package_version())


def test_claim_v2_schema_and_producer_boundary() -> None:
    identity = _identity()
    claim = build_claim_v2(
        identity=identity,
        claim_type="production_ready",
        environment="local",
        workspace_id="ws-joymesh",
        claimant="joymesh-producer",
        requested_profile="joymesh-production-readiness-v1",
        supporting_evidence=["evidence:soak-qualification"],
        limitations=["8h soak incomplete"],
    )
    assert claim["version"] == "joylegal.claim/v2"
    assert claim["status"] == "submitted"
    assert claim["scope"]["qualification_observation"] == "awaiting_joylegal_decision"
    assert "ALLOW" not in json.dumps(claim)
    check = validate_against_schema(claim, "claim-v2.json")
    assert check["ok"], check


def test_certification_observation_v2_never_verdict() -> None:
    identity = _identity()
    soak = build_soak_evidence(identity=identity, repo_root=ROOT)
    claim = build_claim_v2(
        identity=identity,
        claim_type="production_ready",
        environment="local",
        workspace_id="ws-joymesh",
        claimant="joymesh-producer",
        requested_profile="joymesh-production-readiness-v1",
        supporting_evidence=[soak["id"]],
        limitations=soak["limitations"],
    )
    report = build_certification_observation_v2(
        identity=identity,
        claim=claim,
        evidence_items=[soak],
        known_gaps=["soak_8h_incomplete"],
    )
    assert report["decision"] == "AWAITING_JOYLEGAL"
    assert report["report_status"] == "PRODUCER_OBSERVATION"
    assert report["evaluator_version"] == "joymesh.producer-observation/v1"
    assert report["evidence_admitted"] == []
    assert any(item.startswith("submitted_pending_admission:") for item in report["evidence_missing"])
    assert "soak_8h_incomplete" in report["evidence_missing"]
    check = validate_against_schema(report, "certification-v2.json")
    assert check["ok"], check
    poisoned = dict(report)
    poisoned["decision"] = "ALLOW"
    bad = validate_against_schema(poisoned, "certification-v2.json")
    assert not bad["ok"]


def test_soak_evidence_reads_real_qualification_file() -> None:
    identity = _identity()
    soak = build_soak_evidence(identity=identity, repo_root=ROOT)
    qual_path = ROOT / "reports/data/production/qualification-1h.json"
    if qual_path.is_file():
        assert soak["source_present"] is True
        assert soak["mode"] == "1h"
        assert soak["requested_duration_seconds"] == 3600
        assert soak["actual_duration_seconds"] is not None
        assert soak["report_status"] == "PRODUCER_OBSERVATION"
    assert soak["soak_8h"]["complete"] is False
    assert any("8h" in item.lower() for item in soak["limitations"])


def test_connector_evidence_marks_fake_local_unsatisfied() -> None:
    identity = _identity()
    connector = build_connector_evidence(identity=identity, repo_root=ROOT)
    fake_ids = {item["harness_id"] for item in connector["fake_local_providers"]}
    assert fake_ids == {"fake", "joy"}
    for item in connector["fake_local_providers"]:
        assert item["live_provider_gate_satisfied"] is False
    unsatisfied = [item for item in connector["connectors"] if not item["live_provider_gate_satisfied"]]
    assert unsatisfied, "catalogue should include connectors below live-provider maturity"
    assert connector["report_status"] == "PRODUCER_OBSERVATION"


def test_bundle_create_and_verify(tmp_path: Path) -> None:
    identity = _identity()
    soak = build_soak_evidence(identity=identity, repo_root=ROOT)
    connector = build_connector_evidence(identity=identity, repo_root=ROOT)
    evidence_items = [soak, connector]
    claim = build_claim_v2(
        identity=identity,
        claim_type="release_candidate",
        environment="local",
        workspace_id="ws-joymesh",
        claimant="joymesh-producer",
        requested_profile="joymesh-production-readiness-v1",
        supporting_evidence=[item["id"] for item in evidence_items],
        limitations=["test"],
    )
    certification = build_certification_observation_v2(
        identity=identity,
        claim=claim,
        evidence_items=evidence_items,
    )
    evidence_dir = tmp_path / "evidence"
    export_evidence(
        identity=identity,
        output_dir=evidence_dir,
        evidence_items=evidence_items,
        repo_root=ROOT,
    )
    out = tmp_path / "bundle"
    result = create_bundle_directory(
        identity=identity,
        output_dir=out,
        claim=claim,
        certification=certification,
        evidence_dir=evidence_dir,
    )
    assert result["manifest"]["version"] == "joylegal.bundle/v2"
    verified = verify_bundle(out)
    assert verified["ok"], verified
    try:
        build_bundle_v2(
            identity=identity,
            subject="joymesh",
            bundle_type="producer_evidence",
            workspace_id=None,
            file_hashes=[{"path": "../escape", "sha256": "a" * 64}],
            included_evidence=[],
            included_schemas=["joylegal.bundle/v2"],
            generator_version="test",
        )
        raise AssertionError("expected path traversal rejection")
    except ValueError as exc:
        assert "unsafe" in str(exc)


def test_cli_legal_compatibility_check() -> None:
    result = RUNNER.invoke(app, ["legal", "compatibility", "check", "--repo", str(ROOT)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_cli_legal_evidence_export(tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    result = RUNNER.invoke(
        app,
        ["legal", "evidence", "export", "--output", str(out), "--repo", str(ROOT)],
    )
    assert result.exit_code == 0, result.output
    assert (out / "producer-evidence.json").is_file()
