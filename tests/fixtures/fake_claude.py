#!/usr/bin/env python3
"""Deterministic fake Claude Code CLI for connector tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    mode = os.environ.get("JOYMESH_FAKE_CLAUDE_MODE", "success")
    if mode == "broken":
        print("cannot execute binary: mach-o wrong architecture", file=sys.stderr)
        return 127
    if "--version" in argv or "-v" in argv:
        if mode == "version_fail":
            print("failed to resolve version", file=sys.stderr)
            return 1
        print("2.1.220 (Claude Code)")
        return 0
    if "--help" in argv or "-h" in argv:
        print("Usage: claude [options] [prompt]")
        print("  --print")
        print("  --output-format  text|json|stream-json")
        print("  --permission-mode")
        print("  --tools")
        print("  --disallowedTools")
        print("  --verbose")
        return 0
    if len(argv) >= 2 and argv[1] == "auth":
        return _auth(argv[2:], mode)

    # Non-interactive print path
    if "--print" in argv or "-p" in argv:
        return _print_run(argv, mode)

    print("unknown command", file=sys.stderr)
    return 2


def _auth(argv: list[str], mode: str) -> int:
    if mode == "unauthenticated":
        print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}))
        return 0
    if mode == "api_key":
        print(json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}))
        return 0
    if mode == "provider_override":
        print(json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "bedrock"}))
        return 0
    if mode == "auth_failure":
        print("authentication failed: invalid token", file=sys.stderr)
        return 1
    if mode == "quota":
        print("Error: quota exhausted for account")
        return 1
    if mode == "rate_limit":
        print("Error: rate limit exceeded")
        return 1
    if mode == "plan_restricted":
        print("Error: plan restriction — upgrade required")
        return 1
    if argv and argv[0] == "status":
        # Default: subscription authenticated
        print(json.dumps({"loggedIn": True, "authMethod": "oauth", "apiProvider": "firstParty"}))
        return 0
    if argv and argv[0] == "login":
        print("login ok")
        return 0
    print("auth ok")
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
        print(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "ses_partial",
                    "tools": ["Read"],
                }
            )
        )
        # Intentionally incomplete JSON line to simulate an interrupted stream.
        print('{"type":"assistant","message":{"content":[{"type":"text","text":"cut"}')
        return 1
    if mode == "rate_limit":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "error": "rate limit exceeded",
                    "session_id": "ses_rl",
                }
            )
        )
        return 1
    if mode == "quota":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "error": "quota exhausted",
                    "session_id": "ses_q",
                }
            )
        )
        return 1
    if mode == "plan_restricted":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "error": "plan restriction",
                    "session_id": "ses_p",
                }
            )
        )
        return 1
    if mode == "auth_failure":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "error": "Not logged in · Please run /login",
                    "session_id": "ses_a",
                }
            )
        )
        return 1

    prompt = argv[-1] if argv else ""
    workspace = Path.cwd()
    session = "ses_test"

    if mode == "deny_edit":
        _emit_init(session, tools=["Read", "Glob", "Grep"])
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": session,
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Edit",
                                "input": {"file_path": "protected.txt"},
                            }
                        ]
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "session_id": session,
                    "error": "Permission denied",
                    "permission_denials": [
                        {
                            "tool_name": "Edit",
                            "tool_use_id": "toolu_1",
                            "tool_input": {"file_path": "protected.txt"},
                        }
                    ],
                }
            )
        )
        return 1

    if mode == "deny_shell":
        _emit_init(session, tools=["Read"])
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "session_id": session,
                    "error": "Permission denied",
                    "permission_denials": [
                        {
                            "tool_name": "Bash",
                            "tool_use_id": "toolu_2",
                            "tool_input": {"command": "echo pwned > shell-created.txt"},
                        }
                    ],
                }
            )
        )
        return 1

    if mode == "deny_network":
        _emit_init(session, tools=["Read"])
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "session_id": session,
                    "error": "Permission denied",
                    "permission_denials": [
                        {
                            "tool_name": "WebFetch",
                            "tool_use_id": "toolu_3",
                            "tool_input": {"url": "https://example.com"},
                        }
                    ],
                }
            )
        )
        return 0

    if mode == "deny_external":
        _emit_init(session, tools=["Read"])
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": session,
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "I cannot read files outside the workspace; "
                                    "external path access is not allowed."
                                ),
                            }
                        ]
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "session_id": session,
                    "result": "external path denied",
                }
            )
        )
        return 0

    if mode == "deny_subprocess":
        _emit_init(session, tools=["Read"])
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "session_id": session,
                    "error": "Permission denied",
                    "permission_denials": [
                        {
                            "tool_name": "Agent",
                            "tool_use_id": "toolu_4",
                            "tool_input": {"description": "spawn subprocess"},
                        }
                    ],
                }
            )
        )
        return 1

    # Default success read-only inspection
    _emit_init(session, tools=["Read", "Glob", "Grep"])
    print(
        json.dumps(
            {
                "type": "assistant",
                "session_id": session,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_r",
                            "name": "Read",
                            "input": {"file_path": "README.md"},
                        }
                    ]
                },
            }
        )
    )
    print(
        json.dumps(
            {
                "type": "user",
                "session_id": session,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_r",
                            "content": f"# {workspace.name}\n",
                        }
                    ]
                },
            }
        )
    )
    print(
        json.dumps(
            {
                "type": "assistant",
                "session_id": session,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Read-only ok in {workspace.name}; prompt={prompt[:40]}",
                        }
                    ]
                },
            }
        )
    )
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session,
                "result": "done",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
    )
    return 0


def _emit_init(session: str, *, tools: list[str]) -> None:
    print(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session,
                "tools": tools,
                "model": "sonnet",
                "permissionMode": "plan",
                "apiKeySource": "none",
            }
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
