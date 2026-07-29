# Browser-first architecture

JoyMesh has four independently deployable layers:

1. The browser application owns identity-aware onboarding, approvals, remote task
   composition, and audit presentation. It cannot inspect the local machine.
2. The cloud control plane owns organisations, sessions, node registration,
   signed task envelopes, durable onboarding, routing policy, and audit history.
3. JoyMesh Node runs on the user's machine. It initiates one outbound TLS
   WebSocket, validates application signatures and workspace grants, and invokes
   the SDK service layer.
4. Harness adapters translate the shared `RunRequest` and normalized event
   contract to Codex, OpenCode, or the deterministic fake harness.

The Python `JoyMesh` service remains the orchestration seam. REST, CLI, the node
gateway, and the browser are adapters; they must not implement independent
routing or lifecycle rules.

The hosted Sites application uses dispatch-owned sign-in and D1 for browser
progress. The Python control plane is canonical for node and harness execution.
A deployment must connect the browser to it through an authenticated
same-origin gateway.
