"""Task analysis — classify missions into capability requirements without selecting a route."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from joymesh.runtime_v1.execution_routing.capabilities import ExecutionCapability


class TaskClass(StrEnum):
    REPOSITORY_REFACTOR = "repository_refactor"
    INTERACTIVE_EDIT = "interactive_edit"
    BUG_FIX = "bug_fix"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    DOCUMENTATION = "documentation"
    TERMINAL_EXECUTION = "terminal_execution"
    VISION = "vision"
    CODE_REVIEW = "code_review"
    LARGE_GENERATION = "large_generation"
    QUICK_QUESTION = "quick_question"
    AUTONOMOUS_CODING = "autonomous_coding"
    PRIVATE_CODEBASE = "private_codebase"
    UNKNOWN = "unknown"


# Semantic harness/connector requirements (orthogonal to ExecutionCapability).
class SemanticCapability(StrEnum):
    REPOSITORY_EDITING = "repository_editing"
    AUTONOMOUS_CODING = "autonomous_coding"
    TERMINAL = "terminal"
    TESTING = "testing"
    LONG_RUNNING = "long_running"
    PATCH_GENERATION = "patch_generation"
    INTERACTIVE_EDITING = "interactive_editing"
    IDE_WORKFLOW = "ide_workflow"
    PAIR_PROGRAMMING = "pair_programming"
    PROVIDER_FLEXIBLE = "provider_flexible"
    OPEN_MODEL_SUPPORT = "open_model_support"
    SCRIPTING = "scripting"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"
    VISION = "vision"
    LOCAL_ONLY = "local_only"
    LOW_LATENCY = "low_latency"
    COST_SENSITIVE = "cost_sensitive"


@dataclass(frozen=True)
class TaskAnalysis:
    task_class: TaskClass
    required_semantic: frozenset[SemanticCapability]
    optional_semantic: frozenset[SemanticCapability] = frozenset()
    derived_execution_capabilities: frozenset[ExecutionCapability] = frozenset()
    privacy_required: bool = False
    prefers_local: bool = False
    estimated_complexity: str = "medium"  # low | medium | high
    reasons: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "task_class": self.task_class.value,
            "required_semantic": sorted(item.value for item in self.required_semantic),
            "optional_semantic": sorted(item.value for item in self.optional_semantic),
            "derived_execution_capabilities": sorted(
                item.value for item in self.derived_execution_capabilities
            ),
            "privacy_required": self.privacy_required,
            "prefers_local": self.prefers_local,
            "estimated_complexity": self.estimated_complexity,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }

    def required_semantic_values(self) -> list[str]:
        return sorted(item.value for item in self.required_semantic)


_RULES: tuple[tuple[re.Pattern[str], TaskClass, frozenset[SemanticCapability], frozenset[ExecutionCapability], str], ...] = (
    (
        re.compile(r"\b(refactor|large.?repo|repository.?wide|migrate codebase)\b", re.I),
        TaskClass.REPOSITORY_REFACTOR,
        frozenset(
            {
                SemanticCapability.REPOSITORY_EDITING,
                SemanticCapability.AUTONOMOUS_CODING,
                SemanticCapability.TERMINAL,
                SemanticCapability.LONG_RUNNING,
                SemanticCapability.LONG_CONTEXT,
            }
        ),
        frozenset({ExecutionCapability.FILESYSTEM}),
        "high",
    ),
    (
        re.compile(r"\b(interactive|pair.?program|in.?ide|cursor.?edit)\b", re.I),
        TaskClass.INTERACTIVE_EDIT,
        frozenset(
            {
                SemanticCapability.INTERACTIVE_EDITING,
                SemanticCapability.IDE_WORKFLOW,
                SemanticCapability.LOW_LATENCY,
            }
        ),
        frozenset({ExecutionCapability.FILESYSTEM}),
        "low",
    ),
    (
        re.compile(r"\b(bug|fix|regress|stack.?trace|crash)\b", re.I),
        TaskClass.BUG_FIX,
        frozenset(
            {
                SemanticCapability.REPOSITORY_EDITING,
                SemanticCapability.TERMINAL,
                SemanticCapability.TESTING,
                SemanticCapability.PATCH_GENERATION,
            }
        ),
        frozenset({ExecutionCapability.FILESYSTEM}),
        "medium",
    ),
    (
        re.compile(r"\b(architect|design system|system design|trade.?off)\b", re.I),
        TaskClass.ARCHITECTURE,
        frozenset({SemanticCapability.REASONING, SemanticCapability.LONG_CONTEXT}),
        frozenset(),
        "high",
    ),
    (
        re.compile(r"\b(plan|roadmap|breakdown|estimate)\b", re.I),
        TaskClass.PLANNING,
        frozenset({SemanticCapability.REASONING}),
        frozenset(),
        "medium",
    ),
    (
        re.compile(r"\b(document|readme|docs|changelog)\b", re.I),
        TaskClass.DOCUMENTATION,
        frozenset({SemanticCapability.REPOSITORY_EDITING}),
        frozenset({ExecutionCapability.FILESYSTEM}),
        "low",
    ),
    (
        re.compile(r"\b(shell|terminal|cli|command|pytest|npm test)\b", re.I),
        TaskClass.TERMINAL_EXECUTION,
        frozenset({SemanticCapability.TERMINAL, SemanticCapability.SCRIPTING}),
        frozenset({ExecutionCapability.LOCAL_PROCESS, ExecutionCapability.FILESYSTEM}),
        "medium",
    ),
    (
        re.compile(r"\b(vision|screenshot|image|diagram|ocr)\b", re.I),
        TaskClass.VISION,
        frozenset({SemanticCapability.VISION}),
        frozenset(),
        "medium",
    ),
    (
        re.compile(r"\b(code.?review|pr review|review diff)\b", re.I),
        TaskClass.CODE_REVIEW,
        frozenset({SemanticCapability.REASONING, SemanticCapability.LONG_CONTEXT}),
        frozenset({ExecutionCapability.FILESYSTEM}),
        "medium",
    ),
    (
        re.compile(r"\b(generate|scaffold|boilerplate|large.?output)\b", re.I),
        TaskClass.LARGE_GENERATION,
        frozenset({SemanticCapability.LONG_CONTEXT, SemanticCapability.AUTONOMOUS_CODING}),
        frozenset(),
        "high",
    ),
    (
        re.compile(r"\b(private|on.?prem|air.?gap|local.?only|no.?cloud)\b", re.I),
        TaskClass.PRIVATE_CODEBASE,
        frozenset({SemanticCapability.PROVIDER_FLEXIBLE, SemanticCapability.OPEN_MODEL_SUPPORT}),
        frozenset({ExecutionCapability.FILESYSTEM, ExecutionCapability.LOCAL_PROCESS}),
        "medium",
    ),
    (
        re.compile(r"\b(cheap|low.?cost|budget|open.?model|qwen)\b", re.I),
        TaskClass.AUTONOMOUS_CODING,
        frozenset(
            {
                SemanticCapability.AUTONOMOUS_CODING,
                SemanticCapability.OPEN_MODEL_SUPPORT,
                SemanticCapability.COST_SENSITIVE,
                SemanticCapability.PROVIDER_FLEXIBLE,
            }
        ),
        # Prefer provider routing as optional preference via requires_provider_route /
        # mission flags — do not hard-require it from keywords alone.
        frozenset(),
        "medium",
    ),
    (
        re.compile(r"\b(quick|simple question|what is|explain briefly)\b", re.I),
        TaskClass.QUICK_QUESTION,
        frozenset({SemanticCapability.LOW_LATENCY}),
        frozenset(),
        "low",
    ),
)


class TaskAnalyzer:
    """Deterministic keyword/heuristic classifier — no LLM required."""

    def analyse(self, prompt: str, *, metadata: dict[str, object] | None = None) -> TaskAnalysis:
        text = prompt or ""
        meta = dict(metadata or {})
        matches: list[tuple[TaskClass, frozenset[SemanticCapability], frozenset[ExecutionCapability], str, str]] = []
        for pattern, task_class, semantic, exec_caps, complexity in _RULES:
            if pattern.search(text):
                matches.append((task_class, semantic, exec_caps, complexity, pattern.pattern))

        if not matches:
            return TaskAnalysis(
                task_class=TaskClass.UNKNOWN,
                required_semantic=frozenset(),
                optional_semantic=frozenset({SemanticCapability.REASONING}),
                reasons=("no_task_class_match",),
                metadata=meta,
            )

        # Prefer highest complexity / richest capability set among matches.
        complexity_rank = {"low": 0, "medium": 1, "high": 2}
        matches.sort(key=lambda item: (complexity_rank.get(item[3], 1), len(item[1])), reverse=True)
        primary = matches[0]
        required = set(primary[1])
        derived = set(primary[2])
        optional: set[SemanticCapability] = set()
        reasons: list[str] = [f"matched:{primary[0].value}:{primary[4]}"]
        for task_class, semantic, exec_caps, complexity, pattern in matches[1:]:
            # Secondary matches contribute optional capabilities and soft execution hints.
            optional |= set(semantic) - required
            derived |= set(exec_caps)
            reasons.append(f"matched_optional:{task_class.value}:{pattern}")

        privacy = primary[0] is TaskClass.PRIVATE_CODEBASE or bool(
            re.search(r"\b(private|on.?prem|air.?gap|local.?only|no.?cloud)\b", text, re.I)
        )
        prefers_local = privacy
        if privacy:
            optional.add(SemanticCapability.LOCAL_ONLY)
        return TaskAnalysis(
            task_class=primary[0],
            required_semantic=frozenset(required),
            optional_semantic=frozenset(optional),
            derived_execution_capabilities=frozenset(derived),
            privacy_required=privacy,
            prefers_local=prefers_local,
            estimated_complexity=primary[3],
            reasons=tuple(reasons),
            metadata=meta,
        )
