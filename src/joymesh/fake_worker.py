"""Subprocess entrypoint for the fake harness adapter."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def emit(event_type: str, message: str) -> None:
    print(json.dumps({"type": event_type, "message": message}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--delay", type=float, default=0.01)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    emit("output", f"Accepted task: {args.task}")
    for progress in (25, 50, 75):
        time.sleep(args.delay)
        emit("progress", f"{progress}%")
    emit("output", f"Completed fake run in {workspace}")


if __name__ == "__main__":
    main()
