"""JoyUniverse ownership guards for JoyMesh.

JoyMesh acts. It must not own mission planning, long-term memory, payment,
legal verdicts, UI presentation, or JoyCTL strategic routing policy.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "joymesh"

_FORBIDDEN_IMPORT_ROOTS = {
    "joycli",
    "joypay",
    "joylegal",
    "joyui",
    "joyview",
    "joyclaw",
    "joymux",
}


def _iter_py(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_joymesh_does_not_import_peer_implementation_packages() -> None:
    offenders: list[str] = []
    for path in _iter_py(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in _FORBIDDEN_IMPORT_ROOTS:
                        offenders.append(f"{path}:{root}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"{path}:{root}")
    assert offenders == []


def test_completion_orchestrator_is_non_authoritative_for_missions() -> None:
    path = SRC / "runtime_v1" / "completion" / "orchestrator.py"
    text = path.read_text(encoding="utf-8")
    assert "DEPRECATED FOR JOYCLI MISSION AUTHORITY" in text
    assert "not authoritative for JoyCTL missions" in text.lower() or "Not authoritative" in text


def test_legal_module_does_not_claim_joylegal_verdicts() -> None:
    path = SRC / "legal" / "emit.py"
    text = path.read_text(encoding="utf-8")
    assert "without claiming JoyLegal verdicts" in text
    assert "awaiting_joylegal_decision" in text
