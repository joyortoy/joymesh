"""Developer CLI for the JoyMesh service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from joymesh.api import create_app
from joymesh.models import BillingRoute, Run, SubscriptionCreate
from joymesh.service import JoyMesh, NoRouteError

app = typer.Typer(help="Harness interoperability for coding agents.")
harness_app = typer.Typer(help="Discover and inspect harnesses.")
subscription_app = typer.Typer(help="Manage subscription and quota profiles.")
route_app = typer.Typer(help="Preview deterministic routes.")
run_app = typer.Typer(help="Launch and inspect harness runs.", invoke_without_command=True)
app.add_typer(harness_app, name="harness")
app.add_typer(subscription_app, name="subscription")
app.add_typer(route_app, name="route")
app.add_typer(run_app, name="run")


def _run[T](operation: Callable[[JoyMesh], Awaitable[T]]) -> T:
    async def execute() -> T:
        mesh = JoyMesh()
        try:
            return await operation(mesh)
        finally:
            await mesh.close()

    return asyncio.run(execute())


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
    """Detect installed harnesses and report availability."""

    _print(_run(lambda mesh: mesh.detect_harnesses()))


@harness_app.command("list")
def harness_list() -> None:
    """List registered harnesses and their capabilities."""

    _print(_run(lambda mesh: mesh.detect_harnesses()))


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
