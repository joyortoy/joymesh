# OAuth, sessions, and passkeys

Public deployments should implement OIDC/OAuth 2.0 Authorization Code with PKCE
against configured Google, GitHub, or Microsoft issuers. Magic links are
single-use, short-lived login assertions. Exact redirect URI matching, state,
nonce, issuer, audience, signature, and PKCE verification are mandatory.

Browser access uses a Secure, HttpOnly, SameSite cookie. The database stores only
a token hash. Rotation links a replacement session to its predecessor and
revokes the old credential. State-changing endpoints enforce same-origin fetch
metadata and CSRF protection. Access tokens are never written to local storage.

WebAuthn passkeys use RP ID/origin validation, challenge expiry, credential
ownership checks, and monotonic signature counters. High-risk changes require
recent step-up authentication. Recovery codes are random, one-time, hashed at
rest, and shown once.

The Sites frontend uses dispatch-owned ChatGPT sign-in rather than an app-owned
OAuth stack. The Python API expects verified identity middleware; arbitrary
identity headers are not safe on a public network.
