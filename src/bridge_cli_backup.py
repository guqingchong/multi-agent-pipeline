#!/usr/bin/env python3
"""bridge_cli.py — Hermes to three-layer architecture bridge

Hermes invokes this script via terminal to wire entry/constraint/suggestion layers.

Usage:
  python bridge_cli.py load <project_name>           # Load project state + dashboard
  python bridge_cli.py route <task_type> [feature_id] # Route task to agent
  python bridge_cli.py suggest <project_name>         # Generate next-step suggestion
  python bridge_cli.py full <project_name>            # Full flow: load + suggest
  python bridge_cli.py check-hermes <task_type>       # Check if Hermes may execute

Examples:
  python bridge_cli.py load chengcetong
  python bridge_cli.py route code F005
  python bridge_cli.py route review F005
  python bridge_cli.py suggest multi-agent-pipeline
  python bridge_cli.py full chengcetong
  python bridge_cli.py check-hermes code
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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


# ─── Command implementations ────────────────────────────────

def cmd_load(project_name: str) -> dict:
    """Load project state + dashboard + intent pre-analysis."""
    base_dir = get_base_dir()

    # 1. Auto-load
    ctx = auto_load(project_name, base_dir)

    # 2. Dashboard
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


def cmd_route(task_type: str, feature_id: str = "") -> dict:
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


def cmd_check_hermes(task_type: str) -> dict:
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


def cmd_suggest(project_name: str) -> dict:
    """Generate next-step suggestion."""
    base_dir = get_base_dir()

    engine = SuggestionEngine(project_name, base_dir)

    # SuggestionEngine auto-loads state internally (pass None)
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


def cmd_full(project_name: str) -> dict:
    """Full flow: load + suggest."""
    load_result = cmd_load(project_name)
    suggest_result = cmd_suggest(project_name)

    return {
        "command": "full",
        "project": project_name,
        "load": load_result,
        "suggest": suggest_result,
    }


# ─── CLI entry point ─────────────────────────────────────────

COMMANDS = {
    "load": lambda args: cmd_load(args[0]),
    "route": lambda args: cmd_route(args[0], args[1] if len(args) > 1 else ""),
    "suggest": lambda args: cmd_suggest(args[0]),
    "full": lambda args: cmd_full(args[0]),
    "check-hermes": lambda args: cmd_check_hermes(args[0]),
    "dispatch": lambda args: cmd_dispatch(args),
    "mode": lambda args: cmd_mode(args[0] if args else ""),
}


def cmd_mode(project_name: str = "") -> dict:
    """查看/检测项目模式。
    
    Usage: bridge_cli.py mode              # 查看当前默认模式
            bridge_cli.py mode <project>   # 检测项目适用模式
    """
    from config import get_config, PipelineConfig
    
    if not project_name:
        cfg = get_config()
        return {
            "command": "mode",
            "current_mode": cfg.pipeline_mode,
            "available_modes": list(cfg.AVAILABLE_MODES.keys()),
        }
    
    detected = PipelineConfig.detect_mode(
        Path(os.environ.get("MULTI_AGENT_PIPELINE_BASE_DIR", ".")) / project_name
    )
    return {
        "command": "mode",
        "project": project_name,
        "detected_mode": detected,
        "available_modes": list(get_config().AVAILABLE_MODES.keys()),
    }


def cmd_dispatch(args: list) -> dict:
    """派发任务到真实 CLI Agent（通过 MCP transport + PipelineExecutor）

    Usage: python bridge_cli.py dispatch <adapter_name> <task_type> [prompt]

    Agent 工作目录由环境变量 PIPELINE_PROJECT_DIR 控制。
    设置后 Agent 进程在该目录下执行，产出文件直接写入项目目录。
    示例: export PIPELINE_PROJECT_DIR="D:/chengcetong2"
    """
    if len(args) < 2:
        return {"error": "Usage: bridge_cli.py dispatch <adapter> <task_type> [prompt]"}

    adapter = args[0]
    task_type = args[1]
    prompt_text = args[2] if len(args) > 2 else ""

    try:
        from pipeline_executor import PipelineExecutor, create_executor
    except ImportError:
        from src.pipeline_executor import PipelineExecutor, create_executor

    project_dir = os.environ.get("PIPELINE_PROJECT_DIR", str(PROJECT_ROOT))
    executor = create_executor(work_dir=project_dir)

    payload = {"prompt": prompt_text} if prompt_text else {}
    result = executor.dispatch_and_wait(
        adapter, task_type, payload,
        timeout_sec=int(os.environ.get("PIPELINE_DISPATCH_TIMEOUT", "600"))
    )

    return {
        "command": "dispatch",
        "adapter": adapter,
        "task_type": task_type,
        "success": result.success,
        "output": result.output[:2000],
        "latency_ms": result.latency_ms,
        "error": result.error,
        "status": result.status.value,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {cmd}", "available": list(COMMANDS.keys())}))
        sys.exit(1)

    if cmd in ("load", "suggest", "full") and len(args) < 1:
        print(json.dumps({"error": f"Usage: bridge_cli.py {cmd} <project_name>"}))
        sys.exit(1)

    if cmd == "check-hermes" and len(args) < 1:
        print(json.dumps({"error": "Usage: bridge_cli.py check-hermes <task_type>"}))
        sys.exit(1)

    if cmd == "route" and len(args) < 1:
        print(json.dumps({"error": "Usage: bridge_cli.py route <task_type> [feature_id]"}))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](args)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except (json.JSONDecodeError, TypeError) as e:
        print(json.dumps({"error": str(e), "command": cmd}))
        sys.exit(1)


if __name__ == "__main__":
    main()
