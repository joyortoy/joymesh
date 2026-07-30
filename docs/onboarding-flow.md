# Resumable onboarding flow

The state machine is:

`NOT_STARTED → ACCOUNT_READY → NODE_PAIRING_REQUIRED → ENVIRONMENT_CHECK →
HARNESS_SELECTION → INSTALLATION_REVIEW → INSTALLING →
AUTHENTICATION_REQUIRED → VERIFYING_ACCOUNTS → CERTIFICATION_REQUIRED →
CERTIFYING → ROUTING_SETUP → FIRECONNECT_SETUP → FINAL_CHECK`.

Terminal states are `COMPLETE`, `LIMITED_MODE`, `FAILED`, and `BLOCKED`; `NODE_OFFLINE` is
resumable. Every transition is durable and attributed to the current user and
workspace. Back, Continue, Retry, Skip optional, Save and exit, and Resume do not
discard prior evidence.

`COMPLETE` requires at least one detected, authenticated, funding-compatible,
certified harness on an online node. Otherwise the final transition is explicit
`LIMITED_MODE`, and remote execution remains disabled.

## Authority split

```text
Browser onboarding
    → Sites BFF
    → Python control plane (canonical progress)
    → connector lifecycle (plan / approve / execute / readiness)

CLI/SDK compatibility
    → existing harness lifecycle
```

Browser onboarding must not write unused `harness_installations` /
`harness_account_states` tables. Connector readiness (`node_connector_*`) is the
install/auth/certify source of truth.

When `JOYMESH_CONTROL_PLANE_URL` is unset, Sites may keep a local D1 preview
store marked `authority=preview-unsynchronised`. That path is not production
authority.
