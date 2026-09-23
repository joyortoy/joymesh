# JoyCTL device transport

The outbound JoyMesh device agent carries a device credential and an access
token. Plain HTTP is accepted only for explicit IP loopback
(`127.0.0.1` or `::1`). A remote hosted JoyCTL endpoint must use HTTPS;
the WebSocket upgrade then uses verified TLS. The client checks a fresh
WebSocket challenge response before it accepts the connection and rejects
redirects, oversized responses, and oversized or malformed frames.

The bootstrap and device-state JSON files contain credentials. Both must be
owned by the device user and inaccessible to group and other users. Device
state is replaced atomically in an owner-only directory.

On the `joy` host, JoyMesh and the hosted JoyCTL server run on the same
machine, so the device bootstrap should use
`http://127.0.0.1:8766`. This change does not make the hosted server's
`0.0.0.0:8766` listener private; that listener requires its own access
policy and TLS if remote clients use it.

The live `joy` host uses the root-owned `joyctl-hosted-guard.service` to
restrict TCP port 8766 to loopback and three named Tailscale Mac peers. Keep
the allowed peer addresses current when devices are added or removed. This
host firewall is an exposure control; it does not turn the local-development
JoyCTL mode into a production deployment. The hosted service still needs a
non-default signing key, managed identity, TLS at the application boundary,
and the production database and configuration gates before it can be called
production ready.

Transport hardening does not attest the task content or isolate a harness
from all files available to the `sam` account. Those are separate execution
authority and OS containment boundaries.
