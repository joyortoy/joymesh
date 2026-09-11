# Observability

## Health vs readiness vs metrics

* Health: process/listener liveness counters
* Readiness: keys present, migrations complete, listener bound, reconciliation under threshold
* Metrics: machine-readable export without private payloads

## JoyCLI

```bash
joyctl runtime intake-status
joyctl runtime metrics
```

Prometheus text is included under the `prometheus` field of metrics output.

## JoyMesh

```bash
joymesh delivery health
```

## Structured log fields

`timestamp`, `level`, `component`, `event`, `correlation_id`, `organisation_id`, `publisher_id`, `execution_id`, `error_code` — never private keys, prompts, or raw evidence.
