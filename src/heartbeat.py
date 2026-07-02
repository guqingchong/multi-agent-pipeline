"""
src/heartbeat.py — P1: Agent Heartbeat Monitor + Task Canceller + Streaming

功能：
  1. HeartbeatMonitor — 检测僵尸 Agent（>N秒无心跳 → 标记DEAD）
  2. TaskCanceller — 异步取消正在执行的任务
  3. StreamingCollector — 收集 Agent 中间输出（非完整结果）

架构：
  Agent Daemon                      Heartbeat Monitor (Hermes)
  ┌──────────────┐                 ┌──────────────────────┐
  │ heartbeat()  │ ──每5s──→       │ check_heartbeats()    │
  │              │                 │   dead_detection()    │
  │ stream(msg)  │ ──中间输出→     │   partial_results{}   │
  │ cancel(id)   │ ←──取消指令──   │   cancel_task()       │
  └──────────────┘                 └──────────────────────┘
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


__all__ = [
    "HeartbeatMonitor",
    "TaskCanceller",
    "StreamingCollector",
    "AgentHealth",
    "CancelToken",
]


class AgentHealth(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"        # 超过心跳间隔但未超时
    DEAD = "dead"          # 超过超时阈值
    UNKNOWN = "unknown"


class CancelToken:
    """任务取消令牌"""
    __slots__ = ("_cancelled", "_reason")

    def __init__(self) -> None:
        self._cancelled = False
        self._reason = ""

    def cancel(self, reason: str = "") -> None:
        self._cancelled = True
        self._reason = reason

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason


# ══════════════════════════════════════════════════════════════════
# Heartbeat Monitor
# ══════════════════════════════════════════════════════════════════

@dataclass
class AgentHeartbeat:
    """Agent 心跳记录"""
    agent_id: str
    last_beat: float = 0.0
    last_task: str = ""
    status: AgentHealth = AgentHealth.UNKNOWN
    tasks_completed: int = 0
    tasks_failed: int = 0


class HeartbeatMonitor:
    """Agent 心跳监控器

    Usage:
        monitor = HeartbeatMonitor(heartbeat_interval=5, death_timeout=30)
        monitor.start()

        # Agent side: heartbeat every 5s
        monitor.beat("codewhale", "reviewing file.py")

        # Hermes side: check health
        health = monitor.check("codewhale")  # HEALTHY/STALE/DEAD
    """

    def __init__(
        self,
        heartbeat_interval: float = 5.0,
        death_timeout: float = 30.0,
        on_death: Optional[Callable[[str], None]] = None,
    ):
        self.heartbeat_interval = heartbeat_interval
        self.death_timeout = death_timeout
        self.on_death = on_death
        self._agents: Dict[str, AgentHeartbeat] = {}
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    def register(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = AgentHeartbeat(agent_id=agent_id)

    def beat(self, agent_id: str, current_task: str = "") -> None:
        """Agent 发送心跳"""
        with self._lock:
            hb = self._agents.get(agent_id)
            if hb is None:
                hb = AgentHeartbeat(agent_id=agent_id)
                self._agents[agent_id] = hb
            hb.last_beat = time.time()
            hb.current_task = current_task
            hb.status = AgentHealth.HEALTHY

    def record_result(self, agent_id: str, success: bool) -> None:
        """记录任务完成"""
        with self._lock:
            hb = self._agents.get(agent_id)
            if hb:
                if success:
                    hb.tasks_completed += 1
                else:
                    hb.tasks_failed += 1

    def check_all(self) -> Dict[str, AgentHealth]:
        with self._lock:
            return {aid: self._check_locked(aid) for aid in self._agents}

    def _check_locked(self, agent_id: str) -> AgentHealth:
        """内部检查（调用方必须持有 self._lock）"""
        hb = self._agents.get(agent_id)
        if hb is None:
            return AgentHealth.UNKNOWN
        elapsed = time.time() - hb.last_beat
        if elapsed > self.death_timeout:
            hb.status = AgentHealth.DEAD
            if self.on_death:
                self.on_death(agent_id)
        elif elapsed > self.heartbeat_interval * 2:
            hb.status = AgentHealth.STALE
        else:
            hb.status = AgentHealth.HEALTHY
        return hb.status

    def check(self, agent_id: str) -> AgentHealth:
        with self._lock:
            return self._check_locked(agent_id)

    def get_dead_agents(self) -> List[str]:
        with self._lock:
            return [aid for aid in self._agents if self._check_locked(aid) == AgentHealth.DEAD]

    def status_report(self) -> Dict[str, Any]:
        with self._lock:
            return {
                aid: {
                    "status": hb.status.value,
                    "last_beat_ago": round(time.time() - hb.last_beat, 1),
                    "current_task": hb.current_task,
                    "completed": hb.tasks_completed,
                    "failed": hb.tasks_failed,
                }
                for aid, hb in self._agents.items()
            }

    def start(self) -> None:
        """启动后台监控线程"""
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False

    def _monitor_loop(self) -> None:
        while self._running:
            time.sleep(self.heartbeat_interval)
            dead = self.get_dead_agents()
            for aid in dead:
                if self.on_death:
                    self.on_death(aid)


# ══════════════════════════════════════════════════════════════════
# Task Canceller
# ══════════════════════════════════════════════════════════════════

class TaskCanceller:
    """任务取消管理器

    支持：
      - 按 task_id 取消
      - 按 agent_id 批量取消
      - 超时自动取消
      - 取消原因记录
    """

    def __init__(self):
        self._tokens: Dict[str, CancelToken] = {}
        self._lock = threading.Lock()
        self._cancelled_tasks: Dict[str, str] = {}  # task_id → reason

    def create_token(self, task_id: str) -> CancelToken:
        with self._lock:
            token = CancelToken()
            self._tokens[task_id] = token
        return token

    def _cancel_locked(self, task_id: str, reason: str) -> bool:
        """内部取消逻辑（调用方必须持有 self._lock）"""
        token = self._tokens.get(task_id)
        if token is not None and not token.cancelled:
            token.cancel(reason)
            self._cancelled_tasks[task_id] = reason
            return True
        return False

    def cancel(self, task_id: str, reason: str = "manually cancelled") -> bool:
        """取消指定任务"""
        with self._lock:
            return self._cancel_locked(task_id, reason)

    def cancel_agent(self, agent_id: str, reason: str = "agent cancelled") -> int:
        """取消某 Agent 的所有任务"""
        count = 0
        with self._lock:
            for tid in list(self._tokens.keys()):
                if agent_id in tid:
                    if self._cancel_locked(tid, reason):
                        count += 1
        return count

    def is_cancelled(self, task_id: str) -> bool:
        token = self._tokens.get(task_id)
        return token.cancelled if token else False

    def check_and_raise(self, task_id: str) -> None:
        """检查取消状态，已取消则抛异常"""
        token = self._tokens.get(task_id)
        if token and token.cancelled:
            raise TaskCancelledError(task_id, token.reason)

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            self._tokens.pop(task_id, None)

    def cancelled_count(self) -> int:
        return len(self._cancelled_tasks)


class TaskCancelledError(Exception):
    def __init__(self, task_id: str, reason: str = ""):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Task {task_id} cancelled: {reason}")


# ══════════════════════════════════════════════════════════════════
# Streaming Collector
# ══════════════════════════════════════════════════════════════════

@dataclass
class StreamChunk:
    """流式输出块"""
    task_id: str
    agent_id: str
    content: str
    timestamp: float = 0.0
    is_final: bool = False


class StreamingCollector:
    """流式输出收集器

    收集 Agent 执行过程中的中间输出（非完整结果）。
    Agent 通过 stream() 推送中间日志/进度。
    Hermes 通过 read() 读取流式输出。
    """

    def __init__(self, max_chunks_per_task: int = 100):
        self._streams: Dict[str, List[StreamChunk]] = {}
        self._final: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._max_chunks = max_chunks_per_task

    def stream(self, task_id: str, agent_id: str, content: str, final: bool = False) -> None:
        """推送流式输出块"""
        with self._lock:
            if task_id not in self._streams:
                self._streams[task_id] = []
            chunk = StreamChunk(
                task_id=task_id,
                agent_id=agent_id,
                content=content,
                timestamp=time.time(),
                is_final=final,
            )
            self._streams[task_id].append(chunk)
            if len(self._streams[task_id]) > self._max_chunks:
                self._streams[task_id] = self._streams[task_id][-self._max_chunks:]

            if final:
                self._final[task_id] = content

    def read(self, task_id: str, since: int = 0) -> List[StreamChunk]:
        """读取流式输出（从指定位置开始）"""
        with self._lock:
            chunks = self._streams.get(task_id, [])
            return chunks[since:]

    def read_all(self, task_id: str) -> str:
        """读取所有流式输出（拼接）"""
        chunks = self._streams.get(task_id, [])
        return "\n".join(c.content for c in chunks)

    def is_done(self, task_id: str) -> bool:
        return task_id in self._final

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            self._streams.pop(task_id, None)
            self._final.pop(task_id, None)
