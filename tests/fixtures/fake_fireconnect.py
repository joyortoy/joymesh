#!/usr/bin/env python3
"""Deterministic fake FireConnect CLI for provider-route tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    mode = os.environ.get("JOYMESH_FAKE_FIRECONNECT_MODE", "success")
    state_path = Path(os.environ.get("JOYMESH_FAKE_FIRECONNECT_STATE", "/tmp/fake-fc-state.json"))
    state = _load(state_path)

    if mode == "broken":
        print("cannot execute binary", file=sys.stderr)
        return 127

    if "--version" in argv or "-V" in argv:
        if mode == "version_fail":
            print("bad version", file=sys.stderr)
            return 1
        if "--json" in argv:
            print(json.dumps({"version": "0.9.0"}))
        else:
            print("v0.9.0")
        return 0

    if len(argv) >= 2 and argv[1] == "status" and "--json" in argv:
        if mode == "malformed_status":
            print("{broken")
            return 0
        if mode == "unauthenticated":
            print(
                json.dumps(
                    {
                        "auth": {"signedIn": False, "reason": "not signed in"},
                        "activeKeySource": None,
                        "environment": {"cliVersion": "0.9.0"},
                        "keychainPresent": False,
                        "envPresent": False,
                        "backendLabel": "none",
                        "perHarness": _per_harness(state),
                    }
                )
            )
            return 0
        print(
            json.dumps(
                {
                    "auth": {
                        "signedIn": True,
                        "email": "user@example.test",
                        "accountId": "acct_secret",
                        "reason": "",
                    },
                    "activeKeySource": "stored",
                    "environment": {"cliVersion": "0.9.0"},
                    "keychainPresent": True,
                    "envPresent": False,
                    "backendLabel": "macOS Keychain",
                    "configRef": "{keychain:fireworks-api-key}",
                    "perHarness": _per_harness(state),
                }
            )
        )
        return 0

    if len(argv) >= 3 and argv[2] == "status" and "--json" in argv:
        harness = argv[1]
        enabled = bool(state.get("enabled", {}).get(harness, False))
        model = state.get("models", {}).get(harness)
        if mode == "invalid_config" and harness == "opencode":
            print(json.dumps({"harness": harness, "provider": "broken"}))
            return 0
        payload = {
            "harness": harness,
            "provider": "fireworks" if enabled else "default",
            "hasAuthToken": enabled,
            "defaults": {"main": "glm-fast-latest"},
            "current": {"main": model if enabled else None},
        }
        if harness == "codex":
            payload["modelProvider"] = "fireworks-ai" if enabled else "default"
            payload["baseUrl"] = "https://api.fireworks.ai/inference/v1" if enabled else None
        print(json.dumps(payload))
        return 0

    if len(argv) >= 3 and argv[2] == "on":
        harness = argv[1]
        if mode == "enable_fail":
            print("enable failed", file=sys.stderr)
            return 1
        model = None
        if "--model" in argv:
            idx = argv.index("--model")
            model = argv[idx + 1] if idx + 1 < len(argv) else None
            if model and ("sk-" in model or "fw_" in model):
                # Never echo secrets back.
                model = "accounts/fireworks/models/redacted-test"
        # verify_fail: claim success but leave harness disabled.
        if mode != "verify_fail":

            def _enable(state: dict) -> None:
                state.setdefault("enabled", {})[harness] = True
                state.setdefault("models", {})[harness] = (
                    model or "accounts/fireworks/models/deepseek-v4-flash"
                )

            _update_state(state_path, _enable)
        print(f"{harness} fireworks routing enabled")
        return 0

    if len(argv) >= 3 and argv[2] == "off":
        harness = argv[1]
        if mode == "restore_fail":
            print("restore failed", file=sys.stderr)
            return 1

        def _disable(state: dict) -> None:
            state.setdefault("enabled", {})[harness] = False
            state.setdefault("models", {})[harness] = None

        _update_state(state_path, _disable)
        print(f"{harness} restored")
        return 0
    if len(argv) >= 2 and argv[1] == "model" and "list" in argv:
        print(json.dumps({"count": 1, "models": [{"id": "accounts/fireworks/models/test"}]}))
        return 0

    print("unknown", file=sys.stderr)
    return 2


def _per_harness(state: dict) -> list[dict]:
    rows = []
    for harness in ("claude", "codex", "opencode", "cursor", "pi", "vscode", "deepagents"):
        rows.append(
            {
                "id": harness,
                "enabled": bool(state.get("enabled", {}).get(harness, False)),
                "readsFrom": "test",
                "storage": "test",
            }
        )
    return rows


def _load(path: Path) -> dict:
    if not path.exists():
        return {"enabled": {}, "models": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _update_state(path: Path, mutator) -> dict:
    """Serialise read-modify-write so concurrent harness mutations do not clobber."""

    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"enabled": {}, "models": {}}), encoding="utf-8")
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        raw = handle.read()
        state = json.loads(raw) if raw.strip() else {"enabled": {}, "models": {}}
        mutator(state)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(state))
        handle.flush()
        return state


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
