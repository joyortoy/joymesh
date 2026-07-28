"""Async SQLAlchemy persistence for runs, events, subscriptions, and usage."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from joymesh.models import (
    BillingRoute,
    EventType,
    NormalizedEvent,
    Run,
    RunStatus,
    SubscriptionCreate,
    SubscriptionProfile,
    utc_now,
)


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task: Mapped[str] = mapped_column(Text)
    workspace: Mapped[str] = mapped_column(Text)
    harness_id: Mapped[str] = mapped_column(String(100), index=True)
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    harness_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    billing_route: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(default=True)
    monthly_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    used_amount: Mapped[float] = mapped_column(Float, default=0)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    cost_weight: Mapped[float] = mapped_column(Float, default=1)


class UsageRow(Base):
    __tablename__ = "usage_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def default_database_url() -> str:
    configured = os.environ.get("JOYMESH_DATABASE_URL")
    if configured:
        return configured
    data_dir = Path.home() / ".local" / "share" / "joymesh"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{data_dir / 'joymesh.db'}"


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or default_database_url()
        self.engine: AsyncEngine = create_async_engine(self.url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._event_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await self.ensure_default_subscription()

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_default_subscription(self) -> None:
        async with self.sessions() as session:
            if await session.get(SubscriptionRow, "fake-local") is not None:
                return
            session.add(
                SubscriptionRow(
                    id="fake-local",
                    harness_id="fake",
                    name="Bundled fake harness",
                    billing_route=BillingRoute.LOCAL.value,
                    enabled=True,
                    monthly_limit=None,
                    used_amount=0,
                    max_concurrency=8,
                    cost_weight=0,
                )
            )
            await session.commit()

    async def create_subscription(self, data: SubscriptionCreate) -> SubscriptionProfile:
        row = SubscriptionRow(
            id=str(uuid4()),
            harness_id=data.harness_id,
            name=data.name,
            billing_route=data.billing_route.value,
            enabled=True,
            monthly_limit=data.monthly_limit,
            used_amount=data.used_amount,
            max_concurrency=data.max_concurrency,
            cost_weight=data.cost_weight,
        )
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
        return self._subscription_model(row)

    async def list_subscriptions(self) -> tuple[SubscriptionProfile, ...]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SubscriptionRow).order_by(SubscriptionRow.harness_id, SubscriptionRow.id)
                )
            ).all()
        return tuple(self._subscription_model(row) for row in rows)

    async def create_run(self, run: Run) -> Run:
        row = RunRow(
            id=run.id,
            task=run.task,
            workspace=run.workspace,
            harness_id=run.harness_id,
            subscription_id=run.subscription_id,
            status=run.status.value,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            exit_code=run.exit_code,
            error=run.error,
        )
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
        return self._run_model(row)

    async def get_run(self, run_id: str) -> Run | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        return None if row is None else self._run_model(row)

    async def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> Run:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                raise KeyError(f"unknown run: {run_id}")
            row.status = status.value
            if started_at is not None:
                row.started_at = started_at
            if finished_at is not None:
                row.finished_at = finished_at
            if exit_code is not None:
                row.exit_code = exit_code
            if error is not None:
                row.error = error
            await session.commit()
        return self._run_model(row)

    async def append_event(self, event: NormalizedEvent) -> NormalizedEvent:
        import json

        async with self._event_lock, self.sessions() as session:
            max_sequence = await session.scalar(
                select(func.max(EventRow.sequence)).where(EventRow.run_id == event.run_id)
            )
            sequence = (max_sequence or 0) + 1
            row = EventRow(
                run_id=event.run_id,
                sequence=sequence,
                type=event.type.value,
                timestamp=event.timestamp,
                message=event.message,
                payload_json=json.dumps(event.payload, sort_keys=True),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return self._event_model(row)

    async def list_events(self, run_id: str, *, after: int = 0) -> tuple[NormalizedEvent, ...]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(EventRow)
                    .where(EventRow.run_id == run_id, EventRow.sequence > after)
                    .order_by(EventRow.sequence)
                )
            ).all()
        return tuple(self._event_model(row) for row in rows)

    async def active_count(self, *, harness_id: str, subscription_id: str | None) -> int:
        query = (
            select(func.count())
            .select_from(RunRow)
            .where(
                RunRow.harness_id == harness_id,
                RunRow.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]),
            )
        )
        if subscription_id is not None:
            query = query.where(RunRow.subscription_id == subscription_id)
        async with self.sessions() as session:
            return int(await session.scalar(query) or 0)

    async def record_usage(
        self,
        *,
        subscription_id: str,
        amount: float,
        run_id: str | None = None,
        source: str = "manual",
    ) -> None:
        async with self.sessions() as session:
            subscription = await session.get(SubscriptionRow, subscription_id)
            if subscription is None:
                raise KeyError(f"unknown subscription: {subscription_id}")
            subscription.used_amount += amount
            session.add(
                UsageRow(
                    subscription_id=subscription_id,
                    run_id=run_id,
                    amount=amount,
                    source=source,
                    recorded_at=utc_now(),
                )
            )
            await session.commit()

    @staticmethod
    def _subscription_model(row: SubscriptionRow) -> SubscriptionProfile:
        return SubscriptionProfile(
            id=row.id,
            harness_id=row.harness_id,
            name=row.name,
            billing_route=BillingRoute(row.billing_route),
            enabled=row.enabled,
            monthly_limit=row.monthly_limit,
            used_amount=row.used_amount,
            max_concurrency=row.max_concurrency,
            cost_weight=row.cost_weight,
        )

    @staticmethod
    def _run_model(row: RunRow) -> Run:
        return Run(
            id=row.id,
            task=row.task,
            workspace=row.workspace,
            harness_id=row.harness_id,
            subscription_id=row.subscription_id,
            status=RunStatus(row.status),
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            exit_code=row.exit_code,
            error=row.error,
        )

    @staticmethod
    def _event_model(row: EventRow) -> NormalizedEvent:
        import json

        return NormalizedEvent(
            id=row.id,
            run_id=row.run_id,
            sequence=row.sequence,
            type=EventType(row.type),
            timestamp=row.timestamp,
            message=row.message,
            payload=json.loads(row.payload_json),
        )
