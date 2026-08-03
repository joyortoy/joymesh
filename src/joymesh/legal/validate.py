"""Validate producer contracts against vendored JoyLegal JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def schema_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "joylegal"


def validate_against_schema(document: dict[str, Any], schema_name: str) -> dict[str, Any]:
    schema_path = schema_dir() / schema_name
    if not schema_path.is_file():
        return {
            "ok": False,
            "schema": schema_name,
            "errors": [f"schema file missing: {schema_path}"],
        }
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    required = schema.get("required", [])
    for key in required:
        if key not in document:
            errors.append(f"missing required field: {key}")
    version_pat = (schema.get("properties") or {}).get("version", {}).get("pattern")
    if version_pat and "version" in document:
        import re

        if not re.match(version_pat, str(document["version"])):
            errors.append(f"version mismatch: {document['version']} !~ {version_pat}")
    blob = json.dumps(document, sort_keys=True).lower()
    forbidden = (
        "certified by joylegal",
        "final verdict: allow",
        "final verdict: deny",
        "joylegal has certified",
        "legally approved",
    )
    for phrase in forbidden:
        if phrase in blob:
            errors.append(f"producer-verdict ownership language forbidden: {phrase}")
    decision = str(document.get("decision", "")).upper()
    if decision in {"ALLOW", "DENY"}:
        errors.append(f"producer may not emit JoyLegal verdict decision: {decision}")
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(document, schema)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - surface schema errors to callers
        errors.append(str(exc))
    return {"ok": not errors, "schema": schema_name, "errors": errors}


def compatibility_check(documents: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    results = [validate_against_schema(doc, schema) for doc, schema in documents]
    return {
        "ok": all(item["ok"] for item in results),
        "checks": results,
        "note": (
            "Producers emit evidence and submitted claims only. "
            "JoyLegal admits evidence and owns legitimacy verdicts."
        ),
    }
