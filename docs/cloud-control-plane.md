# Cloud control plane

The control plane stores identity references, node public keys, hashed browser
and node credentials, onboarding progress, action plans, approvals, workspace
grants, remote task envelopes, routing policy, and audit events. It never stores
harness secrets or node private keys.

`joymesh.control_plane.ControlPlane` is an injectable SDK service. Its reference
in-memory store makes security behavior deterministic in unit tests; production
deployments must back the same contract with the SQLAlchemy schema and Alembic
migrations.

Every remote task is scoped to an organisation, workspace, user, browser
session, node, and harness. Cross-organisation node use and missing workspace
grants fail closed. Node revocation immediately prevents new task creation.

The reference API expects verified identity headers supplied by OIDC/session
middleware. Direct internet exposure without that middleware is unsupported.
