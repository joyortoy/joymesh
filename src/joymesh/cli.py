"""Developer CLI for the JoyMesh service."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer

from joymesh.api import create_app
from joymesh.connectors.planning import ConnectorAction
from joymesh.control_plane.node import JoyMeshNode
from joymesh.control_plane.security import generate_node_keypair, store_private_key
from joymesh.harnesses.contracts import ApprovalToken, LifecycleAction
from joymesh.models import BillingRoute, Run, SubscriptionCreate
from joymesh.service import JoyMesh, NoRouteError

app = typer.Typer(help="Harness interoperability for coding agents.")
harness_app = typer.Typer(help="Discover and inspect harnesses.")
subscription_app = typer.Typer(help="Manage subscription and quota profiles.")
route_app = typer.Typer(help="Preview deterministic routes.")
run_app = typer.Typer(help="Launch and inspect harness runs.", invoke_without_command=True)
node_app = typer.Typer(help="Pair and run an outbound-only JoyMesh Node.")
connector_app = typer.Typer(help="Validate, inspect, and certify versioned connectors.")
app.add_typer(harness_app, name="harness")
app.add_typer(subscription_app, name="subscription")
app.add_typer(route_app, name="route")
app.add_typer(run_app, name="run")
app.add_typer(node_app, name="node")
app.add_typer(connector_app, name="connector")


@node_app.command("init")
def node_init(
    private_key_path: Path | None = typer.Option(None, "--private-key-path"),  # noqa: B008
) -> None:
    """Create a local Ed25519 node key; prints only the public registration value."""

    private_key_path = (
        private_key_path.expanduser()
        if private_key_path is not None
        else Path("~/.config/joymesh/node.ed25519").expanduser()
    )
    if private_key_path.exists():
        raise typer.BadParameter("private key already exists; use key rotation instead")
    private_key, public_key = generate_node_keypair()
    store_private_key(private_key_path, private_key)
    _print(
        {
            "private_key_path": str(private_key_path),
            "public_key": public_key,
            "algorithm": "Ed25519",
        }
    )


@node_app.command("serve")
@node_app.command("connect")
def node_serve(
    node_id: str = typer.Option(..., "--node-id"),
    gateway_url: str = typer.Option(..., "--gateway-url"),
    private_key_path: Path = typer.Option(  # noqa: B008
        Path("~/.config/joymesh/node.ed25519"),
        "--private-key-path",
    ),
    token: str | None = typer.Option(None, "--token", envvar="JOYMESH_NODE_GATEWAY_TOKEN"),
) -> None:
    """Authenticate and maintain the node's outbound TLS WebSocket."""

    async def serve() -> None:
        node = JoyMeshNode.from_key_path(
            node_id=node_id,
            gateway_url=gateway_url,
            private_key_path=private_key_path,
            bearer_token=token or os.environ.get("JOYMESH_NODE_GATEWAY_TOKEN"),
        )

        async def observe(message: Any) -> None:
            _print(message)

        try:
            await node.run(observe)
        finally:
            await node.stop()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        typer.echo("JoyMesh Node stopped", err=True)


@node_app.command("status")
def node_status(
    node_id: str = typer.Option(..., "--node-id"),
    control_plane_url: str = typer.Option("http://127.0.0.1:8000", "--control-plane-url"),
) -> None:
    """Query control-plane session status for a node."""

    import urllib.request

    with urllib.request.urlopen(
        f"{control_plane_url.rstrip('/')}/nodes/{node_id}/session"
    ) as response:
        _print(json.loads(response.read().decode()))


@connector_app.command("discover")
def connector_discover(
    connector_id: str,
    node_id: str = typer.Option("local-node", "--node-id"),
) -> None:
    """Plan and queue connector discovery through the shared lifecycle service."""

    async def operate(mesh: JoyMesh) -> Any:
        plan = await mesh.plan_and_persist_connector_task(
            node_id=node_id,
            connector_id=connector_id,
            action=ConnectorAction.DISCOVER,
        )
        return await mesh.execute_connector_plan(
            plan_id=plan.plan_id, plan_hash=plan.plan_hash, approved=True
        )

    _print(_run(operate))


@connector_app.command("verify-auth")
def connector_verify_auth(
    connector_id: str,
    node_id: str = typer.Option("local-node", "--node-id"),
) -> None:
    """Queue authentication verification for a connector."""

    async def operate(mesh: JoyMesh) -> Any:
        plan = await mesh.plan_and_persist_connector_task(
            node_id=node_id,
            connector_id=connector_id,
            action=ConnectorAction.VERIFY_AUTHENTICATION,
        )
        return await mesh.execute_connector_plan(
            plan_id=plan.plan_id, plan_hash=plan.plan_hash, approved=True
        )

    _print(_run(operate))


@connector_app.command("live-test")
def connector_live_test(
    connector_id: str,
    profile: str = typer.Option("read-only", "--profile"),
    control_plane_url: str = typer.Option("http://127.0.0.1:8787", "--control-plane-url"),
    node_id: str = typer.Option(..., "--node-id"),
    enable_routing: bool = typer.Option(
        False, "--enable-routing", help="Require explicit confirmation to enable routing"
    ),
) -> None:
    """Guide a production live Cursor acceptance run without mocking evidence."""

    from joymesh.connectors.live_test import run_cursor_live_test
    from joymesh.control_plane.security import assert_live_production_config

    if connector_id != "cursor":
        raise typer.BadParameter("live-test currently supports only cursor")
    if profile != "read-only":
        raise typer.BadParameter("only --profile read-only is supported")
    config = assert_live_production_config()
    typer.echo(json.dumps({"runtime": config}, indent=2, sort_keys=True))
    result = run_cursor_live_test(
        control_plane_url=control_plane_url,
        node_id=node_id,
        enable_routing=enable_routing,
    )
    _print(result)
    if result.get("status") != "ready" and result.get("status") != "routing_disabled":
        raise typer.Exit(2)


def _run[T](operation: Callable[[JoyMesh], Awaitable[T]]) -> T:
    async def execute() -> T:
        mesh = JoyMesh()
        try:
            return await operation(mesh)
        finally:
            await mesh.close()

    return asyncio.run(execute())


def _run_value[T](operation: Callable[[JoyMesh], T]) -> T:
    async def execute(mesh: JoyMesh) -> T:
        return operation(mesh)

    return _run(execute)


def _print(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


@harness_app.command("detect")
def harness_detect() -> None:
    """Show the compatibility adapter detection view."""

    _print(_run(lambda mesh: mesh.detect_harnesses()))


@connector_app.command("catalogue")
def connector_catalogue() -> None:
    """Print the versioned connector catalogue used by every public surface."""

    _print(_run_value(lambda mesh: mesh.list_connectors()))


@connector_app.command("inspect")
def connector_inspect(connector_id: str) -> None:
    """Inspect a connector definition without reading credentials."""

    _print(_run_value(lambda mesh: mesh.connector(connector_id)))


@connector_app.command("validate")
def connector_validate(
    connector_id: str,
    max_source_age_days: int = typer.Option(90, "--max-source-age-days", min=1),
) -> None:
    """Validate schema, source freshness, and routing invariants."""

    def validate(mesh: JoyMesh) -> dict[str, object]:
        connector = mesh.connector(connector_id)
        return {
            "connector_id": connector.harness_id,
            "revision": connector.revision,
            "schema_valid": True,
            "source_review_age_days": connector.source_review_age_days,
            "source_fresh": connector.source_review_age_days <= max_source_age_days,
            "routable_by_maturity": connector.routable_by_maturity,
            "remote_execution_supported": connector.remote_execution_supported,
        }

    _print(_run_value(validate))


@connector_app.command("test")
def connector_test(connector_id: str) -> None:
    """Report adapter and executable readiness; this never certifies a real binary."""

    async def test(mesh: JoyMesh) -> dict[str, object]:
        mesh.connector(connector_id)
        discovery = await mesh.discover_harnesses(connector_id, probe_versions=True)
        try:
            adapter = mesh.registry.get(connector_id)
        except KeyError:
            adapter = None
        return {
            "connector_id": connector_id,
            "adapter_registered": adapter is not None,
            "adapter_conformance_declared": (bool(adapter and adapter.conformance_passed)),
            "installations": discovery[0].model_dump(mode="json")["installations"],
            "real_binary_certified": False,
        }

    _print(_run(test))


@connector_app.command("certify")
def connector_certify(
    connector_id: str,
    node_id: str = typer.Option("local", "--node-id"),
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Plan certification, or run the existing bounded smoke profile with approval."""

    if not approve:
        _print(
            _run_value(
                lambda mesh: mesh.plan_connector_task(
                    node_id=node_id,
                    connector_id=connector_id,
                    action=ConnectorAction.CERTIFY,
                )
            )
        )
        return

    async def certify(mesh: JoyMesh) -> Any:
        resolved = mesh.registry.resolve_id(connector_id)
        token = ApprovalToken(
            action=LifecycleAction.CERTIFY,
            harness_id=resolved,
            approved=True,
            nonce=str(uuid4()),
        )
        return await mesh.certify_harness(resolved, approval=token)

    _print(_run(certify))


@harness_app.command("list")
def harness_list() -> None:
    """List the complete declarative harness catalogue."""

    _print(_run_value(lambda mesh: mesh.list_harnesses()))


@harness_app.command("discover")
def harness_discover(
    harness_id: str | None = typer.Argument(None),
    probe_versions: bool = typer.Option(False, "--probe-versions"),
) -> None:
    """Locate binaries; version execution is separately opt-in."""

    _print(
        _run(
            lambda mesh: mesh.discover_harnesses(
                harness_id,
                probe_versions=probe_versions,
            )
        )
    )


@harness_app.command("inspect")
def harness_inspect(harness_id: str) -> None:
    """Inspect definition, installations, auth boundary, and certifications."""

    _print(_run(lambda mesh: mesh.inspect_harness(harness_id)))


def _lifecycle_command(
    harness_id: str,
    action: LifecycleAction,
    approve: bool,
) -> None:
    async def operation(mesh: JoyMesh) -> Any:
        planner = {
            LifecycleAction.INSTALL: mesh.plan_install,
            LifecycleAction.UPGRADE: mesh.plan_upgrade,
        }[action]
        plan = planner(harness_id, dry_run=not approve)
        if not approve:
            return plan
        token = ApprovalToken(
            action=action,
            harness_id=plan.harness_id,
            approved=True,
            nonce=str(uuid4()),
        )
        return await mesh.execute_lifecycle_plan(plan, approval=token)

    _print(_run(operation))


@harness_app.command("install")
def harness_install(
    harness_id: str,
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Print an install plan, or execute it only with --approve."""

    _lifecycle_command(harness_id, LifecycleAction.INSTALL, approve)


@harness_app.command("upgrade")
def harness_upgrade(
    harness_id: str,
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Print an upgrade plan, or execute it only with --approve."""

    _lifecycle_command(harness_id, LifecycleAction.UPGRADE, approve)


@harness_app.command("doctor")
def harness_doctor(harness_id: str) -> None:
    """Show read-only lifecycle diagnostics without reading credentials."""

    _print(_run(lambda mesh: mesh.inspect_harness(harness_id)))


@harness_app.command("certify")
def harness_certify(
    harness_id: str | None = typer.Argument(None),
    all_installed: bool = typer.Option(False, "--all-installed"),
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Print the approval-gated real-binary certification plan."""

    if all_installed:

        async def operation(mesh: JoyMesh) -> tuple[Any, ...]:
            discovered = await mesh.discover_harnesses()
            return tuple(
                mesh.plan_certification(item.harness_id)
                for item in discovered
                if item.installations
            )

        _print(_run(operation))
        return
    if harness_id is None:
        raise typer.BadParameter("provide HARNESS_ID or --all-installed")
    if approve:

        async def certify(mesh: JoyMesh) -> Any:
            resolved = mesh.registry.resolve_id(harness_id)
            token = ApprovalToken(
                action=LifecycleAction.CERTIFY,
                harness_id=resolved,
                approved=True,
                nonce=str(uuid4()),
            )
            return await mesh.certify_harness(resolved, approval=token)

        _print(_run(certify))
        return
    _print(_run_value(lambda mesh: mesh.plan_certification(harness_id)))


@subscription_app.command("list")
def subscription_list() -> None:
    """List subscription and billing routes."""

    _print(_run(lambda mesh: mesh.list_subscriptions()))


@subscription_app.command("add")
def subscription_add(
    harness: str = typer.Option(..., "--harness"),
    name: str = typer.Option(..., "--name"),
    billing_route: BillingRoute = BillingRoute.UNKNOWN,
    monthly_limit: float | None = typer.Option(None, "--monthly-limit", min=0),
    used_amount: float = typer.Option(0, "--used-amount", min=0),
    max_concurrency: int = typer.Option(1, "--max-concurrency", min=1),
    cost_weight: float = typer.Option(1, "--cost-weight", min=0),
) -> None:
    """Create a manual subscription and quota profile."""

    data = SubscriptionCreate(
        harness_id=harness,
        name=name,
        billing_route=billing_route,
        monthly_limit=monthly_limit,
        used_amount=used_amount,
        max_concurrency=max_concurrency,
        cost_weight=cost_weight,
    )
    _print(_run(lambda mesh: mesh.create_subscription(data)))


@route_app.command("preview")
def route_preview(
    task: str = typer.Option(..., "--task"),
    workspace: str = typer.Option(".", "--workspace"),
    preferred_harness: str | None = typer.Option(None, "--preferred-harness"),
) -> None:
    """Preview routing without launching a harness."""

    _print(
        _run(
            lambda mesh: mesh.preview_routes(
                task=task,
                workspace=workspace,
                preferred_harness=preferred_harness,
            )
        )
    )


@run_app.callback()
def run_launch(
    ctx: typer.Context,
    workspace: str | None = typer.Option(None, "--workspace"),
    task: str | None = typer.Option(None, "--task"),
) -> None:
    """Launch a run when called without a run subcommand."""

    if ctx.invoked_subcommand is not None:
        return
    if workspace is None or task is None:
        raise typer.BadParameter("--workspace and --task are required")

    async def operation(mesh: JoyMesh) -> Run:
        run = await mesh.run(task=task, workspace=workspace)
        return await mesh.wait(run.id)

    try:
        completed = _run(operation)
    except NoRouteError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    _print(completed)


@run_app.command("inspect")
def run_inspect(run_id: str) -> None:
    """Inspect a persisted run."""

    run = _run(lambda mesh: mesh.inspect_run(run_id))
    if run is None:
        typer.echo("Run not found", err=True)
        raise typer.Exit(1)
    _print(run)


@run_app.command("cancel")
def run_cancel(run_id: str) -> None:
    """Cancel an active run."""

    try:
        _print(_run(lambda mesh: mesh.cancel(run_id)))
    except KeyError as exc:
        typer.echo("Run not found", err=True)
        raise typer.Exit(1) from exc


@app.command("api")
def api_server(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port", min=1, max=65535),
) -> None:
    """Run the local REST API."""

    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    app()
