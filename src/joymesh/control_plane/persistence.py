"""Relational control-plane schema.

Sensitive credentials are represented only by provider/key references. Secret
material belongs in a managed secret store or the node's OS credential store.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from joymesh.persistence import Base


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrganisationRow(Base):
    __tablename__ = "organisations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MembershipRow(Base):
    __tablename__ = "organisation_memberships"
    __table_args__ = (UniqueConstraint("organisation_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))


class BrowserSessionRow(Base):
    __tablename__ = "browser_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rotated_from_id: Mapped[str | None] = mapped_column(ForeignKey("browser_sessions.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyRow(Base):
    __tablename__ = "passkeys"
    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecoveryCodeRow(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NodeRow(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    public_key: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(100), index=True)
    platform: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeCredentialRow(Base):
    __tablename__ = "node_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingSessionRow(Base):
    __tablename__ = "pairing_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_code: Mapped[str] = mapped_column(String(20), unique=True)
    device_code_hash: Mapped[str] = mapped_column(String(128), unique=True)
    code_challenge: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class NodePresenceRow(Base):
    __tablename__ = "node_presence"
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(100), unique=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30))


class OnboardingProgressRow(Base):
    __tablename__ = "onboarding_progress"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"))
    state: Mapped[str] = mapped_column(String(50), index=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HarnessInstallationRow(Base):
    __tablename__ = "harness_installations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    harness_id: Mapped[str] = mapped_column(String(100), index=True)
    executable: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(300))
    readiness: Mapped[str] = mapped_column(String(50), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(128))


class HarnessAccountStateRow(Base):
    __tablename__ = "harness_account_states"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("harness_installations.id"), index=True)
    status: Mapped[str] = mapped_column(String(50))
    funding_kind: Mapped[str] = mapped_column(String(50))
    billing_mode: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[str] = mapped_column(String(30))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionPlanRow(Base):
    __tablename__ = "action_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    browser_session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"))
    harness_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    risk: Mapped[str] = mapped_column(String(30))
    plan_hash: Mapped[str] = mapped_column(String(128), unique=True)
    plan_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RemoteTaskRow(Base):
    __tablename__ = "remote_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_context_id: Mapped[str] = mapped_column(String(36), index=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    harness_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), index=True)
    nonce: Mapped[str] = mapped_column(String(100), unique=True)
    envelope_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "control_plane_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("action_plans.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    browser_session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    plan_hash: Mapped[str] = mapped_column(String(128))
    approved: Mapped[bool]
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RoutingPolicyRow(Base):
    __tablename__ = "routing_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    paid_route_policy: Mapped[str] = mapped_column(String(30), default="ask")
    limits_json: Mapped[str] = mapped_column(Text, default="{}")
    fireconnect_enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceGrantRow(Base):
    __tablename__ = "workspace_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    root_path: Mapped[str] = mapped_column(Text)
    allow_read: Mapped[bool] = mapped_column(default=True)
    allow_write: Mapped[bool] = mapped_column(default=False)
    allow_shell: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
