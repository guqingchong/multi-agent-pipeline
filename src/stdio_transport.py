"""
src/stdio_transport.py — P2: MCP stdio JSON-RPC Transport

实时双向通信层。Agent 通过 stdin/stdout 与 Hermes 交换 JSON-RPC 消息。

协议格式 (JSON-RPC 2.0 over stdio):
  请求: {"jsonrpc":"2.0","id":1,"method":"task/execute","params":{...}}
  响应: {"jsonrpc":"2.0","id":1,"result":{...}}
  通知: {"jsonrpc":"2.0","method":"task/progress","params":{...}}
  错误: {"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"..."}}

支持方法:
  task/execute     — 派发任务
  task/cancel      — 取消任务
  task/progress    — Agent 推送进度（通知）
  heartbeat/ping   — 心跳检测
  agent/status     — 查询 Agent 状态

与 SQLite transport 互斥选择：当 stdio 可用时优先使用，
fallback 到 SQLite transport。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "StdioTransport",
    "StdioEndpoint",
    "StdioRequest",
    "StdioResponse",
    "StdioNotification",
    "StreamingSession",
]


# ── JSON-RPC Data Types ──────────────────────────────────────────

@dataclass
class StdioRequest:
    """JSON-RPC 请求"""
    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }, ensure_ascii=False)


@dataclass
class StdioResponse:
    """JSON-RPC 响应"""
    id: int
    result: Any = None
    error: Optional[Dict[str, Any]] = None
    success: bool = True

    @classmethod
    def from_json(cls, data: str) -> "StdioResponse":
        d = json.loads(data)
        err = d.get("error")
        return cls(
            id=d.get("id", 0),
            result=d.get("result"),
            error=err,
            success=err is None,
        )


@dataclass
class StdioNotification:
    """JSON-RPC 通知（无 id，不需要响应）"""
    method: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
        }, ensure_ascii=False)


@dataclass
class StreamingSession:
    """流式会话状态"""
    task_id: str
    agent_id: str
    buffer: List[str] = field(default_factory=list)
    is_active: bool = True
    started_at: float = 0.0
    last_chunk_at: float = 0.0


# ── Stdio Endpoint ───────────────────────────────────────────────

@dataclass
class StdioEndpoint:
    """stdio Agent 端点配置"""
    agent_id: str
    command: List[str]           # 启动命令，如 ["qwen", "prompt"]
    work_dir: str = "."
    env: Dict[str, str] = field(default_factory=dict)
    timeout_sec: float = 300.0
    request_id: int = 0           # 自增请求 ID


# ══════════════════════════════════════════════════════════════════
# Stdio Transport
# ══════════════════════════════════════════════════════════════════

class StdioTransport:
    """MCP stdio JSON-RPC 传输层

    每个 Agent 对应一个子进程，通过 stdin/stdout 通信。

    Usage:
        transport = StdioTransport()
        transport.register("qwen-code", ["qwen", "prompt"], env={...})

        # 派发任务（请求-响应）
        resp = transport.request("qwen-code", "task/execute",
            {"prompt": "run tests", "task_id": "T001"})

        # 取消任务
        transport.notify("qwen-code", "task/cancel", {"task_id": "T001"})

        # 流式读取进度
        transport.on_progress("qwen-code", lambda chunk: print(chunk))

        # 心跳
        alive = transport.ping("qwen-code")
    """

    def __init__(self):
        self._endpoints: Dict[str, StdioEndpoint] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._readers: Dict[str, threading.Thread] = {}
        self._sessions: Dict[str, StreamingSession] = {}
        self._progress_handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
        self._response_buffers: Dict[int, StdioResponse] = {}
        self._response_events: Dict[int, threading.Event] = {}
        self._running = False

    def register(self, agent_id: str, command: List[str], **kwargs) -> None:
        """注册 Agent endpoint"""
        self._endpoints[agent_id] = StdioEndpoint(
            agent_id=agent_id,
            command=command,
            **kwargs,
        )

    # ── Process Management ────────────────────────────────────────

    def connect(self, agent_id: str) -> bool:
        """启动 Agent 子进程并建立 stdio 连接"""
        ep = self._endpoints.get(agent_id)
        if ep is None:
            return False

        if agent_id in self._processes:
            return True  # already connected

        try:
            import os as _os
            env = _os.environ.copy()
            env.update(ep.env)

            proc = subprocess.Popen(
                ep.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=ep.work_dir,
                env=env,
                bufsize=1,          # line buffered
            )
            self._processes[agent_id] = proc

            # Start reader thread
            reader = threading.Thread(
                target=self._reader_loop,
                args=(agent_id, proc),
                daemon=True,
            )
            self._readers[agent_id] = reader
            reader.start()

            return True
        except (subprocess.SubprocessError, OSError):
            return False

    def disconnect(self, agent_id: str) -> None:
        """断开 Agent 连接"""
        proc = self._processes.pop(agent_id, None)
        if proc:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=5)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                proc.kill()

        reader = self._readers.pop(agent_id, None)
        if reader:
            reader.join(timeout=2)

    def disconnect_all(self) -> None:
        for aid in list(self._processes.keys()):
            self.disconnect(aid)

    def is_connected(self, agent_id: str) -> bool:
        proc = self._processes.get(agent_id)
        return proc is not None and proc.poll() is None

    # ── Request/Response ──────────────────────────────────────────

    def _next_id(self, agent_id: str) -> int:
        ep = self._endpoints.get(agent_id)
        if ep:
            ep.request_id += 1
            return ep.request_id
        return 1

    def request(
        self,
        agent_id: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 60.0,
    ) -> StdioResponse:
        """发送 JSON-RPC 请求并等待响应"""
        proc = self._processes.get(agent_id)
        if proc is None or proc.poll() is not None:
            # Fallback: try connect
            if not self.connect(agent_id):
                return StdioResponse(id=0, error={"code": -1, "message": f"Not connected: {agent_id}"})

            proc = self._processes.get(agent_id)
            if proc is None:
                return StdioResponse(id=0, error={"code": -1, "message": "Connection failed"})

        req_id = self._next_id(agent_id)
        request = StdioRequest(method=method, params=params or {}, id=req_id)

        event = threading.Event()
        self._response_events[req_id] = event

        try:
            with self._lock:
                proc.stdin.write(request.to_json() + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.disconnect(agent_id)
            return StdioResponse(id=req_id, error={"code": -2, "message": "Broken pipe"})

        if event.wait(timeout=timeout_sec):
            resp = self._response_buffers.pop(req_id, None)
            if resp:
                return resp

        return StdioResponse(id=req_id, error={"code": -3, "message": "Timeout"})

    def notify(self, agent_id: str, method: str, params: Optional[Dict[str, Any]] = None) -> bool:
        """发送 JSON-RPC 通知（不等待响应）"""
        proc = self._processes.get(agent_id)
        if proc is None or proc.poll() is not None:
            return False

        notification = StdioNotification(method=method, params=params or {})
        try:
            with self._lock:
                proc.stdin.write(notification.to_json() + "\n")
                proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            self.disconnect(agent_id)
            return False

    # ── Heartbeat ─────────────────────────────────────────────────

    def ping(self, agent_id: str, timeout_sec: float = 5.0) -> bool:
        """心跳检测"""
        resp = self.request(agent_id, "heartbeat/ping", timeout_sec=timeout_sec)
        return resp.success

    # ── Progress / Streaming ──────────────────────────────────────

    def on_progress(self, agent_id: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """注册进度回调"""
        self._progress_handlers.setdefault(agent_id, []).append(handler)

    def start_session(self, task_id: str, agent_id: str) -> StreamingSession:
        session = StreamingSession(
            task_id=task_id,
            agent_id=agent_id,
            started_at=time.time(),
        )
        self._sessions[task_id] = session
        return session

    def get_session(self, task_id: str) -> Optional[StreamingSession]:
        return self._sessions.get(task_id)

    def end_session(self, task_id: str) -> None:
        self._sessions.pop(task_id, None)

    # ── Internal Reader ───────────────────────────────────────────

    def _reader_loop(self, agent_id: str, proc: subprocess.Popen) -> None:
        """后台线程：读取 Agent stdout 中的 JSON-RPC 消息"""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON output → stream chunk
                    handlers = self._progress_handlers.get(agent_id, [])
                    for h in handlers:
                        try:
                            h({"agent": agent_id, "content": line, "type": "text"})
                        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                            pass
                    continue

                # JSON-RPC 响应
                if "id" in data and data["id"] is not None:
                    req_id = data["id"]
                    resp = StdioResponse.from_json(line)
                    self._response_buffers[req_id] = resp
                    event = self._response_events.get(req_id)
                    if event:
                        event.set()

                # JSON-RPC 通知（进度/事件）
                elif "method" in data:
                    method = data["method"]
                    params = data.get("params", {})

                    if method == "task/progress":
                        task_id = params.get("task_id", "")
                        content = params.get("content", "")
                        if task_id in self._sessions:
                            self._sessions[task_id].buffer.append(content)
                            self._sessions[task_id].last_chunk_at = time.time()

                    handlers = self._progress_handlers.get(agent_id, [])
                    for h in handlers:
                        try:
                            h({"method": method, "params": params})
                        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                            pass
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Public API (convenience) ──────────────────────────────────

    def execute_task(
        self,
        agent_id: str,
        task_id: str,
        prompt: str,
        *,
        timeout_sec: float = 300.0,
    ) -> StdioResponse:
        """派发任务并等待完成"""
        self.start_session(task_id, agent_id)
        return self.request(
            agent_id,
            "task/execute",
            {"task_id": task_id, "prompt": prompt},
            timeout_sec=timeout_sec,
        )

    def cancel_task(self, agent_id: str, task_id: str) -> bool:
        """取消任务"""
        return self.notify(agent_id, "task/cancel", {"task_id": task_id})

    def read_progress(self, task_id: str) -> List[str]:
        """读取任务进度"""
        session = self._sessions.get(task_id)
        if session:
            return list(session.buffer)
        return []

    def status(self) -> Dict[str, Any]:
        return {
            "endpoints": list(self._endpoints.keys()),
            "connected": [aid for aid in self._processes if self.is_connected(aid)],
            "sessions": {
                tid: {
                    "agent": s.agent_id,
                    "active": s.is_active,
                    "chunks": len(s.buffer),
                }
                for tid, s in self._sessions.items()
            },
        }
