# Harness catalogue

The built-in catalogue is declared in
`src/joymesh/harnesses/catalogue.py`. Claims are intentionally conservative:
experimental means the official interface exists but the JoyMesh adapter has
not passed version-specific real-binary certification.

The detailed status table is generated as
[harness-capability-matrix.md](harness-capability-matrix.md).

| ID | Native interface | JoyMesh maturity | Important boundary |
| --- | --- | --- | --- |
| `codex` | JSONL `codex exec --json` | beta | installed binary uncertified |
| `opencode` | JSONL `opencode run --format json` | beta | installed binary uncertified |
| `claude-code` | streaming JSON print mode | beta | adapter and binary uncertified |
| `gemini-cli` | streaming JSON headless mode | beta | adapter and binary uncertified |
| `github-copilot` | JSONL programmatic mode | experimental | tool approvals remain native |
| `aider` | single-message text mode | experimental | no structured usage/session contract |
| `goose` | non-interactive text mode | experimental | structured output contract not documented |
| `pi` | JSON event/JSON-RPC modes | experimental | adapter uncertified |
| `continue` | headless text mode | experimental | structured usage contract not documented |
| `amazon-q` | discovery only | discovery only | general coding-chat headless contract not documented |
| `qwen-code` | streaming JSON headless mode | experimental | adapter uncertified |
| `cline` | headless JSON mode | experimental | session and usage contracts unknown |
| `roo-code` | pre-release CLI | experimental discovery | no runnable adapter until the pre-release protocol is certified |

Primary references are stored on every `HarnessDefinition`. Key sources include
the official [Codex CLI reference](https://developers.openai.com/codex/cli/reference/),
[Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-reference),
[Gemini headless reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md),
[Copilot programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference),
[Aider scripting guide](https://aider.chat/docs/scripting.html),
[Continue CLI guide](https://docs.continue.dev/cli/quickstart),
[Qwen headless guide](https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/),
and [Cline CLI guide](https://docs.cline.bot/usage/cli-overview).
