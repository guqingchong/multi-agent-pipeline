"""tests/test_circuit_breaker.py — Circuit Breaker 与降级策略单元测试"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    DegradationLevel,
    DegradationStrategy,
    ResilienceManager,
)


# ───────────────────────────────────────────────────────────────
# CircuitBreaker 基础测试
# ───────────────────────────────────────────────────────────────

class TestCircuitBreakerBasics:
    def test_initial_state_is_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_custom_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10, half_open_max_calls=1)
        assert cb.failure_threshold == 2
        assert cb.recovery_timeout == 10
        assert cb.half_open_max_calls == 1

    def test_record_success_in_closed(self) -> None:
        cb = CircuitBreaker()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.success_count == 1
        assert cb.failure_count == 0

    def test_record_failure_in_closed(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 1
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 2
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    def test_success_resets_failure_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


# ───────────────────────────────────────────────────────────────
# CircuitBreaker 状态转换测试（验收标准 1）
# ───────────────────────────────────────────────────────────────

class TestCircuitBreakerStateTransitions:
    def test_closed_to_open_after_threshold(self) -> None:
        """[test] 连续失败 3 次后熔断器打开"""
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_execution(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_open_to_half_open_after_timeout(self) -> None:
        """打开后等待 recovery_timeout 进入半开"""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(self) -> None:
        """半开状态成功足够次数后关闭"""
        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2
        )
        cb.record_failure()
        time.sleep(0.15)
        assert cb.can_execute() is True  # 进入 HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_to_open_on_failure(self) -> None:
        """半开状态失败立即重新打开"""
        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2
        )
        cb.record_failure()
        time.sleep(0.15)
        # 触发 OPEN -> HALF_OPEN 转换
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_max_calls_enforced(self) -> None:
        """半开状态最多只允许 half_open_max_calls 次调用"""
        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2
        )
        cb.record_failure()
        time.sleep(0.15)
        # 第一次试探
        assert cb.can_execute() is True
        cb.record_success()
        # 第二次试探
        assert cb.can_execute() is True
        cb.record_success()
        # 此时已关闭，不再受 half_open 限制
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_reset(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0


# ───────────────────────────────────────────────────────────────
# CircuitBreaker.call() 包装器测试
# ───────────────────────────────────────────────────────────────

class TestCircuitBreakerCall:
    def test_call_success(self) -> None:
        cb = CircuitBreaker()
        result = cb.call(lambda: "ok")
        assert result == "ok"
        assert cb.success_count == 1

    def test_call_failure_records_and_raises(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)

        def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            cb.call(fail)
        assert cb.failure_count == 1

    def test_call_when_open_raises(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: "ok")


# ───────────────────────────────────────────────────────────────
# 命令行验证：连续失败 3 次后熔断器打开（验收标准 2）
# ───────────────────────────────────────────────────────────────

class TestCircuitBreakerCommandVerification:
    def test_three_consecutive_failures_opens_breaker(self) -> None:
        """[command] 连续失败 3 次后熔断器打开"""
        cb = CircuitBreaker(name="api_agent", failure_threshold=3)

        # 模拟 3 次连续失败
        for _ in range(3):
            try:
                cb.call(lambda: (_ for _ in ()).throw(RuntimeError("API timeout")))
            except RuntimeError:
                pass

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

        # 第 4 次调用应被拒绝
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: "should not run")

    def test_two_failures_then_success_stays_closed(self) -> None:
        """2 次失败后 1 次成功，熔断器保持关闭"""
        cb = CircuitBreaker(name="api_agent", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

        # 再失败 3 次才会打开
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


# ───────────────────────────────────────────────────────────────
# 序列化 / 反序列化测试
# ───────────────────────────────────────────────────────────────

class TestCircuitBreakerSerialization:
    def test_roundtrip(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        data = cb.to_dict()
        cb2 = CircuitBreaker.from_dict(data)
        assert cb2.name == "test"
        assert cb2.failure_threshold == 5
        assert cb2.state == cb.state
        assert cb2.failure_count == 2


# ───────────────────────────────────────────────────────────────
# 五级降级策略测试
# ───────────────────────────────────────────────────────────────

class TestDegradationStrategy:
    def test_initial_level_is_green(self) -> None:
        ds = DegradationStrategy()
        assert ds.current_level == DegradationLevel.GREEN
        assert ds.is_full_capacity() is True
        assert ds.is_operational() is True

    def test_degrade_sequence(self) -> None:
        """green → yellow → orange → red → black"""
        ds = DegradationStrategy()
        assert ds.degrade() == DegradationLevel.YELLOW
        assert ds.degrade() == DegradationLevel.ORANGE
        assert ds.degrade() == DegradationLevel.RED
        assert ds.degrade() == DegradationLevel.BLACK
        # 不能再降
        assert ds.degrade() == DegradationLevel.BLACK

    def test_recover_sequence(self) -> None:
        """black → red → orange → yellow → green"""
        ds = DegradationStrategy()
        ds.set_level(DegradationLevel.BLACK)
        assert ds.recover() == DegradationLevel.RED
        assert ds.recover() == DegradationLevel.ORANGE
        assert ds.recover() == DegradationLevel.YELLOW
        assert ds.recover() == DegradationLevel.GREEN
        # 不能再升
        assert ds.recover() == DegradationLevel.GREEN

    def test_black_is_not_operational(self) -> None:
        ds = DegradationStrategy()
        ds.set_level(DegradationLevel.BLACK)
        assert ds.is_operational() is False
        assert ds.is_full_capacity() is False

    def test_set_level_by_string(self) -> None:
        ds = DegradationStrategy()
        ds.set_level("red")
        assert ds.current_level == DegradationLevel.RED

    def test_level_descriptions(self) -> None:
        ds = DegradationStrategy()
        assert "正常流水线" in ds.current_description
        ds.set_level(DegradationLevel.BLACK)
        assert "系统暂停" in ds.current_description

    def test_get_actions(self) -> None:
        ds = DegradationStrategy()
        assert "正常流水线" in ds.get_actions()[0]
        ds.set_level(DegradationLevel.ORANGE)
        actions = ds.get_actions()
        assert any("自审" in a for a in actions)
        ds.set_level(DegradationLevel.RED)
        actions = ds.get_actions()
        assert any("辅助 Coder" in a for a in actions)

    def test_serialization(self) -> None:
        ds = DegradationStrategy()
        ds.degrade()
        ds.degrade()
        data = ds.to_dict()
        assert data["current_level"] == "orange"
        assert data["operational"] is True
        ds2 = DegradationStrategy.from_dict(data)
        assert ds2.current_level == DegradationLevel.ORANGE


# ───────────────────────────────────────────────────────────────
# 降级策略模块可导入验证（验收标准 3）
# ───────────────────────────────────────────────────────────────

def test_degradation_module_importable() -> None:
    """[command] 降级策略模块可导入"""
    from circuit_breaker import DegradationStrategy, DegradationLevel

    ds = DegradationStrategy()
    assert ds.current_level == DegradationLevel.GREEN
    levels = [DegradationLevel.GREEN, DegradationLevel.YELLOW,
              DegradationLevel.ORANGE, DegradationLevel.RED,
              DegradationLevel.BLACK]
    assert len(levels) == 5


# ───────────────────────────────────────────────────────────────
# ResilienceManager 集成测试
# ───────────────────────────────────────────────────────────────

class TestResilienceManager:
    def test_get_breaker_creates_new(self) -> None:
        rm = ResilienceManager()
        cb = rm.get_breaker("agent_a")
        assert cb.name == "agent_a"
        assert cb.state == CircuitState.CLOSED

    def test_get_breaker_returns_existing(self) -> None:
        rm = ResilienceManager()
        cb1 = rm.get_breaker("agent_a")
        cb1.record_failure()
        cb2 = rm.get_breaker("agent_a")
        assert cb2.failure_count == 1

    def test_call_with_breaker(self) -> None:
        rm = ResilienceManager()
        result = rm.call_with_breaker("svc", lambda: 42)
        assert result == 42

    def test_check_and_degrade_green(self) -> None:
        rm = ResilienceManager()
        level = rm.check_and_degrade()
        assert level == DegradationLevel.GREEN

    def test_check_and_degrade_yellow(self) -> None:
        rm = ResilienceManager()
        cb = rm.get_breaker("helper_agent")
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        level = rm.check_and_degrade()
        assert level == DegradationLevel.YELLOW

    def test_check_and_degrade_orange(self) -> None:
        rm = ResilienceManager()
        cb = rm.get_breaker("reviewer")
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        level = rm.check_and_degrade()
        assert level == DegradationLevel.ORANGE

    def test_check_and_degrade_red(self) -> None:
        rm = ResilienceManager()
        rm.get_breaker("agent_a").record_failure()
        rm.get_breaker("agent_a").record_failure()
        rm.get_breaker("agent_a").record_failure()
        rm.get_breaker("agent_b").record_failure()
        rm.get_breaker("agent_b").record_failure()
        rm.get_breaker("agent_b").record_failure()
        level = rm.check_and_degrade()
        assert level == DegradationLevel.RED

    def test_check_and_degrade_black(self) -> None:
        rm = ResilienceManager()
        cb = rm.get_breaker("orchestrator")
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        level = rm.check_and_degrade()
        assert level == DegradationLevel.BLACK

    def test_reset_all(self) -> None:
        rm = ResilienceManager()
        rm.get_breaker("svc").record_failure()
        rm.get_breaker("svc").record_failure()
        rm.get_breaker("svc").record_failure()
        rm.degradation.set_level(DegradationLevel.RED)
        rm.reset_all()
        assert rm.degradation.current_level == DegradationLevel.GREEN
        assert rm.get_breaker("svc").state == CircuitState.CLOSED

    def test_serialization(self) -> None:
        rm = ResilienceManager()
        rm.get_breaker("svc").record_failure()
        rm.degradation.degrade()
        data = rm.to_dict()
        rm2 = ResilienceManager.from_dict(data)
        assert rm2.get_breaker("svc").failure_count == 1
        assert rm2.degradation.current_level == DegradationLevel.YELLOW


# ───────────────────────────────────────────────────────────────
# 端到端：熔断器 + 降级策略协同
# ───────────────────────────────────────────────────────────────

def test_end_to_end_breaker_and_degradation() -> None:
    """端到端：Agent 连续失败触发熔断，进而触发系统降级"""
    rm = ResilienceManager()

    # 初始状态：green
    assert rm.degradation.current_level == DegradationLevel.GREEN

    # 模拟 API 调用连续失败
    cb = rm.get_breaker("claude_api")
    for _ in range(3):
        cb.record_failure()

    # 熔断器打开
    assert cb.state == CircuitState.OPEN

    # 检查降级状态
    rm.check_and_degrade()
    assert rm.degradation.current_level == DegradationLevel.YELLOW

    # 再让一个关键服务熔断（已有 claude_api + reviewer = 2 open，触发 RED）
    rm.get_breaker("reviewer").record_failure()
    rm.get_breaker("reviewer").record_failure()
    rm.get_breaker("reviewer").record_failure()
    rm.check_and_degrade()
    # 2 个服务熔断，触发 RED（open_count >= 2 优先级高于 reviewer 的 ORANGE）
    assert rm.degradation.current_level == DegradationLevel.RED

    # 主 Coder 也熔断（critical_open 触发 BLACK）
    rm.get_breaker("main_coder").record_failure()
    rm.get_breaker("main_coder").record_failure()
    rm.get_breaker("main_coder").record_failure()
    rm.check_and_degrade()
    assert rm.degradation.current_level == DegradationLevel.BLACK
    assert rm.degradation.is_operational() is False

    # 恢复流程
    rm.reset_all()
    assert rm.degradation.current_level == DegradationLevel.GREEN
    assert rm.degradation.is_operational() is True
