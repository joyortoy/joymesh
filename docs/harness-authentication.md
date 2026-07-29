# Harness authentication

Installation is not authentication. JoyMesh launches only the harness vendor's
official login or device flow and reports one normalized state:
`authenticated`, `unauthenticated`, `expired`, `unknown`, or `misconfigured`.

Credentials remain in the harness configuration or the node's OS credential
store. Browser and cloud logs redact tokens, cookies, authorization headers,
environment secrets, and credential-shaped output. JoyMesh does not parse
credential files merely to infer login status.

Authentication failure for one harness does not block unrelated selected
harnesses. Logout, re-authentication, and account switching are separate,
approval-aware actions.
