"""Approval-gated install, upgrade, uninstall, and login planning."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from uuid import uuid4

from joymesh.harnesses.contracts import (
    ApprovalToken,
    CommandTemplate,
    HarnessDefinition,
    LifecycleAction,
    LifecyclePlan,
    LifecycleResult,
)
from joymesh.harnesses.discovery import DiscoveryPolicy, HarnessDiscovery
from joymesh.security import filter_environment, redact_secrets


class LifecycleApprovalError(PermissionError):
    pass


class LifecyclePlanError(RuntimeError):
    pass


class HarnessLifecycleService:
    def __init__(
        self,
        definitions: tuple[HarnessDefinition, ...],
        discovery: HarnessDiscovery,
    ) -> None:
        self._definitions = {definition.id: definition for definition in definitions}
        self._discovery = discovery

    def plan_install(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self._command_plan(harness_id, LifecycleAction.INSTALL, "install", dry_run)

    def plan_upgrade(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self._command_plan(harness_id, LifecycleAction.UPGRADE, "upgrade", dry_run)

    def plan_uninstall(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        return self._command_plan(harness_id, LifecycleAction.UNINSTALL, "uninstall", dry_run)

    def plan_login(self, harness_id: str, *, dry_run: bool = True) -> LifecyclePlan:
        definition = self._definitions[harness_id]
        method = next(
            (item for item in definition.authentication if item.login_argv is not None),
            None,
        )
        if method is None or method.login_argv is None:
            raise LifecyclePlanError(f"{harness_id} has no documented interactive login command")
        return LifecyclePlan(
            id=str(uuid4()),
            action=LifecycleAction.LOGIN,
            harness_id=harness_id,
            argv=method.login_argv,
            dry_run=dry_run,
            notes=("Interactive authentication may open a browser or prompt in the terminal.",),
        )

    async def execute(
        self,
        plan: LifecyclePlan,
        *,
        approval: ApprovalToken,
    ) -> LifecycleResult:
        self._validate_approval(plan, approval)
        self._validate_executable_plan(plan)
        if plan.dry_run:
            return LifecycleResult(plan_id=plan.id, return_code=0, stdout="", stderr="")
        executable = shutil.which(plan.argv[0])
        if executable is None:
            raise LifecyclePlanError(f"required executable is unavailable: {plan.argv[0]}")
        process = await asyncio.create_subprocess_exec(
            executable,
            *plan.argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=filter_environment(),
        )
        stdout, stderr = await process.communicate()
        self._discovery.invalidate(plan.harness_id)
        discovery = await self._discovery.discover(
            plan.harness_id,
            policy=DiscoveryPolicy(execute_version_commands=True),
        )
        installation = discovery.installations[0] if discovery.installations else None
        return LifecycleResult(
            plan_id=plan.id,
            return_code=int(process.returncode or 0),
            stdout=redact_secrets(stdout.decode(errors="replace")),
            stderr=redact_secrets(stderr.decode(errors="replace")),
            installation=installation,
        )

    def _command_plan(
        self,
        harness_id: str,
        action: LifecycleAction,
        attribute: str,
        dry_run: bool,
    ) -> LifecyclePlan:
        definition = self._definitions[harness_id]
        commands = getattr(definition, attribute)
        selected = self._select_command(commands)
        if selected is None:
            raise LifecyclePlanError(
                f"{harness_id} has no safe documented {action.value} command for this platform"
            )
        self._validate_argv(selected.argv)
        return LifecyclePlan(
            id=str(uuid4()),
            action=action,
            harness_id=harness_id,
            argv=selected.argv,
            source=selected.source,
            dry_run=dry_run,
            notes=(
                "This plan is never executed by discovery or routing.",
                "No administrator privileges will be requested automatically.",
            ),
        )

    @staticmethod
    def _select_command(commands: tuple[CommandTemplate, ...]) -> CommandTemplate | None:
        compatible = [item for item in commands if sys.platform in item.platforms]
        available = [item for item in compatible if shutil.which(item.argv[0])]
        if available:
            return available[0]
        if compatible:
            return compatible[0]
        return None

    @staticmethod
    def _validate_argv(argv: tuple[str, ...]) -> None:
        if not argv or not argv[0] or any("\x00" in item for item in argv):
            raise LifecyclePlanError("invalid lifecycle command")
        if Path(argv[0]).name in {"sh", "bash", "zsh", "cmd", "powershell", "sudo"}:
            raise LifecyclePlanError("shells and privilege escalation are not allowed")
        if any(item in {"|", "&&", ";"} for item in argv):
            raise LifecyclePlanError("shell operators are not allowed")

    @staticmethod
    def _validate_approval(plan: LifecyclePlan, approval: ApprovalToken) -> None:
        if (
            not approval.approved
            or approval.action is not plan.action
            or approval.harness_id != plan.harness_id
        ):
            raise LifecycleApprovalError("approval does not authorize this lifecycle plan")

    def _validate_executable_plan(self, plan: LifecyclePlan) -> None:
        self._validate_argv(plan.argv)
        if plan.action is LifecycleAction.INSTALL:
            expected = self.plan_install(plan.harness_id, dry_run=plan.dry_run)
        elif plan.action is LifecycleAction.UPGRADE:
            expected = self.plan_upgrade(plan.harness_id, dry_run=plan.dry_run)
        elif plan.action is LifecycleAction.UNINSTALL:
            expected = self.plan_uninstall(plan.harness_id, dry_run=plan.dry_run)
        elif plan.action is LifecycleAction.LOGIN:
            expected = self.plan_login(plan.harness_id, dry_run=plan.dry_run)
        else:
            raise LifecyclePlanError(f"{plan.action.value} is not a lifecycle executable action")
        if plan.argv != expected.argv or plan.source != expected.source:
            raise LifecyclePlanError("lifecycle plan does not match the official catalogue")
