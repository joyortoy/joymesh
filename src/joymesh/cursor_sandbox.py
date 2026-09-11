"""Host-enforced write confinement for Cursor; native CLI flags are not proof.

macOS only. Writable runtime directories are explicit exceptions. Network and
ordinary reads remain available for Cursor authentication and model access.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


def cursor_sandbox_argv(argv: Sequence[str], workspace: str, *, read_only: bool) -> tuple[str, ...]:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("Cursor host sandbox unavailable; refusing unconfined execution")
    scratch = Path(tempfile.mkdtemp(prefix="joymesh-cursor-sandbox-")).resolve()
    home = Path.home()
    runtime_paths = [
        home / ".cursor" / name
        for name in ("chats", "ai-tracking", "debug-logs", "plans", "projects", "snapshots")
    ]
    runtime_paths.append(home / ".local/share/cursor-agent/logs")
    profile = write_confinement_profile(
        Path(workspace), scratch, runtime_paths, read_only=read_only
    )
    return ("/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/env", f"TMPDIR={scratch}", *argv)


def write_confinement_profile(
    workspace: Path, scratch: Path, runtime_paths: Sequence[Path], *, read_only: bool
) -> str:
    workspace = workspace.resolve()
    # A pre-existing symlink must not redirect an explicit runtime exception to
    # an unrelated directory before the kernel receives the profile.
    if any(p.absolute() != p.resolve() for p in runtime_paths):
        raise ValueError("Cursor runtime state contains a redirected path")
    runtime = [p.resolve() for p in runtime_paths]
    if any(workspace.is_relative_to(p) or p.is_relative_to(workspace) for p in runtime):
        raise ValueError("Workspace overlaps writable Cursor runtime state")
    allowed = [scratch.resolve(), *runtime]
    if not read_only:
        allowed.append(workspace)
    lines = [
        "(version 1)",
        "(allow default)",
        # The trusted supervisor shares the host UID. Prevent a sandboxed
        # process from stopping it (or another host process) using kill(2).
        "(deny signal)",
        "(allow signal (target self) (target children))",
        "(deny file-write*)",
        "(deny network-outbound (remote unix-socket))",
        '(allow network-outbound (literal "/private/var/run/mDNSResponder") '
        '(literal "/var/run/mDNSResponder"))',
        '(deny network-outbound (remote ip "localhost:*"))',
        '(allow file-write* (literal "/dev/null"))',
    ]
    for path in allowed:
        value = str(path)
        if any(ord(c) < 32 for c in value):
            raise ValueError("Invalid sandbox path")
        lines.append(f"(allow file-write* (subpath {json.dumps(value)}))")
    # Keep JoyMux-owned credential state out of the agent process boundary.
    for path in (
        Path.home() / ".joymux/secrets",
        Path.home() / ".joymux/security",
        Path.home() / ".ssh",
    ):
        lines.append(f"(deny file-read* file-write* (subpath {json.dumps(str(path.resolve()))}))")
    return "\n".join(lines)
