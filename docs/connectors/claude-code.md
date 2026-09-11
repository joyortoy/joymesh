# Claude Code

- Platforms: macOS, Linux, Windows/WSL or Git Bash; executable: `claude`.
- Install (user-scoped): `npm install -g @anthropic-ai/claude-code`.
- Tested version: **2.1.220**.
- Authentication: Claude subscription (`claude auth login`), Anthropic API key,
  Bedrock/Vertex provider routes. Status: `claude auth status --json`.
- Execution: `claude --print --output-format stream-json --verbose <prompt>`
  with cwd = workspace.
- Read-only certification: `--permission-mode plan` plus
  `--tools Read,Glob,Grep` and `--disallowedTools Edit,Write,MultiEdit,NotebookEdit,Bash,WebFetch,WebSearch,Agent`.
- Runtime connector id: `claude` (catalogue harness id remains `claude-code`).
- Live test: `joymesh connector live-test claude` / `JOYMESH_LIVE_CLAUDE=1`.
- JoyMesh maturity: adapter-conformant for Runtime v1 read-only routing; real
  live certification requires an authenticated Claude session.
- Official source reviewed 2026-07-29: <https://docs.anthropic.com/en/docs/claude-code/getting-started>.
