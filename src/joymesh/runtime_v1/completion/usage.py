"""Idempotent usage finalisation for executions and attempts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from joymesh.runtime_v1.completion.models import UsageFact


class UsageFinaliser:
    def __init__(self) -> None:
        self._attempt_usage: dict[str, dict[str, Any]] = {}
        self._execution_finalised: set[str] = set()
        self._execution_aggregate: dict[str, dict[str, Any]] = {}

    def record_attempt(self, fact: UsageFact) -> Mapping[str, Any]:
        key = f"{fact.execution_id}:{fact.attempt_id}"
        if key in self._attempt_usage:
            return dict(self._attempt_usage[key])
        # Tenant ownership check.
        existing = self._execution_aggregate.get(fact.execution_id)
        if existing and existing.get("organisation_id") and fact.organisation_id:
            if existing["organisation_id"] != fact.organisation_id:
                raise PermissionError("wrong-tenant usage cannot be attached")
        row = fact.as_dict()
        self._attempt_usage[key] = row
        return dict(row)

    def finalise(
        self,
        *,
        execution_id: str,
        organisation_id: str | None,
        project_id: str | None,
        mission_id: str,
        attempt_facts: Sequence[UsageFact] | Sequence[Mapping[str, Any]] = (),
    ) -> Mapping[str, Any]:
        if execution_id in self._execution_finalised:
            return dict(self._execution_aggregate[execution_id])

        for item in attempt_facts:
            if isinstance(item, UsageFact):
                self.record_attempt(item)
            else:
                self.record_attempt(
                    UsageFact(
                        organisation_id=item.get("organisation_id"),
                        project_id=item.get("project_id"),
                        mission_id=str(item.get("mission_id") or mission_id),
                        execution_id=execution_id,
                        attempt_id=str(item.get("attempt_id") or "unknown"),
                        backend_id=str(item.get("backend_id") or "unknown"),
                        harness_id=item.get("harness_id"),
                        facts=dict(item.get("facts") or {}),
                    )
                )

        attempts = [
            value
            for key, value in self._attempt_usage.items()
            if key.startswith(f"{execution_id}:")
        ]
        wall = sum(float(item.get("facts", {}).get("wall_clock_seconds") or 0) for item in attempts)
        tokens_in = sum(int(item.get("facts", {}).get("input_tokens") or 0) for item in attempts)
        tokens_out = sum(int(item.get("facts", {}).get("output_tokens") or 0) for item in attempts)
        aggregate = {
            "execution_id": execution_id,
            "organisation_id": organisation_id,
            "project_id": project_id,
            "mission_id": mission_id,
            "attempt_count": len(attempts),
            "wall_clock_seconds": wall,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "attempts": attempts,
            "finalised": True,
        }
        self._execution_aggregate[execution_id] = aggregate
        self._execution_finalised.add(execution_id)
        return dict(aggregate)

    def is_finalised(self, execution_id: str) -> bool:
        return execution_id in self._execution_finalised
