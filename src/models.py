"""src/models.py — Shared data models to break circular imports.

This module contains core data models used across the pipeline:
  - Phase (imported from phase_model)
  - ProjectState dataclass
  - Unified exceptions

Import this module instead of pipeline.py to avoid circular imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from phase_model import phase_names, Phase
except ImportError:
    from src.phase_model import phase_names, Phase


__all__ = [
    "phase_names",
    "Phase",
    "ProjectState",
    "PipelineError",
    "PhaseBlockedError",
    "ProjectNotFoundError",
    "CheckpointNotFoundError",
    "ApprovalRequiredError",
]

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
