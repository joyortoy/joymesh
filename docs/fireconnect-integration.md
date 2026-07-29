# FireConnect and Fireworks integration

FireConnect is a provider-routing transform, not a harness adapter.

Read-only status reports whether the CLI is installed, its reported version,
sign-in state, enabled targets, and configured model routes. It never returns
tokens or credential-store contents.

Enabling or disabling a target is plan-first:

```python
plan = fireconnect.plan_connect(
    "codex",
    "accounts/fireworks/models/kimi-k3",
)
```

Executing that plan requires a matching
`LifecycleAction.ROUTE_TRANSFORM` approval token. This prevents routing from
silently changing a harness from an included subscription to paid API
inference.

Fireworks deployment routers are represented by their model resource name,
such as `accounts/<account>/routers/<router>`, in the provider/model portion of
a route. Their weighted deployment selection is external to JoyMesh. JoyMesh
continues to own harness availability, capability, funding, quota, concurrency,
approval, fallback, and event normalization.
