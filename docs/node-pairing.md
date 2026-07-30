# Node pairing

Desktop pairing uses OAuth-style PKCE. The browser creates a short-lived pairing
session and the node presents the verifier only when redeeming the approved
session. Headless machines use the Device Authorization Grant shape: device
code, human user code, verification URI, polling interval, and expiry.

The device code is stored as a hash. Approval binds the organisation and
workspace. Registration uploads only the node public key and metadata; the
private key never leaves the machine.

Pairing codes expire after ten minutes and are single use. Registration returns
a separately rotatable node credential. Rotation creates an overlapping public
key window; revocation closes current gateway connections and rejects future
tasks. Unregistering a node does not delete audit history.
