"""tests/test_adapter_tolerance.py — 容错层异常恢复测试

F012 验收标准：
- 容错层异常恢复测试通过
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from adapters import (
    AgentResult,
    ClaudeCodeAdapter,
    CodeWhaleAdapter,
    QwenCodeAdapter,
    ToleranceLayer,
    TimeoutError,
    CrashError,
    TruncationError,
    ParseError,
    AgentNotReadyError,
)


# ───────────────────────────────────────────────────────────────
# ToleranceLayer 基础测试
# ───────────────────────────────────────────────────────────────

class TestToleranceLayerBasics:
    def test_create_default(self) -> None:
        tl = ToleranceLayer()
        assert tl.max_retries == 3
        assert tl.retry_delay == 1.0
        assert tl.timeout_seconds == 60

    def test_create_custom(self) -> None:
        tl = ToleranceLayer(max_retries=5, retry_delay=2.0, timeout_seconds=120)
        assert tl.max_retries == 5
        assert tl.retry_delay == 2.0
        assert tl.timeout_seconds == 120

    def test_retry_count_starts_at_zero(self) -> None:
        tl = ToleranceLayer()
        assert tl.retry_count == 0


# ───────────────────────────────────────────────────────────────
# 超时恢复测试
# ───────────────────────────────────────────────────────────────

class TestTimeoutRecovery:
    def test_timeout_detection(self) -> None:
        tl = ToleranceLayer(timeout_seconds=1)
        assert tl.is_timeout(2.0) is True
        assert tl.is_timeout(0.5) is False

    def test_timeout_recovery_increments_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.retry_count == 0
        tl.record_retry()
        assert tl.retry_count == 1

    def test_timeout_recovery_exhausted(self) -> None:
        tl = ToleranceLayer(max_retries=2)
        tl.record_retry()
        tl.record_retry()
        assert tl.retries_exhausted() is True

    def test_timeout_recovery_not_exhausted(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        tl.record_retry()
        assert tl.retries_exhausted() is False

    def test_timeout_recovery_reset(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        tl.record_retry()
        tl.record_retry()
        assert tl.retry_count == 2
        tl.reset_retries()
        assert tl.retry_count == 0

    def test_adaptive_timeout_doubles(self) -> None:
        tl = ToleranceLayer(timeout_seconds=30)
        assert tl.adaptive_timeout() == 60
        assert tl.adaptive_timeout() == 120

    def test_adaptive_timeout_cap(self) -> None:
        tl = ToleranceLayer(timeout_seconds=300)
        assert tl.adaptive_timeout() == 600
        assert tl.adaptive_timeout() == 600  # capped

    def test_should_retry_on_timeout(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(TimeoutError("timed out")) is True

    def test_should_not_retry_after_exhaustion(self) -> None:
        tl = ToleranceLayer(max_retries=1)
        tl.record_retry()
        assert tl.should_retry(TimeoutError("timed out")) is False

    def test_should_not_retry_on_fatal_error(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(ValueError("bad")) is False


# ───────────────────────────────────────────────────────────────
# 崩溃恢复测试
# ───────────────────────────────────────────────────────────────

class TestCrashRecovery:
    def test_crash_error_is_exception(self) -> None:
        with pytest.raises(CrashError):
            raise CrashError("process crashed")

    def test_should_retry_on_crash(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(CrashError("segfault")) is True

    def test_crash_recovery_increments_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        tl.record_retry()
        assert tl.retry_count == 1

    def test_crash_recovery_exhausted(self) -> None:
        tl = ToleranceLayer(max_retries=1)
        tl.record_retry()
        assert tl.should_retry(CrashError("crash")) is False

    def test_crash_backoff_increases(self) -> None:
        tl = ToleranceLayer(retry_delay=1.0)
        assert tl.backoff_delay() == 1.0
        tl.record_retry()
        assert tl.backoff_delay() == 2.0
        tl.record_retry()
        assert tl.backoff_delay() == 4.0


# ───────────────────────────────────────────────────────────────
# 截断恢复测试
# ───────────────────────────────────────────────────────────────

class TestTruncationRecovery:
    def test_truncation_error_is_exception(self) -> None:
        with pytest.raises(TruncationError):
            raise TruncationError("output truncated")

    def test_should_retry_on_truncation(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(TruncationError("truncated")) is True

    def test_truncation_recovery_strategy_hint(self) -> None:
        tl = ToleranceLayer()
        hint = tl.recovery_hint(TruncationError("truncated"))
        assert hint is not None
        assert "shorter" in hint.lower() or "chunk" in hint.lower() or "prompt" in hint.lower()

    def test_truncation_with_context_shortening(self) -> None:
        tl = ToleranceLayer()
        short = tl.shorten_context("a very long prompt " * 100)
        assert len(short) < len("a very long prompt " * 100)

    def test_truncation_detection_in_output(self) -> None:
        tl = ToleranceLayer()
        assert tl.detect_truncation("abc [truncated]") is True
        assert tl.detect_truncation("abc") is False

    def test_truncation_recovery_increments_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        tl.record_retry()
        assert tl.retry_count == 1


# ───────────────────────────────────────────────────────────────
# 解析失败恢复测试
# ───────────────────────────────────────────────────────────────

class TestParseErrorRecovery:
    def test_parse_error_is_exception(self) -> None:
        with pytest.raises(ParseError):
            raise ParseError("cannot parse")

    def test_should_retry_on_parse_error(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(ParseError("bad json")) is True

    def test_parse_recovery_hint(self) -> None:
        tl = ToleranceLayer()
        hint = tl.recovery_hint(ParseError("bad json"))
        assert hint is not None
        assert "parse" in hint.lower() or "format" in hint.lower() or "retry" in hint.lower()

    def test_parse_recovery_exhausted(self) -> None:
        tl = ToleranceLayer(max_retries=1)
        tl.record_retry()
        assert tl.should_retry(ParseError("bad")) is False


# ───────────────────────────────────────────────────────────────
# AgentNotReady 恢复测试
# ───────────────────────────────────────────────────────────────

class TestAgentNotReadyRecovery:
    def test_agent_not_ready_is_exception(self) -> None:
        with pytest.raises(AgentNotReadyError):
            raise AgentNotReadyError("agent not ready")

    def test_should_retry_on_not_ready(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        assert tl.should_retry(AgentNotReadyError("not ready")) is True

    def test_not_ready_recovery_hint(self) -> None:
        tl = ToleranceLayer()
        hint = tl.recovery_hint(AgentNotReadyError("not ready"))
        assert hint is not None
        assert "wait" in hint.lower() or "ready" in hint.lower() or "restart" in hint.lower()


# ───────────────────────────────────────────────────────────────
# 综合容错场景测试
# ───────────────────────────────────────────────────────────────

class TestIntegratedTolerance:
    def test_successful_call_no_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3)
        result = tl.execute(lambda: AgentResult(success=True, output="ok"))
        assert result.success is True
        assert result.output == "ok"
        assert tl.retry_count == 0

    def test_retry_once_then_success(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        call_count = 0

        def flaky() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timeout")
            return AgentResult(success=True, output="ok")

        result = tl.execute(flaky)
        assert result.success is True
        assert call_count == 2
        assert tl.retry_count == 1

    def test_retry_twice_then_success(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        call_count = 0

        def flaky() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise CrashError("crash")
            return AgentResult(success=True, output="ok")

        result = tl.execute(flaky)
        assert result.success is True
        assert call_count == 3
        assert tl.retry_count == 2

    def test_all_retries_exhausted_raises(self) -> None:
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)

        def always_fail() -> AgentResult:
            raise TimeoutError("timeout")

        with pytest.raises(TimeoutError):
            tl.execute(always_fail)
        assert tl.retry_count == 2

    def test_mixed_errors_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        errors = [TimeoutError("t1"), CrashError("c1"), TruncationError("tr1")]
        idx = 0

        def mixed() -> AgentResult:
            nonlocal idx
            e = errors[idx]
            idx += 1
            raise e

        with pytest.raises(TruncationError):
            tl.execute(mixed)
        assert tl.retry_count == 3

    def test_non_retryable_error_raises_immediately(self) -> None:
        tl = ToleranceLayer(max_retries=3)

        def fatal() -> AgentResult:
            raise ValueError("fatal")

        with pytest.raises(ValueError):
            tl.execute(fatal)
        assert tl.retry_count == 0

    def test_execute_with_adapter_timeout(self) -> None:
        tl = ToleranceLayer(max_retries=1, timeout_seconds=0.1, retry_delay=0.01)

        def slow() -> AgentResult:
            time.sleep(0.2)
            return AgentResult(success=True, output="ok")

        with pytest.raises(TimeoutError):
            tl.execute(slow)

    def test_execute_with_result_validation(self) -> None:
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)

        def bad_result() -> AgentResult:
            return AgentResult(success=False, output="", error_message="fail")

        with pytest.raises(ParseError):
            tl.execute(bad_result, validate_result=True)

    def test_execute_without_validation_allows_failure(self) -> None:
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)

        def bad_result() -> AgentResult:
            return AgentResult(success=False, output="", error_message="fail")

        result = tl.execute(bad_result, validate_result=False)
        assert result.success is False

    def test_state_preserved_across_retries(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        states: list[int] = []

        def stateful() -> AgentResult:
            states.append(len(states))
            if len(states) < 3:
                raise TimeoutError("timeout")
            return AgentResult(success=True, output=f"ok {states}")

        result = tl.execute(stateful)
        assert result.success is True
        assert states == [0, 1, 2]

    def test_retry_delay_respected(self) -> None:
        tl = ToleranceLayer(max_retries=2, retry_delay=0.05)
        times: list[float] = []

        def timed() -> AgentResult:
            times.append(time.time())
            if len(times) == 1:
                raise TimeoutError("timeout")
            return AgentResult(success=True, output="ok")

        result = tl.execute(timed)
        assert result.success is True
        assert len(times) == 2
        assert times[1] - times[0] >= 0.04  # allow small variance


# ───────────────────────────────────────────────────────────────
# Adapter 集成容错测试
# ───────────────────────────────────────────────────────────────

class TestAdapterIntegrationTolerance:
    def test_claude_adapter_with_tolerance_timeout(self) -> None:
        adapter = ClaudeCodeAdapter()
        tl = ToleranceLayer(max_retries=2, timeout_seconds=0.1, retry_delay=0.01)

        def slow_call() -> AgentResult:
            time.sleep(0.2)
            return adapter.parse_output("ok")

        with pytest.raises(TimeoutError):
            tl.execute(slow_call)

    def test_claude_adapter_with_tolerance_crash(self) -> None:
        adapter = ClaudeCodeAdapter()
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)
        call_count = 0

        def flaky_call() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise CrashError("segfault")
            return adapter.parse_output("success")

        result = tl.execute(flaky_call)
        assert result.success is True
        assert call_count == 2

    def test_codewhale_adapter_with_tolerance_truncation(self) -> None:
        adapter = CodeWhaleAdapter()
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)
        call_count = 0

        def flaky_call() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TruncationError("truncated")
            return adapter.parse_output("All good")

        result = tl.execute(flaky_call)
        assert result.success is True
        assert call_count == 2

    def test_qwen_adapter_with_tolerance_parse_error(self) -> None:
        adapter = QwenCodeAdapter()
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)
        call_count = 0

        def flaky_call() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ParseError("bad json")
            return adapter.parse_output('{"success": true}')

        result = tl.execute(flaky_call)
        assert result.success is True
        assert call_count == 2

    def test_adapter_result_validation(self) -> None:
        adapter = ClaudeCodeAdapter()
        tl = ToleranceLayer(max_retries=2, retry_delay=0.01)

        def bad_call() -> AgentResult:
            return adapter.parse_output("")

        result = tl.execute(bad_call, validate_result=False)
        assert result.success is True

    def test_adapter_full_pipeline_simulation(self) -> None:
        """模拟完整调用流程：build_command -> execute -> parse_output"""
        adapter = ClaudeCodeAdapter()
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        call_count = 0

        def pipeline() -> AgentResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timeout")
            if call_count == 2:
                raise TruncationError("truncated")
            # 第三次成功
            cmd = adapter.build_command("write code", timeout=60)
            assert "write code" in cmd
            raw = "Done writing code"
            return adapter.parse_output(raw)

        result = tl.execute(pipeline)
        assert result.success is True
        assert result.output == "Done writing code"
        assert call_count == 3
        assert tl.retry_count == 2


# ───────────────────────────────────────────────────────────────
# 序列化 / 状态恢复测试
# ───────────────────────────────────────────────────────────────

class TestToleranceSerialization:
    def test_to_dict(self) -> None:
        tl = ToleranceLayer(max_retries=5, retry_delay=2.0, timeout_seconds=120)
        tl.record_retry()
        d = tl.to_dict()
        assert d["max_retries"] == 5
        assert d["retry_delay"] == 2.0
        assert d["timeout_seconds"] == 120
        assert d["retry_count"] == 1

    def test_from_dict(self) -> None:
        d = {
            "max_retries": 4,
            "retry_delay": 1.5,
            "timeout_seconds": 90,
            "retry_count": 2,
        }
        tl = ToleranceLayer.from_dict(d)
        assert tl.max_retries == 4
        assert tl.retry_delay == 1.5
        assert tl.timeout_seconds == 90
        assert tl.retry_count == 2

    def test_roundtrip(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.5, timeout_seconds=60)
        tl.record_retry()
        tl2 = ToleranceLayer.from_dict(tl.to_dict())
        assert tl2.max_retries == tl.max_retries
        assert tl2.retry_delay == tl.retry_delay
        assert tl2.timeout_seconds == tl.timeout_seconds
        assert tl2.retry_count == tl.retry_count

    def test_state_after_successful_retry(self) -> None:
        tl = ToleranceLayer(max_retries=3, retry_delay=0.01)
        tl.record_retry()
        tl.record_retry()
        tl.reset_retries()
        assert tl.retry_count == 0
        assert tl.retries_exhausted() is False


# ───────────────────────────────────────────────────────────────
# 边界条件测试
# ───────────────────────────────────────────────────────────────

class TestToleranceEdgeCases:
    def test_zero_max_retries(self) -> None:
        tl = ToleranceLayer(max_retries=0)
        assert tl.retries_exhausted() is True
        assert tl.should_retry(TimeoutError("t")) is False

    def test_negative_retry_delay(self) -> None:
        tl = ToleranceLayer(retry_delay=-1.0)
        assert tl.backoff_delay() == 0.0

    def test_very_large_timeout(self) -> None:
        tl = ToleranceLayer(timeout_seconds=3600)
        assert tl.is_timeout(7200) is True
        assert tl.is_timeout(1800) is False

    def test_multiple_reset_calls(self) -> None:
        tl = ToleranceLayer()
        tl.reset_retries()
        tl.reset_retries()
        assert tl.retry_count == 0

    def test_record_retry_beyond_max(self) -> None:
        tl = ToleranceLayer(max_retries=2)
        tl.record_retry()
        tl.record_retry()
        tl.record_retry()
        assert tl.retry_count == 3
        assert tl.retries_exhausted() is True

    def test_adaptive_timeout_with_zero_base(self) -> None:
        tl = ToleranceLayer(timeout_seconds=0)
        assert tl.adaptive_timeout() == 0

    def test_backoff_with_zero_delay(self) -> None:
        tl = ToleranceLayer(retry_delay=0)
        assert tl.backoff_delay() == 0.0
        tl.record_retry()
        assert tl.backoff_delay() == 0.0

    def test_recovery_hint_for_unknown_error(self) -> None:
        tl = ToleranceLayer()
        hint = tl.recovery_hint(ValueError("unknown"))
        assert hint is not None

    def test_is_timeout_with_none(self) -> None:
        tl = ToleranceLayer(timeout_seconds=10)
        assert tl.is_timeout(None) is False

    def test_execute_with_none_callable(self) -> None:
        tl = ToleranceLayer()
        with pytest.raises(TypeError):
            tl.execute(None)  # type: ignore[arg-type]

    def test_shorten_context_empty(self) -> None:
        tl = ToleranceLayer()
        assert tl.shorten_context("") == ""

    def test_shorten_context_short(self) -> None:
        tl = ToleranceLayer()
        text = "short"
        assert tl.shorten_context(text) == text

    def test_detect_truncation_none(self) -> None:
        tl = ToleranceLayer()
        assert tl.detect_truncation(None) is False  # type: ignore[arg-type]

    def test_should_retry_non_exception(self) -> None:
        tl = ToleranceLayer()
        assert tl.should_retry("string") is False  # type: ignore[arg-type]

    def test_tolerance_layer_str(self) -> None:
        tl = ToleranceLayer(max_retries=3, timeout_seconds=60)
        s = str(tl)
        assert "ToleranceLayer" in s
        assert "3" in s
        assert "60" in s

    def test_tolerance_layer_repr(self) -> None:
        tl = ToleranceLayer(max_retries=3, timeout_seconds=60)
        r = repr(tl)
        assert "ToleranceLayer" in r

    def test_agent_result_str(self) -> None:
        r = AgentResult(success=True, output="ok")
        s = str(r)
        assert "success" in s.lower() or "True" in s

    def test_agent_result_repr(self) -> None:
        r = AgentResult(success=True, output="ok")
        r2 = repr(r)
        assert "AgentResult" in r2
