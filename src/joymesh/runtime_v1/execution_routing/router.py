"""ExecutionRouter — validates JoyMux placement and drives execution.

Phase 3.5: production paths must not rank/select harnesses or backends.
`select` resolves already-placed IDs. Legacy ranking remains only under
JOYMESH_ALLOW_TEST_WITHOUT_PLACEMENT=1 (sunset: phase3.5-placement-required-v1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from joymesh.models import utc_now
from joymesh.placement_validation import (
    decision_from_placement,
    enforce_placement,
    extract_placement_payloads,
    test_without_placement_allowed,
)
from joymesh.runtime_v1.execution_routing.cancellation import CancellationRegistry
from joymesh.runtime_v1.execution_routing.capabilities import (
    KNOWN_HARNESSES,
    ExecutionCapability,
)
from joymesh.runtime_v1.execution_routing.capability_routing import (
    CapabilityAwareRouteSelector,
)
from joymesh.runtime_v1.execution_routing.capability_routing.policies import RoutingPolicy
from joymesh.runtime_v1.execution_routing.capability_routing.task_analysis import (
    SemanticCapability,
    TaskAnalysis,
    TaskAnalyzer,
    TaskClass,
)
from joymesh.runtime_v1.execution_routing.failures import (
    ExecutionFailureClass,
    may_fallback,
)
from joymesh.runtime_v1.execution_routing.models import (
    BackendAuditEvent,
    ExecutionAttemptRecord,
    ExecutionDecision,
    ExecutionIntent,
    ExecutionResult,
    ExecutionStatus,
)
from joymesh.runtime_v1.execution_routing.registry import BackendRegistry, BackendRegistryError


class ExecutionRouterError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class ExecutionRouter:
    """Validates JoyMux placement and resolves adapter IDs for execution.

    Deprecated: ranking/selection. Removal milestone: phase3.5-placement-required-v1.
    """

    def __init__(
        self,
        registry: BackendRegistry,
        *,
        available_harnesses: Sequence[str] | None = None,
        subscription_allows: Mapping[str, bool] | None = None,
        quota_allows: Mapping[str, bool] | None = None,
        cancellation: CancellationRegistry | None = None,
        route_selector: CapabilityAwareRouteSelector | None = None,
        available_connectors: Sequence[str] | None = None,
    ) -> None:
        self.registry = registry
        self.available_harnesses = tuple(
            available_harnesses if available_harnesses is not None else sorted(KNOWN_HARNESSES)
        )
        self.subscription_allows = dict(subscription_allows or {})
        self.quota_allows = dict(quota_allows or {})
        self.cancellation = cancellation or CancellationRegistry()
        self.route_selector = route_selector or CapabilityAwareRouteSelector()
        self.available_connectors = (
            tuple(available_connectors) if available_connectors is not None else None
        )
        self._task_analyzer = TaskAnalyzer()

    def select(self, intent: ExecutionIntent) -> ExecutionDecision:
        """Validate JoyMux placement and resolve selected adapter IDs.

        Does not rank candidates in production. Without placement, returns
        placement_required unless the explicit test bypass is enabled.
        """

        self._enforce_entitlements(intent)
        placement, requirements = extract_placement_payloads(metadata=dict(intent.metadata or {}))
        validation = enforce_placement(
            placement=placement,
            requirements=requirements,
            runtime_facts={
                "worker_alive": True,
                "runtime_alive": True,
                "available_harnesses": set(self.available_harnesses),
                "available_capabilities": {c.value for c in intent.required_capabilities},
                "quota_available": True,
                "authentication_valid": True,
                "compatible_runtimes": (
                    [placement.get("selected_runtime")]
                    if placement and placement.get("selected_runtime")
                    else []
                ),
                "runtime_snapshot_revision": (placement or {}).get("runtime_snapshot_revision"),
                "fallback_authorized": True,
            },
        )
        if validation is not None and not validation.valid:
            raise ExecutionRouterError(
                validation.reason_codes[0] if validation.reason_codes else "placement_required",
                ",".join(validation.reason_codes) or "placement_required",
            )
        if placement is not None:
            return self._decision_from_validated_placement(intent, placement)
        # Compatibility-only legacy ranking for tests.
        if not test_without_placement_allowed():
            raise ExecutionRouterError("placement_required", "JoyMux placement is required")
        return self._select_legacy_for_tests(intent)

    def _decision_from_validated_placement(
        self, intent: ExecutionIntent, placement: Mapping[str, Any]
    ) -> ExecutionDecision:
        resolved = decision_from_placement(
            execution_id=intent.execution_id,
            placement=placement,
            default_backend_id=self.registry.priority_order()[0]
            if self.registry.priority_order()
            else "local",
        )
        harness_id = str(resolved["selected_harness_id"] or intent.preferred_harness or "")
        if not harness_id:
            raise ExecutionRouterError("harness_unavailable", "placement missing selected_harness")
        if harness_id not in self.available_harnesses and self.available_harnesses:
            # Resolve by casefold match before rejecting.
            match = next(
                (h for h in self.available_harnesses if h.casefold() == harness_id.casefold()),
                None,
            )
            if match is None:
                raise ExecutionRouterError(
                    "harness_unavailable",
                    f"placed harness not available: {harness_id}",
                )
            harness_id = match
        backend_id = str(resolved["selected_backend_id"])
        # Resolve adapter by ID only — never rank.
        try:
            backend = self.registry.get(backend_id)
        except BackendRegistryError:
            # Fall back to first registered backend that supports the harness, by ID lookup order
            # (deterministic registry order), not by score.
            ordered = self.registry.priority_order()
            backend_id = ordered[0] if ordered else backend_id
            try:
                backend = self.registry.get(backend_id)
            except BackendRegistryError as exc:
                raise ExecutionRouterError("no_compatible_backend", str(exc)) from exc
        _ = backend
        analysis = self._task_analysis(intent)
        return ExecutionDecision(
            execution_id=intent.execution_id,
            selected_backend_id=backend_id,
            selected_harness_id=harness_id,
            reason="validated_joymux_placement",
            fallback_order=(),
            provider_routing_required=bool(intent.requires_provider_route),
            retry_policy={"max_fallback": 0, "on_failure": "reselect_via_joymux"},
            scores={},
            capability_match={
                "required": sorted(c.value for c in intent.required_capabilities),
                "harness_id": harness_id,
                "placement_id": placement.get("placement_id"),
            },
            policy_result={"placement_validated": True},
            quota_snapshot=dict(self.quota_allows),
            registry_revision=self.registry.revision,
            selected_connector_id=resolved.get("selected_connector_id"),
            selected_model_id=resolved.get("selected_model_id") or intent.preferred_model,
            route_score=None,
            route_candidates=(),
            task_analysis=analysis.as_dict() if hasattr(analysis, "as_dict") else {},
        )

    def _select_legacy_for_tests(self, intent: ExecutionIntent) -> ExecutionDecision:
        """Deprecated ranking path — test/compat only. Do not use in production."""

        analysis = self._task_analysis(intent)
        policy = self._routing_policy(intent)
        harness_order = self.route_selector.order_harnesses(
            available_harnesses=self.available_harnesses,
            analysis=analysis,
            policy=policy,
            preferred_harness=intent.preferred_harness,
        )
        if not harness_order:
            raise ExecutionRouterError("harness_unavailable", "no harnesses available")

        provider_needed = intent.requires_provider_route or (
            ExecutionCapability.PROVIDER_ROUTING in intent.required_capabilities
        )

        # Preferred harness first (compat); otherwise capability-ranked order.
        explore_harnesses = list(
            dict.fromkeys(
                [
                    *(
                        [intent.preferred_harness]
                        if intent.preferred_harness
                        and intent.preferred_harness in self.available_harnesses
                        else []
                    ),
                    *harness_order,
                ]
            )
        )

        chosen_harness: str | None = None
        candidates: list[tuple[str, float, str]] = []
        for harness_id in explore_harnesses:
            ranked = self._rank_backends(intent, harness_id=harness_id)
            if provider_needed:
                ranked = [
                    item
                    for item in ranked
                    if ExecutionCapability.PROVIDER_ROUTING
                    in self.registry.get(item[0]).capabilities()
                ]
            if not ranked:
                continue
            chosen_harness = harness_id
            candidates = ranked
            # Preferred is first in explore_harnesses; if it had no backends we fall through
            # to the next capable harness and stop there.
            break

        if not chosen_harness or not candidates:
            if provider_needed:
                raise ExecutionRouterError(
                    "provider_routing_unavailable",
                    "provider routing required but no backend advertises provider_routing",
                )
            raise ExecutionRouterError(
                "no_compatible_backend",
                "no enabled backend satisfies capability and policy constraints",
            )

        if intent.preferred_harness:
            select_harnesses: list[str] = [chosen_harness]
            select_backends = candidates
        else:
            select_harnesses = list(harness_order)
            select_backends = (
                self._backends_for_harnesses(intent, harness_order, provider_needed=provider_needed)
                or candidates
            )

        selection = self.route_selector.select(
            analysis=analysis,
            policy=policy,
            available_harnesses=select_harnesses,
            ranked_backends=select_backends,
            preferred_harness=intent.preferred_harness,
            available_connectors=self.available_connectors,
            subscription_by_backend=self.subscription_allows,
            quota_by_backend=self.quota_allows,
        )

        route = selection.selected
        if not intent.preferred_harness and route is not None:
            # Full capability-aware Route selection when harness is not pinned.
            selected_id = route.backend_id
            harness_id = route.harness_id
            score = route.score
            reason = (
                f"capability route {harness_id}+{selected_id}"
                f"+{route.connector_id or '-'}"
                f"+{route.model_id or '-'}: {score:.1f}"
            )
            harness_backends = self._rank_backends(intent, harness_id=harness_id)
            if provider_needed:
                harness_backends = [
                    item
                    for item in harness_backends
                    if ExecutionCapability.PROVIDER_ROUTING
                    in self.registry.get(item[0]).capabilities()
                ]
            candidates = harness_backends or candidates
            if selected_id not in {item[0] for item in candidates}:
                selected_id, score, reason = candidates[0]
                harness_id = chosen_harness
                route = next(
                    (
                        c
                        for c in selection.candidates
                        if c.eligible and c.backend_id == selected_id and c.harness_id == harness_id
                    ),
                    None,
                )
        else:
            # Preferred harness (or no scored Route): preserve backend priority /
            # fallback semantics; attach best connector/model for chosen backend.
            selected_id, score, reason = candidates[0]
            harness_id = chosen_harness
            route = next(
                (
                    c
                    for c in selection.candidates
                    if c.eligible and c.backend_id == selected_id and c.harness_id == harness_id
                ),
                None,
            )
            if route is not None:
                reason = (
                    f"{reason}; route "
                    f"{route.connector_id or '-'}/{route.model_id or '-'} "
                    f"score={route.score:.1f}"
                )

        fallback = tuple(item[0] for item in candidates if item[0] != selected_id)
        configured = [
            backend_id
            for backend_id in self.registry.fallback_order()
            if backend_id != selected_id and backend_id in {item[0] for item in candidates}
        ]
        if configured:
            ranked_rest = [item[0] for item in candidates if item[0] != selected_id]
            fallback = tuple(
                sorted(
                    ranked_rest,
                    key=lambda backend_id: (
                        configured.index(backend_id)
                        if backend_id in configured
                        else len(configured) + ranked_rest.index(backend_id)
                    ),
                )
            )

        scores = {item[0]: item[1] for item in candidates}
        if route is not None:
            scores[selected_id] = route.score

        preferred_model = intent.preferred_model
        selected_connector = route.connector_id if route else None
        selected_model = preferred_model or (route.model_id if route else None)

        return ExecutionDecision(
            execution_id=intent.execution_id,
            selected_backend_id=selected_id,
            selected_harness_id=harness_id,
            reason=reason,
            fallback_order=fallback,
            provider_routing_required=provider_needed,
            retry_policy={"max_fallback": len(fallback), "on_failure": "next_backend"},
            scores=scores,
            capability_match={
                "required": sorted(c.value for c in intent.required_capabilities),
                "required_semantic": sorted(analysis.required_semantic_values()),
                "harness_id": harness_id,
                "harness_order": list(selection.harness_order),
                "match": route.breakdown.get("capability_match") if route else None,
            },
            policy_result={
                "subscription": dict(self.subscription_allows),
                "routing_policy": policy.as_dict(),
            },
            quota_snapshot=dict(self.quota_allows),
            registry_revision=self.registry.revision,
            selected_connector_id=selected_connector,
            selected_model_id=selected_model,
            route_score=route.score if route else score,
            route_candidates=tuple(c.as_dict() for c in selection.candidates[:10] if c.eligible),
            task_analysis=analysis.as_dict(),
        )

    def _backends_for_harnesses(
        self,
        intent: ExecutionIntent,
        harness_ids: Sequence[str],
        *,
        provider_needed: bool,
    ) -> list[tuple[str, float, str]]:
        """Union of backend candidates across harnesses (best score kept per backend)."""
        best: dict[str, tuple[str, float, str]] = {}
        for harness_id in harness_ids:
            for backend_id, backend_score, reason in self._rank_backends(
                intent, harness_id=harness_id
            ):
                if provider_needed and (
                    ExecutionCapability.PROVIDER_ROUTING
                    not in self.registry.get(backend_id).capabilities()
                ):
                    continue
                prior = best.get(backend_id)
                if prior is None or backend_score > prior[1]:
                    best[backend_id] = (backend_id, backend_score, reason)
        return sorted(best.values(), key=lambda item: -item[1])

    def _task_analysis(self, intent: ExecutionIntent) -> TaskAnalysis:
        if intent.task_analysis:
            required = frozenset(
                SemanticCapability(value)
                for value in intent.required_semantic_capabilities
                if value in {item.value for item in SemanticCapability}
            )
            try:
                task_class = TaskClass(str(intent.task_class or "unknown"))
            except ValueError:
                task_class = TaskClass.UNKNOWN
            return TaskAnalysis(
                task_class=task_class,
                required_semantic=required,
                privacy_required=bool(intent.task_analysis.get("privacy_required")),
                prefers_local=bool(intent.task_analysis.get("prefers_local")),
                estimated_complexity=str(
                    intent.task_analysis.get("estimated_complexity") or "medium"
                ),
                reasons=tuple(intent.task_analysis.get("reasons") or ()),
                metadata=dict(intent.task_analysis.get("metadata") or {}),
            )
        return self._task_analyzer.analyse(intent.prompt, metadata=dict(intent.metadata))

    def _routing_policy(self, intent: ExecutionIntent) -> RoutingPolicy:
        raw = dict(intent.routing_preferences or {})
        if intent.cost_preference and "preset" not in raw:
            raw.setdefault("preset", intent.cost_preference)
        if intent.locality_preference == "local":
            raw.setdefault("prefer_local", True)
        if intent.preferred_model:
            preferred = list(raw.get("preferred_models") or [])
            if intent.preferred_model not in preferred:
                preferred.insert(0, intent.preferred_model)
            raw["preferred_models"] = preferred
        denied = intent.subscription_constraints.get("denied_harnesses") or ()
        if denied:
            raw["denied_harnesses"] = list(
                dict.fromkeys([*list(raw.get("denied_harnesses") or ()), *list(denied)])
            )
        return RoutingPolicy.from_mapping(raw)

    async def execute_with_fallback(
        self,
        intent: ExecutionIntent,
        *,
        decision: ExecutionDecision | None = None,
    ) -> ExecutionResult:
        # Production: placement required before any attempt. No JoyMesh reselection.
        placement, requirements = extract_placement_payloads(metadata=dict(intent.metadata or {}))
        precheck = enforce_placement(placement=placement, requirements=requirements)
        if precheck is not None and not precheck.valid:
            return ExecutionResult(
                ok=False,
                execution_id=intent.execution_id,
                backend_id="",
                harness_id=str((placement or {}).get("selected_harness") or ""),
                status=ExecutionStatus.BLOCKED,
                message=",".join(precheck.reason_codes),
                attempted_backends=(),
                decision=None,
                audits=(
                    {
                        "event_type": "placement.rejected",
                        "execution_id": intent.execution_id,
                        "reason": ",".join(precheck.reason_codes),
                    },
                ),
                attempts=(),
                failure_class=ExecutionFailureClass.CAPABILITY_CHANGED.value,
            )
        decision = decision or self.select(intent)
        audits: list[BackendAuditEvent] = [
            BackendAuditEvent(
                event_type="backend.selected",
                execution_id=intent.execution_id,
                backend_id=decision.selected_backend_id,
                harness_id=decision.selected_harness_id,
                reason=decision.reason,
            )
        ]
        attempts: list[ExecutionAttemptRecord] = []
        # With JoyMux placement, never chain alternate backends (no silent reroute).
        if placement is not None or decision.reason == "validated_joymux_placement":
            order = (decision.selected_backend_id,)
        else:
            order = (decision.selected_backend_id, *decision.fallback_order)
        attempted: list[str] = []
        last_error = "no backend attempted"
        last_failure = ExecutionFailureClass.UNKNOWN

        await self.cancellation.register(
            execution_id=intent.execution_id,
            backend_id=decision.selected_backend_id,
            harness_id=decision.selected_harness_id,
        )

        for backend_id in order:
            if self.cancellation.is_cancelled(intent.execution_id):
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.cancelled",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                        reason="cancelled",
                    )
                )
                return ExecutionResult(
                    ok=False,
                    execution_id=intent.execution_id,
                    backend_id=backend_id,
                    harness_id=decision.selected_harness_id,
                    status=ExecutionStatus.CANCELLED,
                    message="execution cancelled",
                    attempted_backends=tuple(attempted) or (backend_id,),
                    decision=decision,
                    audits=tuple(item.as_dict() for item in audits),
                    attempts=tuple(item.as_dict() for item in attempts),
                    failure_class=ExecutionFailureClass.CANCELLED.value,
                )

            # Re-check entitlements before each attempt.
            try:
                self._enforce_entitlements(intent, backend_id=backend_id)
            except ExecutionRouterError as exc:
                failure = _entitlement_failure(exc.reason_code)
                audits.append(
                    BackendAuditEvent(
                        event_type="execution.blocked",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        reason=exc.message,
                    )
                )
                return ExecutionResult(
                    ok=False,
                    execution_id=intent.execution_id,
                    backend_id=backend_id,
                    harness_id=decision.selected_harness_id,
                    status=ExecutionStatus.BLOCKED,
                    message=exc.message,
                    attempted_backends=tuple(attempted),
                    decision=decision,
                    audits=tuple(item.as_dict() for item in audits),
                    attempts=tuple(item.as_dict() for item in attempts),
                    failure_class=failure.value,
                )

            attempted.append(backend_id)
            attempt_id = f"execution_attempt_{uuid4().hex}"
            attempt = ExecutionAttemptRecord(
                attempt_id=attempt_id,
                execution_id=intent.execution_id,
                attempt_number=len(attempts) + 1,
                backend_id=backend_id,
                harness_id=decision.selected_harness_id,
                started_at=utc_now(),
                status="started",
            )
            try:
                backend = self.registry.get(backend_id)
            except BackendRegistryError as exc:
                attempts.append(
                    _complete_attempt(
                        attempt,
                        status="unavailable",
                        failure_class=ExecutionFailureClass.BACKEND_UNAVAILABLE.value,
                        fallback_reason=str(exc),
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.unavailable",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        reason=str(exc),
                    )
                )
                last_error = str(exc)
                last_failure = ExecutionFailureClass.BACKEND_UNAVAILABLE
                continue

            await self.cancellation.update_attempt(
                intent.execution_id,
                attempt_id=attempt_id,
                backend_id=backend_id,
                harness_id=decision.selected_harness_id,
                cancel_fn=_make_cancel_fn(backend, intent.execution_id),
            )

            health = await backend.health()
            if not health.healthy or health.state in {"disabled", "unsupported"}:
                failure = ExecutionFailureClass.BACKEND_UNHEALTHY
                attempts.append(
                    _complete_attempt(
                        attempt,
                        status="unavailable",
                        failure_class=failure.value,
                        fallback_reason=health.detail,
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.unavailable",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        reason=health.detail,
                    )
                )
                last_error = health.detail
                last_failure = failure
                if may_fallback(failure):
                    audits.append(
                        BackendAuditEvent(
                            event_type="backend.fallback",
                            execution_id=intent.execution_id,
                            backend_id=backend_id,
                            reason=health.detail,
                        )
                    )
                    continue
                break

            if not backend.supports(intent, harness_id=decision.selected_harness_id):
                failure = ExecutionFailureClass.CAPABILITY_CHANGED
                attempts.append(
                    _complete_attempt(
                        attempt,
                        status="failed",
                        failure_class=failure.value,
                        fallback_reason="capability mismatch",
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.failed",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        reason="capability mismatch",
                    )
                )
                last_error = "capability mismatch"
                last_failure = failure
                continue

            # Phase 3: JoyMesh validates JoyMux placement; never silently reroutes.
            placement_payload = None
            requirements_payload = None
            meta = dict(getattr(intent, "metadata", None) or {})
            placement_payload = meta.get("context_placement") or meta.get("placement")
            requirements_payload = meta.get("strategic_requirements") or meta.get("requirements")
            if placement_payload is not None:
                from joymesh.placement_validation import require_valid_placement

                validation = require_valid_placement(
                    requirements=requirements_payload
                    or {"requirements_id": placement_payload.get("requirements_id")},
                    placement=placement_payload,
                    runtime_facts={
                        "worker_alive": health.healthy,
                        "runtime_alive": health.healthy,
                        "available_harnesses": {decision.selected_harness_id, backend_id},
                        "available_capabilities": set(
                            getattr(intent, "required_capabilities", ()) or ()
                        ),
                        "quota_available": True,
                        "authentication_valid": True,
                        "compatible_runtimes": (
                            [placement_payload["selected_runtime"]]
                            if placement_payload.get("selected_runtime")
                            else []
                        ),
                        "runtime_snapshot_revision": placement_payload.get(
                            "runtime_snapshot_revision"
                        ),
                        "is_fallback": len(attempted) > 1,
                        "fallback_authorized": True,
                    },
                    require_placement=True,
                )
                if validation is not None and not validation.valid:
                    failure = ExecutionFailureClass.CAPABILITY_CHANGED
                    attempts.append(
                        _complete_attempt(
                            attempt,
                            status="failed",
                            failure_class=failure.value,
                            fallback_reason=",".join(validation.reason_codes),
                        )
                    )
                    audits.append(
                        BackendAuditEvent(
                            event_type="placement.rejected",
                            execution_id=intent.execution_id,
                            backend_id=backend_id,
                            harness_id=decision.selected_harness_id,
                            reason=",".join(validation.reason_codes),
                        )
                    )
                    last_error = ",".join(validation.reason_codes)
                    last_failure = failure
                    # Do not silently pick another runtime — stop this attempt chain
                    # when rejection is runtime_changed / placement_expired.
                    if any(
                        code in validation.reason_codes
                        for code in (
                            "runtime_changed",
                            "placement_expired",
                            "session_unavailable",
                            "checkpoint_unavailable",
                        )
                    ):
                        break
                    continue

            try:
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.preparing",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                    )
                )
                prepared = await backend.prepare(intent, decision)
                await backend.validate(intent, decision)
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.ready",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="execution.started",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                    )
                )
                result = await backend.execute(intent, decision, prepared=prepared)
                failure = _parse_failure(result.failure_class)
                attempts.append(
                    _complete_attempt(
                        attempt,
                        status="succeeded" if result.ok else "failed",
                        failure_class=result.failure_class,
                        usage=dict(result.usage),
                        evidence_refs=tuple(result.evidence_refs),
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.completed" if result.ok else "backend.failed",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                        reason=result.message,
                    )
                )
                if result.ok:
                    await self.cancellation.clear(intent.execution_id)
                    return ExecutionResult(
                        ok=True,
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        harness_id=decision.selected_harness_id,
                        status=ExecutionStatus.SUCCEEDED,
                        message=result.message,
                        attempted_backends=tuple(attempted),
                        decision=decision,
                        output=result.output,
                        audits=tuple(item.as_dict() for item in audits),
                        attempts=tuple(item.as_dict() for item in attempts),
                        usage=result.usage,
                        evidence_refs=result.evidence_refs,
                        verification=result.verification,
                    )
                last_error = result.message
                last_failure = failure
                if may_fallback(failure):
                    audits.append(
                        BackendAuditEvent(
                            event_type="backend.fallback",
                            execution_id=intent.execution_id,
                            backend_id=backend_id,
                            reason=result.message,
                        )
                    )
                    continue
                break
            except Exception as exc:
                last_error = str(exc)
                last_failure = ExecutionFailureClass.PREPARATION_FAILURE
                attempts.append(
                    _complete_attempt(
                        attempt,
                        status="failed",
                        failure_class=last_failure.value,
                        fallback_reason=type(exc).__name__,
                    )
                )
                audits.append(
                    BackendAuditEvent(
                        event_type="backend.failed",
                        execution_id=intent.execution_id,
                        backend_id=backend_id,
                        reason=type(exc).__name__,
                        payload={"error": str(exc)[:200]},
                    )
                )
                if may_fallback(last_failure):
                    audits.append(
                        BackendAuditEvent(
                            event_type="backend.fallback",
                            execution_id=intent.execution_id,
                            backend_id=backend_id,
                            reason=type(exc).__name__,
                        )
                    )
                else:
                    break
            finally:
                try:
                    await backend.cleanup(intent.execution_id)
                except Exception:
                    pass

        status = (
            ExecutionStatus.BLOCKED
            if last_failure
            in {
                ExecutionFailureClass.PROVIDER_RESTORE_FAILURE,
                ExecutionFailureClass.POLICY_DENIED,
                ExecutionFailureClass.WORKSPACE_VIOLATION,
                ExecutionFailureClass.BACKEND_UNAVAILABLE,
                ExecutionFailureClass.BACKEND_UNHEALTHY,
                ExecutionFailureClass.ENTITLEMENT_REQUIRED,
                ExecutionFailureClass.BACKEND_NOT_ENTITLED,
                ExecutionFailureClass.HARNESS_NOT_ENTITLED,
                ExecutionFailureClass.QUOTA_EXHAUSTED,
            }
            else ExecutionStatus.FAILED
        )
        await self.cancellation.clear(intent.execution_id)
        return ExecutionResult(
            ok=False,
            execution_id=intent.execution_id,
            backend_id=decision.selected_backend_id,
            harness_id=decision.selected_harness_id,
            status=status if attempted else ExecutionStatus.BLOCKED,
            message=f"all backends failed or unavailable: {last_error}",
            attempted_backends=tuple(attempted),
            decision=decision,
            output={},
            audits=tuple(item.as_dict() for item in audits),
            attempts=tuple(item.as_dict() for item in attempts),
            failure_class=last_failure.value,
        )

    async def cancel(self, execution_id: str) -> Mapping[str, object]:
        result = await self.cancellation.cancel(execution_id)
        return dict(result)

    def _enforce_entitlements(
        self,
        intent: ExecutionIntent,
        *,
        backend_id: str | None = None,
    ) -> None:
        constraints = intent.subscription_constraints
        if constraints.get("denied"):
            raise ExecutionRouterError(
                "entitlement_required",
                str(constraints.get("detail") or "subscription entitlement required"),
            )
        if backend_id and self.subscription_allows and backend_id in self.subscription_allows:
            if not self.subscription_allows[backend_id]:
                raise ExecutionRouterError(
                    "backend_not_entitled",
                    f"backend not entitled: {backend_id}",
                )
        if backend_id and self.quota_allows and backend_id in self.quota_allows:
            if not self.quota_allows[backend_id]:
                raise ExecutionRouterError(
                    "quota_exhausted",
                    f"quota exhausted for backend: {backend_id}",
                )
        harness = intent.preferred_harness
        denied_harnesses = constraints.get("denied_harnesses") or ()
        if harness and harness in denied_harnesses:
            raise ExecutionRouterError(
                "harness_not_entitled",
                f"harness not entitled: {harness}",
            )

    def _select_harness(self, intent: ExecutionIntent) -> str:
        if intent.preferred_harness:
            if intent.preferred_harness not in self.available_harnesses:
                raise ExecutionRouterError(
                    "harness_unavailable",
                    f"preferred harness unavailable: {intent.preferred_harness}",
                )
            return intent.preferred_harness
        if not self.available_harnesses:
            raise ExecutionRouterError("harness_unavailable", "no harnesses available")
        return self.available_harnesses[0]

    def _rank_backends(
        self,
        intent: ExecutionIntent,
        *,
        harness_id: str,
    ) -> list[tuple[str, float, str]]:
        ranked: list[tuple[str, float, str]] = []
        priority = list(self.registry.priority_order())
        for backend in self.registry.enabled():
            backend_id = backend.backend_id
            if self.subscription_allows and backend_id in self.subscription_allows:
                if not self.subscription_allows[backend_id]:
                    continue
            if not backend.supports(intent, harness_id=harness_id):
                continue
            caps = set(backend.capabilities())
            override = self.registry.override_capabilities(backend_id)
            if override is not None:
                caps |= set(override)
            missing = intent.required_capabilities - frozenset(caps)
            if missing:
                continue
            score = 100.0
            if backend_id in priority:
                score -= priority.index(backend_id) * 5
            else:
                score -= 50
            if intent.cost_preference == "cheapest" and backend_id == "local":
                score += 10
            if intent.cost_preference == "fastest" and backend_id == "fireconnect":
                score += 5
            if intent.requires_provider_route and ExecutionCapability.PROVIDER_ROUTING in caps:
                score += 20
            if intent.locality_preference == "local" and backend_id == "local":
                score += 15
            if intent.locality_preference == "remote" and backend_id == "joymesh":
                score += 15
            ranked.append((backend_id, score, f"matched capabilities via {backend_id}"))
        ranked.sort(
            key=lambda item: (
                -item[1],
                priority.index(item[0]) if item[0] in priority else 99,
            )
        )
        if self.registry.config.default_backend:
            default = self.registry.config.default_backend
            for index, item in enumerate(ranked):
                if item[0] == default:
                    ranked.insert(0, ranked.pop(index))
                    break
        return ranked


def _complete_attempt(
    attempt: ExecutionAttemptRecord,
    *,
    status: str,
    failure_class: str | None = None,
    fallback_reason: str | None = None,
    usage: Mapping[str, object] | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id=attempt.attempt_id,
        execution_id=attempt.execution_id,
        attempt_number=attempt.attempt_number,
        backend_id=attempt.backend_id,
        harness_id=attempt.harness_id,
        started_at=attempt.started_at,
        completed_at=utc_now(),
        failure_class=failure_class,
        fallback_reason=fallback_reason,
        usage=dict(usage or {}),
        evidence_refs=evidence_refs,
        status=status,
    )


def _parse_failure(value: str | None) -> ExecutionFailureClass:
    if not value:
        return ExecutionFailureClass.PROCESS_FAILURE
    try:
        return ExecutionFailureClass(value)
    except ValueError:
        return ExecutionFailureClass.UNKNOWN


def _entitlement_failure(reason_code: str) -> ExecutionFailureClass:
    mapping = {
        "entitlement_required": ExecutionFailureClass.ENTITLEMENT_REQUIRED,
        "backend_not_entitled": ExecutionFailureClass.BACKEND_NOT_ENTITLED,
        "harness_not_entitled": ExecutionFailureClass.HARNESS_NOT_ENTITLED,
        "quota_exhausted": ExecutionFailureClass.QUOTA_EXHAUSTED,
    }
    return mapping.get(reason_code, ExecutionFailureClass.POLICY_DENIED)


async def _cancel_backend(backend: object, execution_id: str) -> Mapping[str, object]:
    cancel = getattr(backend, "cancel", None)
    if callable(cancel):
        result = cancel(execution_id)
        if hasattr(result, "__await__"):
            awaited = await result
            if isinstance(awaited, Mapping):
                return dict(awaited)
    return {"cancelled": True, "execution_id": execution_id}


def _make_cancel_fn(
    backend: object, execution_id: str
) -> Callable[[], Awaitable[Mapping[str, object]]]:
    async def _cancel() -> Mapping[str, object]:
        return await _cancel_backend(backend, execution_id)

    return _cancel
