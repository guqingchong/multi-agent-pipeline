#!/usr/bin/env python3
"""bridge_cli.py — Hermes to three-layer architecture bridge

Hermes invokes this script via terminal to wire entry/constraint/suggestion layers.

Commands that mirror ``pipeline.py`` call ``PhaseFlow`` and ``StateStore``
directly instead of proxying through ``pipeline.py``.

Usage:
  python bridge_cli.py load --project <project_name>
  python bridge_cli.py route --task-type <task_type> --feature-id [feature_id]
  python bridge_cli.py suggest --project <project_name>
  python bridge_cli.py full --project <project_name>
  python bridge_cli.py check-hermes --task-type <task_type>
  python bridge_cli.py dispatch --adapter <adapter_name> --task-type <task_type> --prompt [prompt]
  python bridge_cli.py init --project <project_name> --description [desc] --stack [stack] --force
  python bridge_cli.py advance --project <project_name>
  python bridge_cli.py status --project <project_name>
  python bridge_cli.py resume --project <project_name> --checkpoint-id [id]
  python bridge_cli.py rollback --project <project_name> --checkpoint-id [id]
  python bridge_cli.py rollback-phase --project <project_name> --to <phase> --approved
  python bridge_cli.py approve --project <project_name> --phase <phase>
  python bridge_cli.py mark-tests --project <project_name> --passed|--failed
  python bridge_cli.py mode [project_name]
  python bridge_cli.py inspect --project <project_name> --phase [phase]
  python bridge_cli.py audit-report --project <project_name>
  python bridge_cli.py debate --topic <topic> --participants <p1,p2>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

# ─── Path setup ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─── Import three-layer modules ──────────────────────────────
try:
    from entry import auto_load, show_dashboard, identify_intent, UserIntent
    from system_constraint import SystemConstraint, ConstraintViolation
    from suggestion_engine import SuggestionEngine
except ImportError as e:
    print(json.dumps({"error": f"Import failed: {e}", "hint": "Run from project root or ensure src/ is in PYTHONPATH"}))
    sys.exit(1)

try:
    from config import get_config
except ImportError as e:
    print(json.dumps({"error": f"Config import failed: {e}"}))
    sys.exit(1)

try:
    from debate.session import SessionManager, DebateSession
    from debate.context import ContextBuilder, PromptType
    from debate.protocols import ProtocolType, ProtocolFactory, get_available_protocols
    from debate.convergence import ConvergenceAnalyzer, ConvergenceStatus
except ImportError as e:
    print(json.dumps({"error": f"Debate module import failed: {e}", "hint": "Check debate module"}))
    sys.exit(1)


# ─── Configurable base directory ─────────────────────────────

def get_base_dir() -> Path:
    """Return the projects base directory.

    Priority:
      1. MULTI_AGENT_PIPELINE_BASE_DIR environment variable
      2. PROJECT_ROOT.parent (legacy fallback)
    """
    env_base = os.environ.get("MULTI_AGENT_PIPELINE_BASE_DIR")
    if env_base:
        return Path(env_base)
    return PROJECT_ROOT.parent


def _get_db_path(project_name: str) -> Path:
    return get_config().db_path(get_base_dir() / project_name)


# ─── Health Check Functions ──────────────────────────────────

def check_endpoint_availability(adapter_name: str) -> Dict[str, Any]:
    """Check adapter availability using REGISTRY metadata."""
    result = {
        "adapter": adapter_name,
        "cli_exists": False,
        "version_works": False,
        "api_key_valid": False,
        "issues": [],
        "suggestions": []
    }

    try:
        from registry import REGISTRY
    except (ModuleNotFoundError, ImportError):
        from src.registry import REGISTRY

    agent_def = REGISTRY.agents.get(adapter_name)
    if agent_def is None:
        result["issues"].append(f"Agent '{adapter_name}' not found in REGISTRY")
        result["suggestions"].append(f"Available agents: {list(REGISTRY.agents.keys())}")
        return result

    cli_path = agent_def.cli_path
    cli_exists = os.path.exists(cli_path) if cli_path else False
    if cli_exists:
        result["cli_exists"] = True
    else:
        result["issues"].append(f"CLI not found at {cli_path}")
        result["suggestions"].append(f"Install {adapter_name} or update REGISTRY cli_path")

    if cli_exists:
        try:
            version_result = subprocess.run(
                [cli_path, "--version"],
                capture_output=True, text=True, timeout=10
            )
            if version_result.returncode == 0:
                result["version_works"] = True
            else:
                result["issues"].append(f"--version failed: {version_result.stderr[:100]}")
                result["suggestions"].append(f"Check {adapter_name} installation")
        except subprocess.TimeoutExpired:
            result["issues"].append("--version timed out")
        except Exception as e:
            result["issues"].append(f"--version error: {str(e)[:80]}")

    if agent_def.env_vars:
        missing_keys = []
        for var_name in agent_def.env_vars:
            if os.environ.get(var_name):
                result["api_key_valid"] = True
            else:
                missing_keys.append(var_name)
        if missing_keys:
            result["issues"].append(f"Missing env vars: {missing_keys}")
            result["suggestions"].append(f"Set {missing_keys} environment variables")
    else:
        result["api_key_valid"] = True

    return result


# ─── Shared pipeline commands (called directly, not via pipeline.py) ─

def cmd_init(project_name: str, description: str = "", stack: str = "", force: bool = False) -> Dict[str, Any]:
    """Initialize a project directly using StateStore/PhaseFlow."""
    from phase_flow import init_project

    base_dir = get_base_dir()
    proj_dir = base_dir / project_name

    if proj_dir.exists() and not force:
        return {
            "command": "init",
            "project": project_name,
            "error": f"Project directory already exists: {proj_dir}",
            "return_code": 1,
        }

    state, metadata_files, git_init = init_project(
        project_name=project_name,
        base_dir=base_dir,
        description=description,
        stack=stack,
    )

    return {
        "command": "init",
        "project": project_name,
        "return_code": 0,
        "git_initialized": git_init,
        "metadata_files": metadata_files,
    }


def cmd_advance(project_name: str) -> Dict[str, Any]:
    """Advance project to next phase using PhaseFlow."""
    from phase_flow import phase_advance

    base_dir = get_base_dir()
    passed, msg = phase_advance(project_name, base_dir)
    return {
        "command": "advance",
        "project": project_name,
        "return_code": 0 if passed else 1,
        "message": msg,
        "success": passed,
    }


def cmd_status(project_name: str) -> Dict[str, Any]:
    """Return project status directly from StateStore."""
    from state_store import StateStore

    base_dir = get_base_dir()
    db_path = get_config().db_path(base_dir / project_name)
    store = StateStore(db_path)
    record = store.get_project(project_name)
    raw_state = store.legacy_load("state")
    state_dict = json.loads(raw_state) if raw_state else None

    return {
        "command": "status",
        "project": project_name,
        "return_code": 0,
        "phase": record.current_phase if record else (state_dict.get("phase") if state_dict else "unknown"),
        "state": state_dict,
    }


def cmd_resume(project_name: str, checkpoint_id: Optional[int] = None) -> Dict[str, Any]:
    """Resume project from checkpoint directly via StateStore."""
    from state_store import StateStore
    from models import ProjectState

    base_dir = get_base_dir()
    db_path = get_config().db_path(base_dir / project_name)
    store = StateStore(db_path)

    if checkpoint_id is not None:
        cp = store.get_checkpoint(checkpoint_id)
    else:
        cp = store.get_latest_checkpoint(project_name)

    if cp is None:
        return {
            "command": "resume",
            "project": project_name,
            "return_code": 1,
            "error": "checkpoint not found",
        }

    state_dict = store.restore_checkpoint(cp.id)
    if state_dict is None:
        return {
            "command": "resume",
            "project": project_name,
            "return_code": 1,
            "error": "checkpoint state is empty",
        }

    state = ProjectState.from_dict(state_dict)
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))
    store.write_checkpoint(
        project_id=project_name,
        phase=str(state.phase),
        state_dict=state_dict,
        agent="bridge_cli",
        action="resume",
        result="ok",
    )

    return {
        "command": "resume",
        "project": project_name,
        "return_code": 0,
        "checkpoint_id": cp.id,
        "phase": str(state.phase),
    }


def cmd_rollback(project_name: str, checkpoint_id: int) -> Dict[str, Any]:
    """Rollback to a specific checkpoint directly via StateStore."""
    from state_store import StateStore
    from models import ProjectState

    base_dir = get_base_dir()
    db_path = get_config().db_path(base_dir / project_name)
    store = StateStore(db_path)

    state_dict = store.rollback(project_name, checkpoint_id)
    if state_dict is None:
        return {
            "command": "rollback",
            "project": project_name,
            "return_code": 1,
            "error": "checkpoint not found or rollback failed",
        }

    state = ProjectState.from_dict(state_dict)
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    store.write_checkpoint(
        project_id=project_name,
        phase=str(state.phase),
        state_dict=state_dict,
        agent="bridge_cli",
        action="rollback",
        result="ok",
    )

    return {
        "command": "rollback",
        "project": project_name,
        "return_code": 0,
        "checkpoint_id": checkpoint_id,
        "phase": str(state.phase),
    }


def cmd_rollback_phase(project_name: str, target_phase: str, approved: bool = False) -> Dict[str, Any]:
    """Rollback to a phase directly via PhaseFlow."""
    from phase_flow import phase_rollback

    base_dir = get_base_dir()
    passed, msg = phase_rollback(project_name, base_dir, target_phase, approved=approved)
    return {
        "command": "rollback-phase",
        "project": project_name,
        "return_code": 0 if passed else 1,
        "message": msg,
        "success": passed,
    }


def cmd_approve(project_name: str, phase: str) -> Dict[str, Any]:
    """Approve a phase directly via PhaseFlow."""
    from phase_flow import phase_approve_design, phase_approve_accept

    base_dir = get_base_dir()
    if phase == "design":
        passed, msg = phase_approve_design(project_name, base_dir)
    elif phase == "accept":
        passed, msg = phase_approve_accept(project_name, base_dir)
    else:
        return {
            "command": "approve",
            "project": project_name,
            "return_code": 1,
            "error": f"Unknown approval phase: {phase}",
        }

    return {
        "command": "approve",
        "project": project_name,
        "return_code": 0 if passed else 1,
        "message": msg,
        "success": passed,
    }


def cmd_mark_tests(project_name: str, passed: bool) -> Dict[str, Any]:
    """Mark end-to-end test status directly via PhaseFlow."""
    from phase_flow import phase_mark_tests

    base_dir = get_base_dir()
    ok, msg = phase_mark_tests(project_name, base_dir, passed=passed)
    return {
        "command": "mark-tests",
        "project": project_name,
        "return_code": 0 if ok else 1,
        "message": msg,
        "success": ok,
        "passed": passed,
    }


# ─── Bridge-specific command implementations ──────────────────

def cmd_load(project_name: str) -> Dict[str, Any]:
    """Load project state + dashboard + intent pre-analysis."""
    base_dir = get_base_dir()

    ctx = auto_load(project_name, base_dir)
    dashboard = show_dashboard(project_name, base_dir, rich_mode=False)

    result = {
        "command": "load",
        "project": project_name,
        "exists": ctx.project_exists,
        "phase": ctx.current_phase,
        "feature_count": len(ctx.features),
        "dashboard": dashboard,
        "intent_hint": None,
    }

    if ctx.project_exists and ctx.features:
        passed = sum(1 for f in ctx.features if hasattr(f, 'status') and f.status == 'passed')
        pending = sum(1 for f in ctx.features if hasattr(f, 'status') and f.status == 'pending')
        result["passed_features"] = passed
        result["pending_features"] = pending

    return result


def cmd_route(task_type: str, feature_id: str = "") -> Dict[str, Any]:
    """Route task: determine which agent should execute it."""
    constraint = SystemConstraint()
    spec = {"feature_id": feature_id} if feature_id else {}

    try:
        result = constraint.route_task(task_type, spec)
        return {
            "command": "route",
            "task_type": task_type,
            "feature_id": feature_id,
            "target_agent": result.get("target_adapter", "unknown"),
            "allowed": True,
        }
    except ConstraintViolation as e:
        return {
            "command": "route",
            "task_type": task_type,
            "feature_id": feature_id,
            "allowed": False,
            "violation": str(e),
            "required_agent": e.required_agent,
        }


def cmd_check_hermes(task_type: str) -> Dict[str, Any]:
    """Check whether Hermes is allowed to execute this task."""
    constraint = SystemConstraint()

    try:
        constraint.hermes_only_orchestration(task_type)
        return {
            "command": "check-hermes",
            "task_type": task_type,
            "hermes_allowed": True,
            "message": f"Hermes can execute {task_type}",
        }
    except ConstraintViolation:
        target = constraint.route_task(task_type, {}).get("target_agent", "unknown")
        return {
            "command": "check-hermes",
            "task_type": task_type,
            "hermes_allowed": False,
            "message": f"Hermes cannot execute {task_type}. Must delegate to {target}.",
            "must_delegate_to": target,
        }


def cmd_suggest(project_name: str) -> Dict[str, Any]:
    """Generate next-step suggestion."""
    base_dir = get_base_dir()
    engine = SuggestionEngine(project_name, base_dir)
    suggestion = engine.suggest_next_phase(None)

    return {
        "command": "suggest",
        "project": project_name,
        "suggestion_type": suggestion.type.value,
        "current_phase": suggestion.current_phase,
        "next_phase": suggestion.next_phase,
        "reason": suggestion.reason,
        "blockers": suggestion.blockers,
        "can_advance": suggestion.can_advance,
        "requires_approval": suggestion.requires_approval,
    }


def cmd_full(project_name: str) -> Dict[str, Any]:
    """Full flow: load + suggest."""
    return {
        "command": "full",
        "project": project_name,
        "load": cmd_load(project_name),
        "suggest": cmd_suggest(project_name),
    }


def cmd_dispatch(adapter: str, task_type: str, prompt: str = "", timeout: int = 600, feature_id: str = "") -> Dict[str, Any]:
    """Dispatch task to real CLI Agent via PipelineExecutor."""
    health_result = check_endpoint_availability(adapter)
    if not all([health_result['cli_exists'], health_result['version_works'], health_result['api_key_valid']]):
        return {
            "command": "dispatch",
            "adapter": adapter,
            "task_type": task_type,
            "error": "Health check failed",
            "health_check": health_result
        }

    try:
        from pipeline_executor import create_executor
    except ImportError:
        from src.pipeline_executor import create_executor

    project_dir = os.environ.get("PIPELINE_PROJECT_DIR", str(PROJECT_ROOT))
    executor = create_executor(work_dir=project_dir)

    payload = {"prompt": prompt} if prompt else {}
    result = executor.dispatch_and_wait(
        adapter, task_type, payload,
        timeout_sec=timeout
    )

    return {
        "command": "dispatch",
        "adapter": adapter,
        "task_type": task_type,
        "feature_id": feature_id,
        "success": result.success,
        "output": result.output[:2000],
        "latency_ms": result.latency_ms,
        "error": result.error,
        "status": result.status.value,
    }


def cmd_mode(project_name: str = "") -> Dict[str, Any]:
    """View or detect project mode."""
    if not project_name:
        cfg = get_config()
        return {
            "command": "mode",
            "current_mode": cfg.pipeline_mode,
            "available_modes": list(cfg.AVAILABLE_MODES.keys()),
        }

    detected = get_config().detect_mode(get_base_dir() / project_name)
    return {
        "command": "mode",
        "project": project_name,
        "detected_mode": detected,
        "available_modes": list(get_config().AVAILABLE_MODES.keys()),
    }


def cmd_inspect(project_name: str, phase: str = "") -> Dict[str, Any]:
    """Run independent audit on current or given phase."""
    from inspector import Inspector
    from phase_flow import PhaseFlow

    base_dir = get_base_dir()
    project_dir = base_dir / project_name
    inspector = Inspector(project_dir)
    target_phase = phase or PhaseFlow(project_name, base_dir).current_phase()
    report = inspector.audit(target_phase)
    return {"command": "inspect", "project": project_name, "phase": target_phase, "report": report.to_dict()}


def cmd_audit_report(project_name: str) -> Dict[str, Any]:
    """Show inspector audit history."""
    from state_store import StateStore

    base_dir = get_base_dir()
    db_path = get_config().db_path(base_dir / project_name)
    store = StateStore(db_path)
    logs = store.list_audit_logs(project_name, event="inspector_audit", limit=50)
    return {
        "command": "audit-report",
        "project": project_name,
        "logs": [
            {
                "id": log.id,
                "phase": log.phase,
                "event": log.event,
                "details": log.details(),
                "created_at": log.created_at,
            }
            for log in logs
        ],
    }


def cmd_debate(session_id: str = "", protocol: str = "NI", topic: str = "", participants: Optional[List[str]] = None,
               iterations: int = 10, output_file: str = "") -> Dict[str, Any]:
    """Run a debate protocol."""
    from datetime import datetime

    if participants is None:
        participants = ["Agent1", "Agent2"]

    session_manager = SessionManager()

    if session_id:
        session = session_manager.get_session(session_id)
        if not session:
            session_path = os.path.join(session_manager.sessions_dir, f"{session_id}.json")
            if os.path.exists(session_path):
                session = session_manager.load_session(session_path)
            else:
                session = session_manager.create_session(name=f"Debate_{topic.replace(' ', '_')}", session_id=session_id)
    else:
        session = session_manager.create_session(name=f"Debate_{topic.replace(' ', '_')}")
        session_id = session.session_id

    analyzer = ConvergenceAnalyzer()
    analyzer.set_budget_limits({"iterations": iterations, "time": 3600})

    try:
        protocol_enum = ProtocolType(protocol.lower())
    except ValueError:
        protocol_enum = ProtocolType.NI

    protocol_instance = ProtocolFactory.create_protocol(protocol_enum)
    init_prompt = protocol_instance.initialize_debate(participants, topic)

    session.add_context("topic", topic)
    session.add_context("protocol", protocol)
    session.add_context("participants", participants)
    session.add_context("initial_prompt", init_prompt)

    current_speaker_idx = 0
    agreement_history = []

    for i in range(iterations):
        session.increment_iteration()
        if session.is_budget_exhausted():
            break

        current_speaker = participants[current_speaker_idx]
        context_builder = ContextBuilder()
        context_builder.set_topic(topic)
        for p in participants:
            context_builder.add_participant(Participant(p, "Participant", "General Knowledge", "Neutral"))

        for stmt in session.statements:
            context_builder.add_statement(Statement(
                stmt["speaker"],
                stmt["statement"],
                datetime.fromisoformat(stmt["timestamp"]) if isinstance(stmt["timestamp"], str) else stmt["timestamp"]
            ))

        current_prompt = context_builder.build_prompt(PromptType.REPLY, current_speaker=current_speaker)
        simulated_response = f"This is a simulated response from {current_speaker} in discussion about '{topic}'."
        session.add_statement(current_speaker, simulated_response)

        input_data = {
            "stance": f"My stance on {topic}",
            "reasoning": f"My reasoning as {current_speaker}",
            "evidence": [f"Point from {current_speaker}"],
            "adjustment": 0.1,
            "confidence": 0.8,
        }
        protocol_instance.process_turn(current_speaker, input_data)

        agreement_score = min(0.5 + (i * 0.05), 0.95)
        agreement_history.append(agreement_score)
        analyzer.record_iteration(agreement_score)

        convergence_status, details = analyzer.evaluate_state()
        if convergence_status in [ConvergenceStatus.CONVERGED, ConvergenceStatus.STALEMATE, ConvergenceStatus.BUDGET_EXHAUSTED]:
            session.update_convergence(agreement_score)
            break

        current_speaker_idx = (current_speaker_idx + 1) % len(participants)

    session_manager.save_session(session, f"debate_session_{session.session_id}.json")
    final_report = analyzer.get_final_report()

    return {
        "command": "debate",
        "session_id": session.session_id,
        "protocol": protocol,
        "topic": topic,
        "participants": participants,
        "iterations_completed": session.budget["current_iteration"],
        "final_agreement_score": final_report["final_agreement_score"],
        "convergence_status": final_report["final_status"],
        "summary": final_report["summary"],
        "output_saved_to": output_file or f"debate_session_{session.session_id}.json"
    }


# ─── CLI entry point with argparse ─────────────────────────────────────────

def _phase_choices() -> List[str]:
    try:
        from registry import REGISTRY
    except ImportError:
        from src.registry import REGISTRY
    return REGISTRY.list_phases()


def build_parser() -> argparse.ArgumentParser:
    phase_choices = _phase_choices()

    parser = argparse.ArgumentParser(
        prog="bridge_cli.py",
        description="Bridge CLI for Hermes to three-layer architecture"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # load command
    load_parser = subparsers.add_parser("load", help="Load project state + dashboard")
    load_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    load_parser.add_argument("--project", help="Project name")

    # route command
    route_parser = subparsers.add_parser("route", help="Route task to agent")
    route_parser.add_argument("task_type_pos", nargs="?", help="Type of task to route (positional)")
    route_parser.add_argument("--task-type", help="Type of task to route")
    route_parser.add_argument("feature_id_pos", nargs="?", default="", help="Feature ID (positional)")
    route_parser.add_argument("--feature-id", default="", help="Feature ID (optional)")

    # suggest command
    suggest_parser = subparsers.add_parser("suggest", help="Generate next-step suggestion")
    suggest_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    suggest_parser.add_argument("--project", help="Project name")

    # full command
    full_parser = subparsers.add_parser("full", help="Full flow: load + suggest")
    full_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    full_parser.add_argument("--project", help="Project name")

    # check-hermes command
    check_hermes_parser = subparsers.add_parser("check-hermes", help="Check if Hermes may execute")
    check_hermes_parser.add_argument("task_type_pos", nargs="?", help="Type of task to check (positional)")
    check_hermes_parser.add_argument("--task-type", help="Type of task to check")

    # dispatch command
    dispatch_parser = subparsers.add_parser("dispatch", help="Dispatch task to agent")
    dispatch_parser.add_argument("adapter_pos", nargs="?", help="Adapter name (positional)")
    dispatch_parser.add_argument("--adapter", help="Adapter name")
    dispatch_parser.add_argument("task_type_pos", nargs="?", help="Type of task to dispatch (positional)")
    dispatch_parser.add_argument("--task-type", help="Type of task to dispatch")
    dispatch_parser.add_argument("prompt_pos", nargs="?", default="", help="Prompt for the task (positional)")
    dispatch_parser.add_argument("--prompt", default="", help="Prompt for the task")
    dispatch_parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    dispatch_parser.add_argument("--feature-id", default="", help="Feature ID (optional)")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize project")
    init_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    init_parser.add_argument("--project", help="Project name")
    init_parser.add_argument("--description", default="", help="Project description")
    init_parser.add_argument("--stack", default="", help="Tech stack")
    init_parser.add_argument("--force", action="store_true", help="Force overwrite existing directory")

    # advance command
    advance_parser = subparsers.add_parser("advance", help="Advance to next phase")
    advance_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    advance_parser.add_argument("--project", help="Project name")

    # status command
    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    status_parser.add_argument("--project", help="Project name")

    # resume command
    resume_parser = subparsers.add_parser("resume", help="Resume project from checkpoint")
    resume_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    resume_parser.add_argument("--project", help="Project name")
    resume_parser.add_argument("--checkpoint-id", type=int, default=None, help="Checkpoint ID (default: latest)")

    # rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback to specific checkpoint")
    rollback_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    rollback_parser.add_argument("--project", help="Project name")
    rollback_parser.add_argument("--checkpoint-id", type=int, required=True, help="Checkpoint ID")

    # rollback-phase command
    rollback_phase_parser = subparsers.add_parser("rollback-phase", help="Rollback to a specific phase (requires approval)")
    rollback_phase_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    rollback_phase_parser.add_argument("--project", help="Project name")
    rollback_phase_parser.add_argument("--to", required=True, choices=phase_choices, help="Target phase")
    rollback_phase_parser.add_argument("--approved", action="store_true", help="Confirm manual approval")

    # approve command
    approve_parser = subparsers.add_parser("approve", help="Manual approval for a specific phase")
    approve_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    approve_parser.add_argument("--project", help="Project name")
    approve_parser.add_argument("--phase", required=True, choices=[p for p in phase_choices if p in ("design", "accept")], help="Phase to approve")

    # mark-tests command
    mark_tests_parser = subparsers.add_parser("mark-tests", help="Mark end-to-end test status")
    mark_tests_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    mark_tests_parser.add_argument("--project", help="Project name")
    mark_tests_parser.add_argument("--passed", action="store_true", help="Mark as passed")
    mark_tests_parser.add_argument("--failed", action="store_true", help="Mark as failed")

    # mode command
    mode_parser = subparsers.add_parser("mode", help="Show project mode")
    mode_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    mode_parser.add_argument("--project", help="Project name (optional)")

    # inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Run independent audit on current or given phase")
    inspect_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    inspect_parser.add_argument("--project", help="Project name")
    inspect_parser.add_argument("--phase", default="", help="Phase to audit (default: current)")

    # audit-report command
    audit_report_parser = subparsers.add_parser("audit-report", help="Show inspector audit history")
    audit_report_parser.add_argument("project_pos", nargs="?", help="Project name (positional)")
    audit_report_parser.add_argument("--project", help="Project name")

    # debate command
    debate_parser = subparsers.add_parser("debate", help="Run debate protocol")
    debate_parser.add_argument("--session", help="Session ID (optional, creates new session if not provided)")
    debate_parser.add_argument("--protocol", choices=["ni", "more", "samre"], default="ni",
                              help="Debate protocol to use")
    debate_parser.add_argument("--topic", required=True, help="Topic for the debate")
    debate_parser.add_argument("--participants", type=lambda x: x.split(","),
                              help="Comma-separated list of participants (default: Agent1,Agent2)")
    debate_parser.add_argument("--iterations", type=int, default=10,
                              help="Maximum number of iterations (default: 10)")
    debate_parser.add_argument("--output-file", help="File to save the debate output (optional)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, 'failed') and args.failed:
        args.passed = False

    if hasattr(args, 'project_pos') and args.project_pos and not args.project:
        args.project = args.project_pos
    if hasattr(args, 'task_type_pos') and args.task_type_pos and not args.task_type:
        args.task_type = args.task_type_pos
    if hasattr(args, 'feature_id_pos') and args.feature_id_pos and not args.feature_id:
        args.feature_id = args.feature_id_pos
    if hasattr(args, 'adapter_pos') and args.adapter_pos and not args.adapter:
        args.adapter = args.adapter_pos
    if hasattr(args, 'prompt_pos') and args.prompt_pos and not args.prompt:
        args.prompt = args.prompt_pos

    if args.command in ["load", "suggest", "full", "init", "advance", "status", "resume", "rollback", "rollback-phase", "approve", "mark-tests", "inspect", "audit-report"]:
        if not args.project:
            print(json.dumps({"error": f"Project name is required for {args.command} command"}))
            sys.exit(1)
    elif args.command == "route":
        if not args.task_type:
            print(json.dumps({"error": "Task type is required for route command"}))
            sys.exit(1)
    elif args.command == "check-hermes":
        if not args.task_type:
            print(json.dumps({"error": "Task type is required for check-hermes command"}))
            sys.exit(1)
    elif args.command == "dispatch":
        if not args.adapter or not args.task_type:
            print(json.dumps({"error": "Adapter and task type are required for dispatch command"}))
            sys.exit(1)

    if args.command == "load":
        result = cmd_load(args.project)
    elif args.command == "route":
        result = cmd_route(args.task_type, args.feature_id)
    elif args.command == "suggest":
        result = cmd_suggest(args.project)
    elif args.command == "full":
        result = cmd_full(args.project)
    elif args.command == "check-hermes":
        result = cmd_check_hermes(args.task_type)
    elif args.command == "dispatch":
        result = cmd_dispatch(args.adapter, args.task_type, args.prompt, args.timeout, args.feature_id)
    elif args.command == "init":
        result = cmd_init(args.project, args.description, args.stack, args.force)
    elif args.command == "advance":
        result = cmd_advance(args.project)
    elif args.command == "status":
        result = cmd_status(args.project)
    elif args.command == "resume":
        result = cmd_resume(args.project, args.checkpoint_id)
    elif args.command == "rollback":
        result = cmd_rollback(args.project, args.checkpoint_id)
    elif args.command == "rollback-phase":
        result = cmd_rollback_phase(args.project, args.to, args.approved)
    elif args.command == "approve":
        result = cmd_approve(args.project, args.phase)
    elif args.command == "mark-tests":
        result = cmd_mark_tests(args.project, args.passed)
    elif args.command == "inspect":
        result = cmd_inspect(args.project, phase=args.phase)
    elif args.command == "audit-report":
        result = cmd_audit_report(args.project)
    elif args.command == "mode":
        result = cmd_mode(args.project)
    elif args.command == "debate":
        participants = args.participants or ["Agent1", "Agent2"]
        result = cmd_debate(
            session_id=args.session,
            protocol=args.protocol.upper(),
            topic=args.topic,
            participants=participants,
            iterations=args.iterations,
            output_file=args.output_file
        )
    else:
        print(json.dumps({"error": f"Unknown command: {args.command}", "available": [
            "load", "route", "suggest", "full", "check-hermes", "dispatch",
            "init", "advance", "status", "resume", "rollback", "rollback-phase",
            "approve", "mark-tests", "mode", "inspect", "audit-report", "debate"
        ]}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
