# Resumable onboarding flow

The state machine is:

`NOT_STARTED → ACCOUNT_READY → NODE_PAIRING_REQUIRED → ENVIRONMENT_CHECK →
HARNESS_SELECTION → INSTALLATION_REVIEW → INSTALLING →
AUTHENTICATION_REQUIRED → VERIFYING_ACCOUNTS → CERTIFICATION_REQUIRED →
CERTIFYING → ROUTING_SETUP → FIRECONNECT_SETUP → FINAL_CHECK`.

Terminal states are `COMPLETE`, `LIMITED_MODE`, and `BLOCKED`; `NODE_OFFLINE` is
resumable. Every transition is durable and attributed to the current user and
workspace. Back, Continue, Retry, Skip optional, Save and exit, and Resume do not
discard prior evidence.

`COMPLETE` requires at least one detected, authenticated, funding-compatible,
certified harness on an online node. Otherwise the final transition is explicit
`LIMITED_MODE`, and remote execution remains disabled.
