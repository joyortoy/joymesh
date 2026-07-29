"""Async SQLAlchemy persistence for the JoyMesh service layer."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from joymesh.harnesses.contracts import CertificationEvidence, CertificationState
from joymesh.models import (
    BillingRoute,
    EventType,
    FallbackProposal,
    NormalizedEvent,
    RouteCandidate,
    Run,
    RunStatus,
    SubscriptionCreate,
    SubscriptionProfile,
    SubscriptionState,
    UsageRecord,
    utc_now,
)


class Base(DeclarativeBase):
    pass


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
    quota_reserve: Mapped[float] = mapped_column(Float, default=0)
    quota_known: Mapped[bool] = mapped_column(default=False)
    state: Mapped[str] = mapped_column(String(30))
    requires_paid_approval: Mapped[bool] = mapped_column(default=False)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task: Mapped[str] = mapped_column(Text)
    workspace: Mapped[str] = mapped_column(Text)
    harness_id: Mapped[str] = mapped_column(String(100), index=True)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"))
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    task_context_id: Mapped[str] = mapped_column(String(36), index=True)
    continuation_of_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    native_session_id: Mapped[str | None] = mapped_column(String(200))
    process_id: Mapped[int | None] = mapped_column(Integer)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class UsageRow(Base):
    __tablename__ = "usage_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    amount: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(50))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FallbackProposalRow(Base):
    __tablename__ = "fallback_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    route_json: Mapped[str] = mapped_column(Text)
    requires_approval: Mapped[bool]
    approved: Mapped[bool] = mapped_column(default=False)
    continuation_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CertificationEvidenceRow(Base):
    __tablename__ = "certification_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    harness_id: Mapped[str] = mapped_column(String(100), index=True)
    adapter_version: Mapped[str] = mapped_column(String(50))
    binary_version: Mapped[str | None] = mapped_column(String(300))
    executable: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(40))
    checks_json: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


def default_database_url() -> str:
    if configured := os.environ.get("JOYMESH_DATABASE_URL"):
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
            await connection.run_sync(_upgrade_legacy_sqlite_schema)
        await self.ensure_default_subscription()

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_default_subscription(self) -> None:
        async with self.sessions() as session:
            if await session.get(SubscriptionRow, "fake-local"):
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
                    quota_reserve=0,
                    quota_known=True,
                    state=SubscriptionState.HEALTHY.value,
                    requires_paid_approval=False,
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
            quota_reserve=data.quota_reserve,
            quota_known=data.quota_known,
            state=SubscriptionState.HEALTHY.value,
            requires_paid_approval=data.requires_paid_approval,
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

    async def set_subscription_state(self, subscription_id: str, state: SubscriptionState) -> None:
        async with self.sessions() as session:
            row = await session.get(SubscriptionRow, subscription_id)
            if row is None:
                raise KeyError(f"unknown subscription: {subscription_id}")
            row.state = state.value
            await session.commit()

    async def create_run(self, run: Run) -> Run:
        row = RunRow(**run.model_dump(mode="python", exclude={"status"}), status=run.status.value)
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
        return self._run_model(row)

    async def get_run(self, run_id: str) -> Run | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        return None if row is None else self._run_model(row)

    async def list_runs(self, *, limit: int = 25) -> tuple[Run, ...]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
                )
            ).all()
        return tuple(self._run_model(row) for row in rows)

    async def update_run(
        self, run_id: str, *, status: RunStatus | None = None, **values: object
    ) -> Run:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                raise KeyError(f"unknown run: {run_id}")
            if status is not None:
                row.status = status.value
            for key, value in values.items():
                if value is not None:
                    setattr(row, key, value)
            await session.commit()
        return self._run_model(row)

    async def append_event(self, event: NormalizedEvent) -> NormalizedEvent:
        async with self._event_lock, self.sessions() as session:
            maximum = await session.scalar(
                select(func.max(EventRow.sequence)).where(EventRow.run_id == event.run_id)
            )
            row = EventRow(
                run_id=event.run_id,
                sequence=(maximum or 0) + 1,
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
        if subscription_id:
            query = query.where(RunRow.subscription_id == subscription_id)
        async with self.sessions() as session:
            return int(await session.scalar(query) or 0)

    async def record_usage(
        self,
        *,
        subscription_id: str,
        run_id: str | None,
        input_tokens: int,
        output_tokens: int,
        amount: float = 0,
        source: str = "observed",
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
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    source=source,
                    recorded_at=utc_now(),
                )
            )
            await session.commit()

    async def list_usage(self, *, run_id: str | None = None) -> tuple[UsageRecord, ...]:
        query = select(UsageRow).order_by(UsageRow.id)
        if run_id:
            query = query.where(UsageRow.run_id == run_id)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return tuple(UsageRecord.model_validate(row) for row in rows)

    async def create_fallback(self, proposal: FallbackProposal) -> FallbackProposal:
        row = FallbackProposalRow(
            id=proposal.id,
            original_run_id=proposal.original_run_id,
            route_json=proposal.route.model_dump_json(),
            requires_approval=proposal.requires_approval,
            approved=proposal.approved,
            continuation_run_id=proposal.continuation_run_id,
            reason=proposal.reason,
            created_at=proposal.created_at,
        )
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
        return self._fallback_model(row)

    async def get_fallback_for_run(self, run_id: str) -> FallbackProposal | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(FallbackProposalRow).where(FallbackProposalRow.original_run_id == run_id)
            )
        return None if row is None else self._fallback_model(row)

    async def approve_fallback(
        self, proposal_id: str, continuation_run_id: str
    ) -> FallbackProposal:
        async with self.sessions() as session:
            row = await session.get(FallbackProposalRow, proposal_id)
            if row is None:
                raise KeyError(f"unknown fallback proposal: {proposal_id}")
            row.approved = True
            row.continuation_run_id = continuation_run_id
            await session.commit()
        return self._fallback_model(row)

    async def record_certification(self, evidence: CertificationEvidence) -> CertificationEvidence:
        metadata = {
            **evidence.metadata,
            "joymesh_version": evidence.joymesh_version,
            "operating_system": evidence.operating_system,
            "test_suite_version": evidence.test_suite_version,
            "diagnostics": list(evidence.diagnostics),
        }
        row = CertificationEvidenceRow(
            id=evidence.id,
            harness_id=evidence.harness_id,
            adapter_version=evidence.adapter_version,
            binary_version=evidence.binary_version,
            executable=evidence.executable,
            state=evidence.state.value,
            checks_json=json.dumps(evidence.checks, sort_keys=True),
            detail=evidence.detail,
            recorded_at=evidence.recorded_at,
            metadata_json=json.dumps(metadata, sort_keys=True),
        )
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
        return evidence

    async def list_certifications(
        self, *, harness_id: str | None = None
    ) -> tuple[CertificationEvidence, ...]:
        query = select(CertificationEvidenceRow).order_by(
            CertificationEvidenceRow.recorded_at.desc(),
            CertificationEvidenceRow.id,
        )
        if harness_id:
            query = query.where(CertificationEvidenceRow.harness_id == harness_id)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        evidence: list[CertificationEvidence] = []
        for row in rows:
            metadata = json.loads(row.metadata_json)
            evidence.append(
                CertificationEvidence(
                    id=row.id,
                    harness_id=row.harness_id,
                    adapter_version=row.adapter_version,
                    binary_version=row.binary_version,
                    executable=row.executable,
                    state=CertificationState(row.state),
                    checks=json.loads(row.checks_json),
                    detail=row.detail,
                    recorded_at=row.recorded_at,
                    joymesh_version=str(metadata.pop("joymesh_version", "0.1.0")),
                    operating_system=str(metadata.pop("operating_system", "unknown")),
                    test_suite_version=str(metadata.pop("test_suite_version", "1")),
                    diagnostics=tuple(metadata.pop("diagnostics", ())),
                    metadata=metadata,
                )
            )
        return tuple(evidence)

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
            quota_reserve=row.quota_reserve,
            quota_known=row.quota_known,
            state=SubscriptionState(row.state),
            requires_paid_approval=row.requires_paid_approval,
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
            task_context_id=row.task_context_id,
            continuation_of_run_id=row.continuation_of_run_id,
            native_session_id=row.native_session_id,
            process_id=row.process_id,
        )

    @staticmethod
    def _event_model(row: EventRow) -> NormalizedEvent:
        return NormalizedEvent(
            id=row.id,
            run_id=row.run_id,
            sequence=row.sequence,
            type=EventType(row.type),
            timestamp=row.timestamp,
            message=row.message,
            payload=json.loads(row.payload_json),
        )

    @staticmethod
    def _fallback_model(row: FallbackProposalRow) -> FallbackProposal:
        return FallbackProposal(
            id=row.id,
            original_run_id=row.original_run_id,
            route=RouteCandidate.model_validate_json(row.route_json),
            requires_approval=row.requires_approval,
            approved=row.approved,
            continuation_run_id=row.continuation_run_id,
            reason=row.reason,
            created_at=row.created_at,
        )


def _upgrade_legacy_sqlite_schema(connection: Connection) -> None:
    """Bring pre-Alembic development databases up to the current initial schema."""
    if connection.dialect.name != "sqlite":
        return
    inspector = inspect(connection)
    additions = {
        "subscriptions": {
            "quota_reserve": "FLOAT NOT NULL DEFAULT 0",
            "quota_known": "BOOLEAN NOT NULL DEFAULT 0",
            "state": "VARCHAR(30) NOT NULL DEFAULT 'healthy'",
            "requires_paid_approval": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "runs": {
            "task_context_id": "VARCHAR(36) NOT NULL DEFAULT ''",
            "continuation_of_run_id": "VARCHAR(36)",
            "native_session_id": "VARCHAR(200)",
            "process_id": "INTEGER",
        },
        "usage_ledger": {
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
        },
    }
    table_names = set(inspector.get_table_names())
    for table_name, columns in additions.items():
        if table_name not in table_names:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, definition in columns.items():
            if column_name not in existing:
                connection.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')
                )
    if "runs" in table_names:
        connection.execute(text("UPDATE runs SET task_context_id = id WHERE task_context_id = ''"))
