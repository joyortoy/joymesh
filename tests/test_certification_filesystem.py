"""Certification workspace and cancellation helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path

from joymesh.connectors.node_runner import (
    _executable_fingerprint,
    _terminate_process_tree,
    _workspace_manifest,
)


def test_workspace_manifest_permissions_and_hashes(tmp_path: Path) -> None:
    workspace = tmp_path / "cert"
    workspace.mkdir(mode=0o700)
    readme = workspace / "README.md"
    readme.write_text("# JoyMesh Cursor Certification ABC\n", encoding="utf-8")
    manifest = _workspace_manifest(workspace)
    assert "README.md" in manifest["files"]
    assert manifest["hashes"]["README.md"] == manifest["files"]["README.md"]
    assert manifest["symlink_escape"] is False
    assert int(str(manifest["mode"]), 8) == 0o700


def test_symlink_escape_detected(tmp_path: Path) -> None:
    workspace = tmp_path / "cert"
    workspace.mkdir(mode=0o700)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "escape"
    link.symlink_to(outside)
    manifest = _workspace_manifest(workspace)
    assert manifest["symlink_escape"] is True


def test_executable_fingerprint_hashes_file(tmp_path: Path) -> None:
    binary = tmp_path / "cursor-agent"
    binary.write_bytes(b"#!/bin/sh\necho hi\n")
    first = _executable_fingerprint(str(binary))
    second = _executable_fingerprint(str(binary))
    assert first == second
    assert len(first) == 64


async def test_terminate_process_tree() -> None:
    process = await asyncio.create_subprocess_exec(
        "sleep",
        "30",
        start_new_session=True,
    )
    result = await _terminate_process_tree(process)
    assert result["cancelled"] is True
    assert result["lingering"] is False
    assert process.returncode is not None
