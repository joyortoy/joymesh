"""Subprocess entrypoint for the fake harness adapter."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


def emit(event_type: str, message: str) -> None:
    print(json.dumps({"type": event_type, "message": message}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--delay", type=float, default=0.01)
    parser.add_argument("--session")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    session_id = args.session or f"fake-{uuid4()}"
    print(
        json.dumps({"type": "session", "message": "Session started", "session_id": session_id}),
        flush=True,
    )
    if "SPAWN_CHILD" in args.task:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        emit("output", f"child_pid={child.pid}")
        time.sleep(30)
    if "SLOW" in args.task:
        time.sleep(30)
    if "FAIL" in args.task:
        emit("output", "native failure")
        raise SystemExit(2)
    emit("output", f"Accepted task: {args.task}")
    for progress in (25, 50, 75):
        time.sleep(args.delay)
        emit("progress", f"{progress}%")
    emit("output", f"Completed fake run in {workspace}")
    print(
        json.dumps(
            {
                "type": "usage",
                "message": "Usage recorded",
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
