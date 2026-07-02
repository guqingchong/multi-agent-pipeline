#!/usr/bin/env python3
"""bridge_cli.py — Hermes to three-layer architecture bridge

Hermes invokes this script via terminal to wire entry/constraint/suggestion layers.

Usage:
  python bridge_cli.py load --project <project_name>           # Load project state + dashboard
  python bridge_cli.py route --task-type <task_type> --feature-id [feature_id] # Route task to agent
  python bridge_cli.py suggest --project <project_name>         # Generate next-step suggestion
  python bridge_cli.py full --project <project_name>            # Full flow: load + suggest
  python bridge_cli.py check-hermes --task-type <task_type>       # Check if Hermes may execute
  python bridge_cli.py dispatch --adapter <adapter_name> --task-type <task_type> --prompt [prompt] --timeout [timeout] --feature-id [feature_id] # Dispatch task to agent
  python bridge_cli.py init --project <project_name> --description [desc] --stack [stack] --force [force] # Initialize project
  python bridge_cli.py advance --project <project_name>         # Advance to next phase
  python bridge_cli.py status --project <project_name>          # Show project status
  python bridge_cli.py resume --project <project_name> --checkpoint-id [id] # Resume from checkpoint

Examples:
  python bridge_cli.py load --project chengcetong
  python bridge_cli.py route --task-type code --feature-id F005
  python bridge_cli.py route --task-type review --feature-id F005
  python bridge_cli.py suggest --project multi-agent-pipeline
  python bridge_cli.py full --project chengcetong
  python bridge_cli.py check-hermes --task-type code
  python bridge_cli.py dispatch --adapter claude-code --task-type code --prompt "Implement feature X"
  python bridge_cli.py init --project my-project --description "My awesome project" --stack "Python, Django"
  python bridge_cli.py advance --project my-project
  python bridge_cli.py status --project my-project
  python bridge_cli.py resume --project my-project --checkpoint-id 123
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
    from pipeline import (
        cmd_init as pipeline_cmd_init,
        cmd_advance as pipeline_cmd_advance,
        cmd_status as pipeline_cmd_status,
        cmd_resume as pipeline_cmd_resume,
        cmd_rollback as pipeline_cmd_rollback,
        cmd_rollback_phase as pipeline_cmd_rollback_phase,
        cmd_approve as pipeline_cmd_approve,
        cmd_mark_tests as pipeline_cmd_mark_tests,
        phase_names,
    )
except ImportError as e:
    print(json.dumps({"error": f"Pipeline import failed: {e}", "hint": "Check pipeline module"}))
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


# ─── Health Check Functions ──────────────────────────────────

def check_endpoint_availability(adapter_name: str) -> Dict[str, Any]:
    """检查端点可用性
    
    检查步骤:
    1. CLI路径存在（从REGISTRY读取）
    2. --version 可执行
    3. API Key有效性（从REGISTRY.env_vars读取key变量名）
    """
    result = {
        "adapter": adapter_name,
        "cli_exists": False,
        "version_works": False,
        "api_key_valid": False,
        "issues": [],
        "suggestions": []
    }

    # 从REGISTRY获取Agent定义
    try:
        from registry import REGISTRY
    except (ModuleNotFoundError, ImportError):
        from src.registry import REGISTRY
    
    agent_def = REGISTRY.agents.get(adapter_name)
    if agent_def is None:
        result["issues"].append(f"Agent '{adapter_name}' not found in REGISTRY")
        result["suggestions"].append(f"Available agents: {list(REGISTRY.agents.keys())}")
        return result

    # 1. CLI路径存在（从REGISTRY的cli_path读取）
    cli_path = agent_def.cli_path
    cli_exists = os.path.exists(cli_path) if cli_path else False
    if cli_exists:
        result["cli_exists"] = True
    else:
        result["issues"].append(f"CLI not found at {cli_path}")
        result["suggestions"].append(f"Install {adapter_name} or update REGISTRY cli_path")

    # 2. --version 可执行
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

    # 3. API Key检查（从REGISTRY的env_vars读取需要的变量名）
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
        # Agent不需要环境变量（如codewhale用config.toml）
        result["api_key_valid"] = True

    return result


# ─── Command implementations ────────────────────────────────

def cmd_load(project_name: str) -> Dict[str, Any]:
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


def cmd_full(project_name: str) -> Dict[str, Any]:
    """Full flow: load + suggest."""
    load_result = cmd_load(project_name)
    suggest_result = cmd_suggest(project_name)

    return {
        "command": "full",
        "project": project_name,
        "load": load_result,
        "suggest": suggest_result,
    }


def cmd_dispatch(adapter: str, task_type: str, prompt: str = "", timeout: int = 600, feature_id: str = "") -> Dict[str, Any]:
    """派发任务到真实 CLI Agent（通过 MCP transport + PipelineExecutor）

    Agent 工作目录由环境变量 PIPELINE_PROJECT_DIR 控制。
    设置后 Agent 进程在该目录下执行，产出文件直接写入项目目录。
    示例: export PIPELINE_PROJECT_DIR="D:/chengcetong2"
    """
    # 首先执行健康检查
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
        from pipeline_executor import PipelineExecutor, create_executor
    except ImportError:
        from src.pipeline_executor import PipelineExecutor, create_executor

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


def cmd_debate(session_id: str = "", protocol: str = "NI", topic: str = "", participants: List[str] = None, 
               iterations: int = 10, output_file: str = "") -> Dict[str, Any]:
    """执行辩论协议。
    
    Usage: bridge_cli.py debate --session <session_id> --protocol <protocol_type> --topic <topic> 
                                 --participants <p1,p2,p3> --iterations <num> --output-file <file>
    """
    from datetime import datetime
    
    if participants is None:
        participants = ["Agent1", "Agent2"]  # 默认参与者
    
    # 创建会话管理器
    session_manager = SessionManager()
    
    # 获取或创建辩论会话
    if session_id:
        try:
            session = session_manager.get_session(session_id)
            if not session:
                # 尝试从文件加载会话
                session_path = os.path.join(session_manager.sessions_dir, f"{session_id}.json")
                if os.path.exists(session_path):
                    session = session_manager.load_session(session_path)
                else:
                    session = session_manager.create_session(name=f"Debate_{topic.replace(' ', '_')}", session_id=session_id)
        except:
            session = session_manager.create_session(name=f"Debate_{topic.replace(' ', '_')}", session_id=session_id)
    else:
        session = session_manager.create_session(name=f"Debate_{topic.replace(' ', '_')}")
        session_id = session.session_id
    
    # 设置预算限制
    analyzer = ConvergenceAnalyzer()
    analyzer.set_budget_limits({
        "iterations": iterations,
        "time": 3600  # 1小时
    })
    
    # 根据协议类型创建协议实例
    try:
        protocol_enum = ProtocolType(protocol.lower())
    except ValueError:
        # 如果协议名称无效，使用默认的NI协议
        protocol_enum = ProtocolType.NI
    
    protocol_instance = ProtocolFactory.create_protocol(protocol_enum)
    
    # 初始化辩论
    init_prompt = protocol_instance.initialize_debate(participants, topic)
    
    # 更新会话上下文
    session.add_context("topic", topic)
    session.add_context("protocol", protocol)
    session.add_context("participants", participants)
    session.add_context("initial_prompt", init_prompt)
    
    # 进行辩论迭代
    current_speaker_idx = 0
    agreement_history = []
    
    for i in range(iterations):
        session.increment_iteration()
        
        if session.is_budget_exhausted():
            break
            
        current_speaker = participants[current_speaker_idx]
        
        # 构建上下文
        context_builder = ContextBuilder()
        context_builder.set_topic(topic)
        for p in participants:
            role = "Participant"  # 在实际应用中，可以根据需要设置不同角色
            expertise = "General Knowledge"  # 在实际应用中，可以设置不同专长
            position = "Neutral"  # 在实际应用中，可以设置不同立场
            context_builder.add_participant(Participant(p, role, expertise, position))
        
        # 添加历史发言
        for stmt in session.statements:
            context_builder.add_statement(Statement(
                stmt["speaker"], 
                stmt["statement"], 
                datetime.fromisoformat(stmt["timestamp"]) if isinstance(stmt["timestamp"], str) else stmt["timestamp"]
            ))
        
        # 构建当前轮次的prompt
        current_prompt = context_builder.build_prompt(PromptType.REPLY, current_speaker=current_speaker)
        
        # 这里应该调用真实的AI模型来生成回应
        # 为了演示，我们使用模拟回应
        simulated_response = f"This is a simulated response from {current_speaker} in discussion about '{topic}'."
        
        # 记录发言
        session.add_statement(current_speaker, simulated_response)
        
        # 在实际应用中，这里会处理AI模型的输出并将其转换为协议所需的数据结构
        # 现在我们使用模拟数据进行演示
        input_data = {
            "stance": f"My stance on {topic}",
            "reasoning": f"My reasoning as {current_speaker}",
            "evidence": [f"Point from {current_speaker}"],
            "adjustment": 0.1,  # 立场调整程度
            "confidence": 0.8
        }
        
        # 处理当前回合
        turn_result = protocol_instance.process_turn(current_speaker, input_data)
        
        # 更新协议分数（模拟）
        agreement_score = 0.5 + (i * 0.05)  # 模拟协议分数随迭代提高
        agreement_score = min(agreement_score, 0.95)  # 限制最大分数
        agreement_history.append(agreement_score)
        analyzer.record_iteration(agreement_score)
        
        # 检查收敛
        convergence_status, details = analyzer.evaluate_state()
        if convergence_status in [ConvergenceStatus.CONVERGED, ConvergenceStatus.STALEMATE, ConvergenceStatus.BUDGET_EXHAUSTED]:
            session.update_convergence(agreement_score)
            break
            
        # 下一位发言人
        current_speaker_idx = (current_speaker_idx + 1) % len(participants)
    
    # 保存会话
    session_manager.save_session(session, f"debate_session_{session.session_id}.json")
    
    # 生成最终报告
    final_report = analyzer.get_final_report()
    
    result = {
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
    
    return result


# ─── CLI entry point with argparse ─────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
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
    route_parser.add_argument("task_type_pos", nargs="?", help="Type of task to route (positional, for backward compatibility)")
    route_parser.add_argument("--task-type", help="Type of task to route")
    route_parser.add_argument("feature_id_pos", nargs="?", default="", help="Feature ID (positional, for backward compatibility)")
    route_parser.add_argument("--feature-id", default="", help="Feature ID (optional)")

    # suggest command
    suggest_parser = subparsers.add_parser("suggest", help="Generate next-step suggestion")
    suggest_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    suggest_parser.add_argument("--project", help="Project name")

    # full command
    full_parser = subparsers.add_parser("full", help="Full flow: load + suggest")
    full_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    full_parser.add_argument("--project", help="Project name")

    # check-hermes command
    check_hermes_parser = subparsers.add_parser("check-hermes", help="Check if Hermes may execute")
    check_hermes_parser.add_argument("task_type_pos", nargs="?", help="Type of task to check (positional, for backward compatibility)")
    check_hermes_parser.add_argument("--task-type", help="Type of task to check")

    # dispatch command
    dispatch_parser = subparsers.add_parser("dispatch", help="Dispatch task to agent")
    dispatch_parser.add_argument("adapter_pos", nargs="?", help="Adapter name (positional, for backward compatibility)")
    dispatch_parser.add_argument("--adapter", help="Adapter name")
    dispatch_parser.add_argument("task_type_pos", nargs="?", help="Type of task to dispatch (positional, for backward compatibility)")
    dispatch_parser.add_argument("--task-type", help="Type of task to dispatch")
    dispatch_parser.add_argument("prompt_pos", nargs="?", default="", help="Prompt for the task (positional, for backward compatibility)")
    dispatch_parser.add_argument("--prompt", default="", help="Prompt for the task")
    dispatch_parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    dispatch_parser.add_argument("--feature-id", default="", help="Feature ID (optional)")

    # init command (from pipeline)
    init_parser = subparsers.add_parser("init", help="Initialize project")
    init_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    init_parser.add_argument("--project", help="Project name")
    init_parser.add_argument("--description", default="", help="Project description")
    init_parser.add_argument("--stack", default="", help="Tech stack")
    init_parser.add_argument("--force", action="store_true", help="Force overwrite existing directory")

    # advance command (from pipeline)
    advance_parser = subparsers.add_parser("advance", help="Advance to next phase")
    advance_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    advance_parser.add_argument("--project", help="Project name")

    # status command (from pipeline)
    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    status_parser.add_argument("--project", help="Project name")

    # resume command (from pipeline)
    resume_parser = subparsers.add_parser("resume", help="Resume project from checkpoint")
    resume_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    resume_parser.add_argument("--project", help="Project name")
    resume_parser.add_argument("--checkpoint-id", type=int, default=None, help="Checkpoint ID (default: latest)")

    # rollback command (from pipeline)
    rollback_parser = subparsers.add_parser("rollback", help="Rollback to specific checkpoint")
    rollback_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    rollback_parser.add_argument("--project", help="Project name")
    rollback_parser.add_argument("--checkpoint-id", type=int, required=True, help="Checkpoint ID")

    # rollback-phase command (from pipeline)
    rollback_phase_parser = subparsers.add_parser("rollback-phase", help="Rollback to a specific phase (requires approval)")
    rollback_phase_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    rollback_phase_parser.add_argument("--project", help="Project name")
    rollback_phase_parser.add_argument("--to", required=True, choices=phase_names(), help="Target phase")
    rollback_phase_parser.add_argument("--approved", action="store_true", help="Confirm manual approval")

    # approve command (from pipeline)
    approve_parser = subparsers.add_parser("approve", help="Manual approval for a specific phase")
    approve_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    approve_parser.add_argument("--project", help="Project name")
    approve_parser.add_argument("--phase", required=True, choices=["design", "accept"], help="Phase to approve")

    # mark-tests command (from pipeline)
    mark_tests_parser = subparsers.add_parser("mark-tests", help="Mark end-to-end test status")
    mark_tests_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    mark_tests_parser.add_argument("--project", help="Project name")
    mark_tests_parser.add_argument("--passed", action="store_true", help="Mark as passed")
    mark_tests_parser.add_argument("--failed", action="store_true", help="Mark as failed")

    # mode command
    mode_parser = subparsers.add_parser("mode", help="Show project mode")
    mode_parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
    mode_parser.add_argument("--project", help="Project name (optional)")

    # debate command
    debate_parser = subparsers.add_parser("debate", help="Run debate protocol")
    debate_parser.add_argument("--session", help="Session ID (optional, creates new session if not provided)")
    debate_parser.add_argument("--protocol", choices=["ni", "more", "samre"], default="ni", 
                              help="Debate protocol to use: NI (Negotiated Iteration), "
                                   "MORE (Multi-Objective Reasoning Exchange), "
                                   "SAMRE (Structured Argumentative Multi-Reasoning Exchange)")
    debate_parser.add_argument("--topic", required=True, help="Topic for the debate")
    debate_parser.add_argument("--participants", type=lambda x: x.split(","), 
                              help="Comma-separated list of participants (default: Agent1,Agent2)")
    debate_parser.add_argument("--iterations", type=int, default=10, 
                              help="Maximum number of iterations (default: 10)")
    debate_parser.add_argument("--output-file", help="File to save the debate output (optional)")

    return parser


def main():
    import shutil  # Import here to avoid issues with the original bridge_cli.py
    
    parser = build_parser()
    args = parser.parse_args()

    # Handle mark-tests passed/failed mutual exclusion
    if hasattr(args, 'failed') and args.failed:
        args.passed = False

    # Resolve positional vs named arguments
    # Use positional if named is not provided
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

    # Validate required arguments
    if args.command in ["load", "suggest", "full", "init", "advance", "status", "resume", "rollback", "rollback-phase", "approve", "mark-tests"]:
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

    # Map commands to functions
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
        # Convert args to namespace that pipeline expects
        pipeline_args = argparse.Namespace(
            project=args.project,
            description=args.description,
            stack=args.stack,
            force=args.force
        )
        result = {"command": "init", "return_code": pipeline_cmd_init(pipeline_args)}
    elif args.command == "advance":
        pipeline_args = argparse.Namespace(project=args.project)
        result = {"command": "advance", "return_code": pipeline_cmd_advance(pipeline_args)}
    elif args.command == "status":
        pipeline_args = argparse.Namespace(project=args.project)
        result = {"command": "status", "return_code": pipeline_cmd_status(pipeline_args)}
    elif args.command == "resume":
        pipeline_args = argparse.Namespace(project=args.project, checkpoint_id=args.checkpoint_id)
        result = {"command": "resume", "return_code": pipeline_cmd_resume(pipeline_args)}
    elif args.command == "rollback":
        pipeline_args = argparse.Namespace(project=args.project, checkpoint_id=args.checkpoint_id)
        result = {"command": "rollback", "return_code": pipeline_cmd_rollback(pipeline_args)}
    elif args.command == "rollback-phase":
        pipeline_args = argparse.Namespace(project=args.project, to=args.to, approved=args.approved)
        result = {"command": "rollback-phase", "return_code": pipeline_cmd_rollback_phase(pipeline_args)}
    elif args.command == "approve":
        pipeline_args = argparse.Namespace(project=args.project, phase=args.phase)
        result = {"command": "approve", "return_code": pipeline_cmd_approve(pipeline_args)}
    elif args.command == "mark-tests":
        pipeline_args = argparse.Namespace(project=args.project, passed=args.passed)
        result = {"command": "mark-tests", "return_code": pipeline_cmd_mark_tests(pipeline_args)}
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
        print(json.dumps({"error": f"Unknown command: {args.command}", "available": ["load", "route", "suggest", "full", "check-hermes", "dispatch", "init", "advance", "status", "resume", "rollback", "rollback-phase", "approve", "mark-tests", "mode", "debate"]}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()