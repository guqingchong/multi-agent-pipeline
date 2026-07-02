#!/usr/bin/env python3
"""pipeline.py — Minimal state machine supporting init / develop / check / advance / resume commands.

Phase 0-3 flow:
  Phase 0: init       → Create project skeleton
  Phase 1: develop    → Development mode (requires check pass)
  Phase 2: review     → Review phase (requires check pass)
  Phase 3: test       → Test phase (requires check pass)

Each advance must pass the check function, otherwise BLOCKED.
Supports resume from checkpoint (F008).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from models import (
        PipelineError, PhaseBlockedError, ProjectNotFoundError,
        CheckpointNotFoundError, ApprovalRequiredError,
        Phase, ProjectState, PHASE_NAMES,
    )
except ModuleNotFoundError:
    from src.models import (
        PipelineError, PhaseBlockedError, ProjectNotFoundError,
        CheckpointNotFoundError, ApprovalRequiredError,
        Phase, ProjectState, PHASE_NAMES,
    )

try:
    from config import get_config
except (ModuleNotFoundError, ImportError):
    from src.config import get_config

try:
    from state_store import StateStore, CheckpointRecord
except ModuleNotFoundError:
    from src.state_store import StateStore, CheckpointRecord

try:
    from phase_checks import (
        CHECK_REGISTRY,
        check_init,
        check_design,
        check_decompose,
        check_develop,
        check_test,
        check_accept,
        check_deploy,
        run_check,
        get_all_phase_names,
    )
except ModuleNotFoundError:
    from src.phase_checks import (
        CHECK_REGISTRY,
        check_init,
        check_design,
        check_decompose,
        check_develop,
        check_test,
        check_accept,
        check_deploy,
        run_check,
        get_all_phase_names,
    )

try:
    from phase_flow import (
        PhaseFlow,
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
        phase_check,
        phase_advance,
        phase_rollback,
        phase_approve_design,
        phase_approve_accept,
        phase_mark_tests,
    )


# ───────────────────────────────────────────────────────────────
# Constants / Config
# ───────────────────────────────────────────────────────────────

DB_FILENAME = get_config().db_name


# ───────────────────────────────────────────────────────────────
# Check function registry
# ───────────────────────────────────────────────────────────────

# Legacy CheckFunc kept for compatibility; new check functions moved to phase_checks.py
# Keep references to legacy check_init / check_develop / check_test for existing tests

CheckFunc = Callable[[ProjectState], Tuple[bool, str]]

# Legacy check functions (compatible with old tests)
# New phase_checks.py uses (project_name, base_dir) signature


def check_init(state: ProjectState) -> Tuple[bool, str]:
    """Legacy compatibility: Phase 0 → Phase 1 check."""
    errors: List[str] = []
    if not state.created:
        errors.append("Project directory not created")
    if not state.git_init:
        errors.append("Git repo not initialized")
    if not state.db_created:
        errors.append("SQLite DB not created")
    required_files = ["SOUL.md", "AGENTS.md", "progress.md", "features.json"]
    missing = [f for f in required_files if f not in state.metadata_files]
    if missing:
        errors.append(f"Missing metadata files: {', '.join(missing)}")
    if errors:
        return False, " | ".join(errors)
    return True, "PASS"


def check_develop(state: ProjectState) -> Tuple[bool, str]:
    """Legacy compatibility: Phase 1 -> Phase 2 check."""
    if not state.check_results.get("develop_started", False):
        return False, "Development not started (develop_started=false)"
    if not state.check_results.get("code_written", False):
        return False, "No code to review (code_written=false)"
    return True, "PASS"


def check_review(state: ProjectState) -> Tuple[bool, str]:
    """Legacy compatibility: Phase 2 → Phase 3 check."""
    if not state.check_results.get("code_written", False):
        return False, "No code to review (code_written=false)"
    if not state.check_results.get("tests_passed", False):
        return False, "Tests not passed (tests_passed=false)"
    return True, "PASS"


def check_test(state: ProjectState) -> Tuple[bool, str]:
    """Legacy compatibility: Phase 3 → (complete) check."""
    if not state.check_results.get("tests_passed", False):
        return False, "Tests not passed (tests_passed=false)"
    return True, "PASS"


# ───────────────────────────────────────────────────────────────
# Helper functions
# ───────────────────────────────────────────────────────────────

def _get_db_path(base_dir: Path) -> Path:
    return base_dir / DB_FILENAME


def _get_store(base_dir: Path) -> StateStore:
    return StateStore(_get_db_path(base_dir))


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


def _load_state(store: StateStore, project_name: str) -> Optional[ProjectState]:
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

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if proj_dir.exists() and not args.force:
        print(f"[ERROR] Project directory already exists: {proj_dir}")
        return 1

    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "src").mkdir(exist_ok=True)
    (proj_dir / "tests").mkdir(exist_ok=True)
    (proj_dir / "specs").mkdir(exist_ok=True)
    (proj_dir / ".logs").mkdir(exist_ok=True)

    # Create metadata files
    metadata_files = []
    for filename, content in [
        ("SOUL.md", f"# SOUL.md\n\nProject: {project_name}\nDescription: {description}\nStack: {stack}\n"),
        ("AGENTS.md", "# AGENTS.md\n\n## Collaboration Rules\n\n(TBD)\n"),
        ("progress.md", f"# progress.md\n\nProject: {project_name}\nCurrent Phase: init\n"),
        ("features.json", json.dumps({"project": project_name, "features": []}, indent=2)),
    ]:
        filepath = proj_dir / filename
        filepath.write_text(content, encoding="utf-8")
        metadata_files.append(filename)

    # Initialize git
    git_init = False
    result = subprocess.run(["git", "init", "-q"], cwd=str(proj_dir), capture_output=True)
    if result.returncode == 0:
        git_init = True

    # Initialize SQLite (Layer 2 + backward compatibility)
    store = _get_store(proj_dir)
    state = ProjectState(
        name=project_name,
        phase=Phase("init"),
        description=description,
        stack=stack,
        created=True,
        git_init=git_init,
        metadata_files=metadata_files,
        db_created=True,
    )
    _save_state(store, project_name, state, "init")
    store.create_project(project_id=project_name, name=project_name, current_phase="init")

    print(f"[OK] Project '{project_name}' initialized")
    print(f"     Directory: {base_dir}")
    print(f"     Phase: {state.phase}")
    print(f"     Metadata files: {', '.join(metadata_files)}")
    print(f"     Git: {'initialized' if git_init else 'init failed'}")
    print(f"     DB: {store.db_path}")
    return 0


def cmd_develop(args: argparse.Namespace) -> int:
    """Enter development mode (advance phase to develop, requires check)."""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] Project does not exist: {project_name}")
        return 1

    # develop command requires check_init to pass
    passed, msg = check_init(state)
    if not passed:
        print(f"[BLOCKED] Cannot enter develop: {msg}")
        return 1

    if state.phase.name in ("develop", "review", "test"):
        print(f"[OK] Already in {state.phase} phase, no need to advance")
        return 0

    # In new Phase 0-6, init->develop goes through design and decompose
    # For compatibility with old tests, directly advance to develop (legacy INIT->DEVELOP)
    state.phase = Phase("develop")
    state.check_results["develop_started"] = True
    _save_state(store, project_name, state, "develop")

    print(f"[OK] Entered develop phase: {project_name}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Check whether current phase meets advance conditions (prefer phase_checks.py)."""
    project_name: str = args.project
    base_dir = Path.cwd()

    # Prefer new phase_checks
    passed, msg = phase_check(project_name, base_dir)
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] check: {msg}")
    return 0 if passed else 1


def cmd_advance(args: argparse.Namespace) -> int:
    """Advance to next phase (auto check, BLOCK if not passed)."""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name

    # Try new phase_advance first
    msg = "Advance failed"
    try:
        passed, msg = phase_advance(project_name, base_dir)
        if passed:
            print(f"[OK] {msg}")
            return 0
    except ValueError:
        # New PHASE_ORDER does not include review etc., fall back to legacy logic
        pass

    # Fallback to legacy 3-state machine logic (compatible with old tests)
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[BLOCKED] {msg}")
        return 1

    # Legacy F005 order: init -> develop -> review -> test
    legacy_order = ["init", "develop", "review", "test"]
    phase_name = state.phase.name
    if phase_name not in legacy_order:
        print(f"[BLOCKED] {msg}")
        return 1

    idx = legacy_order.index(phase_name)
    if idx >= len(legacy_order) - 1:
        print(f"[OK] Already in final phase {state.phase}, no need to advance")
        return 0

    next_phase_name = legacy_order[idx + 1]

    # Legacy check mapping
    old_check_map = {
        "init": check_init,
        "develop": check_develop,
        "review": check_review,
        "test": check_test,
    }
    old_check = old_check_map.get(phase_name)
    if old_check is not None:
        old_passed, old_msg = old_check(state)
        if old_passed:
            original_phase = state.phase
            state.phase = Phase(next_phase_name)
            _save_state(store, project_name, state, f"advance:{state.phase.name.lower()}")
            print(f"[OK] Advanced from {original_phase} to {state.phase}")
            return 0
        else:
            print(f"[BLOCKED] {old_msg}")
            return 1

    print(f"[BLOCKED] {msg}")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show project status."""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] Project does not exist: {project_name}")
        return 1
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume project state from checkpoint."""
    project_name: str = args.project
    checkpoint_id: Optional[int] = getattr(args, "checkpoint_id", None)

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    db_path = _get_db_path(proj_dir)
    if not db_path.exists():
        print(f"[ERROR] Database does not exist: {db_path}")
        return 1

    store = _get_store(proj_dir)

    # 1. Get checkpoint
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

    # 2. Restore state
    state_dict = store.restore_checkpoint(cp.id)
    if state_dict is None:
        print(f"[ERROR] checkpoint {cp.id} state is empty")
        return 1

    state = ProjectState.from_dict(state_dict)

    # 3. Write back to legacy table
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))

    # 4. Write resume marker checkpoint
    _write_checkpoint(store, project_name, state, "resume")

    print(f"[OK] Project '{project_name}' resumed from checkpoint {cp.id}")
    print(f"     Restored Phase: {state.phase}")
    print(f"     Restored at: {cp.created_at}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Rollback to a specific checkpoint."""
    project_name: str = args.project
    checkpoint_id: int = args.checkpoint_id

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] Project directory does not exist: {proj_dir}")
        return 1

    db_path = _get_db_path(proj_dir)
    if not db_path.exists():
        print(f"[ERROR] Database does not exist: {db_path}")
        return 1

    store = _get_store(proj_dir)
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


# ───────────────────────────────────────────────────────────────
# Phase 0-6 new commands
# ───────────────────────────────────────────────────────────────

def cmd_rollback_phase(args: argparse.Namespace) -> int:
    """Rollback to a specific phase (requires approval)."""
    project_name: str = args.project
    target_phase: str = args.to
    approved: bool = args.approved

    base_dir = Path.cwd()
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

    base_dir = Path.cwd()
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

    base_dir = Path.cwd()
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
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Pipeline state machine — Phase 0-6 full flow + SQLite persistence",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Create project skeleton")
    p_init.add_argument("project", help="Project name")
    p_init.add_argument("--description", default="", help="Project description")
    p_init.add_argument("--stack", default="", help="Tech stack")
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing directory")

    # develop
    p_dev = sub.add_parser("develop", help="Enter development mode")
    p_dev.add_argument("project", help="Project name")

    # check
    p_check = sub.add_parser("check", help="Check current phase conditions")
    p_check.add_argument("project", help="Project name")

    # advance
    p_adv = sub.add_parser("advance", help="Advance to next phase")
    p_adv.add_argument("project", help="Project name")

    # status
    p_status = sub.add_parser("status", help="Show project status")
    p_status.add_argument("project", help="Project name")

    # resume (F008)
    p_resume = sub.add_parser("resume", help="Resume project from checkpoint")
    p_resume.add_argument("project", help="Project name")
    p_resume.add_argument("--checkpoint-id", type=int, default=None, help="Checkpoint ID (default: latest)")

    # rollback (F008)
    p_rollback = sub.add_parser("rollback", help="Rollback to specific checkpoint")
    p_rollback.add_argument("project", help="Project name")
    p_rollback.add_argument("--checkpoint-id", type=int, required=True, help="Checkpoint ID")

    # rollback-phase (F013)
    p_rollback_phase = sub.add_parser("rollback-phase", help="Rollback to a specific phase (requires approval)")
    p_rollback_phase.add_argument("project", help="Project name")
    p_rollback_phase.add_argument("--to", required=True, choices=PHASE_NAMES, help="Target phase")
    p_rollback_phase.add_argument("--approved", action="store_true", help="Confirm manual approval")

    # approve (F013)
    p_approve = sub.add_parser("approve", help="Manual approval for a specific phase")
    p_approve.add_argument("project", help="Project name")
    p_approve.add_argument("--phase", required=True, choices=["design", "accept"], help="Phase to approve")

    # mark-tests (F013)
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
        "develop": cmd_develop,
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
