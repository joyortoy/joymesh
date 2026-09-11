# Routing policy

Deterministic routing filters before scoring:

- installation must be present and node online;
- required capabilities must be supported;
- certification must match the binary fingerprint and adapter version;
- authentication and funding state must permit the route;
- subscription must be healthy and below concurrency;
- configured quota reserve must remain untouched;
- workspace and harness permissions must allow the task.

Eligible candidates are scored with stable input ordering, cost weights, reserve
pressure, health, and an uncertainty penalty for unknown quota. The preview
records a reason for every acceptance or rejection. Identical inputs and state
produce identical output.

Billing modes are explicit route properties. Fallback proposals preserve the
generic task context but create a new run and native session identity.
