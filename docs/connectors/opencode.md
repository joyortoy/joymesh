# OpenCode

- Platforms: macOS, Linux, Windows; executable: `opencode`; official npm and Homebrew methods.
- Authentication: `opencode auth login` / `opencode auth list`.
- Execution: `opencode run --format json --dir <workspace> <prompt>` (JSONL events).
- Read-only: `OPENCODE_PERMISSION` deny rules for `edit` / `bash` / network tools.
- Runtime connector: `joymesh.runtime_v1.connectors.opencode.OpenCodeConnectorRuntime`.
- Live test: `joymesh connector live-test opencode` (optional `JOYMESH_LIVE_OPENCODE=1`).
- JoyMesh maturity: Runtime v1 read-only connector (#3); catalogue still adapter-conformant.
- Official source reviewed 2026-07-29: <https://opencode.ai/docs/cli/>.
