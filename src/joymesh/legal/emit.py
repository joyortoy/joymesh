"""Emit joylegal.claim/v2 and joylegal.bundle/v2 without claiming JoyLegal verdicts."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS
from joymesh.legal.canonical import content_hash, deterministic_id, sha256_hex
from joymesh.legal.identity import SourceIdentity

FORBIDDEN_VERDICT_KEYS = frozenset(
    {
        "joylegal_verdict",
        "final_verdict",
        "certified_by_joylegal",
        "production_ready_certified",
    }
)

FORBIDDEN_DECISIONS = frozenset({"ALLOW", "DENY"})
LIVE_PROVIDER_MATURITY = frozenset(
    {"real_binary_tested", "certified", "production_ready"},
)
DEFAULT_PROFILE_ID = "joymesh-production-readiness-v1"
DEFAULT_PROFILE_VERSION = "v1"
SOAK_1H_PATH = Path("reports/data/production/qualification-1h.json")
SOAK_8H_JSON = Path("reports/data/production/qualification-8h.json")
SOAK_8H_STARTED = Path("reports/data/production/qualification-8h/started.txt")
SOAK_8H_PRIOR_DEAD = Path("reports/data/production/prior-8h-dead.json")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None = None) -> str:
    value = moment or _now()
    return value.isoformat().replace("+00:00", "Z")


def _safe_rel(path: str) -> str:
    if ".." in path or path.startswith("/") or "\\" in path:
        raise ValueError(f"unsafe bundle path: {path}")
    return path


def _reject_producer_verdict(decision: str) -> None:
    if decision.upper() in FORBIDDEN_DECISIONS:
        raise ValueError(f"producer may not emit JoyLegal verdict: {decision}")


def build_claim_v2(
    *,
    identity: SourceIdentity,
    claim_type: str,
    environment: str,
    workspace_id: str,
    claimant: str,
    requested_profile: str,
    supporting_evidence: list[str],
    limitations: list[str],
    jurisdiction: str = "unspecified",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build a submitted claim. status is always 'submitted' — JoyLegal evaluates outcomes."""
    moment = _now()
    scope: dict[str, Any] = {
        "subject_system": identity.producer_system,
        "commit": identity.commit,
        "branch": identity.branch,
        "tag": identity.tag,
        "dirty_worktree": identity.dirty,
        "package_version": identity.package_version,
        "requested_profile": requested_profile,
        "limitations": limitations,
        "producer_result": "claim_submitted",
        "qualification_observation": "awaiting_joylegal_decision",
        "note": "This claim does not constitute a JoyLegal legitimacy verdict.",
    }
    for key in FORBIDDEN_VERDICT_KEYS:
        if key in scope:
            raise ValueError(f"forbidden producer verdict key: {key}")
    base: dict[str, Any] = {
        "version": "joylegal.claim/v2",
        "claimant": claimant,
        "subject": identity.producer_system,
        "claim_type": claim_type,
        "scope": scope,
        "environment": environment,
        "applicable_version": identity.package_version,
        "jurisdiction": jurisdiction,
        "requested_valid_from": _iso(moment),
        "requested_valid_until": None,
        "supporting_evidence": supporting_evidence,
        "applicable_rule_ids": [],
        "status": "submitted",
        "producer_system": identity.producer_system,
        "workspace_id": workspace_id,
        "correlation_id": correlation_id
        or deterministic_id("corr", {"commit": identity.commit, "claim_type": claim_type}),
        "integration_refs": [],
    }
    claim_id = deterministic_id("claim", base)
    draft = {**base, "claim_id": claim_id, "integrity_hash": "0" * 64}
    integrity = content_hash(draft, drop=("integrity_hash",))
    return {**draft, "integrity_hash": integrity}


def build_certification_observation_v2(
    *,
    identity: SourceIdentity,
    claim: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_version: str = DEFAULT_PROFILE_VERSION,
    environment: str = "local",
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    known_gaps: list[str] | None = None,
) -> dict[str, Any]:
    """Emit a producer-side certification observation — never an ALLOW/DENY verdict."""
    decision = "AWAITING_JOYLEGAL"
    _reject_producer_verdict(decision)
    moment = _now()
    evidence_ids = [
        str(item.get("id") or item.get("evidence_id"))
        for item in evidence_items
        if item.get("id") or item.get("evidence_id")
    ]
    evidence_missing = [f"submitted_pending_admission:{item}" for item in evidence_ids]
    evidence_missing.extend(known_gaps or [])
    base: dict[str, Any] = {
        "version": "joylegal.certification/v2",
        "profile_id": profile_id,
        "profile_version": profile_version,
        "subject": identity.producer_system,
        "claim_id": claim["claim_id"],
        "environment": environment,
        "decision": decision,
        "evaluator_version": "joymesh.producer-observation/v1",
        "generated_at": _iso(moment),
        "report_status": "PRODUCER_OBSERVATION",
        "producer_system": identity.producer_system,
        "workspace_id": workspace_id or claim.get("workspace_id"),
        "correlation_id": correlation_id or claim.get("correlation_id"),
        "evidence_admitted": [],
        "evidence_missing": evidence_missing,
        "evidence_rejected": [],
        "reason_codes": ["PRODUCER_OBSERVATION_ONLY"],
        "source_repository_ids": [
            deterministic_id("repo", {"commit": identity.commit, "root": identity.repository_root})
        ],
    }
    report_id = deterministic_id("report", base)
    decision_id = deterministic_id("decision", {"report_id": report_id, "decision": decision})
    draft = {
        **base,
        "report_id": report_id,
        "decision_id": decision_id,
        "canonical_content_hash": "0" * 64,
    }
    hashed = content_hash(draft, drop=("canonical_content_hash",))
    return {**draft, "canonical_content_hash": hashed}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _soak_8h_status(repo_root: Path) -> dict[str, Any]:
    complete_path = repo_root / SOAK_8H_JSON
    if complete_path.is_file():
        payload = _load_json(complete_path) or {}
        return {
            "complete": bool(payload.get("ok")),
            "requested_duration_seconds": payload.get("duration_seconds", 8 * 3600),
            "actual_duration_seconds": payload.get("elapsed_seconds"),
            "source_path": str(SOAK_8H_JSON),
            "limitations": [],
        }
    limitations: list[str] = []
    if (repo_root / SOAK_8H_STARTED).is_file():
        limitations.append("8h soak started but final qualification-8h.json not present.")
    prior = _load_json(repo_root / SOAK_8H_PRIOR_DEAD)
    if prior is not None:
        limitations.append(
            "Prior 8h soak run did not complete; durable output was lost before final JSON."
        )
    if not limitations:
        limitations.append("8h production soak not completed or not recorded.")
    return {
        "complete": False,
        "requested_duration_seconds": 8 * 3600,
        "actual_duration_seconds": None,
        "source_path": None,
        "limitations": limitations,
    }


def build_soak_evidence(
    *,
    identity: SourceIdentity,
    repo_root: Path,
    qualification_path: Path | None = None,
) -> dict[str, Any]:
    """Map local qualification JSON into structured producer soak evidence."""
    path = qualification_path or (repo_root / SOAK_1H_PATH)
    payload = _load_json(path)
    soak_8h = _soak_8h_status(repo_root)
    limitations: list[str] = [
        "Producer soak evidence is observational only; JoyLegal owns admission and verdicts.",
    ]
    if payload is None:
        return {
            "schema": "joymesh.producer-soak-evidence/v1",
            "id": "evidence:soak-qualification",
            "producer_system": identity.producer_system,
            "report_status": "PRODUCER_OBSERVATION",
            "qualification_observation": "awaiting_joylegal_decision",
            "mode": "unknown",
            "requested_duration_seconds": None,
            "actual_duration_seconds": None,
            "source_path": str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path),
            "source_present": False,
            "gates": {},
            "operations": {},
            "soak_8h": soak_8h,
            "limitations": [
                *limitations,
                f"Missing qualification file: {path}",
                *soak_8h["limitations"],
            ],
        }

    requested = payload.get("duration_seconds")
    actual = payload.get("elapsed_seconds")
    mode = "1h" if requested == 3600 else f"{requested}s" if requested else "unknown"
    if not soak_8h["complete"]:
        limitations.extend(soak_8h["limitations"])
    return {
        "schema": "joymesh.producer-soak-evidence/v1",
        "id": "evidence:soak-qualification",
        "producer_system": identity.producer_system,
        "report_status": "PRODUCER_OBSERVATION",
        "qualification_observation": "awaiting_joylegal_decision",
        "mode": mode,
        "requested_duration_seconds": requested,
        "actual_duration_seconds": actual,
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "source_path": str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path),
        "source_present": True,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "producer_observed_ok": payload.get("ok"),
        "gates": payload.get("gates", {}),
        "operations": payload.get("operations", {}),
        "note": payload.get("note"),
        "soak_8h": soak_8h,
        "limitations": limitations,
    }


def _connector_live_provider_satisfied(definition: Any) -> tuple[bool, str]:
    harness_id = definition.harness_id
    if harness_id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
        return False, "forbidden_test_harness"
    maturity = str(definition.maturity)
    if maturity not in LIVE_PROVIDER_MATURITY:
        return False, f"maturity_below_live_provider_gate:{maturity}"
    if not definition.remote_execution_supported:
        return False, "remote_execution_not_supported"
    return True, "catalogue_declares_live_remote_execution"


def build_connector_evidence(
    *,
    identity: SourceIdentity,
    repo_root: Path,
) -> dict[str, Any]:
    """Observe connector catalogue readiness against live-provider gates."""
    from joymesh.connectors.loader import ConnectorCatalogue

    catalogue = ConnectorCatalogue.builtins()
    connectors: list[dict[str, Any]] = []
    unsatisfied = 0
    for definition in catalogue.all():
        satisfied, reason = _connector_live_provider_satisfied(definition)
        if not satisfied:
            unsatisfied += 1
        connectors.append(
            {
                "connector_id": definition.harness_id,
                "display_name": definition.display_name,
                "maturity": str(definition.maturity),
                "remote_execution_supported": definition.remote_execution_supported,
                "live_provider_gate_satisfied": satisfied,
                "observation_reason": reason,
            }
        )
    fake_local = [
        {
            "harness_id": harness_id,
            "live_provider_gate_satisfied": False,
            "observation_reason": "forbidden_test_harness_not_production_registered",
        }
        for harness_id in sorted(FORBIDDEN_PRODUCTION_HARNESS_IDS)
    ]
    limitations = [
        "Connector observations reflect catalogue metadata only.",
        "JoyLegal owns live-provider gate verdicts after evidence admission.",
    ]
    if unsatisfied:
        limitations.append(
            f"{unsatisfied} catalogue connector(s) do not satisfy live-provider gates by maturity/remote flags."
        )
    return {
        "schema": "joymesh.producer-connector-evidence/v1",
        "id": "evidence:connector-catalogue",
        "producer_system": identity.producer_system,
        "report_status": "PRODUCER_OBSERVATION",
        "qualification_observation": "awaiting_joylegal_decision",
        "generated_at": _iso(),
        "source_identity": identity.as_dict(),
        "connectors": connectors,
        "fake_local_providers": fake_local,
        "limitations": limitations,
    }


def build_bundle_v2(
    *,
    identity: SourceIdentity,
    subject: str,
    bundle_type: str,
    workspace_id: str | None,
    file_hashes: list[dict[str, str]],
    included_evidence: list[str],
    included_schemas: list[str],
    generator_version: str,
) -> dict[str, Any]:
    moment = _now()
    safe_hashes = [{"path": _safe_rel(item["path"]), "sha256": item["sha256"]} for item in file_hashes]
    base: dict[str, Any] = {
        "version": "joylegal.bundle/v2",
        "bundle_type": bundle_type,
        "subject": subject,
        "workspace": workspace_id,
        "generated_at": _iso(moment),
        "generator_version": generator_version,
        "included_schemas": included_schemas,
        "included_records": [
            f"commit:{identity.commit}",
            f"branch:{identity.branch}",
            f"producer:{identity.producer_system}",
        ],
        "included_evidence": included_evidence,
        "file_hashes": safe_hashes,
        "integration_targets": [identity.producer_system],
        "timeline_ids": [],
        "graph_ids": [],
        "export_policy": "producer_evidence_only",
    }
    bundle_id = deterministic_id("bundle", base)
    draft = {
        **base,
        "bundle_id": bundle_id,
        "manifest_hash": "0" * 64,
        "canonical_content_hash": "0" * 64,
    }
    hashed = content_hash(draft, drop=("manifest_hash", "canonical_content_hash"))
    return {**draft, "manifest_hash": hashed, "canonical_content_hash": hashed}


def export_evidence(
    *,
    identity: SourceIdentity,
    output_dir: Path,
    evidence_items: list[dict[str, Any]],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write structured evidence JSON. Never labels results as JoyLegal certified."""
    output_dir.mkdir(parents=True, exist_ok=True)
    root = repo_root or Path(identity.repository_root)
    if not any(item.get("id") == "evidence:soak-qualification" for item in evidence_items):
        evidence_items = [*evidence_items, build_soak_evidence(identity=identity, repo_root=root)]
    if not any(item.get("id") == "evidence:connector-catalogue" for item in evidence_items):
        evidence_items = [
            *evidence_items,
            build_connector_evidence(identity=identity, repo_root=root),
        ]
    pack: dict[str, Any] = {
        "schema": "joymesh.producer-evidence-pack/v1",
        "producer_system": identity.producer_system,
        "source_identity": identity.as_dict(),
        "generated_at": _iso(),
        "items": evidence_items,
        "qualification_observation": "awaiting_joylegal_decision",
        "limitations": [
            "Producer evidence is observational only.",
            "JoyLegal owns admission and legitimacy verdicts.",
        ],
    }
    path = output_dir / "producer-evidence.json"
    path.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "sha256": digest, "pack": pack, "items": evidence_items}


def create_bundle_directory(
    *,
    identity: SourceIdentity,
    output_dir: Path,
    claim: dict[str, Any] | None,
    certification: dict[str, Any] | None,
    evidence_dir: Path | None,
    extra_files: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Materialize a joylegal.bundle/v2 directory with relative file hashes."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    written: dict[str, bytes] = {}
    if claim is not None:
        written["claim-v2.json"] = (json.dumps(claim, indent=2, sort_keys=True) + "\n").encode()
    if certification is not None:
        written["certification-v2.json"] = (
            json.dumps(certification, indent=2, sort_keys=True) + "\n"
        ).encode()
    if evidence_dir is not None and evidence_dir.is_dir():
        for path in sorted(evidence_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(evidence_dir).as_posix()
                _safe_rel(rel)
                written[f"evidence/{rel}"] = path.read_bytes()
    if extra_files:
        for rel, data in extra_files.items():
            _safe_rel(rel)
            written[rel] = data
    identity_doc = {
        "schema": "joymesh.source-identity/v1",
        "source_identity": identity.as_dict(),
        "qualification_observation": "awaiting_joylegal_decision",
    }
    written["source-identity.json"] = (
        json.dumps(identity_doc, indent=2, sort_keys=True) + "\n"
    ).encode()

    file_hashes: list[dict[str, str]] = []
    for rel, data in sorted(written.items()):
        target = output_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        file_hashes.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})

    included_schemas = ["joylegal.bundle/v2"]
    if claim is not None:
        included_schemas.append("joylegal.claim/v2")
    if certification is not None:
        included_schemas.append("joylegal.certification/v2")
    manifest = build_bundle_v2(
        identity=identity,
        subject=identity.producer_system,
        bundle_type="producer_evidence",
        workspace_id=claim.get("workspace_id") if claim else None,
        file_hashes=file_hashes,
        included_evidence=[item["path"] for item in file_hashes],
        included_schemas=included_schemas,
        generator_version=f"joymesh.legal/{identity.package_version}",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "output_dir": str(output_dir),
        "manifest": manifest,
        "file_count": len(file_hashes),
        "note": "Bundle contains producer evidence only; JoyLegal owns final decisions.",
    }


def verify_bundle(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "errors": ["missing manifest.json"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("version") != "joylegal.bundle/v2":
        errors.append(f"unexpected version: {manifest.get('version')}")
    for entry in manifest.get("file_hashes", []):
        path = bundle_dir / entry["path"]
        if not path.is_file():
            errors.append(f"missing file: {entry['path']}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            errors.append(f"hash mismatch: {entry['path']}")
    draft = dict(manifest)
    draft["manifest_hash"] = "0" * 64
    draft["canonical_content_hash"] = "0" * 64
    recomputed = content_hash(draft, drop=("manifest_hash", "canonical_content_hash"))
    if recomputed != manifest.get("canonical_content_hash"):
        errors.append("canonical_content_hash mismatch")
    cert_path = bundle_dir / "certification-v2.json"
    if cert_path.is_file():
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        decision = str(cert.get("decision", "")).upper()
        if decision in FORBIDDEN_DECISIONS:
            errors.append(f"producer bundle contains forbidden verdict: {decision}")
        if cert.get("report_status") != "PRODUCER_OBSERVATION":
            errors.append("certification report must be PRODUCER_OBSERVATION")
    return {
        "ok": not errors,
        "errors": errors,
        "bundle_id": manifest.get("bundle_id"),
        "canonical_content_hash": manifest.get("canonical_content_hash"),
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_hex(value)
