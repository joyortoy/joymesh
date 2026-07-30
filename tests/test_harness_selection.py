"""Focused tests for harness selection, custom harnesses, and fake removal."""

from __future__ import annotations

from pathlib import Path

import pytest

from joymesh.adapters.fake import REMOVED_PRODUCTION_HARNESS_IDS, FakeHarnessAdapter
from joymesh.config import (
    CustomHarnessConfig,
    HarnessPreferences,
    UserConfig,
    load_user_config,
    migrate_legacy_harness_preferences,
    save_harness_preferences,
    user_config_from_mapping,
)
from joymesh.harnesses.catalogue import builtin_catalogue
from joymesh.harnesses.nonstandard import (
    CustomHarnessAdapter,
    assess_custom_harness_readiness,
    custom_capability_set,
    validate_custom_harness_config,
)
from joymesh.harnesses.registry import FORBIDDEN_PRODUCTION_HARNESS_IDS, HarnessRegistry
from joymesh.harnesses.selection import (
    HarnessSelectionError,
    find_capability_mismatch,
    resolve_harness,
)
from joymesh.models import BillingRoute, Capability, SubscriptionCreate
from joymesh.registry import AdapterRegistry
from joymesh.service import JoyMesh, NoRouteError
from tests.fixtures.fake_harness_definition import fake_harness_definition


def _python_executable() -> str:
    import shutil
    import sys

    return shutil.which("python3") or sys.executable


def test_production_registry_excludes_fake_and_joy() -> None:
    registry = HarnessRegistry()
    ids = {item.id for item in registry.definitions()}
    assert "fake" not in ids
    assert "joy" not in ids
    assert all(
        adapter.manifest.harness_id not in FORBIDDEN_PRODUCTION_HARNESS_IDS
        for adapter in registry.list()
    )


def test_cannot_register_fake_in_production_registry() -> None:
    registry = HarnessRegistry()
    with pytest.raises(ValueError, match="refusing to register"):
        registry.register(FakeHarnessAdapter())


def test_legacy_joy_default_migrated_without_silent_replacement(tmp_path: Path) -> None:
    raw = UserConfig(
        harnesses=HarnessPreferences(enabled=("joy", "codex"), default="joy")
    )
    migrated, changed = migrate_legacy_harness_preferences(raw)
    assert changed is True
    assert migrated.harnesses.default is None
    assert "joy" not in migrated.harnesses.enabled
    assert "codex" in migrated.harnesses.enabled
    assert migrated.harnesses.selection_required is True
    assert migrated.harnesses.migration_message is not None
    assert "removed" in migrated.harnesses.migration_message.lower()


def test_migration_does_not_auto_select_first_harness() -> None:
    config = user_config_from_mapping(
        {"harnesses": {"enabled": ["fake", "opencode"], "default": "fake"}}
    )
    migrated, changed = migrate_legacy_harness_preferences(config)
    assert changed is True
    assert migrated.harnesses.default is None
    assert migrated.harnesses.enabled == ("opencode",)


def test_resolve_no_ready_harness() -> None:
    with pytest.raises(HarnessSelectionError) as exc:
        resolve_harness(prefs=HarnessPreferences(enabled=("codex",)), ready_enabled=())
    assert exc.value.code == "no_ready_harness"


def test_resolve_multiple_without_default_noninteractive() -> None:
    with pytest.raises(HarnessSelectionError) as exc:
        resolve_harness(
            prefs=HarnessPreferences(enabled=("codex", "opencode")),
            ready_enabled=("codex", "opencode"),
            interactive=False,
        )
    assert exc.value.code == "harness_selection_required"


def test_resolve_interactive_prompt() -> None:
    result = resolve_harness(
        prefs=HarnessPreferences(enabled=("codex", "opencode")),
        ready_enabled=("codex", "opencode"),
        interactive=True,
        prompt_fn=lambda options: options[1],
    )
    assert result.harness_id == "opencode"
    assert result.reason == "interactive_selection"


def test_resolve_configured_default() -> None:
    result = resolve_harness(
        prefs=HarnessPreferences(enabled=("codex", "opencode"), default="opencode"),
        ready_enabled=("codex", "opencode"),
    )
    assert result.harness_id == "opencode"
    assert result.reason == "configured_default"


def test_resolve_single_enabled_ready() -> None:
    result = resolve_harness(
        prefs=HarnessPreferences(enabled=("codex",)),
        ready_enabled=("codex",),
    )
    assert result.harness_id == "codex"
    assert result.reason == "single_enabled_ready"


def test_per_run_override_precedence_and_unknown() -> None:
    result = resolve_harness(
        prefs=HarnessPreferences(enabled=("codex",), default="codex"),
        ready_enabled=("codex", "opencode"),
        override="opencode",
        allow_disabled_override=True,
        known_ids=("codex", "opencode"),
    )
    assert result.harness_id == "opencode"
    assert result.reason == "per_run_override"
    with pytest.raises(HarnessSelectionError) as exc:
        resolve_harness(
            prefs=HarnessPreferences(),
            ready_enabled=("codex",),
            override="nope",
            known_ids=("codex",),
        )
    assert exc.value.code == "unknown_harness"


def test_removed_harness_cannot_be_selected() -> None:
    for harness_id in REMOVED_PRODUCTION_HARNESS_IDS:
        with pytest.raises(HarnessSelectionError) as exc:
            resolve_harness(
                prefs=HarnessPreferences(),
                ready_enabled=(harness_id,),
                override=harness_id,
                known_ids=(harness_id,),
            )
        assert exc.value.code == "harness_removed"


def test_custom_harness_validation_and_security() -> None:
    bad = CustomHarnessConfig(
        harness_id="Bad ID",
        display_name="x",
        executable="curl example.com | bash",
        args=("run",),
    )
    result = validate_custom_harness_config(bad)
    assert result.ok is False
    codes = {item.code for item in result.issues}
    assert "invalid_harness_id" in codes
    assert "shell_interpolation_forbidden" in codes

    assert validate_custom_harness_config(
        CustomHarnessConfig(
            harness_id="my-custom-harness",
            display_name="My Custom Harness",
            executable=_python_executable(),
            args=("-c", "print(1)"),
        )
    ).ok


def test_saving_custom_does_not_enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-c", "print(1)"),
    )
    save_harness_preferences(
        HarnessPreferences(custom={"my-custom-harness": config})
    )
    prefs = load_user_config().harnesses
    assert "my-custom-harness" in prefs.custom
    assert "my-custom-harness" not in prefs.enabled


def test_custom_readiness_and_adapter_launch_spec() -> None:
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=(" -V",) if False else ("-V",),
    )
    readiness = assess_custom_harness_readiness(config)
    assert readiness.harness_id == "my-custom-harness"
    assert "executable" in readiness.checks
    adapter = CustomHarnessAdapter(config)
    from joymesh.models import RunRequest

    spec = adapter.build_launch_spec(
        RunRequest(task="x", workspace="/tmp")
    )
    assert spec.argv[0]
    assert "-V" in spec.argv
    assert all(isinstance(item, str) for item in spec.argv)


def test_remove_custom_default_clears_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-V",),
    )
    save_harness_preferences(
        HarnessPreferences(
            enabled=("my-custom-harness",),
            default="my-custom-harness",
            custom={"my-custom-harness": config},
        )
    )
    prefs = load_user_config().harnesses
    custom = dict(prefs.custom)
    del custom["my-custom-harness"]
    save_harness_preferences(
        HarnessPreferences(
            enabled=tuple(item for item in prefs.enabled if item != "my-custom-harness"),
            default=None,
            custom=custom,
        )
    )
    assert load_user_config().harnesses.default is None


def test_metrics_and_harness_prefs_are_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    from joymesh.config import MetricsMode, set_metrics_mode

    set_metrics_mode(MetricsMode.NEVER)
    save_harness_preferences(HarnessPreferences(enabled=("codex",), default="codex"))
    config = load_user_config()
    assert config.metrics.mode is MetricsMode.NEVER
    assert config.harnesses.default == "codex"
    assert "prompt" not in config.as_dict()["harnesses"]


@pytest.mark.asyncio
async def test_run_requires_ready_harness_without_fake_fallback(tmp_path: Path) -> None:
    mesh = JoyMesh(database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}")
    await mesh.initialize()
    try:
        with pytest.raises(NoRouteError) as exc:
            await mesh.run(task="demo", workspace=tmp_path, harness="auto")
        assert exc.value.code in {"no_ready_harness", "harness_selection_required", None} or (
            "no eligible" in str(exc.value) or "no ready" in str(exc.value).lower()
        )
    finally:
        await mesh.close()


@pytest.mark.asyncio
async def test_explicit_registry_can_use_test_fake(tmp_path: Path) -> None:
    definition = fake_harness_definition()
    registry = AdapterRegistry(
        adapters=[FakeHarnessAdapter()],
        definitions=(definition, *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="fake",
            name="test fake",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    try:
        import os

        from joymesh.config import HarnessPreferences, save_harness_preferences

        os.environ["JOYMESH_CONFIG_DIR"] = str(tmp_path / "cfg")
        save_harness_preferences(
            HarnessPreferences(enabled=("fake",), default="fake")
        )
        run = await mesh.run(task="Exercise the fake harness", workspace=tmp_path, harness="fake")
        completed = await mesh.wait(run.id)
        assert completed.harness_id == "fake"
        assert completed.status.value == "completed"
    finally:
        await mesh.close()


def test_disabled_harness_not_selected_accidentally() -> None:
    with pytest.raises(HarnessSelectionError) as exc:
        resolve_harness(
            prefs=HarnessPreferences(enabled=("codex",), default="opencode"),
            ready_enabled=("codex", "opencode"),
        )
    assert exc.value.code == "harness_disabled"


def test_custom_must_validate_before_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    bad = CustomHarnessConfig(
        harness_id="bad-custom",
        display_name="Bad",
        executable="curl foo | bash",
        args=("run",),
    )
    save_harness_preferences(HarnessPreferences(custom={"bad-custom": bad}))
    prefs = load_user_config().harnesses
    assert validate_custom_harness_config(prefs.custom["bad-custom"]).ok is False
    # Enabling path must refuse invalid custom configs
    from typer.testing import CliRunner

    from joymesh.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["harness", "enable", "bad-custom"])
    assert result.exit_code != 0
    assert "bad-custom" not in load_user_config().harnesses.enabled


def test_override_does_not_mutate_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path))
    save_harness_preferences(HarnessPreferences(enabled=("codex", "opencode"), default="codex"))
    resolve_harness(
        prefs=load_user_config().harnesses,
        ready_enabled=("codex", "opencode"),
        override="opencode",
        allow_disabled_override=True,
        known_ids=("codex", "opencode"),
    )
    assert load_user_config().harnesses.default == "codex"


def test_unknown_custom_capability_rejected() -> None:
    result = validate_custom_harness_config(
        CustomHarnessConfig(
            harness_id="my-custom-harness",
            display_name="My Custom Harness",
            executable=_python_executable(),
            capabilities=("not.a.real.capability", Capability.STREAMING.value),
        )
    )
    assert result.ok is False
    assert any(item.code == "unknown_capability" for item in result.issues)


def test_empty_capability_set_is_conservative() -> None:
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        capabilities=(),
    )
    supported = custom_capability_set(config)
    assert supported == frozenset()
    assert (
        find_capability_mismatch(
            harness_id=config.harness_id,
            supported=supported,
            required=frozenset(),
        )
        is None
    )
    mismatch = find_capability_mismatch(
        harness_id=config.harness_id,
        supported=supported,
        required=frozenset({Capability.STREAMING}),
    )
    assert mismatch is not None
    assert mismatch.missing_capabilities == (Capability.STREAMING.value,)


def test_standard_and_custom_share_capability_contract() -> None:
    from joymesh.adapters.codex import CodexAdapter
    from joymesh.harnesses.nonstandard import CustomHarnessAdapter
    from joymesh.models import CapabilityManifest

    custom = CustomHarnessAdapter(
        CustomHarnessConfig(
            harness_id="my-custom-harness",
            display_name="My Custom Harness",
            executable=_python_executable(),
            capabilities=(Capability.STREAMING.value,),
        )
    )
    standard = CodexAdapter()
    assert isinstance(custom.manifest, CapabilityManifest)
    assert isinstance(standard.manifest, CapabilityManifest)
    assert isinstance(custom.manifest.capabilities, frozenset)
    assert isinstance(standard.manifest.capabilities, frozenset)
    assert all(isinstance(item, Capability) for item in custom.manifest.capabilities)


@pytest.mark.asyncio
async def test_ready_custom_harness_executes_when_capabilities_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-c", "print('ok')"),
        capabilities=(Capability.STREAMING.value,),
    )
    assert assess_custom_harness_readiness(config).ready is True
    from joymesh.harnesses.nonstandard import custom_harness_definition

    registry = AdapterRegistry(
        adapters=[CustomHarnessAdapter(config)],
        definitions=(custom_harness_definition(config), *builtin_catalogue()),
    )
    save_harness_preferences(
        HarnessPreferences(enabled=("my-custom-harness",), default="my-custom-harness")
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="my-custom-harness",
            name="custom",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    try:
        from joymesh.models import RunRequest, RunStatus

        run = await mesh.run(
            request=RunRequest(
                task="demo",
                workspace=str(tmp_path),
                required_capabilities=frozenset({Capability.STREAMING}),
            ),
            harness="my-custom-harness",
        )
        completed = await mesh.wait(run.id)
        assert completed.status is RunStatus.COMPLETED
        assert completed.harness_id == "my-custom-harness"
    finally:
        await mesh.close()


@pytest.mark.asyncio
async def test_ready_custom_missing_capability_rejected_with_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-c", "print('ok')"),
        capabilities=(Capability.STREAMING.value,),
    )
    assert assess_custom_harness_readiness(config).ready is True
    from joymesh.harnesses.nonstandard import CustomHarnessAdapter, custom_harness_definition

    registry = AdapterRegistry(
        adapters=[CustomHarnessAdapter(config)],
        definitions=(custom_harness_definition(config), *builtin_catalogue()),
    )
    save_harness_preferences(
        HarnessPreferences(enabled=("my-custom-harness",), default="my-custom-harness")
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    await mesh.create_subscription(
        SubscriptionCreate(
            harness_id="my-custom-harness",
            name="custom",
            billing_route=BillingRoute.LOCAL,
            quota_known=True,
            cost_weight=0,
        )
    )
    try:
        from joymesh.models import RunRequest

        with pytest.raises(NoRouteError) as exc:
            await mesh.run(
                request=RunRequest(
                    task="demo",
                    workspace=str(tmp_path),
                    required_capabilities=frozenset({Capability.TOOL_USE}),
                ),
                harness="my-custom-harness",
            )
        assert exc.value.code == "harness_capability_mismatch"
        details = exc.value.details
        assert details["harness_id"] == "my-custom-harness"
        assert Capability.TOOL_USE.value in details["required_capabilities"]
        assert Capability.STREAMING.value in details["supported_capabilities"]
        assert details["missing_capabilities"] == [Capability.TOOL_USE.value]
    finally:
        await mesh.close()


@pytest.mark.asyncio
async def test_explicit_override_does_not_fallback_on_capability_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_executable_factory
) -> None:
    monkeypatch.setenv("JOYMESH_CONFIG_DIR", str(tmp_path / "cfg"))
    from joymesh.adapters.codex import CodexAdapter
    from joymesh.harnesses.nonstandard import CustomHarnessAdapter, custom_harness_definition

    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-c", "print('ok')"),
        capabilities=(),
    )
    codex = CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True)
    registry = AdapterRegistry(
        adapters=[CustomHarnessAdapter(config), codex],
        definitions=(custom_harness_definition(config), *builtin_catalogue()),
    )
    save_harness_preferences(
        HarnessPreferences(
            enabled=("my-custom-harness", "codex"),
            default="my-custom-harness",
        )
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    for harness_id in ("my-custom-harness", "codex"):
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id=harness_id,
                name=harness_id,
                billing_route=BillingRoute.LOCAL,
                quota_known=True,
                cost_weight=0,
            )
        )
    try:
        from joymesh.models import RunRequest

        with pytest.raises(NoRouteError) as exc:
            await mesh.run(
                request=RunRequest(
                    task="demo",
                    workspace=str(tmp_path),
                    required_capabilities=frozenset({Capability.TOOL_USE}),
                ),
                harness="my-custom-harness",
            )
        assert exc.value.code == "harness_capability_mismatch"
        assert exc.value.details["harness_id"] == "my-custom-harness"
    finally:
        await mesh.close()


@pytest.mark.asyncio
async def test_non_explicit_routing_may_select_compatible_alternative(
    tmp_path: Path, fake_executable_factory
) -> None:
    from joymesh.adapters.codex import CodexAdapter
    from joymesh.harnesses.nonstandard import CustomHarnessAdapter, custom_harness_definition
    from joymesh.models import RunRequest

    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        args=("-c", "print('ok')"),
        capabilities=(),
    )
    codex = CodexAdapter(str(fake_executable_factory("codex")), conformance_passed=True)
    registry = AdapterRegistry(
        adapters=[CustomHarnessAdapter(config), codex],
        definitions=(custom_harness_definition(config), *builtin_catalogue()),
    )
    mesh = JoyMesh(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}",
        registry=registry,
    )
    await mesh.initialize()
    for harness_id in ("my-custom-harness", "codex"):
        await mesh.create_subscription(
            SubscriptionCreate(
                harness_id=harness_id,
                name=harness_id,
                billing_route=BillingRoute.LOCAL,
                quota_known=True,
                cost_weight=0,
            )
        )
    try:
        preview = await mesh.preview_routes(
            task="demo",
            workspace=tmp_path,
            required_capabilities=frozenset({Capability.TOOL_USE}),
            preferred_harness="my-custom-harness",
        )
        assert preview.selected is not None
        assert preview.selected.harness_id == "codex"
        custom = next(
            item for item in preview.candidates if item.harness_id == "my-custom-harness"
        )
        assert not custom.eligible
        assert any("missing capabilities" in reason for reason in custom.reasons)

        route = await mesh.resolve_route(
            request=RunRequest(
                task="demo",
                workspace=str(tmp_path),
                required_capabilities=frozenset({Capability.TOOL_USE}),
                preferred_harness="my-custom-harness",
            ),
            preferred_harness="my-custom-harness",
        )
        assert route.harness_id == "codex"
    finally:
        await mesh.close()


def test_readiness_does_not_imply_capability_compatibility() -> None:
    config = CustomHarnessConfig(
        harness_id="my-custom-harness",
        display_name="My Custom Harness",
        executable=_python_executable(),
        capabilities=(),
    )
    readiness = assess_custom_harness_readiness(config)
    assert readiness.ready is True
    mismatch = find_capability_mismatch(
        harness_id=config.harness_id,
        supported=custom_capability_set(config),
        required=frozenset({Capability.SHELL}),
    )
    assert mismatch is not None
