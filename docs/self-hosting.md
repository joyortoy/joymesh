# Self-hosting

Requirements are Python 3.12+, Node.js 22.13+, a SQL database, a TLS reverse
proxy with WebSocket support, and an OIDC provider.

Install with `uv sync --all-extras`, configure `JOYMESH_DATABASE_URL`, run
`.venv/bin/alembic upgrade head`, and start `joymesh serve`. Configure identity
middleware to emit the four verified JoyMesh identity claims only after session,
organisation membership, and CSRF validation.

Build the browser with `npm ci && npm run build` in `frontend/`. The Sites
runtime provisions D1 from the `DB` binding and bundled Drizzle migrations. For
a non-Sites host, replace dispatch-owned sign-in and D1 behind the same frontend
contracts; do not copy the trusted-header development seam to the public edge.

Nodes require a `wss://` gateway URL, short-lived pairing grant, and local secure
key store.
