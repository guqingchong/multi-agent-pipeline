"""
src/event_engine.py — P0: Event Callback Chain Engine

Agent 完成任务后自动触发下游任务，实现管道化并行。

架构：
  review_complete → auto_dispatch_fix → fix_complete → auto_dispatch_test
  test_complete → notify_hermes

用法：
  engine = EventEngine(executor)
  engine.chain(
      ("codewhale", "review", review_payload),
      ("claude-code", "code", fix_payload, lambda r: r.success),   # 仅审查发现问题时修复
      ("qwen-code", "test", test_payload),
  )
  engine.start(blocking=False)
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp_transport import MCPResult, MCPStatus
except (ModuleNotFoundError, ImportError):
    from src.mcp_transport import MCPResult, MCPStatus


__all__ = [
    "EventEngine",
    "ChainStep",
    "ChainResult",
    "CHAIN_TEMPLATES",
    "Condition",
]


class ChainStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


Condition = Callable[[MCPResult], bool]


@dataclass
class ChainStep:
    """事件链中的一个步骤"""
    adapter: str
    task_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Condition] = None  # 前置条件：上一步结果满足才执行
    timeout_sec: Optional[float] = None  # None=不超时，由heartbeat检测死进程
    retry: int = 0

    def should_execute(self, prev_result: Optional[MCPResult]) -> bool:
        if self.condition is None:
            return True
        if prev_result is None:
            return True
        return self.condition(prev_result)


@dataclass
class ChainResult:
    """事件链执行结果"""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    total_latency_ms: int = 0
    all_success: bool = False
    error_step: Optional[str] = None

    @property
    def step_count(self) -> int:
        return len([s for s in self.steps if s.get("status") not in ("skipped",)])


class EventEngine:
    """事件回调链引擎

    将多个 Agent 任务串联为管道，前一步完成自动触发下一步。
    支持条件执行（如：仅审查发现问题时才触发修复）。
    """

    def __init__(self, executor=None):
        self._executor = None  # lazy import to avoid circular
        self._executor_ref = executor
        self._running = False
        self._callbacks: Dict[str, List[Callable]] = {}

    @property
    def executor(self):
        if self._executor is None:
            if self._executor_ref is not None:
                self._executor = self._executor_ref
            else:
                try:
                    from pipeline_executor import create_executor
                except ImportError:
                    from src.pipeline_executor import create_executor
                self._executor = create_executor()
        return self._executor

    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调"""
        self._callbacks.setdefault(event, []).append(callback)

    def emit(self, event: str, **data) -> None:
        """触发事件"""
        for cb in self._callbacks.get(event, []):
            try:
                cb(**data)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                pass

    def chain(self, *steps: ChainStep) -> ChainResult:
        """同步执行事件链（阻塞）"""
        result = ChainResult()
        start = time.time()
        prev: Optional[MCPResult] = None

        for i, step in enumerate(steps):
            if not step.should_execute(prev):
                result.steps.append({"index": i, "status": "skipped", "reason": "condition"})
                continue

            step_start = time.time()
            r = self.executor.dispatch_and_wait(
                step.adapter, step.task_type, step.payload,
                timeout_sec=step.timeout_sec,
            )

            latency = int((time.time() - step_start) * 1000)
            result.steps.append({
                "index": i,
                "adapter": step.adapter,
                "task_type": step.task_type,
                "status": "completed" if r.success else "failed",
                "output": r.output[:200],
                "latency_ms": latency,
            })
            prev = r

            if not r.success and step.retry > 0:
                for retry_i in range(step.retry):
                    r = self.executor.dispatch_and_wait(
                        step.adapter, step.task_type, step.payload,
                        timeout_sec=step.timeout_sec,
                    )
                    if r.success:
                        result.steps[-1]["status"] = "completed"
                        result.steps[-1]["retries"] = retry_i + 1
                        break

            if not r.success:
                result.error_step = step.adapter
                break

        result.total_latency_ms = int((time.time() - start) * 1000)
        result.all_success = result.error_step is None
        return result

    def chain_async(self, *steps: ChainStep) -> threading.Thread:
        """异步执行事件链（非阻塞）"""
        t = threading.Thread(target=lambda: self.chain(*steps), daemon=True)
        t.start()
        return t


# ── 预置模板 ─────────────────────────────────────────────────────

def _only_if_issues(r: MCPResult) -> bool:
    """仅当审查发现问题时执行"""
    return r.success and "P0" in r.output


CHAIN_TEMPLATES: Dict[str, List[ChainStep]] = {
    "review_fix_test": [
        ChainStep("codewhale", "review", {"prompt": "审查代码"}),
        ChainStep("claude-code", "code", {"prompt": "修复问题"}, condition=_only_if_issues),
        ChainStep("qwen-code", "test", {"prompt": "运行测试"}),
    ],
    "code_review": [
        ChainStep("claude-code", "code", {"prompt": "编写代码"}),
        ChainStep("codewhale", "review", {"prompt": "审查代码"}),
    ],
    "test_e2e": [
        ChainStep("qwen-code", "test", {"prompt": "单元测试"}),
        ChainStep("qwen-code", "e2e", {"prompt": "E2E测试"}),
    ],
}
