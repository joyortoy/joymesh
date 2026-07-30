#!/usr/bin/env python3
"""Deterministic fake OpenCode CLI for connector tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    mode = os.environ.get("JOYMESH_FAKE_OPENCODE_MODE", "success")
    if mode == "broken":
        print("cannot execute binary: mach-o wrong architecture", file=sys.stderr)
        return 127
    if "--version" in argv or "-v" in argv:
        if mode == "version_fail":
            print("failed to resolve version", file=sys.stderr)
            return 1
        print("opencode 1.2.3")
        return 0
    if len(argv) >= 2 and argv[1] == "auth":
        return _auth(argv[2:], mode)
    if len(argv) >= 2 and argv[1] == "models":
        if mode == "unauthenticated":
            print("")
            return 0
        print("opencode/mimo-v2.5-free")
        print("anthropic/claude-sonnet-4")
        return 0
    if len(argv) >= 2 and argv[1] == "run":
        if "--help" in argv:
            print("Usage: opencode run --format json --dir <dir> <prompt>")
            print("  --format   Format: default or json")
            print("  --dir      Directory to run in")
            return 0
        return _run(argv[2:], mode)
    print("unknown command", file=sys.stderr)
    return 2


def _auth(argv: list[str], mode: str) -> int:
    if mode == "unauthenticated":
        print("Credentials ~/.local/share/opencode/auth.json\n0 credentials")
        return 0
    if mode == "quota":
        print("Error: quota exhausted for provider")
        return 1
    if mode == "rate_limit":
        print("Error: rate limit exceeded")
        return 1
    if argv and argv[0] in {"list", "ls"}:
        print("anthropic  configured\nopenai  configured")
        return 0
    print("auth ok")
    return 0


def _run(argv: list[str], mode: str) -> int:
    if mode == "timeout":
        import time

        time.sleep(30)
        return 0
    if mode == "nonzero":
        print(json.dumps({"type": "text", "part": {"text": "failed"}}))
        return 2
    if mode == "malformed":
        print("not-json-line")
        print("{broken")
        return 0
    if mode == "rate_limit":
        print(json.dumps({"type": "text", "part": {"text": "rate limit exceeded"}}))
        return 1
    if mode == "quota":
        print(json.dumps({"type": "text", "part": {"text": "quota exhausted"}}))
        return 1
    # success JSONL stream
    workspace = Path.cwd()
    if "--dir" in argv:
        idx = argv.index("--dir")
        if idx + 1 < len(argv):
            workspace = Path(argv[idx + 1])
    prompt = argv[-1] if argv else ""
    events = [
        {"type": "step_start", "sessionID": "ses_test"},
        {
            "type": "tool_use",
            "part": {
                "tool": "read",
                "state": {"status": "completed", "input": {"path": "README.md"}},
            },
        },
        {
            "type": "text",
            "part": {
                "text": f"project in {workspace.name}; prompt={prompt[:40]}",
            },
        },
        {"type": "step_finish", "part": {"tokens": {"input": 10, "output": 5}}},
    ]
    for event in events:
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
