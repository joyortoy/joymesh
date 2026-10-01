"""Offline integration seam. No listener, grants, dot lifecycle, or app credentials.

TransportContext must be supplied by existing verified JoyCtl authentication,
never deserialized from MCP arguments. The adapter only narrows input and
delegates authorization/state to JoyCtl. Hosted event delivery is disabled.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import quote

from joymesh.runtime_v1.contracts.workers import ExecutionOffer, FactualExecutionResult


@dataclass(frozen=True)
class TransportContext:
    organisation_id: str
    subject: str
    session: str | None = None


class JoyCtlBinding(Protocol):
    """Caller implements with real authenticated application/HTTP services.

    request must preserve the existing OIDC/RBAC/tenant boundary. Assignment
    and result methods are harness seams, NOT newly claimed public endpoints.
    """

    def request(self, ctx: TransportContext, method: str, path: str,
                body: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def assignment(self, ctx: TransportContext, offer_id: str) -> ExecutionOffer: ...

    def record_result(self, ctx: TransportContext, offer: ExecutionOffer,
                      result: FactualExecutionResult) -> dict[str, Any]: ...


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def blocked(reason: str) -> dict[str, Any]:
    return {"state": "blocked", "reason": reason, "execution_performed": False}


class Bridge:
    SHAPES: ClassVar[dict[str, set[str]]] = {
        "intent_submit": {"workspace_id", "project_id", "title"},
        "execution_submit": {"mission_id", "step_id", "authorization_id", "prompt_id"},
        "mission_fetch": {"mission_id"},
        "status_fetch": {"execution_id"},
        "evidence_fetch": {"mission_id"},
        "verification_fetch": {"mission_id"},
        "report_projection_fetch": {"mission_id"},
        "report_fetch": {"mission_id", "report_version"},
        "report_acknowledge": {"mission_id", "report_version", "event_id"},
    }

    def __init__(self, binding: JoyCtlBinding):
        self.binding = binding

    def call(self, ctx: TransportContext, name: str,
             args: dict[str, Any]) -> dict[str, Any]:
        # Context is trusted transport state; no identity/role fields in tool inputs.
        if not ctx.organisation_id or not ctx.subject:
            raise PermissionError("verified authentication required")
        if name not in self.SHAPES or set(args) != self.SHAPES[name]:
            raise ValueError("unsupported tool or arguments")
        if not all(isinstance(v, str) and v.strip() for v in args.values()):
            raise ValueError("nonempty string arguments required")
        if name == "report_projection_fetch":
            path = "/api/missions/" + quote(args["mission_id"], safe="")
            graph = self.binding.request(ctx, "GET", path + "/graph")
            evidence = self.binding.request(ctx, "GET", path + "/evidence")
            verification = self.binding.request(ctx, "GET", path + "/verification")
            current = self.binding.request(ctx, "GET", path + "/graph")
            if graph.get("version") is None or graph != current:
                return blocked("mission graph changed; refetch required")
            return {"mission_id": args["mission_id"], "projection_version": graph["version"],
                    "kind": "read_projection", "canonical_report": False,
                    "graph": graph, "evidence": evidence, "verification": verification,
                    "snapshot_atomic": False}
        if name in {"report_fetch", "report_acknowledge"}:
            return blocked("supported JoyCtl report application-service binding required")
        if name == "intent_submit":
            # A draft is an intent capture only; no policy_grant is accepted.
            return self.binding.request(ctx, "POST", "/api/missions", args)
        if name == "execution_submit":
            # JoyCtl consumes the prompt/step-bound single-use grant. Never mint it here.
            return self.binding.request(ctx, "POST", "/api/executions", args)
        if name == "status_fetch":
            path = "/api/executions/" + quote(args["execution_id"], safe="")
        else:
            path = "/api/missions/" + quote(args["mission_id"], safe="")
            path += {"evidence_fetch": "/evidence", "verification_fetch": "/verification"}.get(
                name, "")
        return self.binding.request(ctx, "GET", path)

    def receive_result(self, ctx: TransportContext, offer_id: str,
                       result: FactualExecutionResult) -> dict[str, Any]:
        # Authoritative binding rechecks tenant, enrollment/session, subscription,
        # assignment, grant and current lease on EVERY return, including duplicates.
        offer = self.binding.assignment(ctx, offer_id)
        if (result.execution_id, result.attempt_id, result.worker_id, result.harness) != (
            offer.execution_id, offer.attempt_id, offer.worker_id, offer.harness_id
        ):
            raise PermissionError("wrong assignment return")
        return self.binding.record_result(ctx, offer, result)

    def rpc(self, ctx: TransportContext, request: dict[str, Any]) -> dict[str, Any]:
        """In-process MCP protocol harness; no transport or webhook claim."""
        method = request["method"]
        if method == "server/discover":
            result = {"resultType": "complete", "supportedVersions": ["2026-07-28"],
                      "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": name, "description": "Forward through existing JoyCtl authority: "
                 + name, "inputSchema": {
                     "type": "object", "additionalProperties": False,
                     "required": sorted(fields), "properties": {
                         field: {"type": "string", "minLength": 1} for field in sorted(fields)
                     }}, "annotations": {
                         "readOnlyHint": name.endswith("fetch"),
                         "destructiveHint": False, "openWorldHint": False}}
                for name, fields in self.SHAPES.items()
                if name not in {"report_fetch", "report_acknowledge"}
            ]}
        elif method == "events/list":
            result = {"events": []}  # Do not advertise incomplete live event support.
        elif method in {"events/subscribe", "events/unsubscribe"}:
            result = blocked("verified callback and authenticated dot return binding required")
        elif method == "tools/call":
            params = request["params"]
            value = self.call(ctx, params["name"], params["arguments"])
            result = {"structuredContent": value,
                      "content": [{"type": "text", "text": canonical(value)}]}
        else:
            raise ValueError("unsupported MCP method")
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


class ReportInbox:
    """Durable sam delivery cache, not authoritative mission or approval state.

    Accept ONLY projections obtained through the authenticated JoyCtl binding.
    Out-of-order delivery is harmless; same-version divergent content is rejected.
    Local receipt acknowledgement does not acknowledge a JoyCtl canonical report.
    """

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            tenant TEXT, mission TEXT, version INTEGER, digest TEXT, payload TEXT,
            acknowledged INTEGER DEFAULT 0, PRIMARY KEY(tenant, mission, version))""")

    def observe(self, tenant: str, mission: str, version: int,
                projection: dict[str, Any]) -> str:
        if version < 1 or projection.get("mission_id") != mission:
            raise ValueError("invalid report projection")
        payload = canonical(projection)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.db:
            row = self.db.execute("SELECT digest FROM receipts WHERE tenant=? AND mission=? "
                                  "AND version=?", (tenant, mission, version)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("conflicting report version")
                return "duplicate"
            latest = self.db.execute("SELECT max(version) FROM receipts WHERE tenant=? "
                                     "AND mission=?", (tenant, mission)).fetchone()[0]
            self.db.execute("INSERT INTO receipts(tenant,mission,version,digest,payload) "
                            "VALUES(?,?,?,?,?)", (tenant, mission, version, digest, payload))
        return "out_of_order" if latest and version < latest else "new"

    def latest(self, tenant: str, mission: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT payload FROM receipts WHERE tenant=? AND mission=? "
                              "ORDER BY version DESC LIMIT 1", (tenant, mission)).fetchone()
        return json.loads(row[0]) if row else None

    def acknowledge_receipt(self, tenant: str, mission: str, version: int) -> None:
        with self.db:
            cursor = self.db.execute("UPDATE receipts SET acknowledged=1 WHERE tenant=? "
                                     "AND mission=? AND version=?", (tenant, mission, version))
            if cursor.rowcount != 1:
                raise ValueError("unknown report version")
