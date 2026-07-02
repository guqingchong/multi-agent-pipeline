"""src/fallback_manager.py — 降级路径管理器

F017 实现：
- 当主 Coder (Claude) 不可用时自动降级到 Qwen
- 与 CircuitBreaker 集成，根据熔断状态触发降级
- 支持降级策略配置和恢复检测
- 支持多级降级链（Claude → Qwen → CodeWhale）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, TypeVar

try:
    from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState, ResilienceManager
    from adapters import (
        BaseAdapter,
        AgentResult,
        AdapterStatus,
        ClaudeCodeAdapter,
        QwenCodeAdapter,
        create_adapter,
    )
except ImportError:
    from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState, ResilienceManager
    from src.adapters import (
        BaseAdapter,
        AgentResult,
        AdapterStatus,
        ClaudeCodeAdapter,
        QwenCodeAdapter,
        create_adapter,
    )

T = TypeVar("T")


# ───────────────────────────────────────────────────────────────
# 降级状态
# ───────────────────────────────────────────────────────────────


class FallbackStatus(Enum):
    """降级路径状态"""
    PRIMARY_ACTIVE = auto()      # 主 Adapter 正常运行
    FALLBACK_ACTIVE = auto()    # 已降级到备用 Adapter
    DEGRADED_MODE = auto()       # 降级模式运行中
    RECOVERY_PENDING = auto()    # 等待主 Adapter 恢复
    ALL_FAILED = auto()          # 所有 Adapter 都失败


# ───────────────────────────────────────────────────────────────
# 降级配置
# ───────────────────────────────────────────────────────────────


@dataclass
class FallbackConfig:
    """降级路径配置"""

    primary: str = "claude"                      # 主 Adapter
    fallback_chain: List[str] = field(default_factory=lambda: ["qwen", "codewhale"])
    auto_recover: bool = True                    # 是否自动检测恢复
    recover_check_interval: int = 30             # 恢复检测间隔（秒）
    max_fallback_attempts: int = 3               # 最大降级尝试次数
    fallback_timeout_multiplier: float = 1.5     # 降级时Timeout倍数


# ───────────────────────────────────────────────────────────────
# 降级管理器
# ───────────────────────────────────────────────────────────────


@dataclass
class FallbackManager:
    """降级路径管理器

    管理主 Adapter → 备用 Adapter 的自动降级和恢复。
    """

    config: FallbackConfig = field(default_factory=FallbackConfig)
    resilience: Optional[ResilienceManager] = None
    _current_adapter: Optional[BaseAdapter] = None
    _current_adapter_name: str = ""
    _status: FallbackStatus = FallbackStatus.PRIMARY_ACTIVE
    _fallback_count: int = 0
    _last_primary_failure_time: float = 0.0
    _last_recover_check: float = 0.0
    _history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.resilience is None:
            self.resilience = ResilienceManager()

    @property
    def status(self) -> FallbackStatus:
        return self._status

    @property
    def current_adapter_name(self) -> str:
        return self._current_adapter_name

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    def _record_event(self, event: str, detail: Dict[str, Any]) -> None:
        self._history.append({
            "timestamp": time.time(),
            "event": event,
            "detail": detail,
        })

    def _create_adapter(self, name: str) -> BaseAdapter:
        """创建 Adapter 实例"""
        timeout = 60.0
        if self._fallback_count > 0:
            timeout *= self.config.fallback_timeout_multiplier
        return create_adapter(name, timeout_seconds=timeout)

    def _is_primary_available(self) -> bool:
        """检查主 Adapter 是否可用"""
        if self.resilience is None:
            return True
        breaker = self.resilience.get_breaker(self.config.primary)
        return breaker.can_execute()

    def get_active_adapter(self) -> BaseAdapter:
        """获取当前活动的 Adapter（自动降级逻辑）

        如果主 Adapter 可用，返回主 Adapter；
        如果主 Adapter 不可用，按降级链尝试备用 Adapter。
        """
        # 检查主 Adapter
        if self._is_primary_available():
            if self._status != FallbackStatus.PRIMARY_ACTIVE:
                # 主 Adapter 恢复了
                self._status = FallbackStatus.PRIMARY_ACTIVE
                self._fallback_count = 0
                self._record_event("primary_recovered", {"primary": self.config.primary})
            self._current_adapter = self._create_adapter(self.config.primary)
            self._current_adapter_name = self.config.primary
            return self._current_adapter

        # 主 Adapter 不可用，触发降级
        self._last_primary_failure_time = time.time()
        if self._status == FallbackStatus.PRIMARY_ACTIVE:
            self._status = FallbackStatus.FALLBACK_ACTIVE
            self._record_event("fallback_triggered", {"primary": self.config.primary})

        # 按降级链尝试
        for name in self.config.fallback_chain:
            if self.resilience is not None:
                breaker = self.resilience.get_breaker(name)
                if not breaker.can_execute():
                    continue
            try:
                adapter = self._create_adapter(name)
                self._current_adapter = adapter
                self._current_adapter_name = name
                self._fallback_count += 1
                self._status = FallbackStatus.DEGRADED_MODE
                self._record_event("fallback_activated", {
                    "fallback": name,
                    "fallback_count": self._fallback_count,
                })
                return adapter
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                continue

        # 所有降级都失败
        self._status = FallbackStatus.ALL_FAILED
        self._record_event("all_failed", {"chain": self.config.fallback_chain})
        raise RuntimeError(
            f"All adapters failed. Primary: {self.config.primary}, "
            f"fallback chain: {self.config.fallback_chain}"
        )

    def execute_with_fallback(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None,
        validate_fn: Optional[Callable[[AgentResult], bool]] = None,
    ) -> AgentResult:
        """执行任务，带自动降级

        先尝试主 Adapter，失败时自动降级到备用 Adapter。
        """
        context = context or {}
        last_error: Optional[Exception] = None
        attempted: List[str] = []

        # 优先尝试主 Adapter
        primary_available = self._is_primary_available()
        adapters_to_try: List[str] = []
        if primary_available:
            adapters_to_try.append(self.config.primary)
        adapters_to_try.extend(self.config.fallback_chain)

        for name in adapters_to_try:
            if name in attempted:
                continue
            attempted.append(name)

            try:
                adapter = self._create_adapter(name)
                result = adapter.run_with_tolerance(task, context)

                # 验证结果
                if validate_fn is not None and not validate_fn(result):
                    result.success = False
                    result.status = AdapterStatus.FAILED
                    result.error_message = result.error_message or "Validation failed"

                # 记录结果到熔断器
                if self.resilience is not None:
                    breaker = self.resilience.get_breaker(name)
                    if result.success:
                        breaker.record_success()
                    else:
                        breaker.record_failure()

                if result.success:
                    self._current_adapter = adapter
                    self._current_adapter_name = name
                    if name == self.config.primary:
                        self._status = FallbackStatus.PRIMARY_ACTIVE
                        self._fallback_count = 0
                    else:
                        self._status = FallbackStatus.DEGRADED_MODE
                        if name not in (self.config.fallback_chain[:1]):
                            self._fallback_count += 1
                    self._record_event("execute_success", {
                        "adapter": name,
                        "task": task[:100],
                    })
                    return result

                # 执行失败但未抛异常，继续尝试下一个
                last_error = Exception(result.error_message or f"Adapter {name} returned failure")

            except CircuitBreakerOpenError as e:
                last_error = e
                if self.resilience is not None:
                    self.resilience.get_breaker(name).record_failure()
                continue
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
                last_error = e
                if self.resilience is not None:
                    self.resilience.get_breaker(name).record_failure()
                continue

        # 所有尝试都失败
        self._status = FallbackStatus.ALL_FAILED
        self._record_event("execute_all_failed", {
            "attempted": attempted,
            "task": task[:100],
        })
        return AgentResult(
            success=False,
            output="",
            status=AdapterStatus.FAILED,
            error_message=f"All adapters failed after {len(attempted)} attempts. Last error: {last_error}",
        )

    def check_recovery(self) -> bool:
        """检查主 Adapter 是否已恢复

        Returns True if primary adapter is available again.
        """
        if self._status == FallbackStatus.PRIMARY_ACTIVE:
            return True

        now = time.time()
        if now - self._last_recover_check < self.config.recover_check_interval:
            return False
        self._last_recover_check = now

        if self._is_primary_available():
            self._status = FallbackStatus.RECOVERY_PENDING
            self._record_event("recovery_detected", {"primary": self.config.primary})
            return True
        return False

    def confirm_recovery(self) -> None:
        """确认恢复，切换回主 Adapter"""
        if self._status == FallbackStatus.RECOVERY_PENDING:
            self._status = FallbackStatus.PRIMARY_ACTIVE
            self._fallback_count = 0
            self._current_adapter = None
            self._current_adapter_name = ""
            self._record_event("recovery_confirmed", {"primary": self.config.primary})

    def reset(self) -> None:
        """重置降级状态"""
        self._status = FallbackStatus.PRIMARY_ACTIVE
        self._fallback_count = 0
        self._current_adapter = None
        self._current_adapter_name = ""
        self._last_primary_failure_time = 0.0
        self._last_recover_check = 0.0
        self._history.clear()
        if self.resilience is not None:
            self.resilience.reset_all()

    def get_history(self) -> List[Dict[str, Any]]:
        """获取降级历史"""
        return self._history.copy()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self._status.name,
            "current_adapter": self._current_adapter_name,
            "primary": self.config.primary,
            "fallback_chain": self.config.fallback_chain,
            "fallback_count": self._fallback_count,
            "auto_recover": self.config.auto_recover,
            "history_count": len(self._history),
        }


# ───────────────────────────────────────────────────────────────
# 便捷函数
# ───────────────────────────────────────────────────────────────


def create_claude_to_qwen_fallback(
    auto_recover: bool = True,
    resilience: Optional[ResilienceManager] = None,
) -> FallbackManager:
    """创建 Claude → Qwen 降级管理器"""
    config = FallbackConfig(
        primary="claude",
        fallback_chain=["qwen"],
        auto_recover=auto_recover,
    )
    return FallbackManager(config=config, resilience=resilience)


def execute_with_claude_qwen_fallback(
    task: str,
    context: Optional[Dict[str, Any]] = None,
    resilience: Optional[ResilienceManager] = None,
) -> AgentResult:
    """一键执行：Claude 优先，不可用时自动降级到 Qwen"""
    manager = create_claude_to_qwen_fallback(resilience=resilience)
    return manager.execute_with_fallback(task, context)
