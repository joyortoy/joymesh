from __future__ import annotations

import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def fake_executable_factory(tmp_path: Path) -> Callable[[str], Path]:
    def create(kind: str) -> Path:
        path = tmp_path / kind
        path.write_text(
            f"""#!{sys.executable}
import json
import os
import subprocess
import sys
import time

KIND = {kind!r}

if "--version" in sys.argv:
    print(f"{{KIND}} 1.2.3")
    raise SystemExit(0)

task = sys.argv[-1]
resume = None
if KIND == "codex" and "resume" in sys.argv:
    resume = sys.argv[sys.argv.index("resume") + 1]
if KIND == "opencode" and "--session" in sys.argv:
    resume = sys.argv[sys.argv.index("--session") + 1]
session_id = resume or f"{{KIND}}-session-{{os.getpid()}}"

def emit(value):
    print(json.dumps(value), flush=True)

if KIND == "codex":
    emit({{"type": "thread.started", "thread_id": session_id}})
else:
    emit({{"type": "session", "sessionID": session_id, "message": "session"}})

if "SPAWN_CHILD" in task:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    if KIND == "codex":
        emit({{"type": "item.completed", "item": {{"text": f"child_pid={{child.pid}}"}}}})
    else:
        emit({{"type": "text", "part": {{"text": f"child_pid={{child.pid}}"}}}})
    time.sleep(30)

if "CONCURRENT" in task:
    if KIND == "codex":
        emit({{"type": "item.completed", "item": {{"text": f"pid={{os.getpid()}}"}}}})
        emit({{"type": "turn.completed", "usage": {{"input_tokens": 3, "output_tokens": 2}}}})
    else:
        emit({{"type": "text", "part": {{"text": f"pid={{os.getpid()}}"}}}})
        emit({{"type": "step_finish", "part": {{"tokens": {{"input": 3, "output": 2}}}}}})
    time.sleep(30)

if "SLOW" in task:
    time.sleep(30)
if "RATE_LIMIT" in task and KIND == "codex":
    emit({{"type": "error", "message": "429 rate limit exceeded"}})
    raise SystemExit(29)
if "FAIL" in task:
    emit({{"type": "error", "message": "native failure"}})
    raise SystemExit(2)

message = "completed " + task + " secret=supersecretvalue"
if KIND == "codex":
    emit({{"type": "item.completed", "item": {{"text": message}}}})
    emit({{"type": "turn.completed", "usage": {{"input_tokens": 11, "output_tokens": 7}}}})
else:
    emit({{"type": "text", "part": {{"text": message}}}})
    emit({{
        "type": "step_finish",
        "part": {{"tokens": {{"input": 11, "output": 7}}, "cost": 0.02}}
    }})
""",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    return create
