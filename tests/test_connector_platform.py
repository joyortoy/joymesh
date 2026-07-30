from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from joymesh.api import create_app
from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.models import (
    ConnectorExecutionMode,
    ConnectorMaturity,
    ConnectorTier,
)
from joymesh.connectors.planning import (
    ConnectorAction,
    ConnectorPlanError,
    ConnectorPlanner,
)
from joymesh.service import JoyMesh


def test_catalogue_contains_all_reviewed_harness_families() -> None:
    catalogue = ConnectorCatalogue.builtins()

    assert {item.harness_id for item in catalogue.all()} == {
        "aider",
        "amazon-q",
        "amp",
        "claude-code",
        "cline",
        "codex",
        "continue",
        "cursor",
        "factory-droid",
        "gemini-cli",
        "github-copilot",
        "goose",
        "grok",
        "kiro",
        "opencode",
        "openhands",
        "pi",
        "qwen-code",
        "roo-code",
        "warp",
        "windsurf",
    }
    assert not catalogue.stale(max_age_days=90)
    assert len(catalogue.revision_digest()) == 64


def test_catalogue_does_not_confuse_providers_with_harnesses() -> None:
    ids = {item.harness_id for item in ConnectorCatalogue.builtins().all()}

    assert not ids.intersection(
        {
            "fireconnect",
            "fireworks",
            "openrouter",
            "litellm",
            "ollama",
            "openai-api",
            "anthropic-api",
            "bedrock",
            "vertex",
        }
    )


def test_ide_only_connectors_cannot_route() -> None:
    catalogue = ConnectorCatalogue.builtins()

    for connector_id in ("roo-code", "windsurf"):
        connector = catalogue.get(connector_id)
        assert connector.tier is ConnectorTier.IDE
        assert connector.execution.mode is ConnectorExecutionMode.IDE_ONLY
        assert not connector.remote_execution_supported
        assert not connector.routable_by_maturity


def test_no_connector_is_falsely_marked_certified() -> None:
    catalogue = ConnectorCatalogue.builtins()

    assert not {
        item.harness_id
        for item in catalogue.all()
        if item.maturity in {ConnectorMaturity.CERTIFIED, ConnectorMaturity.PRODUCTION_READY}
    }


def test_installation_commands_are_bounded_and_backend_owned() -> None:
    forbidden = {"sh", "bash", "zsh", "cmd", "powershell", "sudo"}
    for connector in ConnectorCatalogue.builtins().all():
        for option in (
            *connector.installation_options,
            *connector.upgrade_options,
            *connector.uninstall_options,
        ):
            if not option.executable:
                assert option.digest_required
                continue
            assert option.argv
            assert Path(option.argv[0]).name.lower() not in forbidden
            assert not {"|", "&&", ";"}.intersection(option.argv)


def test_plan_is_platform_aware_hash_bound_and_not_browser_command_driven() -> None:
    planner = ConnectorPlanner()
    plan = planner.plan(
        node_id="node-1",
        connector_id="codex",
        action=ConnectorAction.INSTALL,
        method_id="npm",
        platform="darwin",
    )

    assert plan.executable == "npm"
    assert plan.arguments == ("install", "--global", "@openai/codex")
    assert plan.connector_revision == "2026-07-29.1"
    planner.validate(plan)
    with pytest.raises(ConnectorPlanError, match="backend-generated"):
        planner.validate(plan.model_copy(update={"arguments": ("install", "untrusted")}))
    with pytest.raises(ConnectorPlanError, match="unavailable"):
        planner.plan(
            node_id="node-1",
            connector_id="codex",
            action=ConnectorAction.INSTALL,
            platform="plan9",
        )


def test_official_scripts_cannot_execute_before_node_binds_digest() -> None:
    planner = ConnectorPlanner()

    with pytest.raises(ConnectorPlanError, match="digest-bound"):
        planner.plan(
            node_id="node-1",
            connector_id="cursor",
            action=ConnectorAction.INSTALL,
            platform="darwin",
        )


async def test_sdk_and_api_share_connector_catalogue_and_planner(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    app = create_app(mesh)
    async with app.router.lifespan_context(app):
        sdk_ids = [item.harness_id for item in mesh.list_connectors()]
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/connector-catalogue")
            assert response.status_code == 200
            assert [item["harness_id"] for item in response.json()] == sdk_ids

            planned = await client.post(
                "/nodes/node-1/connectors/gemini-cli/install/plan",
                json={"method_id": "npm", "platform": "darwin"},
            )
            assert planned.status_code == 200
            body = planned.json()
            plan = body["plan"]
            assert plan["executable"] == "npm"
            assert plan["arguments"] == [
                "install",
                "--global",
                "@google/gemini-cli",
            ]
            assert body["approval_required"] is True
            assert body["next_action"] == "approve"

            rejected = await client.post(
                f"/connector-tasks/{plan['plan_id']}/execute",
                json={"plan_hash": plan["plan_hash"], "approved": False},
            )
            assert rejected.status_code == 409

            accepted = await client.post(
                f"/connector-tasks/{plan['plan_id']}/execute",
                json={"plan_hash": plan["plan_hash"], "approved": True},
            )
            assert accepted.status_code == 200
            assert accepted.json()["status"] in {
                "queued",
                "offered_to_node",
                "accepted_by_node",
                "running",
                "failed",
                "succeeded",
            }

    await mesh.close()
