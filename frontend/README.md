# JoyMesh browser workspace

This Sites application is the authenticated onboarding and remote-access surface
for JoyMesh. It replaces the earlier reference dashboard.

- Authentication is dispatch-owned Sign in with ChatGPT.
- Onboarding progress is durable in D1 through the `DB` binding.
- No token or authoritative progress is stored in browser storage.
- The UI remains in limited mode until a real paired node reports a ready,
  certified harness through the Python control plane.

Run locally:

```bash
npm ci
npm run dev
```

Validate:

```bash
npm run lint
npm test
```

The local auth headers used by tests represent trusted deployment middleware;
never accept equivalent headers directly from an untrusted public client.
