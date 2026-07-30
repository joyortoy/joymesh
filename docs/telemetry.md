# Anonymous execution metrics

JoyMesh can optionally send **anonymous execution metrics** to JoyCLI to help
improve routing, performance, and future model evaluation.

Metrics are **opt-in**. Nothing is sent until you explicitly choose a mode.
There is no default-enabled state and no pre-selected consent option.

## Consent

Shown once on first `joymesh init`, `joymesh run`, or `joymesh node init`
(unless `consent_completed: true` is already stored):

```text
( ) 1) Always send anonymous execution metrics
( ) 2) Ask every time
( ) 3) Never send
```

Pressing Enter alone does not complete consent — you must choose `1`, `2`, or `3`.

Preference is stored in `~/.config/joymesh/config.yaml` (override with
`JOYMESH_CONFIG_DIR`):

```yaml
metrics:
  mode: always
  consent_completed: true
```

Legacy `telemetry:` keys are still read for compatibility; new writes use
`metrics:`.

## CLI

```bash
joymesh metrics status
joymesh metrics on
joymesh metrics ask
joymesh metrics off
joymesh metrics preview
joymesh init
```

`joymesh telemetry …` remains as an alias. Preference commands never submit
metrics.

## What is sent (allowlist)

* `task_type`
* `duration_ms`
* `usage.input_tokens` / `usage.output_tokens` / `usage.total_tokens`
* `quality` (`good` | `bad` | `unknown`)

## What is never sent

Prompts, AI responses, source code, files, repository names, credentials,
personal information, paths, usernames, hostnames, IP addresses, API keys,
OAuth tokens, cookies, environment variables, stack traces, or user identifiers.

## Runtime behaviour

| Mode | Behaviour |
| --- | --- |
| `always` | After execution, best-effort send |
| `ask` | Confirm once per run; preference unchanged |
| `never` | No metrics generation for transmission; no network call |

Transport failures never fail execution, never block the CLI, and never alter
results.
