# Authenticated JoyCtl intake bridge prototype

This example preserves the authority boundary: sam captures intent; JoyCtl owns
planning, authorization and operational task state; JoyMux owns contextual
placement/runtime; JoyMesh dispatches explicit approved work and returns facts;
JoyLang judges semantic satisfaction. Hosted dot names are not identities.

## Supported example surfaces

`examples/sam_dot_bridge.py` narrows in-process MCP tool arguments and forwards
existing JoyCtl draft mission, authorization/prompt-bound execution and read
routes. `examples/joyctl_http_binding.py` provides the concrete HTTP seam using
an externally verified session supplied by the existing authentication layer.
It cannot login, issue credentials or grants, enroll dots, or create authority.

The HTTP binding requires a fixed HTTPS origin, refuses redirects and arbitrary
routes/body fields, bounds timeout/response size, and does not retry POSTs.
Loopback HTTP requires an explicit test-only opt-in. JoyCtl still verifies the
bearer and applies RBAC/tenant rules on every request. Session context is trusted
transport state; it never comes from model-controlled tool arguments.

The report projection is explicitly noncanonical and non-atomic. A second graph
read rejects a changed snapshot. The SQLite receipt cache tolerates duplicate and
out-of-order versions and rejects divergent same-version content; it is not an
authorization or task-state store. Local receipt acknowledgement is not a
canonical report acknowledgement or mission completion.

## Unsupported and blocked

This is an integration example, not a deployed MCP server. Live event discovery,
authorized dot subscriptions, callback verification, dot assignment and canonical
result/report application services are not established. Events are not advertised.
Unsupported canonical report fetch/acknowledgement and authoritative result
bindings fail closed. No hosted dot lifecycle controls are invented.

The native JoyMux execution/placement protocol must not be substituted for the
separate `joymux.computer.v1` API unless that implementation actually supports
the required operations. Development adapters do not prove managed/protected
native enforcement. Context/receipt acknowledgements do not prove comprehension,
semantic satisfaction or successful execution.

## Validation and remaining gates

Focused tests: **35 passed**, against JoyMesh base
`90e5ce39799fb78c0db157722d5913e14b67c9cb` and the isolated JoyCtl signing candidate
based on `89e55e5bd9a2b0adb960d0e04ac10265701546c4`. Ruff passed for the five example
and test files. Existing installed test dependencies were used; the declared
PyJWT test dependency was pinned at 2.10.1 in a disposable directory.

`test_joyctl_http_local_contract.py` uses actual local HTTP and SQLite with
JoyCtl's hosted handler: draft create/read succeeds and viewer create is denied.
It uses ephemeral development authentication only. Other bridge checks exercise
simulated routing, current-assignment checks and report delivery contracts. None
proves live hosted-dot execution or production OIDC application wiring.

JoyCtl focused security/schema/packaging checks: **57 passed**. Full-source
comparison: baseline **995 passed / 296 failed / 43 skipped**, repaired candidate
**999 passed / 295 failed / 43 skipped**, with no newly failing test identifiers.
The integrity failure was resolved. The remaining failures include missing
computer SDK, platform errors and existing assertion failures; full release
qualification remains blocked. This producer evidence is not JoyLegal admission.

Example check (supply installed dependency paths as appropriate):

```sh
PYTHONPATH=src:examples:/path/to/joyctl/src python -m pytest \
  tests/test_sam_dot_bridge.py tests/test_joyctl_http_binding.py \
  tests/test_joyctl_http_local_contract.py -p asyncio
```

Before live use: establish the intended JoyMux implementation and supported
contract, supply the existing verified authentication binding, verify authorized
dot identity/subscription behavior, and implement any missing securely scoped
canonical report/return service through JoyCtl. Obtain scoped approval for any
persistent access or live cutover. This draft does not change deployment,
security configuration or existing task state.
