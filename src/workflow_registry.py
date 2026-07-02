"""src/workflow_registry.py — Workflow template registry and project-type detection.

Exposes:
  - WORKFLOW_TEMPLATES: dict[str, GraphTemplate] with four built-in templates.
  - detect_project_type(): auto-detect the best template for a project.
  - get_template(): retrieve a template by name.
  - list_templates(): list available template names.

Template catalogue
──────────────────
  greenfield (12 phases)
      INIT → PRD → RESEARCH → DESIGN → DESIGN_REVIEW → DECOMPOSE →
      DEVELOP → CODE_REVIEW → TEST → FIX_LOOP → ACCEPT → DEPLOY

  brownfield_feature (10 phases — skips INIT; starts with PRD_UPDATE)
      PRD_UPDATE → RESEARCH → DESIGN → DESIGN_REVIEW → DECOMPOSE →
      DEVELOP → CODE_REVIEW → TEST → ACCEPT → DEPLOY

  brownfield_fix (4 phases)
      TRIAGE → FIX → VERIFY → DEPLOY

  brownfield_audit (3 phases)
      AUDIT → REPORT → REVIEW
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from workflow_template import (
        DEFAULT_CONDITIONS,
        ConditionRule,
        GraphTemplate,
    )
except ModuleNotFoundError:
    from src.workflow_template import (
        DEFAULT_CONDITIONS,
        ConditionRule,
        GraphTemplate,
    )


__all__ = [
    "WORKFLOW_TEMPLATES",
    "detect_project_type",
    "get_template",
    "list_templates",
    "register_template",
]


# ───────────────────────────────────────────────────────────────
# Phase definitions for each template
# ───────────────────────────────────────────────────────────────

# Import REGISTRY to validate phase definitions
try:
    from registry import REGISTRY
except ImportError:
    from src.registry import REGISTRY

# NOTE: These phase lists define the structure of workflow templates.
# While individual phases should ideally be sourced from REGISTRY,
# these templates represent higher-level abstractions with specific
# phase groupings that may not directly correspond to the phases in REGISTRY.
# TODO: Align these phase names with those in REGISTRY for consistency.

# Using REGISTRY phases where available
_GREENFIELD_PHASES = [
    "INIT",
    "PRD", 
    "RESEARCH",
    "DESIGN",
    "DESIGN_REVIEW",
    "DECOMPOSE",
    "DEVELOP",
    "CODE_REVIEW",
    "TEST",
    "FIX_LOOP",
    "ACCEPT",
    "DEPLOY",
]

_BROWNFIELD_FEATURE_PHASES = [
    "PRD_UPDATE",
    "RESEARCH",
    "DESIGN",
    "DESIGN_REVIEW",
    "DECOMPOSE",
    "DEVELOP",
    "CODE_REVIEW",
    "TEST",
    "ACCEPT",
    "DEPLOY",
]

_BROWNFIELD_FIX_PHASES = [
    "TRIAGE",
    "FIX",
    "VERIFY",
    "DEPLOY",
]

_BROWNFIELD_AUDIT_PHASES = [
    "AUDIT",
    "REPORT",
    "REVIEW",
]


# ───────────────────────────────────────────────────────────────
# WORKFLOW_TEMPLATES
# ───────────────────────────────────────────────────────────────

WORKFLOW_TEMPLATES: Dict[str, GraphTemplate] = {
    "greenfield": GraphTemplate(
        name="greenfield",
        phases=list(_GREENFIELD_PHASES),
        conditions=list(DEFAULT_CONDITIONS),
        metadata={
            "description": "Full 12-phase greenfield development pipeline.",
            "phase_count": 12,
            "skips": [],
            "tags": ["new_project", "full_cycle"],
        },
    ),
    "brownfield_feature": GraphTemplate(
        name="brownfield_feature",
        phases=list(_BROWNFIELD_FEATURE_PHASES),
        conditions=list(DEFAULT_CONDITIONS),
        metadata={
            "description": "10-phase brownfield feature pipeline. Skips INIT; starts with PRD_UPDATE.",
            "phase_count": 10,
            "skips": ["INIT"],
            "tags": ["existing_codebase", "feature", "brownfield"],
        },
    ),
    "brownfield_fix": GraphTemplate(
        name="brownfield_fix",
        phases=list(_BROWNFIELD_FIX_PHASES),
        # Fix workflows use a reduced condition set: only test_failures
        conditions=[
            ConditionRule(
                name="test_failures>3",
                predicate=DEFAULT_CONDITIONS[1].predicate,
                action="insert_fix_loop",
                description="When test failures exceed 3, re-enter fix phase.",
            ),
        ],
        metadata={
            "description": "4-phase brownfield hotfix pipeline.",
            "phase_count": 4,
            "skips": ["INIT", "PRD", "PRD_UPDATE", "RESEARCH", "DESIGN", "DESIGN_REVIEW", "DECOMPOSE", "CODE_REVIEW", "ACCEPT"],
            "tags": ["hotfix", "brownfield", "urgent"],
        },
    ),
    "brownfield_audit": GraphTemplate(
        name="brownfield_audit",
        phases=list(_BROWNFIELD_AUDIT_PHASES),
        # Audit workflows use a reduced condition set: only budget_80pct
        conditions=[
            ConditionRule(
                name="budget_80pct",
                predicate=DEFAULT_CONDITIONS[2].predicate,
                action="pause",
                description="When budget reaches 80%, pause audit for approval.",
            ),
        ],
        metadata={
            "description": "3-phase brownfield audit pipeline.",
            "phase_count": 3,
            "skips": ["INIT", "PRD", "PRD_UPDATE", "RESEARCH", "DESIGN", "DESIGN_REVIEW", "DECOMPOSE", "DEVELOP", "CODE_REVIEW", "TEST", "FIX_LOOP", "ACCEPT", "DEPLOY"],
            "tags": ["audit", "brownfield", "review"],
        },
    ),
}


# ───────────────────────────────────────────────────────────────
# Registry API
# ───────────────────────────────────────────────────────────────

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

def detect_project_type(project_name: str, base_dir: Path) -> str:
    """Auto-detect the most appropriate workflow template for a project.

    Heuristics (evaluated in order):
      1. If project directory does NOT exist → "greenfield"
      2. If an ``.audit`` sentinel file is present → "brownfield_audit"
      3. If a ``.hotfix`` sentinel file is present → "brownfield_fix"
      4. If ``src/`` directory exists (existing codebase) →
         "brownfield_feature"
      5. Default: "greenfield"

    Args:
        project_name: The name of the project.
        base_dir: The base directory containing (or to contain) the project.

    Returns:
        One of "greenfield", "brownfield_feature", "brownfield_fix",
        or "brownfield_audit".
    """
    proj_dir = base_dir / project_name

    # 1. Greenfield: project directory does not exist yet
    if not proj_dir.exists():
        return "greenfield"

    # 2. Sentinel files take priority
    if (proj_dir / ".audit").exists():
        return "brownfield_audit"
    if (proj_dir / ".hotfix").exists():
        return "brownfield_fix"
    if (proj_dir / ".fix").exists():
        return "brownfield_fix"

    # 3. Detect existing codebase — look for a src/ directory
    src_dir = proj_dir / "src"
    if src_dir.exists() and src_dir.is_dir():
        # Has source code → brownfield
        return "brownfield_feature"

    # 4. Check for any *.py files (minimal codebase)
    py_files = list(proj_dir.glob("*.py"))
    if py_files:
        return "brownfield_feature"

    # 5. Fallback: treat empty directory as greenfield
    return "greenfield"
