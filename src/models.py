"""src/models.py — Shared data models to break circular imports.

This module contains core data models used across the pipeline:
  - Phase enum
  - ProjectState dataclass
  - Unified exceptions

Import this module instead of pipeline.py to avoid circular imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    from registry import REGISTRY
except ImportError:
    from src.registry import REGISTRY


__all__ = [
    "PHASE_NAMES",
    "Phase",
    "ProjectState",
    "PipelineError",
    "PhaseBlockedError",
    "ProjectNotFoundError",
    "CheckpointNotFoundError",
    "ApprovalRequiredError",
]
# ───────────────────────────────────────────────────────────────
# Unified exceptions
# ───────────────────────────────────────────────────────────────

class PipelineError(Exception):
    """Base exception for all pipeline errors."""
    pass


class PhaseBlockedError(PipelineError):
    """Raised when a phase transition is blocked by checks."""
    pass


class ProjectNotFoundError(PipelineError):
    """Raised when the requested project does not exist."""
    pass


class CheckpointNotFoundError(PipelineError):
    """Raised when a checkpoint is missing or corrupted."""
    pass


class ApprovalRequiredError(PipelineError):
    """Raised when an operation requires user approval."""
    pass


# ───────────────────────────────────────────────────────────────
# Phase enum
# ───────────────────────────────────────────────────────────────

# Dynamic PHASE_NAMES from REGISTRY, maintaining order for backward compatibility
# Filtering only the core phases that match the original enum mapping
def _get_core_phase_names():
    """Get the core phase names that map to the enum values, in the correct order."""
    # Define the expected order based on the enum values
    expected_order = ["init", "design", "decompose", "develop", "test", "accept", "deploy"]
    
    # Filter the registry phases to only include the expected ones
    registry_phases = set(REGISTRY.list_phases())
    core_phases = [phase for phase in expected_order if phase in registry_phases]
    
    # Ensure we have all expected phases
    for phase in expected_order:
        if phase not in registry_phases:
            # Add it anyway to maintain compatibility, though it's not in registry
            if phase not in core_phases:
                core_phases.append(phase)
    
    return core_phases

PHASE_NAMES = _get_core_phase_names()


class Phase(Enum):
    INIT = 0
    DESIGN = 1
    DECOMPOSE = 2
    DEVELOP = 3
    TEST = 4
    ACCEPT = 5
    DEPLOY = 6
    # Backward-compatible alias (F005 legacy used REVIEW)
    REVIEW = 7

    def __str__(self) -> str:
        if self.name == "REVIEW":
            return "review"
        return PHASE_NAMES[self.value]

    @classmethod
    def from_name(cls, name: str) -> Phase:
        try:
            return cls(PHASE_NAMES.index(name.lower()))
        except ValueError:
            if name.lower() == "review":
                return cls.REVIEW
            raise ValueError(f"Unknown phase: {name}")

    def next(self) -> Optional[Phase]:
        """Legacy-only: return the next phase for the old F005 3-state machine (INIT -> DEVELOP -> REVIEW -> TEST).

        This does NOT cover the full v2 phase list (DESIGN, DECOMPOSE, ACCEPT, DEPLOY).
        For new code, use explicit phase transitions instead of this helper.
        """
        if self.name == "INIT":
            return Phase.DEVELOP
        if self.name == "DEVELOP":
            return Phase.REVIEW
        if self.name == "REVIEW":
            return Phase.TEST
        return None

    def prev(self) -> Optional[Phase]:
        """Legacy-only: return the previous phase for the old F005 3-state machine."""
        if self.name == "TEST":
            return Phase.REVIEW
        if self.name == "REVIEW":
            return Phase.DEVELOP
        if self.name == "DEVELOP":
            return Phase.INIT
        return None


# ───────────────────────────────────────────────────────────────
# ProjectState dataclass
# ───────────────────────────────────────────────────────────────

@dataclass
class ProjectState:
    """Project state snapshot."""
    name: str
    phase: Phase
    description: str = ""
    stack: str = ""
    created: bool = False
    git_init: bool = False
    metadata_files: List[str] = field(default_factory=list)
    db_created: bool = False
    check_results: Dict[str, bool] = field(default_factory=dict)
    design_approved: bool = False
    accept_approved: bool = False
    tests_passed: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "phase": str(self.phase),
            "description": self.description,
            "stack": self.stack,
            "created": self.created,
            "git_init": self.git_init,
            "metadata_files": self.metadata_files,
            "db_created": self.db_created,
            "check_results": self.check_results,
            "design_approved": self.design_approved,
            "accept_approved": self.accept_approved,
            "tests_passed": self.tests_passed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectState:
        return cls(
            name=data["name"],
            phase=Phase.from_name(data["phase"]),
            description=data.get("description", ""),
            stack=data.get("stack", ""),
            created=data.get("created", False),
            git_init=data.get("git_init", False),
            metadata_files=data.get("metadata_files", []),
            db_created=data.get("db_created", False),
            check_results=data.get("check_results", {}),
            design_approved=data.get("design_approved", False),
            accept_approved=data.get("accept_approved", False),
            tests_passed=data.get("tests_passed", False),
        )
