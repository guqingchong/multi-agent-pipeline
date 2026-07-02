#!/usr/bin/env python3
"""pipeline.py — Unified pipeline CLI.

Phase flow is fully registry-driven.  Available phases and choices come from
``REGISTRY.list_phases()`` and ordered transitions come from
``phase_model.Phase``.  The legacy 3-state compatibility layer has been
removed; use the real 12-phase chain (or brownfield 7-phase chain) via
``phase_flow.PhaseFlow``.

Commands:
  init              Create project skeleton
  check             Check current phase conditions
  advance           Advance to next phase
  status            Show project status
  resume            Resume project from checkpoint
  rollback          Rollback to specific checkpoint
  rollback-phase    Rollback to a specific phase (requires approval)
  approve           Manual approval for design / accept
  mark-tests        Mark end-to-end test status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from models import ProjectState, Phase
except ModuleNotFoundError:
    from src.models import ProjectState, Phase

try:
    from config import get_config
except (ModuleNotFoundError, ImportError):
    from src.config import get_config

try:
    from state_store import StateStore, CheckpointRecord
except ModuleNotFoundError:
    from src.state_store import StateStore, CheckpointRecord

try:
    from phase_flow import (
        PhaseFlow,
        init_project,
        phase_check,
        phase_advance,
        phase_rollback,
        phase_approve_design,
        phase_approve_accept,
        phase_mark_tests,
    )
except ModuleNotFoundError:
    from src.phase_flow import (
        PhaseFlow,
        init_project,
        phase_check,
        phase_advance,
        phase_rollback,
        phase_approve_design,
        phase_approve_accept,
        phase_mark_tests,
    )

try:
    from registry import REGISTRY
except ModuleNotFoundError:
    from src.registry import REGISTRY


# ───────────────────────────────────────────────────────────────
# Constants / Config
# ───────────────────────────────────────────────────────────────

DB_FILENAME = get_config().db_name


def _phase_choices() -> List[str]:
    """Return registry-driven phase names for argparse choices."""
    return REGISTRY.list_phases()


def _get_base_dir() -> Path:
    """Return the projects base directory.

    Priority:
      1. MULTI_AGENT_PIPELINE_BASE_DIR environment variable
      2. Current working directory
    """
    env_base = os.environ.get("MULTI_AGENT_PIPELINE_BASE_DIR")
    if env_base:
        return Path(env_base)
    return Path.cwd()


def _get_db_path(base_dir: Path, project_name: str) -> Path:
    return base_dir / project_name / DB_FILENAME


def _get_store(base_dir: Path, project_name: str) -> StateStore:
    return StateStore(_get_db_path(base_dir, project_name))


def _write_checkpoint(store: StateStore, project_name: str, state: ProjectState, action: str) -> int:
    """Write a checkpoint after every meaningful action."""
    return store.write_checkpoint(
        project_id=project_name,
        phase=str(state.phase),
        state_dict=state.to_dict(),
        agent="pipeline",
        action=action,
        result="ok",
    )


def _save_state(store: StateStore, project_name: str, state: ProjectState, action: str) -> None:
    """Save state to legacy table + write checkpoint."""
    store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))
    _write_checkpoint(store, project_name, state, action)


def _load_state(store: StateStore) -> Optional[ProjectState]:
    """Load state from legacy table."""
    raw = store.legacy_load("state")
    if raw is None:
        return None
    return ProjectState.from_dict(json.loads(raw))


# ───────────────────────────────────────────────────────────────
# Command implementations
# ───────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    """Create project skeleton."""
    project_name: str = args.project
    description: str = args.description or ""
    stack: str = args.stack or ""

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if proj_dir.exists() and not args.force:
        print(f"[ERROR] Project directory already exists: {proj_dir}")
        return 1

    state, metadata_files, git_init = init_project(
        project_name=project_name,
        base_dir=base_dir,
        description=description,
        stack=stack,
    )

    print(f"[OK] Project '{project_name}' initialized")
    print(f"     Directory: {base_dir}")
    print(f"     Phase: {state.phase}")
    print(f"     Metadata files: {', '.join(metadata_files)}")
    print(f"     Git: {'initialized' if git_init else 'init failed'}")
    print(f"     DB: {base_dir / project_name / DB_FILENAME}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Check whether current phase meets advance conditions."""
    project_name: str = args.project
    base_dir = _get_base_dir()

    passed, msg = phase_check(project_name, base_dir)
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] check: {msg}")
    return 0 if passed else 1


def cmd_advance(args: argparse.Namespace) -> int:
    """Advance to next phase (auto check, BLOCK if not passed)."""
    project_name: str = args.project
    base_dir = _get_base_dir()

    passed, msg = phase_advance(project_name, base_dir)
    if passed:
        print(f"[OK] {msg}")
        return 0
    print(f"[BLOCKED] {msg}")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show project status."""
    project_name: str = args.project
    base_dir = _get_base_dir()
    store = _get_store(base_dir, project_name)
    state = _load_state(store)
    if state is None:
        print(f"[ERROR] Project does not exist: {project_name}")
        return 1
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume project state from checkpoint."""
    project_name: str = args.project
    checkpoint_id: Optional[int] = getattr(args, "checkpoint_id", None)

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    db_path = _get_db_path(base_dir, project_name)
    if not db_path.exists():
        print(f"[ERROR] Database does not exist: {db_path}")
        return 1

    store = _get_store(base_dir, project_name)

    if checkpoint_id is not None:
        cp = store.get_checkpoint(checkpoint_id)
        if cp is None:
            print(f"[ERROR] checkpoint {checkpoint_id} does not exist")
            return 1
    else:
        cp = store.get_latest_checkpoint(project_name)
        if cp is None:
            print(f"[ERROR] Project has no checkpoint, cannot resume")
            return 1

    state_dict = store.restore_checkpoint(cp.id)
    if state_dict is None:
        print(f"[ERROR] checkpoint {cp.id} state is empty")
        return 1

    state = ProjectState.from_dict(state_dict)
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))
    _write_checkpoint(store, project_name, state, "resume")

    print(f"[OK] Project '{project_name}' resumed from checkpoint {cp.id}")
    print(f"     Restored Phase: {state.phase}")
    print(f"     Restored at: {cp.created_at}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Rollback to a specific checkpoint."""
    project_name: str = args.project
    checkpoint_id: int = args.checkpoint_id

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    db_path = _get_db_path(base_dir, project_name)
    if not db_path.exists():
        print(f"[ERROR] Database does not exist: {db_path}")
        return 1

    store = _get_store(base_dir, project_name)
    state_dict = store.rollback(project_name, checkpoint_id)
    if state_dict is None:
        print(f"[ERROR] checkpoint {checkpoint_id} does not exist or rollback failed")
        return 1

    state = ProjectState.from_dict(state_dict)
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    _write_checkpoint(store, project_name, state, "rollback")

    print(f"[OK] Project '{project_name}' rolled back to checkpoint {checkpoint_id}")
    print(f"     Phase after rollback: {state.phase}")
    return 0


def cmd_rollback_phase(args: argparse.Namespace) -> int:
    """Rollback to a specific phase (requires approval)."""
    project_name: str = args.project
    target_phase: str = args.to
    approved: bool = args.approved

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    passed, msg = phase_rollback(project_name, base_dir, target_phase, approved=approved)
    if not passed:
        if "approval" in msg.lower() or "approved" in msg.lower():
            print(f"[BLOCKED] {msg}")
        else:
            print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Manual approval for a specific phase."""
    project_name: str = args.project
    phase: str = args.phase

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    if phase == "design":
        passed, msg = phase_approve_design(project_name, base_dir)
    elif phase == "accept":
        passed, msg = phase_approve_accept(project_name, base_dir)
    else:
        print(f"[ERROR] Unknown approval phase: {phase}")
        return 1

    if not passed:
        print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


def cmd_mark_tests(args: argparse.Namespace) -> int:
    """Mark end-to-end test status."""
    project_name: str = args.project
    passed: bool = args.passed

    base_dir = _get_base_dir()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    ok, msg = phase_mark_tests(project_name, base_dir, passed=passed)
    if not ok:
        print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


# ───────────────────────────────────────────────────────────────
# CLI entry
# ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    phase_choices = _phase_choices()

    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description=(
            "Pipeline state machine — registry-driven phase flow. "
            f"Available phases: {', '.join(phase_choices)}"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Create project skeleton")
    p_init.add_argument("project", help="Project name")
    p_init.add_argument("--description", default="", help="Project description")
    p_init.add_argument("--stack", default="", help="Tech stack")
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing directory")

    # check
    p_check = sub.add_parser("check", help="Check current phase conditions")
    p_check.add_argument("project", help="Project name")

    # advance
    p_adv = sub.add_parser("advance", help="Advance to next phase")
    p_adv.add_argument("project", help="Project name")

    # status
    p_status = sub.add_parser("status", help="Show project status")
    p_status.add_argument("project", help="Project name")

    # resume
    p_resume = sub.add_parser("resume", help="Resume project from checkpoint")
    p_resume.add_argument("project", help="Project name")
    p_resume.add_argument("--checkpoint-id", type=int, default=None, help="Checkpoint ID (default: latest)")

    # rollback
    p_rollback = sub.add_parser("rollback", help="Rollback to specific checkpoint")
    p_rollback.add_argument("project", help="Project name")
    p_rollback.add_argument("--checkpoint-id", type=int, required=True, help="Checkpoint ID")

    # rollback-phase
    p_rollback_phase = sub.add_parser("rollback-phase", help="Rollback to a specific phase (requires approval)")
    p_rollback_phase.add_argument("project", help="Project name")
    p_rollback_phase.add_argument("--to", required=True, choices=phase_choices, help="Target phase")
    p_rollback_phase.add_argument("--approved", action="store_true", help="Confirm manual approval")

    # approve
    p_approve = sub.add_parser("approve", help="Manual approval for a specific phase")
    p_approve.add_argument("project", help="Project name")
    p_approve.add_argument("--phase", required=True, choices=[p for p in phase_choices if p in ("design", "accept")], help="Phase to approve")

    # mark-tests
    p_mark_tests = sub.add_parser("mark-tests", help="Mark end-to-end test status")
    p_mark_tests.add_argument("project", help="Project name")
    p_mark_tests.add_argument("--passed", action="store_true", help="Mark as passed")
    p_mark_tests.add_argument("--failed", action="store_true", help="Mark as failed")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle mark-tests passed/failed mutual exclusion
    if getattr(args, "failed", False):
        args.passed = False

    handlers = {
        "init": cmd_init,
        "check": cmd_check,
        "advance": cmd_advance,
        "status": cmd_status,
        "resume": cmd_resume,
        "rollback": cmd_rollback,
        "rollback-phase": cmd_rollback_phase,
        "approve": cmd_approve,
        "mark-tests": cmd_mark_tests,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"[ERROR] Unknown command: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
