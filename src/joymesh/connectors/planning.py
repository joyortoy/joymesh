"""Backend-only, hash-bound connector lifecycle planning."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from joymesh.connectors import ConnectorCatalogue
from joymesh.connectors.models import AuthenticationMethod, InstallationOption


class ConnectorAction(StrEnum):
    DISCOVER = "discover"
    INSTALL = "install"
    UPGRADE = "upgrade"
    UNINSTALL = "uninstall"
    REPAIR = "repair"
    AUTHENTICATE = "authenticate"
    VERIFY_AUTHENTICATION = "verify_authentication"
    VERIFY_ADAPTER = "verify_adapter"
    CERTIFY = "certify"
    ENABLE_ROUTING = "enable_routing"
    DISABLE_ROUTING = "disable_routing"


class ConnectorPlanError(ValueError):
    pass


class ConnectorTaskPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str
    node_id: str
    connector_id: str
    connector_revision: str
    action: ConnectorAction
    method_id: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str | None = None
    environment_changes: dict[str, str] = Field(default_factory=dict)
    package_source: str
    expected_executables: tuple[str, ...]
    expected_version_pattern: str = r".+"
    requires_admin: bool = False
    modifies_path: bool = False
    modifies_shell_profile: bool = False
    download_digest: str | None = None
    risk_level: str = "medium"
    expires_at: datetime
    plan_hash: str
    approved: bool = False


class ConnectorTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    plan_id: str
    node_id: str
    connector_id: str
    action: ConnectorAction
    status: str
    created_at: datetime
    detail: str | None = None


class ConnectorPlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, ConnectorTaskPlan] = {}
        self._tasks: dict[str, ConnectorTask] = {}

    def put(self, plan: ConnectorTaskPlan) -> ConnectorTaskPlan:
        self._plans[plan.plan_id] = plan
        return plan

    def get(self, plan_id: str) -> ConnectorTaskPlan:
        try:
            return self._plans[plan_id]
        except KeyError as exc:
            raise KeyError(f"unknown or expired connector plan: {plan_id}") from exc

    def create_task(self, plan: ConnectorTaskPlan) -> ConnectorTask:
        task = ConnectorTask(
            task_id=str(uuid4()),
            plan_id=plan.plan_id,
            node_id=plan.node_id,
            connector_id=plan.connector_id,
            action=plan.action,
            status="queued_for_node",
            created_at=datetime.now(UTC),
        )
        self._tasks[task.task_id] = task
        return task

    def task(self, task_id: str) -> ConnectorTask:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown connector task: {task_id}") from exc

    def update_task(self, task_id: str, *, status: str, detail: str | None = None) -> ConnectorTask:
        current = self.task(task_id)
        updated = current.model_copy(update={"status": status, "detail": detail})
        self._tasks[task_id] = updated
        return updated


class ConnectorPlanner:
    def __init__(
        self,
        catalogue: ConnectorCatalogue | None = None,
        store: ConnectorPlanStore | None = None,
    ) -> None:
        self.catalogue = catalogue or ConnectorCatalogue.builtins()
        self.store = store or ConnectorPlanStore()

    def plan(
        self,
        *,
        node_id: str,
        connector_id: str,
        action: ConnectorAction,
        method_id: str | None = None,
        platform: str | None = None,
        download_digest: str | None = None,
    ) -> ConnectorTaskPlan:
        definition = self.catalogue.get(connector_id)
        selected_platform = platform or sys.platform
        if selected_platform not in definition.supported_platforms:
            raise ConnectorPlanError(
                f"{connector_id} is unavailable on platform {selected_platform}"
            )
        if action is ConnectorAction.AUTHENTICATE:
            method = self._authentication(definition.authentication_methods, method_id)
            if not method.login_argv:
                raise ConnectorPlanError(
                    f"{connector_id} authentication requires local user configuration"
                )
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id=method.id,
                argv=method.login_argv,
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
            )
        if action is ConnectorAction.DISCOVER:
            argv: tuple[str, ...]
            if definition.executable_names:
                argv = (definition.executable_names[0], "--version")
            else:
                argv = ("joymesh", "connector", "discover", connector_id)
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id="discover",
                argv=argv,
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
                risk_level="low",
            )
        if action is ConnectorAction.VERIFY_AUTHENTICATION:
            method = self._authentication(definition.authentication_methods, method_id)
            status_argv = method.status_argv or method.login_argv
            if not status_argv:
                raise ConnectorPlanError(f"{connector_id} has no authentication status command")
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id=method.id,
                argv=status_argv,
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
                risk_level="low",
            )
        if action is ConnectorAction.VERIFY_ADAPTER:
            if not definition.executable_names:
                raise ConnectorPlanError(
                    f"{connector_id} has no executable for adapter verification"
                )
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id="adapter-conformance",
                argv=(definition.executable_names[0], "--help"),
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
                risk_level="low",
            )
        if action is ConnectorAction.REPAIR:
            options = definition.installation_options or definition.upgrade_options
            option = self._installation(options, method_id, selected_platform)
            if not option.executable:
                raise ConnectorPlanError(
                    f"{option.id} must be fetched and digest-bound by the JoyMesh Node first"
                )
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id=option.id,
                argv=option.argv,
                package_source=option.package_source,
                expected_executables=definition.executable_names,
                risk_level="medium",
            )
        if action in {ConnectorAction.ENABLE_ROUTING, ConnectorAction.DISABLE_ROUTING}:
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id=action.value,
                argv=("joymesh", "connector", action.value.replace("_", "-"), connector_id),
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
                risk_level="low",
            )
        if action is ConnectorAction.CERTIFY:
            if not definition.certification_profile_id:
                raise ConnectorPlanError(f"{connector_id} has no certification profile")
            return self._create(
                node_id=node_id,
                connector_id=connector_id,
                revision=definition.revision,
                action=action,
                method_id=definition.certification_profile_id,
                argv=("joymesh", "connector", "certify", connector_id),
                package_source=str(definition.official_source.documentation_source),
                expected_executables=definition.executable_names,
                risk_level="high",
            )
        install_actions = {
            ConnectorAction.INSTALL: definition.installation_options,
            ConnectorAction.UPGRADE: definition.upgrade_options,
            ConnectorAction.UNINSTALL: definition.uninstall_options,
        }
        if action not in install_actions:
            raise ConnectorPlanError(f"unsupported connector action: {action.value}")
        options = install_actions[action]
        option = self._installation(options, method_id, selected_platform)
        if not option.executable:
            raise ConnectorPlanError(
                f"{option.id} must be fetched and digest-bound by the JoyMesh Node first"
            )
        if option.digest_required and not download_digest:
            raise ConnectorPlanError("download digest is required for this installation method")
        return self._create(
            node_id=node_id,
            connector_id=connector_id,
            revision=definition.revision,
            action=action,
            method_id=option.id,
            argv=option.argv,
            package_source=option.package_source,
            expected_executables=definition.executable_names,
            requires_admin=option.requires_admin,
            modifies_path=option.modifies_path,
            modifies_shell_profile=option.modifies_shell_profile,
            download_digest=download_digest,
        )

    def validate(self, plan: ConnectorTaskPlan) -> None:
        stored = self.store.get(plan.plan_id)
        if stored.plan_hash != plan.plan_hash or stored != plan:
            raise ConnectorPlanError("connector plan does not match the backend-generated plan")
        if plan.expires_at <= datetime.now(UTC):
            raise ConnectorPlanError("connector plan has expired")

    def _create(
        self,
        *,
        node_id: str,
        connector_id: str,
        revision: str,
        action: ConnectorAction,
        method_id: str,
        argv: tuple[str, ...],
        package_source: str,
        expected_executables: tuple[str, ...],
        requires_admin: bool = False,
        modifies_path: bool = False,
        modifies_shell_profile: bool = False,
        download_digest: str | None = None,
        risk_level: str = "medium",
    ) -> ConnectorTaskPlan:
        if not argv or not argv[0]:
            raise ConnectorPlanError("connector plan has no executable")
        plan_id = str(uuid4())
        expires_at = datetime.now(UTC) + timedelta(minutes=15)
        payload = {
            "plan_id": plan_id,
            "node_id": node_id,
            "connector_id": connector_id,
            "revision": revision,
            "action": action.value,
            "method_id": method_id,
            "argv": argv,
            "package_source": package_source,
            "digest": download_digest,
            "expires_at": expires_at.isoformat(),
        }
        plan_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.store.put(
            ConnectorTaskPlan(
                plan_id=plan_id,
                node_id=node_id,
                connector_id=connector_id,
                connector_revision=revision,
                action=action,
                method_id=method_id,
                executable=argv[0],
                arguments=argv[1:],
                package_source=package_source,
                expected_executables=expected_executables,
                requires_admin=requires_admin,
                modifies_path=modifies_path,
                modifies_shell_profile=modifies_shell_profile,
                download_digest=download_digest,
                risk_level=risk_level,
                expires_at=expires_at,
                plan_hash=plan_hash,
            )
        )

    @staticmethod
    def _installation(
        options: tuple[InstallationOption, ...],
        method_id: str | None,
        platform: str,
    ) -> InstallationOption:
        compatible = [item for item in options if platform in item.platforms]
        if method_id:
            compatible = [item for item in compatible if item.id == method_id]
        if not compatible:
            raise ConnectorPlanError("no reviewed lifecycle method matches this platform")
        return compatible[0]

    @staticmethod
    def _authentication(
        methods: tuple[AuthenticationMethod, ...],
        method_id: str | None,
    ) -> AuthenticationMethod:
        compatible = list(methods)
        if method_id:
            compatible = [item for item in compatible if item.id == method_id]
        if not compatible:
            raise ConnectorPlanError("no reviewed authentication method matches")
        return compatible[0]
