"""src/workflow.py — Unified workflow template registry and graph templates.

This module merges the former ``workflow_registry.py`` and ``workflow_template.py``
into a single, registry-driven workflow layer.

Public API:
  - GraphTemplate / WorkflowTemplate / ConditionRule
  - DEFAULT_CONDITIONS / evaluate_conditions
  - build_workflows() / WORKFLOW_TEMPLATES
  - get_template(name) / list_templates() / register_template(template)
  - detect_project_type(project_dir)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from config import get_config
except ModuleNotFoundError:
    from src.config import get_config

try:
    from registry import REGISTRY
except ModuleNotFoundError:
    from src.registry import REGISTRY


__all__ = [
    "GraphTemplate",
    "WorkflowTemplate",
    "ConditionRule",
    "DEFAULT_CONDITIONS",
    "evaluate_conditions",
    "WORKFLOW_TEMPLATES",
    "build_workflows",
    "get_template",
    "list_templates",
    "register_template",
    "detect_project_type",
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
        name: Unique template name (e.g. "greenfield", "brownfield").
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

    def validate(self) -> None:
        """Validate that all referenced phases exist in the registry.

        Raises:
            ValueError: If any phase is not registered in ``REGISTRY.phases``.
        """
        missing = [p for p in self.phases if p not in REGISTRY.phases]
        if missing:
            raise ValueError(
                f"Workflow {self.name!r} references unknown phases: {missing}"
            )


# Backward-compatible alias used by some callers.
WorkflowTemplate = GraphTemplate


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
        except (
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            OSError,
            ConnectionError,
            TimeoutError,
            ImportError,
            AttributeError,
        ):
            # Silently skip malformed predicates
            continue
    return triggered


# ───────────────────────────────────────────────────────────────
# Workflow template registry
# ───────────────────────────────────────────────────────────────

def build_workflows() -> Dict[str, GraphTemplate]:
    """Build the canonical greenfield and brownfield workflow templates.

    Phase order is sourced from ``config.py`` so a single configuration change
    propagates to both the workflow layer and ``PhaseFlow``.
    """
    cfg = get_config()

    return {
        "greenfield": GraphTemplate(
            name="greenfield",
            phases=list(cfg.greenfield_phase_order),
            conditions=list(DEFAULT_CONDITIONS),
            metadata={
                "description": "Full greenfield development pipeline.",
                "phase_count": len(cfg.greenfield_phase_order),
                "tags": ["new_project", "full_cycle"],
            },
        ),
        "brownfield": GraphTemplate(
            name="brownfield",
            phases=list(cfg.brownfield_phase_order),
            conditions=list(DEFAULT_CONDITIONS),
            metadata={
                "description": "Brownfield optimization pipeline (unified 7-phase chain).",
                "phase_count": len(cfg.brownfield_phase_order),
                "tags": ["existing_codebase", "brownfield"],
            },
        ),
    }


WORKFLOW_TEMPLATES: Dict[str, GraphTemplate] = build_workflows()


def get_template(name: str) -> Optional[GraphTemplate]:
    """Return the GraphTemplate for *name*, or None if not found."""
    return WORKFLOW_TEMPLATES.get(name)


def list_templates() -> List[str]:
    """Return a sorted list of registered template names."""
    return sorted(WORKFLOW_TEMPLATES.keys())


def register_template(template: GraphTemplate) -> None:
    """Register (or overwrite) a workflow template at runtime."""
    WORKFLOW_TEMPLATES[template.name] = template


# ───────────────────────────────────────────────────────────────
# Project-type auto-detection
# ───────────────────────────────────────────────────────────────

def detect_project_type(
    project_dir_or_name: str | Path, base_dir: Optional[Path] = None
) -> str:
    """Auto-detect the most appropriate workflow template for a project.

    Supports two signatures for backward compatibility:
      - detect_project_type(project_dir) -> "greenfield" | "brownfield"
      - detect_project_type(project_name, base_dir) -> "greenfield" | "brownfield"

    Heuristics (evaluated in order):
      1. If project directory does NOT exist and no base_dir given -> "greenfield"
      2. Sentinel files ``.audit`` / ``.hotfix`` / ``.fix`` -> "brownfield"
      3. Existing ``src/`` directory with Python files -> "brownfield"
      4. Existing ``docs/audit-*.md`` reports -> "brownfield"
      5. ``features.json`` with passed features -> "brownfield"
      6. Default -> "greenfield"

    Returns:
        One of "greenfield" or "brownfield".
    """
    if base_dir is not None:
        proj_dir = Path(base_dir) / str(project_dir_or_name)
    else:
        candidate = Path(project_dir_or_name)
        # If the argument looks like an existing path or contains path
        # separators, treat it as a project directory.
        if candidate.exists() or candidate.parent != Path("."):
            proj_dir = candidate
        else:
            # Legacy/fallback: treat as a project name under the current dir.
            proj_dir = Path(".") / str(project_dir_or_name)

    proj_dir = proj_dir.resolve()

    # 1. Greenfield: project directory does not exist yet
    if not proj_dir.exists():
        return "greenfield"

    # 2. Sentinel files take priority
    if any((proj_dir / sentinel).exists() for sentinel in (".audit", ".hotfix", ".fix")):
        return "brownfield"

    # 3+. Delegate to config mode detection, which looks at src/, audit docs,
    # and features.json passed status.
    try:
        detected = get_config().detect_mode(proj_dir)
        if detected == "brownfield":
            return "brownfield"
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ImportError, AttributeError):
        pass

    # Fallback: treat empty directory as greenfield
    return "greenfield"
