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
from joymesh.telemetry import (
    MetricsMode,
    TelemetryMode,
    build_metrics_from_run,
    consent_needed,
    ensure_consent,
    get_telemetry_service,
    load_user_config,
)

app = typer.Typer(help="Harness interoperability for coding agents.")
harness_app = typer.Typer(help="Discover and inspect harnesses.")
subscription_app = typer.Typer(help="Manage subscription and quota profiles.")
route_app = typer.Typer(help="Preview deterministic routes.")
run_app = typer.Typer(help="Launch and inspect harness runs.", invoke_without_command=True)
node_app = typer.Typer(help="Pair and run an outbound-only JoyMesh Node.")
connector_app = typer.Typer(help="Validate, inspect, and certify versioned connectors.")
provider_route_app = typer.Typer(help="Inspect and manage provider routes (not harnesses).")
telemetry_app = typer.Typer(help="Manage anonymous execution metrics preferences (alias).")
metrics_app = typer.Typer(help="Manage anonymous execution metrics consent preferences.")
quota_app = typer.Typer(
    help="Inspect local harness quota and availability.",
    invoke_without_command=True,
)
app.add_typer(harness_app, name="harness")
app.add_typer(subscription_app, name="subscription")
app.add_typer(route_app, name="route")
app.add_typer(run_app, name="run")
app.add_typer(node_app, name="node")
app.add_typer(connector_app, name="connector")
app.add_typer(provider_route_app, name="provider-route")
app.add_typer(metrics_app, name="metrics")
app.add_typer(telemetry_app, name="telemetry")
app.add_typer(quota_app, name="quota")
runtime_app = typer.Typer(
    help="Inspect factual harness runtime snapshots for JoyCLI.",
    invoke_without_command=True,
)
app.add_typer(runtime_app, name="runtime")
delivery_app = typer.Typer(help="JoyCLI runtime-state delivery intake (Unix socket).")
app.add_typer(delivery_app, name="delivery")
production_app = typer.Typer(help="Production readiness utilities.")
app.add_typer(production_app, name="production")
runtime_key_app = typer.Typer(help="Runtime signing key lifecycle.")
runtime_app.add_typer(runtime_key_app, name="key")


@app.command("init")
def init_command() -> None:
    """Initialize JoyMesh user preferences (anonymous metrics consent)."""

    config = ensure_consent(interactive=True)
    settings = config.metrics
    mode = settings.mode.value if settings.mode else "unset"
    typer.echo(f"Metrics preference: {mode}")
    if settings.consent_completed:
        typer.echo("Consent saved. Change anytime with: joymesh metrics status|on|ask|off")
    else:
        typer.echo("No metrics preference saved (non-interactive session).")


def _metrics_status() -> None:
    status = get_telemetry_service().status()
    mode = status.get("mode") or "unset"
    consent = "yes" if status.get("consent_completed") else "no"
    typer.echo(f"mode: {mode}")
    typer.echo(f"consent_completed: {consent}")
    typer.echo(f"config: {status.get('config_path')}")


def _metrics_set(mode: MetricsMode, *, label: str) -> None:
    get_telemetry_service().set_mode(mode)
    typer.echo(f"metrics: {label}")


def _metrics_preview() -> None:
    from joymesh.telemetry import render_preview_yaml

    typer.echo("# Example anonymous execution metrics (placeholders only; not your data)")
    typer.echo(render_preview_yaml().rstrip())


@metrics_app.command("status")
def metrics_status() -> None:
    """Display the current anonymous metrics preference."""

    _metrics_status()


@metrics_app.command("on")
def metrics_on() -> None:
    """Always send anonymous execution metrics."""

    _metrics_set(MetricsMode.ALWAYS, label="always")


@metrics_app.command("ask")
def metrics_ask() -> None:
    """Ask before sending anonymous execution metrics."""

    _metrics_set(MetricsMode.ASK, label="ask")


@metrics_app.command("off")
def metrics_off() -> None:
    """Never send anonymous execution metrics."""

    _metrics_set(MetricsMode.NEVER, label="never")


@metrics_app.command("preview")
def metrics_preview() -> None:
    """Show an example anonymous metrics schema (placeholder values only)."""

    _metrics_preview()


@telemetry_app.command("status")
def telemetry_status() -> None:
    """Alias for ``joymesh metrics status``."""

    _metrics_status()


@telemetry_app.command("on")
def telemetry_on() -> None:
    """Alias for ``joymesh metrics on``."""

    _metrics_set(TelemetryMode.ALWAYS, label="always")


@telemetry_app.command("ask")
def telemetry_ask() -> None:
    """Alias for ``joymesh metrics ask``."""

    _metrics_set(TelemetryMode.ASK, label="ask")


@telemetry_app.command("off")
def telemetry_off() -> None:
    """Alias for ``joymesh metrics off``."""

    _metrics_set(TelemetryMode.NEVER, label="never")


@telemetry_app.command("preview")
def telemetry_preview() -> None:
    """Alias for ``joymesh metrics preview``."""

    _metrics_preview()


def _quota_ids() -> tuple[str, ...]:
    return ("opencode", "claude-code", "codex", "gemini-cli", "grok")


def _print_quota_table(mesh: JoyMesh, snapshots: tuple[object, ...]) -> None:
    typer.echo(mesh.quota.format_table(snapshots))  # type: ignore[arg-type]


@quota_app.callback(invoke_without_command=True)
def quota_root(ctx: typer.Context) -> None:
    """Show local harness quota and availability (not telemetry)."""

    if ctx.invoked_subcommand is not None:
        return
    quota_status()


@quota_app.command("status")
def quota_status() -> None:
    """Display harness availability in a human-readable table."""

    async def operation(mesh: JoyMesh) -> None:
        snapshots = await mesh.list_quota(harness_ids=_quota_ids())
        _print_quota_table(mesh, snapshots)

    _run(operation)


@quota_app.command("refresh")
def quota_refresh(
    harness: str | None = typer.Option(None, "--harness", help="Refresh one harness only"),
) -> None:
    """Invalidate cache and re-probe harness quota."""

    async def operation(mesh: JoyMesh) -> None:
        if harness:
            snapshots = await mesh.refresh_quota(harness)
        else:
            snapshots = await mesh.list_quota(harness_ids=_quota_ids(), refresh=True)
        _print_quota_table(mesh, snapshots)

    _run(operation)


@quota_app.command("json")
def quota_json() -> None:
    """Print quota snapshots as JSON (local routing data only)."""

    async def operation(mesh: JoyMesh) -> None:
        snapshots = await mesh.list_quota(harness_ids=_quota_ids())
        typer.echo(json.dumps(mesh.quota.as_json(snapshots), indent=2, sort_keys=True))

    _run(operation)


def _runtime_ids() -> tuple[str, ...]:
    return _quota_ids()


@runtime_app.callback(invoke_without_command=True)
def runtime_root(ctx: typer.Context) -> None:
    """Show factual harness runtime status for JoyCLI."""

    if ctx.invoked_subcommand is not None:
        return
    runtime_status()


@runtime_app.command("status")
def runtime_status() -> None:
    """Display harness runtime availability (facts only)."""

    async def operation(mesh: JoyMesh) -> None:
        snapshot = await mesh.get_runtime_snapshot()
        # Prefer the canonical five when present.
        wanted = set(_runtime_ids())
        filtered = type(snapshot)(
            snapshot_id=snapshot.snapshot_id,
            observed_at=snapshot.observed_at,
            harnesses=tuple(
                item for item in snapshot.harnesses if item.harness_id in wanted
            ),
            schema_version=snapshot.schema_version,
        )
        if filtered.harnesses:
            typer.echo(mesh.runtime_snapshots.format_table(filtered), nl=False)
        else:
            typer.echo(mesh.runtime_snapshots.format_table(snapshot), nl=False)

    _run(operation)


@runtime_app.command("json")
def runtime_json() -> None:
    """Print the immutable runtime snapshot as JSON."""

    async def operation(mesh: JoyMesh) -> None:
        snapshot = await mesh.get_runtime_snapshot()
        typer.echo(json.dumps(mesh.runtime_snapshots.as_json(snapshot), indent=2, sort_keys=True))

    _run(operation)


@runtime_app.command("refresh")
def runtime_refresh(
    harness: str | None = typer.Option(None, "--harness", help="Refresh one harness only"),
) -> None:
    """Invalidate cache and rebuild the runtime snapshot."""

    async def operation(mesh: JoyMesh) -> None:
        snapshot = await mesh.refresh_runtime_snapshot(harness)
        typer.echo(mesh.runtime_snapshots.format_table(snapshot), nl=False)

    _run(operation)


@delivery_app.command("intake")
def delivery_intake(
    socket: str | None = typer.Option(
        None,
        "--socket",
        help="Unix socket path (default: XDG runtime joymesh-delivery.sock)",
    ),
    store: str | None = typer.Option(
        None,
        "--store",
        help="Durable intake SQLite path",
    ),
) -> None:
    """DEPRECATED: reference/test intake only.

    Production ownership is JoyCLI:
      joyctl runtime intake-serve
    """

    import warnings

    warnings.warn(
        "joymesh delivery intake is deprecated; use `joyctl runtime intake-serve` "
        "(JoyCLI owns the canonical Unix socket receiver).",
        DeprecationWarning,
        stacklevel=1,
    )
    typer.echo(
        "DEPRECATED: use `joyctl runtime intake-serve` for production intake.",
        err=True,
    )

    async def _serve() -> None:
        from joymesh.delivery import UnixSocketDeliveryServer, default_socket_path

        path = Path(socket) if socket else default_socket_path()
        server = UnixSocketDeliveryServer(path, intake_path=store)
        await server.start()
        typer.echo(f"delivery intake listening on {path} (deprecated reference)", err=True)
        stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            await server.stop()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@delivery_app.command("health")
def delivery_health() -> None:
    """Show local delivery transport health from a JoyMesh composition root."""

    async def operation(mesh: JoyMesh) -> dict[str, object]:
        return mesh.delivery_health()

    _print(_run(operation))


@delivery_app.command("backup")
def delivery_backup(
    destination: str = typer.Option(..., "--destination", help="Empty directory for backup"),
    outbox: str | None = typer.Option(None, "--outbox", help="Outbox SQLite path"),
    include_private_key: bool = typer.Option(
        False,
        "--include-private-key",
        help="Include signing private key (explicit and dangerous)",
    ),
) -> None:
    """Backup durable delivery outbox with checksums."""

    from joymesh.delivery.backup import backup_delivery_outbox
    from joymesh.delivery.outbox import default_outbox_path
    from joymesh.production.config import load_production_config

    cfg = load_production_config()
    outbox_path = Path(outbox) if outbox else Path(cfg.outbox_path or default_outbox_path())
    key_path = Path(cfg.signing_key_path).expanduser() if cfg.signing_key_path else None
    manifest = backup_delivery_outbox(
        outbox_path=outbox_path,
        destination=Path(destination),
        signing_key_path=key_path,
        include_private_key=include_private_key,
    )
    _print(manifest.as_dict())


@delivery_app.command("restore")
def delivery_restore(
    source: str = typer.Option(..., "--source", help="Backup directory"),
    outbox: str | None = typer.Option(None, "--outbox", help="Destination outbox path"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outbox"),
) -> None:
    """Restore delivery outbox from a checksummed backup."""

    from joymesh.delivery.backup import restore_delivery_outbox
    from joymesh.delivery.outbox import default_outbox_path
    from joymesh.production.config import load_production_config

    cfg = load_production_config()
    outbox_path = Path(outbox) if outbox else Path(cfg.outbox_path or default_outbox_path())
    manifest = restore_delivery_outbox(
        backup_dir=Path(source),
        outbox_path=outbox_path,
        force=force,
    )
    _print(manifest.as_dict())


@production_app.command("validate-config")
def production_validate_config() -> None:
    """Validate production configuration without starting services."""

    from joymesh.production import validate_production_config

    result = validate_production_config()
    _print(result.as_dict())
    if not result.ok:
        raise typer.Exit(2)


@runtime_key_app.command("generate")
def runtime_key_generate(
    destination: str = typer.Option(..., "--destination", help="Private key output path"),
    key_id: str | None = typer.Option(None, "--key-id", help="Optional key id"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Generate a runtime signing keypair; never prints the private key."""

    from joymesh.delivery.key_lifecycle import generate_runtime_signing_key

    generated = generate_runtime_signing_key(
        destination=Path(destination),
        key_id=key_id,
        overwrite=overwrite,
    )
    _print(generated.as_dict(include_private=False))


@runtime_key_app.command("inspect")
def runtime_key_inspect(
    path: str = typer.Option(..., "--path", help="Private key path"),
) -> None:
    """Inspect a signing key file without printing private material."""

    from joymesh.delivery.key_lifecycle import inspect_runtime_signing_key

    _print(inspect_runtime_signing_key(Path(path)))


def _maybe_prompt_telemetry_consent() -> None:
    if consent_needed(load_user_config()):
        ensure_consent(interactive=True)


def _maybe_send_run_telemetry(run: Run, *, task: str | None = None) -> None:
    try:
        service = get_telemetry_service()
        settings = service.load_settings()
        # Never / incomplete consent: do not generate metrics for transmission.
        if (
            not settings.consent_completed
            or settings.mode is None
            or settings.mode is MetricsMode.NEVER
        ):
            return
        task_type = None
        if task:
            from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import (
                TaskAnalyzer,
            )

            task_type = TaskAnalyzer().analyse(task).task_class.value
        usage_rows = _run(lambda mesh: mesh.usage(run_id=run.id))
        usage_payload = None
        if usage_rows:
            usage_payload = {
                "input_tokens": sum(item.input_tokens for item in usage_rows),
                "output_tokens": sum(item.output_tokens for item in usage_rows),
            }
        metrics = build_metrics_from_run(
            run,
            usage=usage_payload,
            task_type=task_type,
        )
        service.maybe_send(metrics)
    except Exception:
        # Metrics must never interrupt task execution or CLI output.
        return



@node_app.command("init")
def node_init(
    private_key_path: Path | None = typer.Option(None, "--private-key-path"),  # noqa: B008
) -> None:
    """Create a local Ed25519 node key; prints only the public registration value."""

    _maybe_prompt_telemetry_consent()
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
    connector_id: str = typer.Argument(..., help="Built-in connector id (cursor|codex|opencode)"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    workspace: Path | None = typer.Option(  # noqa: B008
        None,
        "--workspace",
        help="Workspace directory for read-only certification",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    prompt: str = typer.Option(
        "Read README.md if present and return the project name without modifying files.",
        "--prompt",
    ),
    timeout_seconds: float = typer.Option(180.0, "--timeout-seconds"),
    profile: str = typer.Option("read-only", "--profile"),
) -> None:
    """Run a connector-neutral local live test via ConnectorRuntime."""

    from joymesh.runtime_v1.connectors import get_connector
    from joymesh.runtime_v1.connectors.live_test import (
        render_live_test_result,
        run_connector_live_test,
    )

    if profile != "read-only":
        raise typer.BadParameter("only --profile read-only is supported")

    try:
        connector = get_connector(connector_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    target = workspace or Path.cwd()

    async def _run() -> object:
        return await run_connector_live_test(
            connector=connector,
            workspace=target,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )

    result = asyncio.run(_run())
    assert hasattr(result, "as_dict")
    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        from joymesh.runtime_v1.connector_protocol import ConnectorLiveTestResult

        assert isinstance(result, ConnectorLiveTestResult)
        typer.echo(render_live_test_result(result))
    if not getattr(result, "certification_passed", False):
        raise typer.Exit(2)


@provider_route_app.command("list")
def provider_route_list(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List built-in provider-route managers."""

    from joymesh.runtime_v1.provider_routes import builtin_provider_route_managers

    managers = builtin_provider_route_managers()
    payload = [
        {"manager_id": item.manager_id, "display_name": item.display_name}
        for item in managers.values()
    ]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in payload:
            typer.echo(f"{item['manager_id']}\t{item['display_name']}")


@provider_route_app.command("status")
def provider_route_status(
    connector_id: str | None = typer.Argument(
        None,
        help="Optional JoyMesh connector id (e.g. opencode)",
    ),
    json_output: bool = typer.Option(False, "--json"),
    manager_id: str = typer.Option("fireconnect", "--manager-id"),
) -> None:
    """Inspect provider routes for a connector (or all supported connectors)."""

    from joymesh.runtime_v1.provider_routes import get_provider_route_manager

    try:
        manager = get_provider_route_manager(manager_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    async def _run() -> dict[str, object]:
        discovery = await manager.discover()
        auth = await manager.inspect_auth()
        routes = await manager.list_routes(connector_id)
        return {
            "manager": {"manager_id": manager.manager_id, "display_name": manager.display_name},
            "discovery": discovery.as_dict(),
            "authentication": auth.as_dict(),
            "routes": [item.as_dict() for item in routes],
        }

    payload = asyncio.run(_run())
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        auth = payload["authentication"]
        routes = payload["routes"]
        assert isinstance(auth, dict)
        assert isinstance(routes, list)
        typer.echo(f"Manager: {manager_id}\nAuth: {auth.get('status')}\nRoutes: {len(routes)}")
        for route in routes:
            assert isinstance(route, dict)
            typer.echo(
                f"  {route.get('connector_id')} provider={route.get('provider_id')} "
                f"enabled={route.get('enabled')} model={route.get('model_id')}"
            )


@provider_route_app.command("enable")
def provider_route_enable(
    manager_id: str = typer.Argument(..., help="Provider-route manager id"),
    connector_id: str = typer.Argument(..., help="JoyMesh connector id"),
    model: str | None = typer.Option(None, "--model"),
    approve: bool = typer.Option(False, "--approve", help="Required explicit approval"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Enable a provider route (mutates harness configuration; requires --approve)."""

    if not approve:
        raise typer.BadParameter("refusing to mutate provider routing without --approve")

    async def _run() -> dict[str, object]:
        from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

        service = ProviderRouteService()
        result = await service.enable_permanently(
            manager_id,
            connector_id,
            model_id=model,
        )
        return result.as_dict()

    try:
        payload = asyncio.run(_run())
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"ok={payload.get('ok')} message={payload.get('message')}")
    if not payload.get("ok"):
        raise typer.Exit(2)


@provider_route_app.command("disable")
def provider_route_disable(
    manager_id: str = typer.Argument(..., help="Provider-route manager id"),
    connector_id: str = typer.Argument(..., help="JoyMesh connector id"),
    approve: bool = typer.Option(False, "--approve", help="Required explicit approval"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Disable a provider route and restore previous configuration when supported."""

    if not approve:
        raise typer.BadParameter("refusing to mutate provider routing without --approve")

    async def _run() -> dict[str, object]:
        from joymesh.runtime_v1.provider_routes.service import ProviderRouteService

        service = ProviderRouteService()
        result = await service.disable_permanently(manager_id, connector_id)
        return result.as_dict()

    try:
        payload = asyncio.run(_run())
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(
            f"ok={payload.get('ok')} restored={payload.get('restored')} "
            f"message={payload.get('message')}"
        )
    if not payload.get("ok"):
        raise typer.Exit(2)


@provider_route_app.command("verify")
def provider_route_verify(
    manager_id: str = typer.Argument(...),
    connector_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Re-inspect and verify a provider route without mutating configuration."""

    from joymesh.runtime_v1.provider_routes import get_provider_route_manager

    try:
        manager = get_provider_route_manager(manager_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    async def _run() -> dict[str, object]:
        route = await manager.verify_route(connector_id)
        return route.as_dict()

    payload = asyncio.run(_run())
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(
            f"connector={payload.get('connector_id')} provider={payload.get('provider_id')} "
            f"enabled={payload.get('enabled')} model={payload.get('model_id')}"
        )


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


@harness_app.command("status")
def harness_status() -> None:
    """Show enabled harnesses, default, and migration state."""

    from joymesh.config import load_user_config

    prefs = load_user_config().harnesses
    payload = {
        "enabled": list(prefs.enabled),
        "default": prefs.default,
        "ask_each_run": prefs.default is None,
        "selection_required": prefs.selection_required,
        "migration_message": prefs.migration_message,
        "custom": sorted(prefs.custom),
    }
    _print(payload)


@harness_app.command("enable")
def harness_enable(harness_id: str) -> None:
    """Enable a harness for selection."""

    from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences
    from joymesh.harnesses.nonstandard import validate_custom_harness_config
    from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS

    if harness_id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
        raise typer.BadParameter(f"harness removed from production: {harness_id}")
    prefs = load_user_config().harnesses
    if harness_id in prefs.custom:
        validation = validate_custom_harness_config(prefs.custom[harness_id])
        if not validation.ok:
            for issue in validation.issues:
                typer.echo(f"{issue.code}: {issue.message}", err=True)
            raise typer.Exit(2)
    enabled = tuple(dict.fromkeys([*prefs.enabled, harness_id]))
    save_harness_preferences(
        HarnessPreferences(
            enabled=enabled,
            default=prefs.default,
            custom=dict(prefs.custom),
            selection_required=False,
            migration_message=None,
        )
    )
    typer.echo(f"enabled: {harness_id}")


@harness_app.command("disable")
def harness_disable(harness_id: str) -> None:
    """Disable a harness."""

    from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences

    prefs = load_user_config().harnesses
    enabled = tuple(item for item in prefs.enabled if item != harness_id)
    default = None if prefs.default == harness_id else prefs.default
    save_harness_preferences(
        HarnessPreferences(
            enabled=enabled,
            default=default,
            custom=dict(prefs.custom),
            selection_required=prefs.selection_required,
            migration_message=prefs.migration_message,
        )
    )
    typer.echo(f"disabled: {harness_id}")


@harness_app.command("default")
def harness_default(
    harness_id: str | None = typer.Argument(None),
    clear: bool = typer.Option(False, "--clear", help="Clear default (ask each run)"),
) -> None:
    """Set or clear the default harness."""

    from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences
    from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS

    prefs = load_user_config().harnesses
    if clear or harness_id in {None, "clear", "ask"}:
        default = None
    else:
        assert harness_id is not None
        if harness_id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
            raise typer.BadParameter(f"harness removed from production: {harness_id}")
        default = harness_id
    save_harness_preferences(
        HarnessPreferences(
            enabled=prefs.enabled,
            default=default,
            custom=dict(prefs.custom),
            selection_required=False,
            migration_message=None,
        )
    )
    typer.echo(f"default: {default or 'ask-each-run'}")


@harness_app.command("select")
def harness_select() -> None:
    """Interactively choose enabled harnesses and an optional default."""

    from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences
    from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS
    from joymesh.models import HarnessAvailability

    defs = _run_value(lambda mesh: mesh.list_harnesses())
    detected = {
        item.manifest.harness_id: item
        for item in _run(lambda mesh: mesh.detect_harnesses())
    }
    typer.echo("Choose the harnesses JoyMesh may use (comma-separated ids):")
    for definition in defs:
        if definition.id in FORBIDDEN_PRODUCTION_HARNESS_IDS:
            continue
        descriptor = detected.get(definition.id)
        ready = (
            descriptor is not None
            and descriptor.availability is HarnessAvailability.AVAILABLE
        )
        state = "ready" if ready else "not ready"
        typer.echo(f"  [ ] {definition.id:20} {definition.display_name} ({state})")
    prefs = load_user_config().harnesses
    for harness_id, custom in prefs.custom.items():
        typer.echo(f"  [ ] {harness_id:20} {custom.display_name} (custom)")
    raw = typer.prompt("Enabled harness ids", default=",".join(prefs.enabled) or "")
    enabled = tuple(
        part.strip()
        for part in raw.replace(" ", ",").split(",")
        if part.strip() and part.strip() not in FORBIDDEN_PRODUCTION_HARNESS_IDS
    )
    typer.echo("Which harness should JoyMesh use by default?")
    typer.echo("  ( ) <harness-id>")
    typer.echo("  ( ) ask   — ask each run")
    default_raw = typer.prompt("Default harness id or 'ask'", default=prefs.default or "ask")
    default = None if default_raw.strip().lower() in {"ask", "none", ""} else default_raw.strip()
    save_harness_preferences(
        HarnessPreferences(
            enabled=enabled,
            default=default,
            custom=dict(prefs.custom),
            selection_required=False,
            migration_message=None,
        )
    )
    typer.echo(f"enabled={list(enabled)} default={default or 'ask-each-run'}")


@harness_app.command("add-custom")
def harness_add_custom(
    harness_id: str = typer.Option(..., "--id"),
    display_name: str = typer.Option(..., "--name"),
    executable: str = typer.Option(..., "--executable"),
    arg: list[str] = typer.Option([], "--arg", help="Repeatable argv entry"),  # noqa: B008
    input_mode: str = typer.Option("stdin", "--input-mode"),
    output_mode: str = typer.Option("jsonl", "--output-mode"),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds"),
) -> None:
    """Define a custom harness (saved but not enabled)."""

    from joymesh.config import (
        CustomHarnessConfig,
        HarnessPreferences,
        load_user_config,
        save_harness_preferences,
    )
    from joymesh.harnesses.nonstandard import validate_custom_harness_config

    config = CustomHarnessConfig(
        harness_id=harness_id,
        display_name=display_name,
        executable=executable,
        args=tuple(arg),
        input_mode=input_mode,
        output_mode=output_mode,
        timeout_seconds=timeout_seconds,
    )
    result = validate_custom_harness_config(config)
    if not result.ok:
        for issue in result.issues:
            typer.echo(f"{issue.code}: {issue.message}", err=True)
        raise typer.Exit(2)
    prefs = load_user_config().harnesses
    custom = dict(prefs.custom)
    custom[harness_id] = config
    save_harness_preferences(
        HarnessPreferences(
            enabled=prefs.enabled,
            default=prefs.default,
            custom=custom,
            selection_required=prefs.selection_required,
            migration_message=prefs.migration_message,
        )
    )
    typer.echo(f"saved custom harness {harness_id} (not enabled)")


@harness_app.command("validate")
def harness_validate(harness_id: str) -> None:
    """Validate a custom harness configuration."""

    from joymesh.config import load_user_config
    from joymesh.harnesses.nonstandard import validate_custom_harness_config

    prefs = load_user_config().harnesses
    config = prefs.custom.get(harness_id)
    if config is None:
        raise typer.BadParameter(f"unknown custom harness: {harness_id}")
    result = validate_custom_harness_config(config)
    _print(
        {
            "ok": result.ok,
            "issues": [{"code": i.code, "message": i.message} for i in result.issues],
        }
    )
    if not result.ok:
        raise typer.Exit(2)


@harness_app.command("test")
def harness_test_custom(harness_id: str) -> None:
    """Non-destructive readiness check for a custom harness."""

    from joymesh.config import load_user_config
    from joymesh.harnesses.nonstandard import assess_custom_harness_readiness

    prefs = load_user_config().harnesses
    config = prefs.custom.get(harness_id)
    if config is None:
        raise typer.BadParameter(f"unknown custom harness: {harness_id}")
    readiness = assess_custom_harness_readiness(config)
    _print(readiness.as_dict())
    if not readiness.ready:
        raise typer.Exit(2)


@harness_app.command("remove-custom")
def harness_remove_custom(harness_id: str) -> None:
    """Remove a custom harness definition."""

    from joymesh.config import HarnessPreferences, load_user_config, save_harness_preferences

    prefs = load_user_config().harnesses
    if harness_id not in prefs.custom:
        raise typer.BadParameter(f"unknown custom harness: {harness_id}")
    custom = dict(prefs.custom)
    del custom[harness_id]
    enabled = tuple(item for item in prefs.enabled if item != harness_id)
    default = None if prefs.default == harness_id else prefs.default
    save_harness_preferences(
        HarnessPreferences(
            enabled=enabled,
            default=default,
            custom=custom,
            selection_required=prefs.selection_required,
            migration_message=prefs.migration_message,
        )
    )
    typer.echo(f"removed custom harness {harness_id}")


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
    harness: str = typer.Option("auto", "--harness", help="Harness id or 'auto'"),
) -> None:
    """Launch a run when called without a run subcommand."""

    if ctx.invoked_subcommand is not None:
        return
    if workspace is None or task is None:
        raise typer.BadParameter("--workspace and --task are required")

    _maybe_prompt_telemetry_consent()

    async def operation(mesh: JoyMesh) -> Run:
        run = await mesh.run(task=task, workspace=workspace, harness=harness)
        return await mesh.wait(run.id)

    try:
        completed = _run(operation)
    except NoRouteError as exc:
        typer.echo(str(exc), err=True)
        if getattr(exc, "remediation", None):
            typer.echo(exc.remediation, err=True)
        raise typer.Exit(2) from exc
    _print(completed)
    _maybe_send_run_telemetry(completed, task=task)


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
