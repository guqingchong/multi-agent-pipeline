"""
src/pipeline_executor.py — Pipeline 执行编排层 v1.0

将真实 CLI Agent 接入 pipeline 工作流。替代虚假的 delegate_task 角色扮演。

架构：
  Hermes (编排)
    │
    ├── system_constraint.route_task()  → 确定目标 adapter
    ├── mcp_transport.push()            → 任务入队（SQLite MQ）
    │
    └── Agent Daemon (独立进程)
          ├── mcp_transport.pull()      → 拉取任务
          ├── adapter.execute()         → 真实 CLI 调用
          └── mcp_transport.complete()  → 返回结果

用法：
  executor = PipelineExecutor(db_path="pipeline.db")
  executor.register_all_endpoints()

  # 同步执行（阻塞等待）
  result = executor.dispatch_and_wait("codewhale", "review", {"prompt": "..."})

  # 异步执行（返回 task_id，后续 collect）
  task_id = executor.dispatch("codewhale", "review", {"prompt": "..."})
  result = executor.collect(task_id)
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp_transport import (
        MCPTransport, MCPTask, MCPResult, MCPStatus,
        AgentEndpoint, create_transport,
    )
except (ModuleNotFoundError, ImportError):
    from src.mcp_transport import (
        MCPTransport, MCPTask, MCPResult, MCPStatus,
        AgentEndpoint, create_transport,
    )

try:
    from system_constraint import (
        SystemConstraint,
    )
except (ModuleNotFoundError, ImportError):
    from src.system_constraint import (
        SystemConstraint,
    )

try:
    from fallback_manager import FallbackManager, FallbackConfig
except (ModuleNotFoundError, ImportError):
    try:
        from src.fallback_manager import FallbackManager, FallbackConfig
    except (ModuleNotFoundError, ImportError):
        FallbackManager = None

try:
    from adapters import AgentResult, AdapterStatus
except (ModuleNotFoundError, ImportError):
    from src.adapters import AgentResult, AdapterStatus

# P3: dispatch_history persistence
try:
    from state_store import StateStore
except (ModuleNotFoundError, ImportError):
    from src.state_store import StateStore

# P0: Event callback chain
try:
    from event_engine import EventEngine, ChainStep, CHAIN_TEMPLATES
except (ModuleNotFoundError, ImportError):
    from src.event_engine import EventEngine, ChainStep, CHAIN_TEMPLATES

# P1: Heartbeat + Cancel + Streaming
try:
    from heartbeat import HeartbeatMonitor, TaskCanceller, StreamingCollector, CancelToken
except (ModuleNotFoundError, ImportError):
    from src.heartbeat import HeartbeatMonitor, TaskCanceller, StreamingCollector, CancelToken

# P2: Stdio transport (optional)
try:
    from stdio_transport import StdioTransport, StdioEndpoint
except (ModuleNotFoundError, ImportError):
    from src.stdio_transport import StdioTransport, StdioEndpoint

# P1: 统一注册表（唯一真相源）
try:
    from registry import REGISTRY
except (ModuleNotFoundError, ImportError):
    from src.registry import REGISTRY


__all__ = [
    "PipelineExecutor",
    "CLIEndpoint",
    "DEFAULT_ENDPOINTS",
    "AgentResult",
    "create_executor",
]


# ── 默认 CLI Endpoint 配置 ───────────────────────────────────────

@dataclass
class CLIEndpoint:
    """CLI Endpoint 定义"""
    adapter_name: str
    cli_path: str
    cli_command: str
    env: Dict[str, str] = field(default_factory=dict)


def _resolve_cli_path(name: str) -> str:
    """解析 CLI 路径（npm global / PATH）"""
    candidates = [
        Path(os.path.expanduser(f"~/AppData/Roaming/npm/{name}.cmd")),
        Path(os.path.expanduser(f"~/AppData/Roaming/npm/{name}.exe")),
        Path(os.path.expanduser(f"~/AppData/Roaming/npm/{name}")),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return name  # fallback: rely on PATH


def _load_key_from_file(source: str) -> Dict[str, str]:
    """从配置文件加载 API 密钥"""
    try:
        if source == "qwen":
            path = Path(os.path.expanduser("~/.qwen/settings.json"))
            if path.exists():
                data = json.loads(path.read_text())
                env_data = data.get("env", {})
                return {k: v for k, v in env_data.items() if "KEY" in k.upper() or "URL" in k.upper()}
        elif source == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
            if key:
                return {"OPENAI_API_KEY": key}
        elif source == "anthropic":
            result = {}
            # 1. 环境变量 ANTHROPIC_*（优先）
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            url = os.environ.get("ANTHROPIC_BASE_URL", "")
            # 1b. KIMI_CODING_* / KIMI_* fallback（.env 可能用这种变量名）
            if not key:
                key = os.environ.get("KIMI_CODING_API_KEY", "") or os.environ.get("KIMI_API_KEY", "")
            if not url:
                url = os.environ.get("KIMI_BASE_URL", "") or os.environ.get("KIMI_CODING_BASE_URL", "")
            # 2. Windows 注册表 fallback（bash/MSYS 不继承新设置的 User env vars）
            if not key or not url:
                try:
                    import subprocess
                    r = subprocess.run(
                        ['powershell', '-Command',
                         '[Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY","User")'],
                        capture_output=True, text=True, timeout=10
                    )
                    if r.stdout.strip():
                        key = key or r.stdout.strip()
                    if not key:
                        r2 = subprocess.run(
                            ['powershell', '-Command',
                             '[Environment]::GetEnvironmentVariable("KIMI_CODING_API_KEY","User")'],
                            capture_output=True, text=True, timeout=10
                        )
                        if r2.stdout.strip():
                            key = r2.stdout.strip()
                except (subprocess.SubprocessError, OSError):
                    pass
                try:
                    r = subprocess.run(
                        ['powershell', '-Command',
                         '[Environment]::GetEnvironmentVariable("ANTHROPIC_BASE_URL","User")'],
                        capture_output=True, text=True, timeout=10
                    )
                    if r.stdout.strip():
                        url = url or r.stdout.strip()
                    if not url:
                        r2 = subprocess.run(
                            ['powershell', '-Command',
                             '[Environment]::GetEnvironmentVariable("KIMI_BASE_URL","User")'],
                            capture_output=True, text=True, timeout=10
                        )
                        if r2.stdout.strip():
                            url = r2.stdout.strip()
                except (subprocess.SubprocessError, OSError):
                    pass
            if key:
                result["ANTHROPIC_API_KEY"] = key
            if url:
                result["ANTHROPIC_BASE_URL"] = url
            return result
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _build_default_endpoints() -> List[CLIEndpoint]:
    """从 REGISTRY.agents 动态构建默认 CLI Endpoint 列表。

    读取注册表中每个 Agent 的 cli_path、cli_command、env_vars，
    并叠加运行时从配置文件/环境变量加载的 API 密钥。
    """
    endpoints: List[CLIEndpoint] = []
    for name, agent in REGISTRY.agents.items():
        # 以注册表中的静态 env_vars 为基准
        env: Dict[str, str] = dict(agent.env_vars)

        # 按 Agent 名称叠加运行时密钥/变量
        if name == "claude-code":
            env.update(_load_key_from_file("anthropic"))
        elif name == "qwen-code":
            env.update(_load_key_from_file("qwen"))
            env.update(_load_key_from_file("openai"))
        elif name == "codewhale":
            # 允许环境变量在运行时覆盖注册表默认值
            env["AGENT_MOCK"] = os.environ.get("AGENT_MOCK", env.get("AGENT_MOCK", "false"))

        endpoints.append(CLIEndpoint(
            adapter_name=name,
            cli_path=_resolve_cli_path(agent.cli_path),
            cli_command=agent.cli_command,
            env=env,
        ))
    return endpoints


DEFAULT_ENDPOINTS: List[CLIEndpoint] = _build_default_endpoints()


# ── PipelineExecutor ─────────────────────────────────────────────

class PipelineExecutor:
    """Pipeline 执行编排器

    将 system_constraint 路由 + MCP transport + 真实 CLI 执行 串联。
    """

    def __init__(
        self,
        transport: Optional[MCPTransport] = None,
        *,
        db_path: str = "mcp_transport.db",
        work_dir: Optional[str] = None,
        enable_heartbeat: bool = True,
        enable_streaming: bool = True,
        enable_stdio: bool = True,
    ):
        self.transport = transport or create_transport(db_path)
        self.constraint = SystemConstraint()
        self.work_dir = work_dir or os.getcwd()
        self.db_path = db_path
        self._store: Optional[StateStore] = None
        self._daemon_procs: Dict[str, subprocess.Popen] = {}
        self._cli_endpoints: Dict[str, CLIEndpoint] = {}

        # P0: Event Engine
        self.events = EventEngine(executor=self)

        # P1: Heartbeat + Canceller + Streaming
        self.heartbeat = HeartbeatMonitor(
            heartbeat_interval=5.0,
            death_timeout=30.0,
            on_death=self._on_agent_death,
        ) if enable_heartbeat else None
        self.canceller = TaskCanceller() if enable_heartbeat else None
        self.streaming = StreamingCollector() if enable_streaming else None

        # P2: Stdio Transport (for real-time agents)
        self.stdio: Optional[StdioTransport] = None
        if enable_stdio:
            self.stdio = StdioTransport()
            self._setup_stdio_endpoints()

        if enable_heartbeat:
            for name in self._cli_endpoints:
                self.heartbeat.register(name)
            self.heartbeat.start()  # auto-start monitor thread

    @property
    def store(self) -> Optional[StateStore]:
        """Lazy StateStore backed by the same db_path."""
        if self._store is None:
            try:
                self._store = StateStore(Path(self.db_path))
            except (OSError, sqlite3.Error) as e:
                # Graceful degradation: do not break dispatch if store fails
                import logging
                logging.getLogger("pipeline_executor").warning(
                    "Failed to open StateStore at %s: %s", self.db_path, e
                )
                self._store = None  # type: ignore[assignment]
        return self._store

    # ── Endpoint 管理 ────────────────────────────────────────────

    def register_cli_endpoint(self, ep: CLIEndpoint) -> None:
        self._cli_endpoints[ep.adapter_name] = ep
        self.transport.register_endpoint(AgentEndpoint(
            agent_id=ep.adapter_name,
            cli_path=ep.cli_path,
            cli_command=ep.cli_command,
            env=ep.env,
        ))

    def register_all_defaults(self) -> None:
        """注册所有默认 CLI Endpoint。

        在注册时刻重新刷新环境变量，避免模块导入后 .env 才注入密钥。
        """
        for ep in DEFAULT_ENDPOINTS:
            refreshed_env = dict(ep.env)
            if ep.adapter_name == "claude-code":
                refreshed_env.update(_load_key_from_file("anthropic"))
            elif ep.adapter_name == "qwen-code":
                refreshed_env.update(_load_key_from_file("qwen"))
                refreshed_env.update(_load_key_from_file("openai"))
            elif ep.adapter_name == "codewhale":
                refreshed_env["AGENT_MOCK"] = os.environ.get(
                    "AGENT_MOCK", refreshed_env.get("AGENT_MOCK", "false")
                )
            refreshed_ep = CLIEndpoint(
                adapter_name=ep.adapter_name,
                cli_path=ep.cli_path,
                cli_command=ep.cli_command,
                env=refreshed_env,
            )
            self.register_cli_endpoint(refreshed_ep)

    # ── 任务派发 ─────────────────────────────────────────────────

    def dispatch(
        self,
        adapter_name: str,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 0,
    ) -> str:
        """异步派发任务到 Agent，返回 task_id

        Args:
            adapter_name: 目标 adapter (claude-code/codewhale/qwen-code)
            task_type: 任务类型 (code/review/test/e2e/doc)
            payload: 任务参数 (prompt, feature_id, diff 等)

        Returns:
            task_id — 用于后续 collect()
        """
        # 1. 系统约束验证
        self.constraint.route_task(task_type, payload or {})

        # 2. 确保 endpoint 已注册
        if adapter_name not in self._cli_endpoints:
            raise ValueError(
                f"Unknown adapter: {adapter_name}. "
                f"Available: {list(self._cli_endpoints.keys())}"
            )

        # 3. 推送到 MCP transport
        task_id = self.transport.push(
            adapter_name, task_type, payload, priority=priority
        )

        return task_id

    def dispatch_and_wait(
        self,
        adapter_name: str,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout_sec: Optional[float] = None,  # None=不超时
    ) -> MCPResult:
        """同步派发任务并等待结果

        如果 daemon 未运行，直接同步执行 CLI（绕过 transport）。
        """
        # 1. 路由验证
        self.constraint.route_task(task_type, payload or {})

        # 2. 尝试通过 transport 派发
        task_id = self.transport.push(adapter_name, task_type, payload)

        # 3. 如果有活跃 daemon，等待 transport 结果
        if adapter_name in self._daemon_procs:
            return self.transport.collect(task_id, timeout_sec)

        # 4. 否则直接同步执行（fallback）
        return self._execute_sync(adapter_name, task_id, task_type, payload, timeout_sec)

    def _execute_sync(
        self,
        adapter_name: str,
        task_id: str,
        task_type: str,
        payload: Optional[Dict[str, Any]],
        timeout_sec: float,
    ) -> MCPResult:
        """同步执行 CLI（不经过 daemon）"""
        ep = self._cli_endpoints.get(adapter_name)
        if ep is None:
            return MCPResult(
                task_id=task_id, agent_id=adapter_name,
                success=False, error=f"No CLI endpoint for {adapter_name}",
                status=MCPStatus.FAILED,
            )

        payload = payload or {}
        prompt = payload.get("prompt", "")
        # Build args list safely (no shell=True, no shell injection)
        # CLI command template like "exec --auto {prompt}" or 'prompt "{prompt}"'
        args = [ep.cli_path]
        if "{prompt}" in ep.cli_command:
            prefix, suffix = ep.cli_command.split("{prompt}", 1)
            if prefix.strip():
                args.extend(prefix.strip().split())
            args.append(prompt)
            if suffix.strip():
                args.extend(suffix.strip().split())
        else:
            args.extend(ep.cli_command.split())

        start = time.time()
        try:
            env = os.environ.copy()
            env.update(ep.env)

            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=self.work_dir,
                env=env,
            )
            latency_ms = int((time.time() - start) * 1000)
            raw = proc.stdout or proc.stderr or ""
            # Strip ANSI escape sequences + CodeWhale TUI prefix "N;emoji "
            import re
            output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw)     # CSI: \x1b[...
            output = re.sub(r'\x1b\][^\x07]*\x07', '', output)     # OSC: \x1b]...\x07
            output = re.sub(r'^\d+;\S[^\n]{0,20}\n?', '', output, flags=re.MULTILINE)  # TUI prefix per-line
            output = output.strip()

            result = MCPResult(
                task_id=task_id,
                agent_id=adapter_name,
                success=proc.returncode == 0,
                output=output[:5000],
                latency_ms=latency_ms,
                status=MCPStatus.COMPLETED if proc.returncode == 0 else MCPStatus.FAILED,
                error="" if proc.returncode == 0 else f"Exit code {proc.returncode}",
            )
        except subprocess.TimeoutExpired:
            result = MCPResult(
                task_id=task_id, agent_id=adapter_name,
                success=False, error=f"Timeout after {timeout_sec}s",
                status=MCPStatus.TIMEOUT,
            )
        except (subprocess.SubprocessError, OSError) as e:
            result = MCPResult(
                task_id=task_id, agent_id=adapter_name,
                success=False, error=str(e),
                status=MCPStatus.FAILED,
            )

        self.transport.complete(task_id, AgentResult(
            success=result.success,
            output=result.output,
            error_message=result.error,
            latency_ms=result.latency_ms,
        ), agent_id=adapter_name)

        # P3: 同步路径也写入 dispatch_history，标注 exec_mode='sync'
        try:
            store = self.store
            if store is not None:
                store.write_dispatch_history(
                    task_id=task_id,
                    agent=adapter_name,
                    task_type=task_type,
                    success=result.success,
                    latency_ms=result.latency_ms or 0,
                    exec_mode="sync",
                    output=result.output,
                    error=result.error,
                )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            # dispatch_history 是记录型数据，不能影响主流程
            pass

        return result

    def collect(self, task_id: str, timeout_sec: Optional[float] = None) -> MCPResult:
        """收集任务结果（阻塞）"""
        return self.transport.collect(task_id, timeout_sec)

    def dispatch_batch(
        self,
        tasks: List[Dict[str, Any]],
        *,
        timeout_sec: float = 600.0,
    ) -> Dict[str, MCPResult]:
        """批量派发并等待全部完成

        tasks: [{"adapter": "codewhale", "type": "review", "payload": {...}}, ...]
        """
        task_ids = []
        for t in tasks:
            tid = self.dispatch(
                t["adapter"], t["type"], t.get("payload", {}),
                priority=t.get("priority", 0),
            )
            task_ids.append(tid)

        return self.transport.collect_all(task_ids, timeout_sec)

    # ── Daemon 管理 ───────────────────────────────────────────────

    def start_daemon(self, adapter_name: str) -> Optional[subprocess.Popen]:
        """启动 Agent Daemon 进程（后台）"""
        ep = self._cli_endpoints.get(adapter_name)
        if ep is None:
            return None

        daemon_script = _build_daemon_script(adapter_name, ep)
        proc = subprocess.Popen(
            [sys.executable, "-c", daemon_script],
            env={**os.environ, **ep.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.work_dir,
        )
        self._daemon_procs[adapter_name] = proc
        return proc

    def start_all_daemons(self) -> Dict[str, subprocess.Popen]:
        for name in self._cli_endpoints:
            self.start_daemon(name)
        return self._daemon_procs

    def stop_daemon(self, adapter_name: str) -> None:
        if adapter_name in self._daemon_procs:
            proc = self._daemon_procs[adapter_name]
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, Exception):
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                    pass
            finally:
                try:
                    if proc.stdout:
                        proc.stdout.close()
                    if proc.stderr:
                        proc.stderr.close()
                except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                    pass
            del self._daemon_procs[adapter_name]

    def stop_all_daemons(self) -> None:
        for name in list(self._daemon_procs.keys()):
            self.stop_daemon(name)

    # ── 状态查询 ─────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        s = {
            "transport": self.transport.all_status(),
            "daemons": {name: "RUNNING" for name in self._daemon_procs},
            "cli_endpoints": list(self._cli_endpoints.keys()),
            "work_dir": self.work_dir,
        }
        # P0
        s["event_engine"] = "active"
        # P1
        if self.heartbeat:
            s["heartbeat"] = self.heartbeat.status_report()
            s["canceller"] = {"cancelled": self.canceller.cancelled_count()}
        if self.streaming:
            s["streaming"] = {"sessions": len(self.streaming._streams)}
        # P2
        if self.stdio:
            s["stdio"] = self.stdio.status()
        else:
            s["stdio"] = "disabled"
        return s

    # ── P0: 事件链快捷方法 ────────────────────────────────────────

    def chain_review_fix_test(self, review_prompt: str, fix_prompt: str = "修复审查发现的问题",
                              test_prompt: str = "运行全量测试") -> ChainResult:
        """预置链：审查→修复→测试"""
        return self.events.chain(
            ChainStep("codewhale", "review", {"prompt": review_prompt}),
            ChainStep("claude-code", "code", {"prompt": fix_prompt},
                      condition=lambda r: r.success and "P0" in r.output),
            ChainStep("qwen-code", "test", {"prompt": test_prompt}),
        )

    # ── P1: 心跳+取消 ──────────────────────────────────────────────

    def cancel_agent_task(self, agent_id: str, task_id: str, reason: str = "") -> bool:
        if self.canceller:
            return self.canceller.cancel(task_id, reason)
        return False

    def stream_progress(self, task_id: str, agent_id: str, content: str, final: bool = False) -> None:
        if self.streaming:
            self.streaming.stream(task_id, agent_id, content, final)

    def read_stream(self, task_id: str) -> str:
        if self.streaming:
            return self.streaming.read_all(task_id)
        return ""

    def _on_agent_death(self, agent_id: str) -> None:
        """Agent 死亡回调 — 取消该 Agent 所有任务 + 记录日志"""
        if self.canceller:
            count = self.canceller.cancel_agent(agent_id, f"Agent {agent_id} detected as DEAD")
            # Log warning
            import logging
            logging.warning(f"Agent {agent_id} DEAD — cancelled {count} tasks")

    # ── P2: Stdio 端点设置 ─────────────────────────────────────────

    def _setup_stdio_endpoints(self) -> None:
        """为每个 CLI endpoint 注册 stdio transport"""
        if self.stdio is None:
            return
        for name, ep in self._cli_endpoints.items():
            cmd = [ep.cli_path]
            # Add any default args from cli_command
            cmd_parts = ep.cli_command.replace('"{prompt}"', "").strip().split()
            cmd.extend(cmd_parts)
            self.stdio.register(name, cmd, work_dir=self.work_dir, env=ep.env)


# ── Daemon 脚本生成 ──────────────────────────────────────────────

def _build_daemon_script(adapter_name: str, ep: CLIEndpoint) -> str:
    """生成 Agent Daemon 的内联 Python 脚本"""
    return f'''
import json, os, subprocess, sys, time
sys.path.insert(0, r"C:\\tmp\\multi-agent-pipeline")
try:
    from src.mcp_transport import MCPTransport, AgentEndpoint
except ImportError:
    from mcp_transport import MCPTransport, AgentEndpoint

transport = MCPTransport("mcp_transport.db")
transport.register_endpoint(AgentEndpoint(
    agent_id="{adapter_name}",
    cli_path=r"{ep.cli_path}",
    cli_command='{ep.cli_command}',
    max_concurrent=1, timeout_sec=300,
))
ep_data = transport.get_endpoint("{adapter_name}")

print(f"[DAEMON] {{adapter_name}} started, polling...")
while True:
    task = transport.pull("{adapter_name}")
    if task is None:
        time.sleep(1)
        continue
    if task.task_type == "shutdown":
        transport.complete(task.id, type('R',(),{{"success":True,"output":"shutdown","structured":None,"latency_ms":0,"tokens_used":0,"error_message":""}})())
        break
    prompt = task.payload.get("prompt", "")
    cmd = f'{{ep.cli_path}} {{ep.cli_command}}'.replace("{{prompt}}", prompt.replace('"','\\\\"'))
    start = time.time()
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, env=os.environ)
        lat = int((time.time()-start)*1000)
        output = (proc.stdout or proc.stderr or "").strip()
        result = type('R',(),{{"success":proc.returncode==0,"output":output[:5000],"structured":None,"latency_ms":lat,"tokens_used":0,"error_message":"" if proc.returncode==0 else f"exit {{proc.returncode}}"}})()
    except (subprocess.SubprocessError, OSError) as e:
        result = type('R',(),{{"success":False,"output":"","structured":None,"latency_ms":0,"tokens_used":0,"error_message":str(e)}})()
    transport.complete(task.id, result, agent_id="{adapter_name}")
print(f"[DAEMON] {{adapter_name}} stopped")
'''


# ── 工厂函数 ─────────────────────────────────────────────────────

def create_executor(
    db_path: str = "mcp_transport.db",
    work_dir: Optional[str] = None,
) -> PipelineExecutor:
    """创建 Pipeline 执行器

    work_dir 优先级: 参数 > PIPELINE_PROJECT_DIR 环境变量 > 当前目录
    """
    if work_dir is None:
        work_dir = os.environ.get("PIPELINE_PROJECT_DIR")
    executor = PipelineExecutor(db_path=db_path, work_dir=work_dir)
    executor.register_all_defaults()
    return executor
