#!/usr/bin/env python3
"""Seed JoyCTL hosted tenant and emit pairing codes for named JoyMesh devices."""
from __future__ import annotations

import json
import os
from pathlib import Path

from joyctl.hosted import HostedControlPlane

STATE = Path(os.path.expanduser("~/.local/share/joy-multihost"))
DB = STATE / "hosted.sqlite3"
OUT = STATE / "bootstrap.json"

DEVICES = (
    {
        "name": "joymesh-joy",
        "capabilities": (
            "read_repository",
            "write_repository",
            "run_tests",
            "local_model",
            "opencode",
            "codex",
            "grok",
        ),
        "routes": (
            {"id": "opencode-ollama", "available": True, "capabilities": ["local_model", "opencode", "read_repository", "write_repository"]},
            {"id": "codex", "available": True, "capabilities": ["codex", "read_repository", "write_repository", "run_tests"]},
            {"id": "grok", "available": True, "capabilities": ["grok", "read_repository"]},
        ),
    },
    {
        "name": "joymesh-mac-mini",
        "capabilities": (
            "read_repository",
            "write_repository",
            "run_tests",
            "opencode",
            "joymux",
        ),
        "routes": (
            {"id": "mac-default", "available": True, "capabilities": ["read_repository", "write_repository", "run_tests", "opencode", "joymux"]},
        ),
    },
)


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    plane = HostedControlPlane.sqlite(DB)
    user = plane.create_user("owner@local.joy", "Joy Owner")
    organisation = plane.create_organisation(user["id"], "Joy Multihost")
    principal = plane.principal(user["id"], organisation["id"])
    workspace = plane.create_workspace(principal, "Primary")
    project = plane.create_project(principal, workspace["id"], "joymesh", "main")
    token = plane.issue_human_access_token(principal, ttl_seconds=86_400)
    pairings = []
    for device in DEVICES:
        pairing = plane.create_pairing(principal)
        pairings.append(
            {
                "device_name": device["name"],
                "pairing_id": pairing["pairing_id"],
                "pairing_code": pairing["pairing_code"],
                "expires_at": pairing["expires_at"],
                "capabilities": list(device["capabilities"]),
                "routes": list(device["routes"]),
            }
        )
    payload = {
        "database": str(DB),
        "user_id": user["id"],
        "organisation_id": organisation["id"],
        "workspace_id": workspace["id"],
        "project_id": project["id"],
        "human_access_token": token,
        "hosted_base_url": "http://100.109.234.47:8766",
        "pairings": pairings,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(OUT, 0o600)
    print(json.dumps({"ok": True, "bootstrap": str(OUT), "devices": [p["device_name"] for p in pairings]}))


if __name__ == "__main__":
    main()
