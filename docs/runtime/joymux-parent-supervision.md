# Cursor parent supervision through JoyMux

The Cursor host sandbox blocks writes outside its explicit roots, protected
credential reads, local broker connections, and signals to unrelated host
processes. Signals to self and children remain available. This macOS profile is
used by both Cursor launch adapters. It is not a general read or egress firewall.

For the JoyMesh SDK harness path, opt into host-side lifecycle supervision:

```python
from pathlib import Path
from joymesh.service import JoyMesh
from joymesh.joymux_supervision import JoyMuxSupervisedRuntime

runtime = JoyMuxSupervisedRuntime(Path.home() / ".joymux/runtime.sock")
mesh = JoyMesh(runtime=runtime)
# Use mesh.run(..., harness="cursor") and mesh.wait(run.id), then mesh.close().
```

The socket path is trusted host configuration. No daemon is started or restarted.
The parent registers and obtains an acknowledged session before launching the
child. It reports lifecycle facts with source `joymesh-parent`, sends periodic
heartbeats, and closes its observation session on normal completion. It sends
no prompt, raw output, invented token counts, or credentials. A bounded local
observation deque correlates the JoyMesh run, JoyMux session and parent-observed PID.

Missing, mismatched, disconnected or timed-out acknowledgements fail the run.
The parent cancels the owned harness task and invokes its process-group cleanup.
There is no silent reconnect or fallback to an unsupervised run. Defaults are a
one-second heartbeat interval and one-second RPC timeout; actual stop time also
includes scheduling and process cleanup. These are not real-time guarantees.

This reports **lifecycle and channel health**, not kernel sandbox denials. Child
output never acts as security authority. JoyMux acknowledges observations;
JoyMesh performs cancellation on channel loss. The child still cannot access the
JoyMux Unix execution broker. A returned cleanup call alone is not proof that
every possible descendant is gone, particularly a process that changes session
or process group. Independent liveness checks are needed for such claims.

This runtime is opt-in, and does not automatically change the CLI, the separate
runtime_v1 connector path, or an already-running JoyMesh service. Test the target
JoyMux deployment before enabling it: unreliable RPC will intentionally prevent
or stop work. Broken connections may leave historical observation sessions;
their facts heartbeat expires rather than proving ongoing execution.

Validation uses a synthetic Unix peer plus real subprocesses for missing peers,
disconnect, silence, wrong response IDs, disconnected acknowledgements and caller
cancellation. macOS kernel tests cover denied host signals and permitted self/child
signals. The local hardening run also exercised an isolated real JoyMux Unix daemon
and a real Cursor tool call. No MCP is used.
