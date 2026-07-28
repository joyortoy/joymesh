#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

git diff --check

if command -v python3 >/dev/null 2>&1 &&
  python3 -c 'import tomllib' >/dev/null 2>&1; then
  python3 - <<'PY'
from pathlib import Path
import tomllib

for path in (
    Path(".codex/config.toml"),
    Path(".codex/environments/environment.toml"),
):
    with path.open("rb") as handle:
        tomllib.load(handle)
    print(f"valid TOML: {path}")
PY
else
  echo "warning: Python 3.11+ unavailable; skipped TOML parsing" >&2
fi

echo "Repository checks passed."
