"""Async drain/retry worker for the durable delivery outbox."""

from __future__ import annotations

import asyncio
from typing import Any

from joymesh.delivery.contracts import DeliveryAckStatus
from joymesh.delivery.outbox import DeliveryOutbox
from joymesh.delivery.transports.protocol import DeliveryTransport


class DeliveryWorker:
    def __init__(
        self,
        outbox: DeliveryOutbox,
        transport: DeliveryTransport,
        *,
        poll_interval: float = 0.25,
        batch_size: int = 32,
        max_attempts: int = 8,
    ) -> None:
        self.outbox = outbox
        self.transport = transport
        self.poll_interval = max(0.05, float(poll_interval))
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.stats: dict[str, Any] = {
            "published": 0,
            "acked": 0,
            "failed": 0,
            "replays": 0,
            "unavailable": 0,
        }

    async def start(self) -> None:
        # Socket may be absent at boot — do not block JoyMesh startup.
        try:
            await self.transport.connect()
        except Exception:
            self.stats["unavailable"] += 1
        self._stop.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="joymesh-delivery-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task),
                    timeout=max(1.0, self.poll_interval * 4),
                )
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.transport.close()

    async def flush_once(self) -> int:
        """Drain pending items once (used by tests and restart recovery).

        Acknowledgement is required before outbox deletion. Connection failure
        leaves items pending — never switches transport.
        """
        try:
            await self.transport.connect()
        except Exception:
            self.stats["unavailable"] += 1
            return 0
        records = self.outbox.pending(limit=self.batch_size)
        drained = 0
        for record in records:
            if record.attempts >= self.max_attempts:
                self.outbox.mark_failed(record.envelope.envelope_id)
                self.stats["failed"] += 1
                continue
            try:
                if record.attempts > 0:
                    self.stats["replays"] += 1
                self.outbox.mark_sent(record.envelope.envelope_id)
                self.stats["published"] += 1
                ack = await self.transport.publish(record.envelope)
                if ack.status is DeliveryAckStatus.ACKED:
                    self.outbox.mark_acked(record.envelope.envelope_id)
                    self.stats["acked"] += 1
                    drained += 1
                else:
                    # Rejected / dropped — keep durable for operator inspection.
                    self.outbox.mark_failed(record.envelope.envelope_id)
                    self.stats["failed"] += 1
            except Exception:
                try:
                    await self.transport.close()
                except Exception:
                    pass
                self.outbox.mark_failed(record.envelope.envelope_id)
                self.stats["failed"] += 1
        return drained

    async def heartbeat(self) -> None:
        try:
            await self.transport.heartbeat()
        except Exception:
            self.stats["unavailable"] += 1
            try:
                await self.transport.close()
            except Exception:
                pass

    def health(self) -> dict[str, Any]:
        transport_health: dict[str, Any] = {"transport": getattr(self.transport, "name", "unknown")}
        health_fn = getattr(self.transport, "health", None)
        if callable(health_fn):
            transport_health.update(health_fn())
        return {
            **transport_health,
            "outbox_size": self.outbox.size(),
            "stats": dict(self.stats),
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.flush_once()
                await self.heartbeat()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue
