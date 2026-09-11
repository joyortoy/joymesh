"""Derive wizard state from connector readiness and task facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from joymesh.connectors.lifecycle_models import (
    ConnectorReadiness,
    ConnectorTaskRecord,
    ConnectorTaskStatus,
    NodeConnectorState,
)
from joymesh.connectors.planning import ConnectorAction
from joymesh.control_plane.contracts import OnboardingProgress, OnboardingState

_INSTALLING_STATES = {
    NodeConnectorState.INSTALLING,
}
_AUTH_STATES = {
    NodeConnectorState.AUTHENTICATION_REQUIRED,
    NodeConnectorState.AUTHENTICATION_IN_PROGRESS,
    NodeConnectorState.AUTHENTICATION_FAILED,
}
_VERIFY_STATES = {
    NodeConnectorState.VERIFICATION_REQUIRED,
    NodeConnectorState.VERIFICATION_IN_PROGRESS,
}
_CERT_STATES = {
    NodeConnectorState.CERTIFICATION_REQUIRED,
    NodeConnectorState.CERTIFICATION_IN_PROGRESS,
}
_READY_STATES = {
    NodeConnectorState.READY,
    NodeConnectorState.ROUTING_DISABLED,
}


def derive_wizard_state(
    progress: OnboardingProgress,
    *,
    readiness: Sequence[ConnectorReadiness] = (),
    active_tasks: Sequence[ConnectorTaskRecord] = (),
    pending_plans: Sequence[Mapping[str, object]] = (),
) -> OnboardingState:
    """Repair stale frontend step strings using backend connector facts."""

    selected = set(progress.selected_harnesses)
    if not progress.node_id:
        return OnboardingState.NODE_PAIRING_REQUIRED
    if (
        progress.state
        in {
            OnboardingState.ACCOUNT_READY,
            OnboardingState.NODE_PAIRING_REQUIRED,
            OnboardingState.ENVIRONMENT_CHECK,
            OnboardingState.HARNESS_SELECTION,
        }
        and not selected
    ):
        if progress.state is not OnboardingState.NOT_STARTED:
            return progress.state
        return OnboardingState.ACCOUNT_READY

    by_id = {item.connector_id: item for item in readiness if item.connector_id in selected}
    selected_rows = [by_id[item] for item in selected if item in by_id]
    installing_tasks = [
        task
        for task in active_tasks
        if task.connector_id in selected
        and task.action == ConnectorAction.INSTALL.value
        and task.status
        in {
            ConnectorTaskStatus.QUEUED,
            ConnectorTaskStatus.RUNNING,
            ConnectorTaskStatus.WAITING_FOR_USER,
        }
    ]
    if installing_tasks or any(row.state in _INSTALLING_STATES for row in selected_rows):
        return OnboardingState.INSTALLING
    if pending_plans:
        return OnboardingState.INSTALLATION_REVIEW
    if selected and len(selected_rows) < len(selected):
        # Missing readiness rows → still need install planning/discovery.
        if progress.state in {
            OnboardingState.INSTALLATION_REVIEW,
            OnboardingState.INSTALLING,
            OnboardingState.HARNESS_SELECTION,
        }:
            return OnboardingState.INSTALLATION_REVIEW
    if any(row.state in _AUTH_STATES for row in selected_rows):
        if any(row.state is NodeConnectorState.AUTHENTICATION_IN_PROGRESS for row in selected_rows):
            return OnboardingState.VERIFYING_ACCOUNTS
        return OnboardingState.AUTHENTICATION_REQUIRED
    if any(row.state in _VERIFY_STATES for row in selected_rows):
        return OnboardingState.VERIFYING_ACCOUNTS
    if any(row.state in _CERT_STATES for row in selected_rows):
        if any(row.state is NodeConnectorState.CERTIFICATION_IN_PROGRESS for row in selected_rows):
            return OnboardingState.CERTIFYING
        return OnboardingState.CERTIFICATION_REQUIRED
    if selected and selected_rows and all(row.state in _READY_STATES for row in selected_rows):
        if progress.state in {
            OnboardingState.ROUTING_SETUP,
            OnboardingState.FIRECONNECT_SETUP,
            OnboardingState.FINAL_CHECK,
            OnboardingState.COMPLETE,
            OnboardingState.LIMITED_MODE,
        }:
            return progress.state
        return OnboardingState.ROUTING_SETUP
    if progress.state is OnboardingState.COMPLETE and selected:
        if not selected_rows or not all(row.state in _READY_STATES for row in selected_rows):
            return OnboardingState.FINAL_CHECK
    return progress.state
