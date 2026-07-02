"""src/registry.py — 统一注册表（唯一真相源）

Phase 1 第 1 步：纯数据模块，零依赖项目内其他模块。
仅使用 Python 标准库，所有配置在模块导入时注册完成，
并通过 REGISTRY.mark_ready() 显式标记为可用。

后续迁移步骤将逐步让 system_constraint / adapters / pipeline_executor /
config / phase_checks / message_queue 等模块从 REGISTRY 读取定义，
而非使用各自的硬编码常量。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ───────────────────────────────────────────────────────────────
# 数据类定义
# ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentDef:
    """Agent（真实 CLI Agent）定义。

    Attributes:
        name: Agent 唯一标识名，如 claude-code。
        capabilities: 该 Agent 可执行的任务类型列表。
        cli_path: 可执行文件名称或路径（运行时由调用方解析为绝对路径）。
        cli_command: 命令模板，使用 {prompt} 作为提示词占位符。
        env_vars: Agent 运行所需的环境变量（静态默认值，运行时可覆盖）。
    """
    name: str
    capabilities: List[str] = field(default_factory=list)
    cli_path: str = ""
    cli_command: str = "{prompt}"
    env_vars: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseDef:
    """Pipeline Phase 定义。

    Attributes:
        name: Phase 唯一标识名，如 init / design / verify。
        check_func: 对应检查函数名（字符串），运行期由 phase_checks 模块解析。
        requires_evidence: 该 Phase 推进时是否需要人工审批或验证证据。
    """
    name: str
    check_func: str = ""
    requires_evidence: bool = False


@dataclass(frozen=True)
class TaskTypeDef:
    """任务类型定义。

    Attributes:
        name: 任务类型唯一标识名，如 code / review / test。
        default_agent: 该任务类型默认派发的 Agent 名（空字符串表示 Hermes 自身处理）。
    """
    name: str
    default_agent: str = ""


# Task type names must start with a lowercase letter and contain only
# lowercase letters, digits, underscores, and hyphens.
_TASK_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


# ───────────────────────────────────────────────────────────────
# 注册表实现
# ───────────────────────────────────────────────────────────────


class Registry:
    """统一注册表，单例模式。

    提供 agents / phases / task_types 三个只读风格注册表，
    并通过 mark_ready() 显式标记注册完成，便于启动脚本检测。
    """

    def __init__(self) -> None:
        # Agent 注册表：name -> AgentDef
        self.agents: Dict[str, AgentDef] = {}
        # Phase 注册表：name -> PhaseDef
        self.phases: Dict[str, PhaseDef] = {}
        # 任务类型注册表：name -> TaskTypeDef
        self.task_types: Dict[str, TaskTypeDef] = {}
        self._ready: bool = False

    def register_agent(self, agent: AgentDef) -> None:
        """注册一个 Agent 定义。"""
        self.agents[agent.name] = agent

    def register_phase(self, phase: PhaseDef) -> None:
        """注册一个 Phase 定义。"""
        self.phases[phase.name] = phase

    def register_task_type(self, task_type: TaskTypeDef) -> None:
        """注册一个任务类型定义。"""
        if not _TASK_TYPE_NAME_RE.match(task_type.name):
            raise ValueError(
                f"Invalid task type name {task_type.name!r}; must match ^[a-z][a-z0-9_-]*$"
            )
        self.task_types[task_type.name] = task_type

    def mark_ready(self) -> None:
        """所有定义注册完成后显式标记注册表可用。"""
        self._ready = True

    def is_ready(self) -> bool:
        """返回注册表是否已完成注册。"""
        return self._ready

    def get_agent(self, name: str) -> Optional[AgentDef]:
        """按名称获取 Agent 定义。"""
        return self.agents.get(name)

    def get_phase(self, name: str) -> Optional[PhaseDef]:
        """按名称获取 Phase 定义。"""
        return self.phases.get(name)

    def get_task_type(self, name: str) -> Optional[TaskTypeDef]:
        """按名称获取任务类型定义。"""
        return self.task_types.get(name)

    def list_agents(self) -> List[str]:
        """返回已注册 Agent 名称列表。"""
        return list(self.agents.keys())

    def list_phases(self) -> List[str]:
        """返回已注册 Phase 名称列表。"""
        return list(self.phases.keys())

    def list_task_types(self) -> List[str]:
        """返回已注册任务类型名称列表。"""
        return list(self.task_types.keys())


# ───────────────────────────────────────────────────────────────
# 全局单例
# ───────────────────────────────────────────────────────────────

REGISTRY = Registry()


# ───────────────────────────────────────────────────────────────
# Agent CLI path resolution
# ───────────────────────────────────────────────────────────────


def _resolve_cli_path(agent_name: str, fallback: str) -> str:
    """Resolve agent CLI path: env > config.agent_cli_paths > registry fallback > PATH."""
    env_key = f"AGENT_CLI_PATH_{agent_name.upper().replace('-', '_')}"
    env_path = os.environ.get(env_key)
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())

    # Consult configurable agent CLI paths only after the registry has finished
    # loading, to avoid a circular import when config.py imports registry.py.
    if REGISTRY.is_ready():
        try:
            from config import get_config
        except ImportError:
            from src.config import get_config
        config_path = get_config().agent_cli_paths.get(agent_name)
        if config_path and Path(config_path).exists():
            return str(Path(config_path).resolve())

    if fallback and Path(fallback).exists():
        return str(Path(fallback).resolve())
    which = shutil.which(agent_name)
    if which:
        return which
    return fallback  # return fallback so health check can report missing


# ───────────────────────────────────────────────────────────────
# 注册 Agent（3 个真实 CLI Agent）
# ───────────────────────────────────────────────────────────────

# Claude Code：主力编码 Agent，也负责对抗审查中的挑战方
REGISTRY.register_agent(AgentDef(
    name="claude-code",
    capabilities=["code", "adversarial"],
    cli_path=_resolve_cli_path("claude-code", r"claude.cmd"),
    cli_command="-p {prompt}",
    env_vars={
        "CLAUDE_CODE_SIMPLE": "1",
    },
))

# CodeWhale：主力代码审核 Agent
REGISTRY.register_agent(AgentDef(
    name="codewhale",
    capabilities=["review"],
    cli_path=_resolve_cli_path("codewhale", r"codewhale-tui.exe"),
    cli_command="exec --auto {prompt}",
    env_vars={
        "AGENT_MOCK": "false",        # true 时返回 mock 结果，用于测试
    },
))

# Qwen Code：测试、文档、E2E、独立审查
REGISTRY.register_agent(AgentDef(
    name="qwen-code",
    capabilities=["test", "doc", "e2e", "inspector"],
    cli_path=_resolve_cli_path("qwen-code", r"qwen.cmd"),
    cli_command="prompt {prompt}",
    env_vars={
        "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1",
    },
))


# ───────────────────────────────────────────────────────────────
# 注册 Phase
# ───────────────────────────────────────────────────────────────

# Brownfield（存量优化）7 个 Phase
BROWNFIELD_PHASES = [
    ("discover",   "check_discover",   False),  # 发现现状
    ("benchmark",  "check_benchmark",  False),  # 对标基准
    ("analyze",    "check_analyze",    False),  # 分析瓶颈
    ("plan",       "check_plan",       False),  # 制定计划
    ("execute",    "check_execute",    False),  # 执行优化
    ("verify",     "check_verify",     True),   # 验证结果（需证据）
    ("deliver",    "check_deliver",    True),   # 交付验收（需证据）
]

# Greenfield（新建项目）12 个 Phase
GREENFIELD_PHASES = [
    ("init",       "check_init",       False),  # 初始化项目
    ("design",     "check_design",     True),   # 架构设计（需人类审批）
    ("decompose",  "check_decompose",  False),  # 任务分解
    ("research",   "check_research",   False),  # 调研选型
    ("prd",        "check_prd",        False),  # 产品需求文档
    ("journey",    "check_journey",    False),  # 用户旅程设计
    ("develop",    "check_develop",    False),  # 编码开发
    ("integrate",  "check_integrate",  False),  # 集成联调
    ("test",       "check_test",       False),  # 测试验证
    ("evaluate",   "check_evaluate",   False),  # 评估验收
    ("accept",     "check_accept",     True),   # 最终验收（需 verify 证据）
    ("deploy",     "check_deploy",     False),  # 部署上线
]

# Legacy compatibility phases (F005 3-state machine)
LEGACY_PHASES = [
    ("review", "check_review", False),  # 代码审核（legacy，兼容旧状态机）
]

for _name, _check, _evidence in BROWNFIELD_PHASES + GREENFIELD_PHASES + LEGACY_PHASES:
    REGISTRY.register_phase(PhaseDef(
        name=_name,
        check_func=_check,
        requires_evidence=_evidence,
    ))


# ───────────────────────────────────────────────────────────────
# 注册 TaskType（7 个可派发给真实 Agent 的任务类型）
# ───────────────────────────────────────────────────────────────

# 编排(orchestrate)、部署(deploy)、分析(analyze) 由 Hermes 自身处理，
# 故不纳入这 7 个可派发任务类型。
TASK_TYPE_DEFAULTS = [
    ("code",        "claude-code"),   # 编码/开发
    ("review",      "codewhale"),     # 代码审核
    ("test",        "qwen-code"),     # 单元测试
    ("doc",         "qwen-code"),     # 文档生成
    ("e2e",         "qwen-code"),     # E2E 测试
    ("inspector",   "qwen-code"),     # 独立审查
    ("adversarial", "claude-code"),   # 对抗审查（挑战方）
    ("orchestrate", ""),              # 编排/协调任务（Hermes 自身）
    ("deploy",      ""),              # 部署任务（Hermes 自身）
    ("analyze",     ""),              # 分析任务（Hermes 自身）
    ("shutdown",    ""),              # 关闭任务，由 Hermes 自身处理
]

for _name, _agent in TASK_TYPE_DEFAULTS:
    REGISTRY.register_task_type(TaskTypeDef(
        name=_name,
        default_agent=_agent,
    ))


# ───────────────────────────────────────────────────────────────
# 显式标记注册完成
# ───────────────────────────────────────────────────────────────

REGISTRY.mark_ready()
