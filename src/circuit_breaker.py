"""src/circuit_breaker.py — Circuit Breaker 与五级降级策略

F010 实现：
- CircuitBreaker: 连续失败3次熔断，300秒恢复，半开状态最多2次调用
- DegradationStrategy: green → yellow → orange → red → black 五级降级
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

T = TypeVar("T")


# ───────────────────────────────────────────────────────────────
# Circuit Breaker
# ───────────────────────────────────────────────────────────────

class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = auto()      # 正常，允许调用
    OPEN = auto()        # 熔断，拒绝调用
    HALF_OPEN = auto()   # 半开，允许有限试探


class CircuitBreakerOpenError(Exception):
    """熔断器打开时抛出的异常"""
    pass


@dataclass
class CircuitBreaker:
    """熔断器实现

    参数：
        failure_threshold:   连续失败多少次后打开熔断器（默认 3）
        recovery_timeout:      打开后等待多少秒进入半开（默认 300）
        half_open_max_calls:   半开状态最多允许多少次试探调用（默认 2）
        name:                  熔断器名称（用于日志/监控）
    """

    failure_threshold: int = 3
    recovery_timeout: int = 300
    half_open_max_calls: int = 2
    name: str = "default"

    # 内部状态（不暴露给构造器）
    _state: CircuitState = field(default=CircuitState.CLOSED, repr=False)
    _failure_count: int = field(default=0, repr=False)
    _success_count: int = field(default=0, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)
    _half_open_calls: int = field(default=0, repr=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def success_count(self) -> int:
        return self._success_count

    def can_execute(self) -> bool:
        """检查当前是否允许执行调用"""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                return True
            return False
        if self._state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False

    def record_success(self) -> None:
        """记录一次成功调用"""
        self._success_count += 1
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                # 半开状态试探成功足够次数，关闭熔断器
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """记录一次失败调用"""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            # 半开状态失败，立即重新打开
            self._state = CircuitState.OPEN
            self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """在熔断器保护下执行函数

        若熔断器打开，抛出 CircuitBreakerOpenError。
        若函数执行失败，记录失败并重新抛出原异常。
        """
        if not self.can_execute():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN. "
                f"Wait {self.recovery_timeout}s before retry."
            )

        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def reset(self) -> None:
        """手动重置熔断器到关闭状态"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0

    def to_dict(self) -> dict:
        """序列化当前状态"""
        return {
            "name": self.name,
            "state": self._state.name,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "half_open_max_calls": self.half_open_max_calls,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "half_open_calls": self._half_open_calls,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CircuitBreaker":
        """从字典反序列化"""
        cb = cls(
            failure_threshold=data.get("failure_threshold", 3),
            recovery_timeout=data.get("recovery_timeout", 300),
            half_open_max_calls=data.get("half_open_max_calls", 2),
            name=data.get("name", "default"),
        )
        cb._state = CircuitState[data.get("state", "CLOSED")]
        cb._failure_count = data.get("failure_count", 0)
        cb._success_count = data.get("success_count", 0)
        cb._last_failure_time = data.get("last_failure_time", 0.0)
        cb._half_open_calls = data.get("half_open_calls", 0)
        return cb


# ───────────────────────────────────────────────────────────────
# 五级降级策略
# ───────────────────────────────────────────────────────────────

class DegradationLevel(Enum):
    """降级等级"""
    GREEN = "green"      # 全部 Agent 可用，正常流水线
    YELLOW = "yellow"    # 辅助 Agent 不可用 → 跳过其环节
    ORANGE = "orange"    # 审查 Agent 不可用 → 编码 Agent 自审 + 增加测试
    RED = "red"          # 主 Coder 不可用 → 降级到辅助 Coder
    BLACK = "black"      # Orchestrator 不可用 → 系统暂停


@dataclass
class DegradationStrategy:
    """系统降级策略

    五级降级：green → yellow → orange → red → black
    """

    _current_level: DegradationLevel = field(
        default=DegradationLevel.GREEN, repr=False
    )

    # 各等级描述
    LEVEL_DESCRIPTIONS: Dict[str, str] = field(default_factory=lambda: {
        "green":  "全部 Agent 可用，正常流水线",
        "yellow": "某个辅助 Agent 不可用 → 跳过其环节，增加其他 Agent 的验证强度",
        "orange": "审查 Agent 不可用 → 编码 Agent 自审 + 增加自动化测试覆盖率要求",
        "red":    "主 Coder 不可用 → 降级到辅助 Coder，降低任务复杂度预期",
        "black":  "Orchestrator 不可用 → 系统暂停，保存所有状态，等待恢复",
    }, repr=False)

    @property
    def current_level(self) -> DegradationLevel:
        return self._current_level

    @property
    def current_description(self) -> str:
        return self.LEVEL_DESCRIPTIONS.get(self._current_level.value, "未知状态")

    def degrade(self) -> DegradationLevel:
        """降级到下一等级，返回新等级"""
        order = [
            DegradationLevel.GREEN,
            DegradationLevel.YELLOW,
            DegradationLevel.ORANGE,
            DegradationLevel.RED,
            DegradationLevel.BLACK,
        ]
        idx = order.index(self._current_level)
        if idx < len(order) - 1:
            self._current_level = order[idx + 1]
        return self._current_level

    def recover(self) -> DegradationLevel:
        """恢复到上一等级，返回新等级"""
        order = [
            DegradationLevel.GREEN,
            DegradationLevel.YELLOW,
            DegradationLevel.ORANGE,
            DegradationLevel.RED,
            DegradationLevel.BLACK,
        ]
        idx = order.index(self._current_level)
        if idx > 0:
            self._current_level = order[idx - 1]
        return self._current_level

    def set_level(self, level: DegradationLevel | str) -> DegradationLevel:
        """直接设置等级"""
        if isinstance(level, str):
            self._current_level = DegradationLevel(level)
        else:
            self._current_level = level
        return self._current_level

    def is_operational(self) -> bool:
        """系统是否仍在运行（非 black 状态）"""
        return self._current_level != DegradationLevel.BLACK

    def is_full_capacity(self) -> bool:
        """系统是否全容量运行（green 状态）"""
        return self._current_level == DegradationLevel.GREEN

    def get_actions(self) -> List[str]:
        """获取当前等级建议的操作列表"""
        actions = {
            "green": [
                "正常流水线执行",
                "所有 Agent 参与",
            ],
            "yellow": [
                "跳过不可用辅助 Agent 环节",
                "增加其他 Agent 验证强度",
                "记录不可用 Agent 日志",
            ],
            "orange": [
                "编码 Agent 启用自审模式",
                "自动化测试覆盖率要求 ≥ 80%",
                "增加静态分析检查",
            ],
            "red": [
                "主 Coder 切换到辅助 Coder",
                "降低任务复杂度预期",
                "拆分大任务为子任务",
                "增加人工审查节点",
            ],
            "black": [
                "系统暂停所有新任务",
                "保存所有状态到持久化存储",
                "触发告警通知人工介入",
                "等待 Orchestrator 恢复",
            ],
        }
        return actions.get(self._current_level.value, [])

    def to_dict(self) -> dict:
        return {
            "current_level": self._current_level.value,
            "description": self.current_description,
            "operational": self.is_operational(),
            "actions": self.get_actions(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DegradationStrategy":
        ds = cls()
        level_str = data.get("current_level", "green")
        ds._current_level = DegradationLevel(level_str)
        return ds


# ───────────────────────────────────────────────────────────────
# 集成：带熔断器的降级管理器
# ───────────────────────────────────────────────────────────────

@dataclass
class ResilienceManager:
    """弹性管理器：整合 Circuit Breaker + Degradation Strategy

    为每个 Agent / 服务维护一个熔断器，并根据熔断器状态触发系统降级。
    """

    breakers: Dict[str, CircuitBreaker] = field(default_factory=dict)
    degradation: DegradationStrategy = field(
        default_factory=DegradationStrategy
    )

    def get_breaker(self, name: str) -> CircuitBreaker:
        """获取或创建指定名称的熔断器"""
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(name=name)
        return self.breakers[name]

    def call_with_breaker(
        self, name: str, fn: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        """在指定熔断器保护下执行函数"""
        breaker = self.get_breaker(name)
        return breaker.call(fn, *args, **kwargs)

    def check_and_degrade(self) -> DegradationLevel:
        """检查所有熔断器状态，必要时触发降级"""
        # 统计各状态
        open_count = 0
        half_open_count = 0
        critical_open = False  # orchestrator 或主 coder 熔断

        critical_services = {"orchestrator", "main_coder", "hermes"}

        for name, breaker in self.breakers.items():
            if breaker.state == CircuitState.OPEN:
                open_count += 1
                if name.lower() in critical_services:
                    critical_open = True
            elif breaker.state == CircuitState.HALF_OPEN:
                half_open_count += 1

        # 根据熔断情况决定降级等级
        if critical_open:
            self.degradation.set_level(DegradationLevel.BLACK)
        elif open_count >= 2:
            self.degradation.set_level(DegradationLevel.RED)
        elif open_count == 1:
            # 判断是审查 Agent 还是辅助 Agent
            reviewer_open = any(
                self.breakers.get(n, CircuitBreaker()).state == CircuitState.OPEN
                for n in {"reviewer", "codewhale", "review"}
            )
            if reviewer_open:
                self.degradation.set_level(DegradationLevel.ORANGE)
            else:
                self.degradation.set_level(DegradationLevel.YELLOW)
        elif half_open_count > 0:
            # 半开状态保持当前或 yellow
            if self.degradation.current_level == DegradationLevel.GREEN:
                self.degradation.set_level(DegradationLevel.YELLOW)
        else:
            # 全部关闭，恢复 green
            self.degradation.set_level(DegradationLevel.GREEN)

        return self.degradation.current_level

    def reset_all(self) -> None:
        """重置所有熔断器和降级状态"""
        for breaker in self.breakers.values():
            breaker.reset()
        self.degradation.set_level(DegradationLevel.GREEN)

    def to_dict(self) -> dict:
        return {
            "breakers": {
                name: breaker.to_dict()
                for name, breaker in self.breakers.items()
            },
            "degradation": self.degradation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResilienceManager":
        rm = cls()
        for name, breaker_data in data.get("breakers", {}).items():
            rm.breakers[name] = CircuitBreaker.from_dict(breaker_data)
        rm.degradation = DegradationStrategy.from_dict(
            data.get("degradation", {})
        )
        return rm
