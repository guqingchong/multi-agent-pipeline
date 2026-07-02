"""src/workflow_template.py — GraphTemplate dataclass and phase definitions.

Defines the GraphTemplate dataclass used by workflow_registry to describe
a named workflow: its ordered phases and dynamic conditions that can
trigger branching behaviors during pipeline execution.

Conditions supported:
  - code_lines>500  → trigger_deep_review
  - test_failures>3 → insert_fix_loop
  - budget_80pct    → pause

This module is imported by workflow_registry.py to build the
WORKFLOW_TEMPLATES dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


__all__ = [
    "GraphTemplate",
    "ConditionRule",
    "DEFAULT_CONDITIONS",
    "evaluate_conditions",
]


# ───────────────────────────────────────────────────────────────
# ConditionRule
# ───────────────────────────────────────────────────────────────

@dataclass
class ConditionRule:
    """A single dynamic condition evaluated against project state.

    Attributes:
        name: Human-readable condition name (e.g. "code_lines>500").
        predicate: Callable receiving a state dict and returning True/False.
        action: The action to take when the condition fires
                (e.g. "trigger_deep_review", "insert_fix_loop", "pause").
        description: Explanation of what this rule does.
    """
    name: str
    predicate: Callable[[Dict[str, Any]], bool]
    action: str
    description: str = ""


# ───────────────────────────────────────────────────────────────
# Built-in condition predicates
# ───────────────────────────────────────────────────────────────

def _cond_code_lines_gt_500(state: Dict[str, Any]) -> bool:
    """Fire when total code lines across source files exceed 500."""
    return state.get("code_lines", 0) > 500


def _cond_test_failures_gt_3(state: Dict[str, Any]) -> bool:
    """Fire when test failures exceed 3."""
    return state.get("test_failures", 0) > 3


def _cond_budget_80pct(state: Dict[str, Any]) -> bool:
    """Fire when budget consumption reaches or exceeds 80%."""
    budget_total = state.get("budget_total", 0)
    budget_spent = state.get("budget_spent", 0)
    if budget_total <= 0:
        return False
    return (budget_spent / budget_total) >= 0.8


def _cond_journey_score_low(state: Dict[str, Any]) -> bool:
    """Fire when journey validation score below 60."""
    return state.get("journey_score", 100) < 60


# ───────────────────────────────────────────────────────────────
# Default condition rules
# ───────────────────────────────────────────────────────────────

DEFAULT_CONDITIONS: List[ConditionRule] = [
    ConditionRule(
        name="code_lines>500",
        predicate=_cond_code_lines_gt_500,
        action="trigger_deep_review",
        description="When total code lines exceed 500, insert a deep review phase.",
    ),
    ConditionRule(
        name="test_failures>3",
        predicate=_cond_test_failures_gt_3,
        action="insert_fix_loop",
        description="When test failures exceed 3, insert a fix-loop before proceeding.",
    ),
    ConditionRule(
        name="budget_80pct",
        predicate=_cond_budget_80pct,
        action="pause",
        description="When budget reaches 80%, pause pipeline for human approval.",
    ),
    ConditionRule(
        name="journey_score<60",
        predicate=_cond_journey_score_low,
        action="redesign_journey",
        description="When journey validation score below 60, trigger redesign before advancing.",
    ),
]


# ───────────────────────────────────────────────────────────────
# GraphTemplate
# ───────────────────────────────────────────────────────────────

@dataclass
class GraphTemplate:
    """Describes a named workflow template with ordered phases and
    dynamic conditions.

    Attributes:
        name: Unique template name (e.g. "greenfield", "brownfield_fix").
        phases: Ordered list of phase names defining the pipeline DAG.
        conditions: List of ConditionRule objects evaluated at runtime
                    to trigger branching (deep review, fix loop, pause).
        metadata: Arbitrary extra metadata (description, tags, etc.).
    """
    name: str
    phases: List[str]
    conditions: List[ConditionRule] = field(default_factory=lambda: list(DEFAULT_CONDITIONS))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"GraphTemplate(name={self.name!r}, phases={self.phases!r}, "
            f"condition_count={len(self.conditions)})"
        )

    def phase_count(self) -> int:
        """Return the number of phases in this template."""
        return len(self.phases)

    def has_phase(self, phase_name: str) -> bool:
        """Check whether a given phase name exists in this template."""
        return phase_name in self.phases

    def index_of(self, phase_name: str) -> int:
        """Return the 0-based index of phase_name, or -1 if absent."""
        try:
            return self.phases.index(phase_name)
        except ValueError:
            return -1

    def phases_between(self, start: str, end: str) -> List[str]:
        """Return the list of phases between start and end (exclusive)."""
        i = self.index_of(start)
        j = self.index_of(end)
        if i == -1 or j == -1 or i >= j:
            return []
        return self.phases[i + 1 : j]


# ───────────────────────────────────────────────────────────────
# Condition evaluation helper
# ───────────────────────────────────────────────────────────────

def evaluate_conditions(
    template: GraphTemplate, state: Dict[str, Any]
) -> List[ConditionRule]:
    """Evaluate all conditions in a template against a state dict.

    Args:
        template: The GraphTemplate whose conditions to evaluate.
        state: A dict of runtime state (code_lines, test_failures,
               budget_total, budget_spent, etc.).

    Returns:
        A list of ConditionRules that fired (predicate returned True).
        An empty list means no conditions triggered.
    """
    triggered: List[ConditionRule] = []
    for rule in template.conditions:
        try:
            if rule.predicate(state):
                triggered.append(rule)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
            # Silently skip malformed predicates
            continue
    return triggered
