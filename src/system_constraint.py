"""src/system_constraint.py — 系统级约束层 (F024)

自动约束任务路由，不是 Hermes 自我约束：
- 编码任务 → Claude Code
- 审核任务 → CodeWhale
- 测试任务 → Qwen Code
- Hermes 仅限编排权限，禁止直接执行编码/审核/测试任务

验收标准：
1. 约束层自动拦截违规操作
2. 约束层单元测试通过
3. Hermes 尝试编码被自动拦截
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple, Type


# ───────────────────────────────────────────────────────────────
# 异常定义
# ───────────────────────────────────────────────────────────────

class ConstraintViolation(Exception):
    """系统级约束违规异常

    当任务被路由到错误的 Agent，或 Hermes 尝试执行非编排操作时抛出。
    """

    def __init__(
        self,
        message: str,
        *,
        task_type: Optional[str] = None,
        attempted_agent: Optional[str] = None,
        required_agent: Optional[str] = None,
        action: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.task_type = task_type
        self.attempted_agent = attempted_agent
        self.required_agent = required_agent
        self.action = action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": str(self),
            "task_type": self.task_type,
            "attempted_agent": self.attempted_agent,
            "required_agent": self.required_agent,
            "action": self.action,
        }


class HermesPermissionDenied(ConstraintViolation):
    """Hermes 权限不足异常

    当 Hermes 尝试执行仅限其他 Agent 的操作时抛出。
    """
    pass


# ───────────────────────────────────────────────────────────────
# 任务类型枚举
# ───────────────────────────────────────────────────────────────

class TaskType(Enum):
    """系统支持的任务类型"""
    CODE = "code"           # 编码/开发任务
    REVIEW = "review"       # 代码审核任务
    TEST = "test"           # 测试任务（单元测试/E2E）
    DOC = "doc"             # 文档生成任务
    E2E = "e2e"             # E2E 测试任务
    ORCHESTRATE = "orchestrate"  # 编排/协调任务（仅限 Hermes）
    DEPLOY = "deploy"       # 部署任务
    ANALYZE = "analyze"   # 分析任务


# ───────────────────────────────────────────────────────────────
# Agent 标识枚举
# ───────────────────────────────────────────────────────────────

class Agent(Enum):
    """系统中可用的 Agent"""
    CLAUDE = "claude"       # Claude Code — 编码专家
    CODEWHALE = "codewhale" # CodeWhale — 审核专家
    QWEN = "qwen"           # Qwen Code — 测试专家
    HERMES = "hermes"       # Hermes — 编排器（仅协调）


# ───────────────────────────────────────────────────────────────
# 任务类型到 Agent 的映射（系统级约束核心）
# ───────────────────────────────────────────────────────────────

TASK_AGENT_MAP: Dict[TaskType, Agent] = {
    TaskType.CODE: Agent.CLAUDE,
    TaskType.REVIEW: Agent.CODEWHALE,
    TaskType.TEST: Agent.QWEN,
    TaskType.DOC: Agent.QWEN,
    TaskType.E2E: Agent.QWEN,
    TaskType.ORCHESTRATE: Agent.HERMES,
    TaskType.DEPLOY: Agent.HERMES,
    TaskType.ANALYZE: Agent.HERMES,
}

# Agent 能力映射（反向查询）
AGENT_CAPABILITIES: Dict[Agent, List[TaskType]] = {
    Agent.CLAUDE: [TaskType.CODE],
    Agent.CODEWHALE: [TaskType.REVIEW],
    Agent.QWEN: [TaskType.TEST, TaskType.DOC, TaskType.E2E],
    Agent.HERMES: [TaskType.ORCHESTRATE, TaskType.DEPLOY, TaskType.ANALYZE],
}


# ───────────────────────────────────────────────────────────────
# 约束配置
# ───────────────────────────────────────────────────────────────

@dataclass
class ConstraintConfig:
    """系统约束层配置"""
    # 是否启用严格模式（违规时抛出异常而非返回错误）
    strict_mode: bool = True
    # 是否允许 Hermes 在紧急模式下执行受限任务
    emergency_override: bool = False
    # 紧急模式密码（简单哈希校验）
    _emergency_password_hash: Optional[str] = None
    # 违规回调（用于日志/告警）
    violation_callbacks: List[Callable[[ConstraintViolation], None]] = field(default_factory=list)
    # 路由成功回调
    route_callbacks: List[Callable[[TaskType, Agent, Any], None]] = field(default_factory=list)

    def set_emergency_password(self, password: str) -> None:
        """设置紧急模式密码（存储简单哈希）"""
        import hashlib
        self._emergency_password_hash = hashlib.sha256(password.encode()).hexdigest()[:16]

    def verify_emergency_password(self, password: str) -> bool:
        """验证紧急模式密码"""
        if self._emergency_password_hash is None:
            return False
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()[:16] == self._emergency_password_hash


# ───────────────────────────────────────────────────────────────
# 系统约束层核心类
# ───────────────────────────────────────────────────────────────

class SystemConstraint:
    """系统级约束层

    负责：
    1. 自动路由任务到正确的 Agent
    2. 拦截 Hermes 的违规操作
    3. 记录和报告约束违规
    4. 支持紧急模式覆盖（需密码）

    使用示例：
        constraint = SystemConstraint()
        # 自动路由编码任务到 Claude
        result = constraint.route_task("code", {"feature_id": "F001"})
        # 拦截 Hermes 尝试编码
        constraint.hermes_only_orchestration("code")  # 抛出 ConstraintViolation
    """

    def __init__(self, config: Optional[ConstraintConfig] = None) -> None:
        self.config = config or ConstraintConfig()
        self._violation_count: int = 0
        self._route_count: int = 0
        self._emergency_active: bool = False
        self._emergency_until: float = 0.0

    # ───────────────────────────────────────────────────────────
    # 属性
    # ───────────────────────────────────────────────────────────

    @property
    def violation_count(self) -> int:
        """累计违规次数"""
        return self._violation_count

    @property
    def route_count(self) -> int:
        """累计路由次数"""
        return self._route_count

    @property
    def is_emergency_active(self) -> bool:
        """紧急模式是否激活"""
        import time
        if self._emergency_active and time.time() > self._emergency_until:
            self._emergency_active = False
        return self._emergency_active

    # ───────────────────────────────────────────────────────────
    # 核心路由方法
    # ───────────────────────────────────────────────────────────

    def route_task(
        self,
        task_type: str,
        spec: Dict[str, Any],
        *,
        requested_agent: Optional[str] = None,
        bypass_check: bool = False,
    ) -> Dict[str, Any]:
        """自动路由任务到正确的 Agent

        Args:
            task_type: 任务类型（code/review/test/doc/e2e/orchestrate/deploy/analyze）
            spec: 任务规格字典
            requested_agent: 请求指定的 Agent（可选，用于校验）
            bypass_check: 是否绕过约束检查（仅紧急模式）

        Returns:
            路由结果字典，包含目标 Agent 和任务规格

        Raises:
            ConstraintViolation: 当任务类型无效或路由冲突时
        """
        import time

        # 解析任务类型
        try:
            tt = TaskType(task_type)
        except ValueError:
            raise ConstraintViolation(
                f"Unknown task type: {task_type}",
                task_type=task_type,
                action="route_task",
            )

        # 确定目标 Agent
        target_agent = TASK_AGENT_MAP.get(tt)
        if target_agent is None:
            raise ConstraintViolation(
                f"No agent mapped for task type: {task_type}",
                task_type=task_type,
                action="route_task",
            )

        # 校验请求指定的 Agent（如果提供了）
        if requested_agent is not None:
            try:
                req_agent = Agent(requested_agent)
            except ValueError:
                raise ConstraintViolation(
                    f"Unknown requested agent: {requested_agent}",
                    task_type=task_type,
                    attempted_agent=requested_agent,
                    required_agent=target_agent.value,
                    action="route_task",
                )
            if req_agent != target_agent:
                raise ConstraintViolation(
                    f"Task '{task_type}' must be routed to '{target_agent.value}', "
                    f"but requested '{requested_agent}'",
                    task_type=task_type,
                    attempted_agent=requested_agent,
                    required_agent=target_agent.value,
                    action="route_task",
                )

        # 紧急模式检查
        if bypass_check and not self.is_emergency_active:
            raise ConstraintViolation(
                "Bypass check requires active emergency mode",
                task_type=task_type,
                action="route_task",
            )

        # 构建路由结果
        self._route_count += 1
        result = {
            "task_type": tt.value,
            "target_agent": target_agent.value,
            "spec": spec,
            "routed_at": time.time(),
            "constraint_enforced": True,
        }

        # 触发路由回调
        for cb in self.config.route_callbacks:
            try:
                cb(tt, target_agent, spec)
            except Exception:
                pass

        return result

    # ───────────────────────────────────────────────────────────
    # Hermes 权限检查
    # ───────────────────────────────────────────────────────────

    def hermes_only_orchestration(self, action: str) -> None:
        """检查 Hermes 是否仅执行编排操作

        Args:
            action: Hermes 尝试执行的操作类型

        Raises:
            HermesPermissionDenied: 当 Hermes 尝试执行非编排操作时
        """
        # 定义 Hermes 允许的操作
        allowed_actions = {
            "orchestrate", "route", "delegate", "monitor",
            "init", "advance", "check", "resume", "status",
            "deploy", "analyze", "coordinate", "schedule",
        }

        # 定义 Hermes 禁止的操作（必须由其他 Agent 执行）
        forbidden_actions = {
            "code", "write", "implement", "develop", "program",
            "review", "audit", "inspect", "check_code",
            "test", "run_test", "e2e_test", "playwright",
            "doc", "document", "generate_doc",
        }

        action_lower = action.lower().strip()

        # 直接匹配禁止列表
        if action_lower in forbidden_actions:
            self._violation_count += 1
            violation = HermesPermissionDenied(
                f"Hermes is not allowed to perform '{action}'. "
                f"This action must be delegated to the appropriate agent.",
                task_type=action_lower,
                attempted_agent="hermes",
                action=action,
            )
            self._notify_violation(violation)
            raise violation

        # 模糊匹配：检查 action 是否包含禁止关键词
        for forbidden in forbidden_actions:
            if forbidden in action_lower and action_lower not in allowed_actions:
                self._violation_count += 1
                violation = HermesPermissionDenied(
                    f"Hermes action '{action}' appears to contain forbidden operation '{forbidden}'. "
                    f"Hermes can only orchestrate, not execute tasks.",
                    task_type=action_lower,
                    attempted_agent="hermes",
                    action=action,
                )
                self._notify_violation(violation)
                raise violation

        # 检查 action 是否在允许列表中
        if action_lower not in allowed_actions:
            # 非严格模式下允许未知操作，但记录警告
            if self.config.strict_mode:
                self._violation_count += 1
                violation = HermesPermissionDenied(
                    f"Hermes action '{action}' is not in the allowed orchestration actions list. "
                    f"Allowed: {', '.join(sorted(allowed_actions))}",
                    task_type=action_lower,
                    attempted_agent="hermes",
                    action=action,
                )
                self._notify_violation(violation)
                raise violation

        # 允许的操作
        return

    def check_hermes_task(self, task_type: str) -> None:
        """检查 Hermes 是否可以执行指定类型的任务

        Args:
            task_type: 任务类型

        Raises:
            HermesPermissionDenied: 当 Hermes 不能执行该任务时
        """
        try:
            tt = TaskType(task_type)
        except ValueError:
            # 未知任务类型，在严格模式下拒绝
            if self.config.strict_mode:
                self._violation_count += 1
                raise HermesPermissionDenied(
                    f"Unknown task type '{task_type}', Hermes cannot execute it.",
                    task_type=task_type,
                    attempted_agent="hermes",
                    action="check_hermes_task",
                )
            return

        target_agent = TASK_AGENT_MAP.get(tt)
        if target_agent != Agent.HERMES:
            self._violation_count += 1
            violation = HermesPermissionDenied(
                f"Hermes cannot execute '{task_type}' tasks. "
                f"These must be routed to '{target_agent.value}'.",
                task_type=task_type,
                attempted_agent="hermes",
                required_agent=target_agent.value,
                action="check_hermes_task",
            )
            self._notify_violation(violation)
            raise violation

    # ───────────────────────────────────────────────────────────
    # 紧急模式
    # ───────────────────────────────────────────────────────────

    def activate_emergency(self, password: str, duration_seconds: float = 300.0) -> bool:
        """激活紧急模式（允许临时绕过约束）

        Args:
            password: 紧急模式密码
            duration_seconds: 紧急模式持续时间

        Returns:
            是否成功激活
        """
        import time
        if not self.config.verify_emergency_password(password):
            return False
        self._emergency_active = True
        self._emergency_until = time.time() + duration_seconds
        return True

    def deactivate_emergency(self) -> None:
        """手动关闭紧急模式"""
        self._emergency_active = False
        self._emergency_until = 0.0

    # ───────────────────────────────────────────────────────────
    # 批量路由
    # ───────────────────────────────────────────────────────────

    def route_batch(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量路由多个任务

        Args:
            tasks: 任务列表，每个任务包含 task_type 和 spec

        Returns:
            路由结果列表

        Raises:
            ConstraintViolation: 当任何任务路由失败时（strict_mode 下）
        """
        results: List[Dict[str, Any]] = []
        errors: List[ConstraintViolation] = []

        for task in tasks:
            task_type = task.get("task_type", "")
            spec = task.get("spec", {})
            try:
                result = self.route_task(task_type, spec)
                results.append(result)
            except ConstraintViolation as e:
                if self.config.strict_mode:
                    raise
                errors.append(e)
                results.append({
                    "task_type": task_type,
                    "error": str(e),
                    "constraint_enforced": True,
                    "routed": False,
                })

        return results

    # ───────────────────────────────────────────────────────────
    # 查询方法
    # ───────────────────────────────────────────────────────────

    def get_agent_for_task(self, task_type: str) -> Optional[str]:
        """获取任务类型对应的目标 Agent

        Args:
            task_type: 任务类型

        Returns:
            Agent 名称，或 None（如果任务类型无效）
        """
        try:
            tt = TaskType(task_type)
            agent = TASK_AGENT_MAP.get(tt)
            return agent.value if agent else None
        except ValueError:
            return None

    def get_allowed_tasks_for_agent(self, agent: str) -> List[str]:
        """获取 Agent 允许执行的任务类型列表

        Args:
            agent: Agent 名称

        Returns:
            任务类型列表
        """
        try:
            ag = Agent(agent)
            return [tt.value for tt in AGENT_CAPABILITIES.get(ag, [])]
        except ValueError:
            return []

    def can_agent_execute(self, agent: str, task_type: str) -> bool:
        """检查 Agent 是否可以执行指定任务类型

        Args:
            agent: Agent 名称
            task_type: 任务类型

        Returns:
            是否可以执行
        """
        try:
            ag = Agent(agent)
            tt = TaskType(task_type)
            return tt in AGENT_CAPABILITIES.get(ag, [])
        except ValueError:
            return False

    def is_hermes_action_allowed(self, action: str) -> bool:
        """检查 Hermes 操作是否被允许（不抛出异常）

        Args:
            action: 操作名称

        Returns:
            是否允许
        """
        try:
            self.hermes_only_orchestration(action)
            return True
        except HermesPermissionDenied:
            return False

    # ───────────────────────────────────────────────────────────
    # 内部方法
    # ───────────────────────────────────────────────────────────

    def _notify_violation(self, violation: ConstraintViolation) -> None:
        """通知约束违规"""
        for cb in self.config.violation_callbacks:
            try:
                cb(violation)
            except Exception:
                pass

    def reset_stats(self) -> None:
        """重置统计计数器"""
        self._violation_count = 0
        self._route_count = 0


# ───────────────────────────────────────────────────────────────
# 便捷函数
# ───────────────────────────────────────────────────────────────

def get_task_agent(task_type: str) -> Optional[str]:
    """获取任务类型对应的目标 Agent（全局便捷函数）"""
    try:
        tt = TaskType(task_type)
        agent = TASK_AGENT_MAP.get(tt)
        return agent.value if agent else None
    except ValueError:
        return None


def assert_hermes_orchestration(action: str) -> None:
    """断言 Hermes 仅执行编排操作（全局便捷函数）"""
    constraint = SystemConstraint()
    constraint.hermes_only_orchestration(action)


def route_task(task_type: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """路由任务到正确 Agent（全局便捷函数）"""
    constraint = SystemConstraint()
    return constraint.route_task(task_type, spec)
