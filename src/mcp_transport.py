"""
src/mcp_transport.py — MCP Transport Layer v1.0

基于 SQLite src/pipeline_queue 的 MCP (Multi-agent Communication Protocol) 传输层。

架构：
  Hermes (Orchestrator)          Agent (Daemon via CLI)
  ┌──────────────────┐          ┌─────────────────────┐
  │ push_task(task)   │ ──DB──→ │ pull_task(agent_id)  │
  │ poll_result(id)   │ ←──DB── │ execute(adapter)     │
  │ collect(result)   │         │ push_result(result)  │
  └──────────────────┘          └─────────────────────┘

协议语义：
  - 原子推送/拉取（SQLite WAL 模式）
  - 任务生命周期：queued→running→completed|failed|dead_letter
  - 心跳与超时检测
  - 结构化结果传输（AgentResult）
  - 每个 Agent 独立队列分区
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.pipeline_queue import Queue, Task

try:
    from adapters import AgentResult, AdapterStatus
except (ModuleNotFoundError, ImportError):
    from src.adapters import AgentResult, AdapterStatus

__all__ = [
    "MCPTransport",
    "MCPTask",
    "MCPResult",
    "MCPStatus",
    "AgentEndpoint",
    "create_transport",
]


class MCPStatus(str, Enum):
    """MCP 任务状态"""
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DEAD = "dead"


@dataclass
class AgentEndpoint:
    """Agent 端点配置"""
    agent_id: str                    # adapter 名称: claude-code/codewhale/qwen-code
    cli_path: str                    # CLI 可执行文件路径
    cli_command: str                 # CLI 命令模板（如 "exec --auto {prompt}"）
    max_concurrent: int = 1          # 最大并发任务数
    timeout_sec: int = 300           # 单任务超时（秒）
    heartbeat_sec: int = 30          # 心跳间隔
    env: Dict[str, str] = field(default_factory=dict)  # 环境变量


@dataclass
class MCPTask:
    """MCP 任务包装"""
    id: str
    agent_id: str
    task_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    status: MCPStatus = MCPStatus.PENDING
    created_at: float = 0.0
    dispatched_at: float = 0.0
    completed_at: float = 0.0
    retry_count: int = 0
    max_retries: int = 3

    def to_message_queue_task(self) -> Task:
        """转换为 src/pipeline_queue 的 Task 格式"""
        return Task(
            id=self.id,
            target_agent=self.agent_id,
            task_type=self.task_type,
            context=self.payload,
            priority=self.priority,
            max_retries=self.max_retries,
        )


@dataclass
class MCPResult:
    """MCP 任务结果"""
    task_id: str
    agent_id: str
    success: bool
    output: str = ""
    structured: Optional[Dict[str, Any]] = None
    latency_ms: int = 0
    tokens_used: int = 0
    error: str = ""
    status: MCPStatus = MCPStatus.COMPLETED

    @classmethod
    def from_agent_result(cls, task_id: str, agent_id: str, result: AgentResult) -> "MCPResult":
        return cls(
            task_id=task_id,
            agent_id=agent_id,
            success=result.success,
            output=result.output[:2000] if result.output else "",
            structured=result.structured,
            latency_ms=result.latency_ms,
            tokens_used=result.tokens_used,
            error=result.error_message or "",
            status=MCPStatus.COMPLETED if result.success else MCPStatus.FAILED,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "success": self.success,
            "output": self.output,
            "structured": self.structured,
            "latency_ms": self.latency_ms,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "status": self.status.value,
        }


class MCPTransport:
    """MCP 传输层 — 基于 src/pipeline_queue 的 Agent 通信协议

    Usage:
        transport = MCPTransport("pipeline.db")
        transport.register_endpoint(AgentEndpoint("codewhale", "codewhale-tui", "exec --auto"))

        # Hermes side: push task
        task_id = transport.push("codewhale", "review", {"prompt": "review code"})

        # Daemon side: pull and execute
        task = transport.pull("codewhale")
        result = adapter.execute(task.payload["prompt"])
        transport.complete(task.id, result)

        # Hermes side: collect
        mcp_result = transport.collect(task_id)
    """

    def __init__(self, db_path: str = "mcp_transport.db"):
        self.mq = Queue(db_path)
        self._endpoints: Dict[str, AgentEndpoint] = {}
        self._pending: Dict[str, MCPTask] = {}
        self._lock = threading.Lock()  # P0-2: protect _pending concurrent access

    # ── Endpoint Management ──────────────────────────────────────

    def register_endpoint(self, endpoint: AgentEndpoint) -> None:
        """注册 Agent 端点"""
        self._endpoints[endpoint.agent_id] = endpoint

    def get_endpoint(self, agent_id: str) -> Optional[AgentEndpoint]:
        return self._endpoints.get(agent_id)

    def list_endpoints(self) -> List[str]:
        return list(self._endpoints.keys())

    # ── Task Push/Pull (Hermes ↔ Agent) ──────────────────────────

    def push(
        self,
        agent_id: str,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 0,
        max_retries: int = 3,
    ) -> str:
        """Hermes 端：推送任务到 Agent 队列

        Returns:
            task_id — 用于后续 poll/collect
        """
        endpoint = self._endpoints.get(agent_id)
        if endpoint is None:
            raise ValueError(f"Unknown agent endpoint: {agent_id}")

        task = Task(
            target_agent=agent_id,
            task_type=task_type,
            context=payload or {},
            priority=priority,
            max_retries=max_retries,
        )

        self.mq.push_sync(task)

        task_id = str(task.id) if task.id else f"mcp-{agent_id}-{int(time.time()*1000)}"
        mcp_task = MCPTask(
            id=task_id,
            agent_id=agent_id,
            task_type=task_type,
            payload=payload or {},
            priority=priority,
            max_retries=max_retries,
            status=MCPStatus.DISPATCHED,
            created_at=time.time(),
        )
        with self._lock:
            self._pending[task_id] = mcp_task

        return task_id

    def pull(self, agent_id: str) -> Optional[MCPTask]:
        """Agent 端（daemon）：拉取下一个待执行任务"""
        task = self.mq.pull_sync(agent_id)
        if task is None:
            return None

        task_id = str(task.id) if task.id else f"mcp-{agent_id}-{int(time.time()*1000)}"
        mcp_task = MCPTask(
            id=task_id,
            agent_id=task.target_agent,
            task_type=task.task_type,
            payload=task.context or {},
            priority=task.priority,
            max_retries=task.max_retries,
            status=MCPStatus.RUNNING,
            created_at=time.time(),
            dispatched_at=time.time(),
        )
        with self._lock:
            self._pending[task_id] = mcp_task
        return mcp_task

    def complete(self, task_id: str, result: AgentResult, agent_id: str = "") -> None:
        """Agent 端：标记任务完成并推送结果"""
        int_id = int(task_id) if task_id.isdigit() else 0
        if int_id:
            result_data = json.dumps({
                "success": result.success,
                "output": result.output[:5000] if result.output else "",
                "structured": result.structured,
                "latency_ms": result.latency_ms,
                "tokens_used": result.tokens_used,
                "error": result.error_message or "",
            }, ensure_ascii=False)

            if result.success:
                self.mq.complete_sync(int_id, {"result": result_data})
            else:
                self.mq.fail_sync(int_id, result.error_message or "Unknown error")

        with self._lock:
            if task_id in self._pending:
                self._pending[task_id].status = (
                    MCPStatus.COMPLETED if result.success else MCPStatus.FAILED
                )
                self._pending[task_id].completed_at = time.time()

    # ── Result Collection (Hermes side) ──────────────────────────

    def poll(self, task_id: str) -> Optional[MCPResult]:
        """Hermes 端：轮询任务结果（非阻塞）"""
        with self._lock:
            if task_id in self._pending:
                mcp_task = self._pending[task_id]
                if mcp_task.status in (MCPStatus.COMPLETED, MCPStatus.FAILED):
                    return MCPResult(
                        task_id=task_id,
                        agent_id=mcp_task.agent_id,
                        success=mcp_task.status == MCPStatus.COMPLETED,
                        status=mcp_task.status,
                    )
        return None

    def collect(self, task_id: str, timeout_sec: Optional[float] = None) -> MCPResult:
        """Hermes 端：阻塞等待任务完成。None=无限等待，由heartbeat终止。"""
        deadline = time.time() + timeout_sec if timeout_sec is not None else float('inf')
        while time.time() < deadline:
            result = self.poll(task_id)
            if result is not None:
                return result
            time.sleep(0.5)

        if task_id in self._pending:
            self._pending[task_id].status = MCPStatus.TIMEOUT
        return MCPResult(
            task_id=task_id,
            agent_id="?",
            success=False,
            error=f"Timeout after {timeout_sec}s",
            status=MCPStatus.TIMEOUT,
        )

    def collect_all(self, task_ids: List[str], timeout_sec: Optional[float] = None) -> Dict[str, MCPResult]:
        """Hermes 端：批量收集结果。None=无限等待。"""
        results: Dict[str, MCPResult] = {}
        deadline = time.time() + timeout_sec if timeout_sec is not None else float('inf')
        pending = set(task_ids)

        while pending and time.time() < deadline:
            for tid in list(pending):
                result = self.poll(tid)
                if result is not None:
                    results[tid] = result
                    pending.remove(tid)
            if pending:
                time.sleep(0.5)

        for tid in pending:
            results[tid] = MCPResult(
                task_id=tid, agent_id="?", success=False,
                error="Timeout", status=MCPStatus.TIMEOUT,
            )

        return results

    # ── Health / Stats ───────────────────────────────────────────

    def agent_queue_depth(self, agent_id: str) -> int:
        """Agent 队列中待处理任务数"""
        # src/pipeline_queue doesn't expose queue_depth; count dispatched tasks
        with self._lock:
            return len([t for t in self._pending.values()
                        if t.agent_id == agent_id and t.status == MCPStatus.DISPATCHED])

    def agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Agent 状态快照"""
        return {
            "agent_id": agent_id,
            "queue_depth": self.agent_queue_depth(agent_id),
            "pending_tasks": len([t for t in self._pending.values() if t.agent_id == agent_id]),
            "endpoint": self._endpoints.get(agent_id, None),
        }

    def all_status(self) -> Dict[str, Any]:
        """全局状态"""
        return {
            "endpoints": self.list_endpoints(),
            "agents": {aid: self.agent_status(aid) for aid in self._endpoints},
            "total_pending": len(self._pending),
        }


# ── Factory ──────────────────────────────────────────────────────

def create_transport(
    db_path: str = "mcp_transport.db",
    endpoints: Optional[List[AgentEndpoint]] = None,
) -> MCPTransport:
    """创建 MCP 传输层实例"""
    transport = MCPTransport(db_path)
    for ep in (endpoints or []):
        transport.register_endpoint(ep)
    return transport
