# Connector protocol and registry

JoyMesh Runtime v1 treats connectors as interchangeable adapters behind one protocol.
The runtime owns scheduling, policy, leases, retries, certification workspaces, Git
verification, manifests, cleanup, evidence persistence, audit, and metrics.

Connectors own only launcher-specific behaviour: discovery, authentication
classification, argv construction, event parsing, cancellation, capability
declaration, and optional execution environment variables.

## Package layout

```text
runtime_v1/
  connector_protocol.py
  connectors/
    __init__.py      # builtin_connectors() — sole built-in registry
    cursor.py
    codex.py
    opencode.py
    claude.py
    grok.py
    live_test.py     # connector-neutral local live-test runner
  cursor.py          # thin compatibility re-export only
```

## Protocol

Every built-in connector must implement `ConnectorRuntime`, including:

| Operation | Owner |
|---|---|
| `connector_id`, `display_name` | Connector |
| `discover` → `DiscoveryResult` | Connector |
| `classify_auth_status` | Connector |
| `declared_capabilities` | Connector |
| `execution_environment(*, read_only)` | Connector |
| `build_exec_argv` | Connector |
| `build_read_only_cert_argv(*, executable, prompt, workspace: Path)` | Connector |
| `verify_adapter` | Connector |
| `adapter_verification_notice` | Connector |
| `parse_events` | Connector |
| workspace / Git / manifests / cleanup / evidence | Runtime |

`DiscoveryResult` is connector-neutral:

```text
executable_path, version, fingerprint
installed, usable, reason_code, details
```

Broken launchers are rejected inside the connector's `discover` / `verify_adapter`.
Generic code consumes `usable` and `reason_code` only.

No protocol methods were added for Claude Code. Auth mode, provider-override
metadata, and permission-enforcement strength live in connector-local details /
notices so Cursor / Codex / OpenCode remain unchanged.

## Connector-neutral live testing

```bash
joymesh connector live-test cursor
joymesh connector live-test codex
joymesh connector live-test opencode
joymesh connector live-test claude
joymesh connector live-test claude --json
```

The CLI resolves the connector via `builtin_connectors()`, then runs
`run_connector_live_test()` which only calls protocol methods and shared process
helpers. It never branches on connector IDs.

Result type: `ConnectorLiveTestResult` (rendered by the CLI; `--json` dumps
`as_dict()`).

Deprecated: `joymesh.connectors.live_test.run_cursor_live_test` is a thin wrapper
around the shared runner.

## Reason codes

Prefer connector-neutral codes such as:

```text
executable_not_found
broken_executable
unsupported_version
connector_auth_required
connector_configuration_invalid
connector_plan_restriction
connector_rate_limited
connector_quota_exhausted
connector_read_only_unsupported
connector_output_unparseable
connector_timeout
connector_execution_failed
permission_denied_edit
permission_denied_shell
permission_denied_network
permission_denied_external_path
permission_denied_subprocess
connector_provider_override_active
```

## Read-only certification contract

* Runtime owns workspace generation, Git verification, manifests, cleanup, evidence
* Connector supplies argv + optional `execution_environment(read_only=True)`
* OpenCode enforces deny rules for `edit`/`bash`/network via `OPENCODE_PERMISSION`
* Cursor uses `--trust` + stream-json; Codex uses `--sandbox read-only`
* Claude Code uses `--permission-mode plan` + `--tools` allow-list +
  `--disallowedTools` (native permission mode + tool filtering)

## OpenCode support status

Supported for Runtime v1 read-only routing:

* discovery / version / fingerprint
* `opencode auth list` classification
* `opencode run --format json --dir <workspace> <prompt>`
* JSONL event normalisation (`step_start` → `run.started`, etc.)
* permission-denied write tools via environment

Limitations:

* No native sandbox flag equivalent to Codex `--sandbox read-only`; rely on
  permission deny rules
* Provider cost may still apply
* Live gate: `JOYMESH_LIVE_OPENCODE=1`

## Claude Code support status

Tested CLI: `@anthropic-ai/claude-code` **2.1.220** (`claude --version`).

| Area | Behaviour |
|---|---|
| Discovery | `shutil.which("claude")` + `--version` health probe + fingerprint |
| Auth | `claude auth status --json` (`loggedIn` / `authMethod` / `apiProvider`) |
| Auth modes | subscription (oauth), API key, provider override (non-firstParty), unauthenticated |
| Output | `--print --output-format stream-json --verbose` |
| Workspace | process cwd (live-test / execute set cwd); no silent provider override |
| Read-only | `--permission-mode plan --tools Read,Glob,Grep --disallowedTools Edit,Write,...` |
| Events | `system/init` → `run.started`; assistant text/tools; `result` → completed/failed; `permission_denials` → `permission.denied` |
| Cost safety | subscription-backed probes allowed when logged in; API-key routes flagged billable; unknown auth blocks live inference |
| Live gate | `JOYMESH_LIVE_CLAUDE=1` |

Catalogue harness id remains `claude-code`; runtime `connector_id` is `claude`.

Denial semantics (same model as OpenCode):

```text
provider_process_success
requested_action_denied
repository_unchanged
security_certification_passed
```

Exit 1 after a blocked edit can still pass security certification when the
repository is unchanged and `permission_denied_*` events are emitted.

Known limitations:

* Prompt-only restrictions are not used for certification
* Headless runs without allowed tools / dontAsk/plan can stall on interactive
  permission prompts — certification always supplies tool filters
* External-path denial may appear as text when `Read` is allowed but scoped to cwd
* Provider override is detected from env key presence only (values never logged)
* Live certification requires `claude auth login` (or equivalent) first

## Grok Build support status

Tested CLI: `@xai-official/grok` **0.2.114** (`grok --version`).

Official install (preferred):

```bash
npm install -g @xai-official/grok
# or: curl -fsSL https://grok.com/build/install.sh | bash
```

| Area | Behaviour |
|---|---|
| Discovery | `shutil.which("grok")` + `--version` health probe + fingerprint |
| Auth | `grok models` probe (authenticated vs not); `auth.json` presence; `XAI_API_KEY` |
| Auth modes | subscription (device login), API key, free access (env), provider override (`GROK_CLI_CHAT_PROXY_BASE_URL`), unauthenticated |
| Output | `--no-auto-update -p <prompt> --output-format streaming-json --cwd <workspace>` |
| Read-only | `--sandbox strict --permission-mode plan --tools read_file,grep,list_dir` + disallowed write/shell/web/Agent + `--deny` Bash/Edit/Write/MCPTool + `--disable-web-search --no-subagents` |
| Events | NDJSON `text`/`thought`/`end`/`error` → JoyMesh run/assistant/reasoning/completed/failed; denials from text + `permission_denied` fields |
| Privacy | cert env sets `GROK_TELEMETRY_ENABLED=0` / `GROK_TELEMETRY_TRACE_UPLOAD=0`; local codebase indexing noted; remote upload reported as `remote_repository_upload_unknown` (no conclusive disable switch) |
| ACP | capability `agent_client_protocol` detected (`grok agent stdio`); certification stays on CLI subprocess |
| Background | capability `background_execution` declared; cert denies Agent/subagents and never enables goal/schedule modes |
| Cost safety | device-login / free-access probes allowed when authenticated; API-key routes flagged billable; unknown auth blocks live inference |
| Live gate | `JOYMESH_LIVE_GROK=1` |

Denial semantics (same model as OpenCode / Claude):

```text
provider_process_success
requested_action_denied
repository_unchanged
security_certification_passed
```

Known limitations:

* macOS network sandbox child-block is a documented no-op; cert relies on tool denylist + `--disable-web-search`
* `streaming-json` emits limited tool lifecycle events; denials often appear as assistant text
* Never pass `--always-approve` / `--yolo` for certification
* Live certification requires `grok login` (or approved free/subscription route) first

## Registry ownership

`builtin_connectors()` is the only built-in registration point. At first load it
validates required methods, capabilities, cert argv signature, and
`execution_environment` return type.

## Prohibited in generic runtime / node_runner / live-test / CLI rendering

Do **not** add:

```python
if connector_id == "cursor":
if connector_id == "codex":
if connector_id == "opencode":
if connector_id == "claude":
if connector_id == "grok":
```

## How to add connector #6

1. Implement `runtime_v1/connectors/<id>.py` conforming to `ConnectorRuntime`
2. Declare capabilities from the shared capability graph
3. Implement discovery with `installed` / `usable` / `reason_code`
4. Implement auth classification and verification
5. Implement `build_exec_argv`, `build_read_only_cert_argv`, `execution_environment`
6. Implement `parse_events` and optional notices
7. Register in `builtin_connectors()` only
8. Add conformance tests + optional `JOYMESH_LIVE_<ID>=1` gate

No changes should be required to scheduler, policy, leases, retry, audit,
metrics structure, node_runner, or the shared live-test runner.

## Provider routes (FireConnect)

FireConnect is **not** a connector. It is registered only through
`builtin_provider_route_managers()` as a provider configuration manager.

See `docs/adr/0002-provider-route-managers.md` and
`docs/runtime/provider-routes.md`.
