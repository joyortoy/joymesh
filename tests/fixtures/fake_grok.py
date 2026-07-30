#!/usr/bin/env python3
"""Deterministic fake Grok Build CLI for connector tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    mode = os.environ.get("JOYMESH_FAKE_GROK_MODE", "success")
    if mode == "broken":
        print("cannot execute binary: mach-o wrong architecture", file=sys.stderr)
        return 127
    if "--version" in argv or "-v" in argv or (len(argv) >= 2 and argv[1] == "version"):
        if mode == "version_fail":
            print("failed to resolve version", file=sys.stderr)
            return 1
        print("grok 0.2.114 (deadbeef)")
        return 0
    if "--help" in argv or "-h" in argv:
        print("Usage: grok [OPTIONS] [PROMPT]")
        print("  -p, --single")
        print("  --output-format  plain|json|streaming-json")
        print("  --sandbox")
        print("  --permission-mode")
        print("  --tools")
        print("  --disallowed-tools")
        print("  agent")
        return 0
    if len(argv) >= 2 and argv[1] == "models":
        return _models(mode)
    if len(argv) >= 2 and argv[1] == "inspect":
        return _inspect(mode)
    if len(argv) >= 2 and argv[1] == "login":
        print("login ok")
        return 0
    if "-p" in argv or "--single" in argv:
        return _print_run(argv, mode)
    print("unknown command", file=sys.stderr)
    return 2


def _models(mode: str) -> int:
    if mode == "unauthenticated":
        print(
            "You are not authenticated.\n\n"
            "Default model: grok-4.5\n\n"
            "Available models:\n  * grok-4.5"
        )
        return 0
    if mode == "api_key":
        print("Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (default)")
        return 0
    if mode == "free_access":
        print("Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (free trial)")
        return 0
    if mode == "provider_override":
        print("Default model: custom-model\n\nAvailable models:\n  * custom-model (default)")
        return 0
    if mode == "auth_failure":
        print("Not signed in. Run grok login.", file=sys.stderr)
        return 1
    if mode == "quota":
        print("Error: quota exhausted")
        return 1
    if mode == "rate_limit":
        print("Error: rate limit exceeded")
        return 1
    if mode == "plan_restricted":
        print("Error: plan restriction — upgrade required")
        return 1
    print("Default model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (default)")
    return 0


def _inspect(mode: str) -> int:
    if mode == "inspect_malformed":
        print("{broken")
        return 0
    payload = {
        "grokVersion": "0.2.114",
        "cwd": str(Path.cwd()),
        "projectTrusted": True,
        "permissions": {"loaded": 0},
        "plugins": [],
        "mcpServers": [],
        "privacy": {
            "telemetry": "disabled",
            "repository_upload": "unknown",
        },
    }
    if mode == "telemetry_active":
        payload["privacy"] = {"telemetry": "enabled", "repository_upload": "unknown"}
    if mode == "upload_warning":
        payload["privacy"] = {
            "telemetry": "disabled",
            "repository_upload": "active",
        }
    if mode == "upload_disabled":
        payload["privacy"] = {
            "telemetry": "disabled",
            "repository_upload": "disabled",
        }
    print(json.dumps(payload))
    return 0


def _print_run(argv: list[str], mode: str) -> int:
    if mode == "timeout":
        import time

        time.sleep(30)
        return 0
    if mode == "malformed":
        print("not-json-line")
        print("{broken")
        print("warning on stderr", file=sys.stderr)
        return 0
    if mode == "interrupted":
        print(json.dumps({"type": "text", "data": "partial"}))
        print('{"type":"text","data":"cut')
        return 1
    if mode == "rate_limit":
        print(json.dumps({"type": "error", "message": "rate limit exceeded"}))
        return 1
    if mode == "quota":
        print(json.dumps({"type": "error", "message": "quota exhausted"}))
        return 1
    if mode == "plan_restricted":
        print(json.dumps({"type": "error", "message": "plan restriction"}))
        return 1
    if mode == "auth_failure":
        print(json.dumps({"type": "error", "message": "Not signed in. Run grok login."}))
        return 1
    if mode == "unexpected_mutation":
        Path("forbidden-created.txt").write_text("pwned\n", encoding="utf-8")
        print(json.dumps({"type": "text", "data": "edited"}))
        print(json.dumps({"type": "end", "stopReason": "EndTurn", "sessionId": "ses_bad"}))
        return 0

    prompt = ""
    if "-p" in argv:
        prompt = argv[argv.index("-p") + 1]
    elif "--single" in argv:
        prompt = argv[argv.index("--single") + 1]
    workspace = Path.cwd()
    if "--cwd" in argv:
        workspace = Path(argv[argv.index("--cwd") + 1])

    denials = {
        "deny_edit": ("search_replace", "permission_denied_edit", "Edit is not allowed"),
        "deny_shell": ("run_terminal_cmd", "permission_denied_shell", "Shell is denied"),
        "deny_network": ("web_fetch", "permission_denied_network", "Network tools unavailable"),
        "deny_external": (
            "read_file",
            "permission_denied_external_path",
            "Cannot read files outside the workspace under strict sandbox",
        ),
        "deny_subprocess": (
            "run_terminal_cmd",
            "permission_denied_subprocess",
            "Cannot launch another process / subprocess",
        ),
        "deny_plugin": ("plugin", "permission_denied_plugin", "Plugin install is not allowed"),
        "deny_mcp": ("MCPTool", "permission_denied_mcp", "MCP server invoke is denied"),
        "deny_subagent": ("Agent", "permission_denied_subagent", "Subagent spawning blocked"),
    }
    if mode in denials:
        tool, _reason, message = denials[mode]
        print(json.dumps({"type": "text", "data": message}))
        print(
            json.dumps(
                {
                    "type": "permission_denied",
                    "tool": tool,
                    "message": message,
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "error",
                    "message": message,
                    "permission_denials": [{"tool_name": tool}],
                }
            )
        )
        return 1 if mode in {"deny_edit", "deny_shell", "deny_subprocess"} else 0

    # success read-only
    print(json.dumps({"type": "text", "data": f"Read-only ok in {workspace.name}; "}))
    print(json.dumps({"type": "text", "data": f"prompt={prompt[:40]}"}))
    print(
        json.dumps(
            {
                "type": "end",
                "stopReason": "EndTurn",
                "sessionId": "ses_test",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
