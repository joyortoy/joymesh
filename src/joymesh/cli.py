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
app.add_typer(harness_app, name="harness")
app.add_typer(subscription_app, name="subscription")
app.add_typer(route_app, name="route")
app.add_typer(run_app, name="run")
app.add_typer(node_app, name="node")


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
def node_serve(
    node_id: str = typer.Option(..., "--node-id"),
    gateway_url: str = typer.Option(..., "--gateway-url"),
    token: str | None = typer.Option(None, "--token", envvar="JOYMESH_NODE_GATEWAY_TOKEN"),
) -> None:
    """Maintain the node's outbound TLS WebSocket until interrupted."""

    gateway_token = token or os.environ.get("JOYMESH_NODE_GATEWAY_TOKEN")
    if not gateway_token:
        raise typer.BadParameter("set JOYMESH_NODE_GATEWAY_TOKEN or pass --token")

    async def serve() -> None:
        node = JoyMeshNode(
            node_id=node_id,
            gateway_url=gateway_url,
            bearer_token=gateway_token,
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
