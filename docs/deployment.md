# Deployment

Deploy the Python control plane behind TLS and verified OIDC/session middleware.
Set `JOYMESH_DATABASE_URL` to a managed database, run `alembic upgrade head`,
configure a strong node gateway credential issuer, and terminate WebSockets at a
proxy that preserves authentication and idle connections.

Run multiple API replicas only with shared node presence, replay, nonce, and
task stores. Ephemeral in-memory control-plane state is for tests and local
evaluation, not multi-replica production.

The Sites frontend uses `.openai/hosting.json`, dispatch-owned sign-in, and the
`DB` D1 binding for resumable browser progress. Production control-plane calls
must use a same-origin authenticated gateway. Configure access policy separately
from sign-in.

Private keys and provider client secrets belong in managed secret storage.
Enable database encryption, encrypted backups, redacted logs, retention limits,
monitoring, and node/offline alerts.
