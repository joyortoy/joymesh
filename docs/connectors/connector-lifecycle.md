# Connector lifecycle

The lifecycle is discover, plan, approve, execute on the selected node, rediscover, authenticate,
verify, conform, certify, and explicitly enable routing.

Plans expire after 15 minutes and bind node, connector revision, action, method, exact argv,
package source, digest, and expiry into a SHA-256 hash. The API accepts a plan ID and matching
hash; it has no arbitrary executable or argument endpoint. Tasks are queued for the outbound
JoyMesh Node rather than executed by the browser.

Upgrade, executable drift, authentication changes, security-policy changes, or connector
revision changes invalidate certification.
