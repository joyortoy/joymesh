# Node protocol v1

Every message contains `protocol_version`, `type`, `message_id`, `node_id`,
monotonic `sequence`, timestamp, optional `reply_to`, and typed payload.

Message types are `hello`, `welcome`, `heartbeat`, `presence`, `task.offer`,
`task.accepted`, `task.rejected`, `task.event`, `task.cancel`, `task.resume`,
`task.complete`, `approval.request`, `approval.decision`, `error`, and
`goodbye`.

On reconnect the node sends its last acknowledged sequence. The control plane
replays retained messages after that point. Message ids and task ids provide
deduplication; sequence gaps trigger resume instead of out-of-order execution.
Replay buffers are bounded and durable in production.

Heartbeats drive presence. Backoff is exponential with jitter and a cap.
Graceful shutdown sends `goodbye`, rejects new offers, drains or cancels active
work by policy, flushes events, and removes process trees.
