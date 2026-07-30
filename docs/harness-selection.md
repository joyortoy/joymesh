# Harness selection

JoyMesh never selects a harness by default and never falls back to a fake
harness. Users explicitly enable harnesses and may set an optional default.

## Removed fake harness

The bundled `fake` / historical `joy` harness is **not** part of production.
Legacy configuration that selected it is cleared and marks
`selection_required` until you choose a real harness.

## Preferences

Stored in `~/.config/joymesh/config.yaml` under `harnesses:` (separate from
anonymous metrics):

```yaml
harnesses:
  enabled:
    - codex
    - opencode
  default: codex   # omit or null => ask each run
  custom: {}
```

## CLI

```bash
joymesh harness list
joymesh harness status
joymesh harness select
joymesh harness enable <id>
joymesh harness disable <id>
joymesh harness default <id>
joymesh harness default --clear
joymesh harness inspect <id>
joymesh harness add-custom --id ... --name ... --executable ... --arg ...
joymesh harness validate <id>
joymesh harness test <id>
joymesh harness remove-custom <id>
joymesh run --workspace . --task "..." --harness codex
```

## Runtime resolution

```text
--harness override
  → preferred/mission harness
  → configured default
  → single enabled ready harness
  → harness_selection_required / no_ready_harness
```

Never: registry-order fallback, silent first-detected, or `fake`/`joy`.

## Custom harnesses

Custom harnesses use argv arrays (never `shell=True`), validated IDs,
executable checks, timeouts, and environment allowlists. Saving a custom
harness does **not** enable it until you `enable` / `select`.

Security restrictions:

* no shell interpolation or `command:` strings
* structured `args` arrays only
* environment allowlist (no unrestricted inheritance)
* no credential-like env keys
* readiness requires executable + permissions + config validation

### Readiness versus compatibility

* **Ready** means the harness can execute (executable found, permissions valid,
  config validated). Readiness alone never authorizes a task.
* **Compatible** means the harness’s declared capabilities cover the task’s
  `required_capabilities` (`joymesh.models.Capability`).

These checks are independent: a ready custom harness is still rejected when
capabilities do not match.

### Custom capability names

Declare only known `Capability` values (examples):

```text
streaming
shell
file.read
file.write
tool.use
session.resume
runtime.cancellation
usage.reporting
```

Unknown names are rejected at validation time and are never treated as supported.
The default custom capability set is **empty** (conservative): only tasks with
no special requirements are compatible until capabilities are declared.

```yaml
harnesses:
  custom:
    my-custom-harness:
      display_name: My Custom Harness
      executable: my-harness
      args: [run, --json]
      capabilities:
        - streaming
        - shell
```

### Capability mismatch

When the selected harness cannot satisfy required capabilities, JoyMesh returns
`harness_capability_mismatch` with:

```yaml
harness_id: my-custom-harness
required_capabilities: [tool.use]
supported_capabilities: [streaming]
missing_capabilities: [tool.use]
```

JoyMesh does not silently emulate missing capabilities.

### Explicit override behavior

`--harness <id>` locks routing to that harness. If it is ready but incompatible,
JoyMesh fails with `harness_capability_mismatch` and does **not** fall back to
another harness. Without an explicit override, existing router policy may still
select another eligible compatible harness (preferred only boosts score).

## Errors

| Code | When |
| --- | --- |
| `no_ready_harness` | no enabled harness is ready |
| `harness_selection_required` | multiple ready harnesses and no default (non-interactive) |
| `harness_removed` | attempt to use `fake` / `joy` |
| `unknown_harness` | unknown id |
| `harness_disabled` | not in enabled set (unless explicit override policy) |
| `harness_not_ready` | selected but not ready |
| `harness_capability_mismatch` | selected harness lacks required capabilities |

## Onboarding

Onboarding harness selection uses the same catalogue/registry as CLI. Choosing
harnesses updates local `harnesses.enabled` preferences. An advanced “custom
harness” path uses `joymesh harness add-custom`.
