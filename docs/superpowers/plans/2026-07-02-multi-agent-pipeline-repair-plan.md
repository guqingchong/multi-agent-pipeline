# Multi-Agent Pipeline 整体修复与轻量化升级方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在纯 Windows、无 Docker 环境下，将当前臃肿且多处重叠的 multi-agent-pipeline 重构为边界清晰、注册表驱动、队列异步、易于本地运行的轻量框架，并在 9 大质量维度 + 实用性维度上达到可交付水平。

**Architecture:** 采用“注册表单一真相源 + 状态持久层 + 统一任务队列 + 编排状态机 + 可插拔 Agent 适配器”四层架构；CLI/API/Bridge 共享同一核心，所有 phase/agent/task_type 从 `registry.py` 派生，移除 `models.py`/`workflow_registry.py`/`config_loader.py` 中的重复定义；队列层合并 `message_queue.py`/`task_queue.py` 为 `queue.py`，保留同步 API 与异步包装。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic-settings, SQLite, pytest, Windows CLI (claude.cmd / qwen.cmd / codewhale-tui.exe), PowerShell/Batch 启动脚本。

---

## 1. 当前核心问题（执行前必须理解）

1. **真相源分裂**：`registry.py` 已注册 19 个 phase、3 个 agent、8 个 task_type，但 `models.py` 仍用 8 值 `Phase` enum；`workflow_registry.py` 用另一套大写 phase 名（`PRD`、`RESEARCH` 等）；`config_loader.py` 若存在则又是一份配置。
2. **队列重复**：`message_queue.py`（同步 SQLite 队列）和 `task_queue.py`（异步包装）功能边界模糊，且没有统一接口；`event_engine.py` 又自建了一套链式调用，三套任务流转机制并存。
3. **入口重叠**：`pipeline.py` 提供 10+ CLI 命令，`bridge_cli.py` 把它们重新暴露一遍并加入 `dispatch/debate`；`main.py` 的 FastAPI 端点大量 mock，没有真正调用核心。
4. **Windows 硬编码**：`registry.py` 写死 `C:\Users\顾庆冲\AppData\Roaming\npm\...`，无法在其他 Windows 环境启动；`docker-compose.yml` 引用不存在的 `Dockerfile` 与 `sqlite:latest` 镜像。
5. **文档与代码脱节**：`README.md` 声称 Phase 0-6 / 773 测试，`progress.md` 声称 v3.0 部署 / 1206 测试，实际测试数与模块数均不一致，`AGENTS.md` 为 `(TBD)`。
6. **实用性差**：没有 Windows 一键启动脚本，没有清晰的健康检查入口，没有环境变量清单，新用户无法在不读源码的情况下跑通 `init → develop → test`。

---

## 2. 目标架构与模块边界（重构后）

```text
┌─────────────────────────────────────────────────────────────────┐
│  Entry Layer                                                     │
│  pipeline.py        — CLI 主入口（init/advance/status/...）      │
│  bridge_cli.py      — Hermes / 外部 Agent 调用的 JSON 桥         │
│  main.py            — FastAPI 服务（可选，真实状态端点）          │
├─────────────────────────────────────────────────────────────────┤
│  Orchestration Layer                                             │
│  phase_flow.py      — 阶段状态机（advance/check/rollback/approve）│
│  phase_checks.py    — 阶段检查函数注册表                         │
│  system_constraint.py — 任务路由 + Hermes 权限约束               │
│  suggestion_engine.py — 下一步建议                               │
│  debate/            — 辩论子系统（本次重构不涉及，保留现状）      │
│  inspector.py       — 独立审计员，持 PRD/架构/旅程，可 veto     │
├─────────────────────────────────────────────────────────────────┤
│  Dispatch & Queue Layer                                          │
│  queue.py           — 统一 SQLite 任务队列（sync + async API）    │
│  adapters.py        — Agent 适配器与 AgentResult                 │
│  pipeline_executor.py — 任务派发与等待                           │
│  event_engine.py    — 基于 queue 的事件链（保留，改为依赖 queue） │
├─────────────────────────────────────────────────────────────────┤
│  Persistence Layer                                               │
│  state_store.py     — 项目/特性/检查点/审计/调度历史持久化        │
│  registry.py        — Agent/Phase/TaskType 单一注册表             │
│  config.py          — Pydantic-settings 环境配置                  │
└─────────────────────────────────────────────────────────────────┘
```

**待删除或合并的文件：**
- `message_queue.py` + `task_queue.py` → 合并为 `src/queue.py`。
- `config_loader.py`（若存在）→ 删除，配置统一走 `config.py`。
- `workflow_registry.py` 与 `workflow_template.py` → 合并为 `src/workflow.py`，且 phase 列表从 `REGISTRY` 动态派生，不再硬编码大写 phase 名。
- `docker-compose.yml` → 删除，替换为 `scripts/start-windows.ps1` 与 `scripts/start-api.ps1`。

---

## 3. 9 大质量维度 + 实用性升级映射

| 维度 | 当前问题 | 升级动作 |
|------|---------|---------|
| 正确性 | `Phase` enum 与注册表不一致 | 将 phase 表示改为注册表驱动 |
| 可靠性 | 队列重启后 running 任务孤儿、缺少熔断 | 统一队列 + 启动恢复 + 熔断 |
| 安全性 | 硬编码 API key 占位、沙箱配置散落 | env 配置化、沙箱配置集中 |
| 性能 | 多处短连接 SQLite、无索引 | WAL + 连接池 + 必要索引 |
| 可维护性 | 重复定义、旧 3-state 兼容代码 | 删除 F005 3-state 兼容层 |
| 可用性 | CLI 帮助不清、Windows 路径硬编码 | argparse 帮助 + env 配置 |
| 可测试性 | 大量测试依赖 `AGENT_MOCK=true` | 官方 mock fixture + 接口契约测试 |
| 可扩展性 | phase/agent 新增需改多处 | 注册表驱动 + 插件化检查函数 |
| 可观测性 | 日志散落、trace 表未充分利用 | 结构化日志 + 关键路径 trace |
| 治理/一致性 | 各阶段产出与 PRD/架构/旅程是否一致无人把关 | 独立 Inspector 持文档 veto |
| 实用性 | 无 Windows 启动脚本、文档过期 | PowerShell 一键脚本 + 文档重写 |

---

## 4. 文件结构（重构后）

**新增文件：**
- `src/queue.py`：统一任务队列（替代 `message_queue.py`/`task_queue.py`）。
- `src/workflow.py`：轻量工作流模板（替代 `workflow_registry.py`/`workflow_template.py`）。
- `src/phase_model.py`：注册表驱动的 phase 抽象（替代 `models.py` 中 enum）。
- `src/inspector.py`：独立审计员，持有 PRD/架构/旅程/验收标准，可在阶段推进前 veto。
- `scripts/start-windows.ps1`：Windows 本地启动（含健康检查）。
- `scripts/start-api.ps1`：启动 FastAPI 服务。
- `scripts/env.example.ps1`：环境变量模板。
- `docs/superpowers/runbooks/windows-setup.md`：Windows 部署手册。

**修改文件：**
- `src/registry.py`：移除硬编码 Windows 路径，改用 env 占位 + 运行时解析。
- `src/models.py`：删除 `Phase` enum，改为从 `phase_model.py` 导入。
- `src/config.py`：增加 `agent_cli_paths` 字段，允许通过 `.env` 覆盖路径。
- `src/adapters.py`：统一 fallback 通道，补充 `version()` 健康检查。
- `src/pipeline_executor.py`：接入统一队列，移除直接 subprocess 的重复逻辑。
- `src/phase_flow.py`：从注册表读取 phase 顺序，不再硬编码。
- `src/phase_checks.py`：检查函数注册表化，删除硬编码阈值（改走 `thresholds.yaml`）。
- `src/state_store.py`：合并索引、移除 v1 schema 兼容（已可安全移除，前提：迁移脚本跑一次）。
- `src/pipeline.py`：精简 CLI，帮助信息从注册表动态生成。
- `src/bridge_cli.py`：移除对 `pipeline.py` 命令的重复代理，改为直接调用核心函数。
- `src/main.py`：将 mock 端点替换为 `state_store` / `queue` 真实查询。
- `README.md` / `progress.md` / `DEPLOY.md` / `AGENTS.md`：重写。

**删除文件：**
- `src/message_queue.py`
- `src/task_queue.py`
- `src/workflow_registry.py`
- `src/workflow_template.py`
- `docker-compose.yml`
- `config_loader.py`（若存在）
- `tests_three_layer/test_p0_issues.py`（反向测试已无意义，若需保留则改为正向后兼容测试）

**本次重构不涉及 `src/debate/` 辩论子系统；保持现状，后续独立优化。**

---

## 5. 任务分解

### Task 1: 建立注册表单一真相源并修复 Phase 模型

**Files:**
- Create: `src/phase_model.py`
- Modify: `src/registry.py`
- Modify: `src/models.py`
- Modify: `src/config.py`
- Delete: `src/workflow_registry.py`, `src/workflow_template.py`（物理删除放到 Task 2 末尾）
- Test: `tests/test_registry.py`, `tests/test_phase_model.py`

- [ ] **Step 1: 新建 `src/phase_model.py`，用注册表驱动的 Phase 表示**

```python
from __future__ import annotations
from typing import List, Optional
try:
    from registry import REGISTRY
except ImportError:
    from src.registry import REGISTRY


class Phase:
    """Registry-driven phase handle. Immutable and comparable by name."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        n = name.lower()
        if n not in REGISTRY.phases:
            raise ValueError(f"Unknown phase: {name!r}")
        self.name = n

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Phase) and self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    # Convenience methods for enum-era compatibility
    def is_init(self) -> bool:
        return self.name == "init"

    def is_start(self, mode: str = "greenfield") -> bool:
        order = get_phase_order(mode)
        return bool(order) and self.name == order[0]

    def is_terminal(self, mode: str = "greenfield") -> bool:
        order = get_phase_order(mode)
        return bool(order) and self.name == order[-1]

    @classmethod
    def list_all(cls) -> List["Phase"]:
        return [cls(n) for n in REGISTRY.list_phases()]

    @classmethod
    def from_name(cls, name: str) -> "Phase":
        return cls(name)

    def next(self, pipeline_mode: str = "greenfield") -> Optional["Phase"]:
        order = get_phase_order(pipeline_mode)
        if self.name not in order:
            return None
        idx = order.index(self.name)
        return Phase(order[idx + 1]) if idx + 1 < len(order) else None

    def prev(self, pipeline_mode: str = "greenfield") -> Optional["Phase"]:
        order = get_phase_order(pipeline_mode)
        if self.name not in order:
            return None
        idx = order.index(self.name)
        return Phase(order[idx - 1]) if idx > 0 else None


def get_phase_order(mode: str = "greenfield") -> List[str]:
    """Return ordered phase names for a pipeline mode from config/registry."""
    from config import get_config
    cfg = get_config()
    if mode == "greenfield":
        return [p for p in cfg.greenfield_phase_order if p in REGISTRY.phases]
    if mode == "brownfield":
        return [p for p in cfg.brownfield_phase_order if p in REGISTRY.phases]
    raise ValueError(f"Unknown pipeline mode: {mode!r}")
```

- [ ] **Step 2: 修改 `src/config.py`，增加 phase_order 与 agent 路径配置**

在 `PipelineConfig` 中新增字段：

```python
from pydantic import Field
from typing import List, Dict

class PipelineConfig(BaseSettings):
    # ... existing fields ...

    greenfield_phase_order: List[str] = Field(default_factory=lambda: [
        "init", "prd", "research", "design", "decompose",
        "journey", "develop", "integrate", "test", "evaluate", "accept", "deploy"
    ])
    brownfield_phase_order: List[str] = Field(default_factory=lambda: [
        "discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"
    ])

    agent_cli_paths: Dict[str, str] = Field(default_factory=dict)
    """Map agent name -> absolute CLI path. Falls back to registry cli_path then PATH."""
```

- [ ] **Step 3: 修改 `src/registry.py`，移除硬编码 Windows 路径并允许 env 覆盖**

将 agent 注册改为：

```python
import os
from pathlib import Path
import shutil


def _resolve_cli_path(agent_name: str, fallback: str) -> str:
    """Resolve agent CLI path: env > registry fallback > PATH."""
    env_key = f"AGENT_CLI_PATH_{agent_name.upper().replace('-', '_')}"
    env_path = os.environ.get(env_key)
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())
    if fallback and Path(fallback).exists():
        return str(Path(fallback).resolve())
    which = shutil.which(agent_name)
    if which:
        return which
    return fallback  # return fallback so health check can report missing


REGISTRY.register_agent(AgentDef(
    name="claude-code",
    capabilities=["code", "adversarial"],
    cli_path=_resolve_cli_path("claude-code", r"claude.cmd"),
    cli_command="-p {prompt}",
    env_vars={"CLAUDE_CODE_SIMPLE": "1"},
))
REGISTRY.register_agent(AgentDef(
    name="codewhale",
    capabilities=["review"],
    cli_path=_resolve_cli_path("codewhale", r"codewhale-tui.exe"),
    cli_command="exec --auto {prompt}",
    env_vars={"AGENT_MOCK": "false"},
))
REGISTRY.register_agent(AgentDef(
    name="qwen-code",
    capabilities=["test", "doc", "e2e", "inspector"],
    cli_path=_resolve_cli_path("qwen-code", r"qwen.cmd"),
    cli_command="prompt {prompt}",
    env_vars={"QWEN_CODE_SUPPRESS_YOLO_WARNING": "1"},
))
```

并移除 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `QWEN_API_KEY` / `OPENAI_API_KEY` 空字符串占位（不应在代码里出现 key 名集合，改由 agent 自身文档说明）。

- [ ] **Step 4: 在 `Registry.register_task_type()` 中增加名称正则校验**

防止 task type 名称含单引号等特殊字符（避免后续 SQL DDL 拼接风险），并统一命名规范：

```python
import re

_TASK_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class Registry:
    # ... existing methods ...

    def register_task_type(self, task_type: TaskTypeDef) -> None:
        if not _TASK_TYPE_NAME_RE.match(task_type.name):
            raise ValueError(
                f"Invalid task type name {task_type.name!r}; must match ^[a-z][a-z0-9_-]*$"
            )
        self.task_types[task_type.name] = task_type
```

- [ ] **Step 5: 修改 `src/models.py`，删除 `Phase` enum，改为从 `phase_model` 导入**

```python
from phase_model import Phase, PHASE_NAMES

__all__ = ["PHASE_NAMES", "Phase", "ProjectState", ...]
```

删除旧的 `_get_core_phase_names`、8 值 enum、`next()`/`prev()` 旧实现，以及 F005 三态兼容注释。

- [ ] **Step 6: 全局替换 `Phase` 相关旧用法（必须在同一 Task 内完成）**

先扫描所有引用点：

```bash
rg "Phase\." src/ tests/ --type py
```

需要同步修改的模块至少包括：
- `src/pipeline.py`：`Phase.INIT` / `Phase.DEVELOP` / `.value` 改为 `Phase("init")` / `Phase("develop")` / `.name`。
- `src/phase_flow.py`：`isinstance(state.phase, Phase)` 仍成立，但 `phase.value` 改为 `phase.name`。
- `src/entry.py`：`ProjectState.phase` 字段类型改为 `Phase`。
- `src/state_store.py`：`Phase.from_name()` 行为不变，直接复用新实现。
- `src/bridge_cli.py`：pipeline 命令代理中涉及 phase 比较处改为字符串比较。
- `src/phase_checks.py`：检查函数中的 `Phase.*` 引用改为 `Phase("...")`。
- 所有测试文件：`Phase.INIT` 等改为 `Phase("init")`。

**原则：** 不在 `Phase` 类中保留旧 enum 值常量（如 `Phase.INIT`），避免真相源再次分裂；所有调用方显式使用 `Phase("init")`。

- [ ] **Step 7: 运行注册表与 phase 模型测试（含受影响的 10+ 测试文件）**

Run: `pytest tests/test_registry.py tests/test_phase_model.py tests/test_pipeline.py tests/test_phase_flow.py tests/test_state_store.py tests/test_phase_checks.py -v`
Expected: PASS（新增测试覆盖 Phase 创建、`next()`/`prev()`、未知 phase 报错、路径解析，以及全局替换后的旧测试回归）。

- [ ] **Step 8: Commit**

```bash
git add src/phase_model.py src/registry.py src/config.py src/models.py \
        src/pipeline.py src/phase_flow.py src/entry.py src/state_store.py \
        src/bridge_cli.py src/phase_checks.py tests/
git commit -m "refactor: registry-driven Phase model and configurable agent CLI paths"
```

---

### Task 2: 合并队列层并接入统一 dispatch

**Files:**
- Create: `src/queue.py`
- Modify: `src/pipeline_executor.py`
- Modify: `src/event_engine.py`（改为依赖 `queue.py` 而非直接 executor）
- Delete: `src/message_queue.py`, `src/task_queue.py`
- Test: `tests/test_queue.py`

- [ ] **Step 1: 新建 `src/queue.py`，同步 + 异步统一接口**

```python
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from config import PipelineConfig
    from registry import REGISTRY
except ImportError:
    from src.config import PipelineConfig
    from src.registry import REGISTRY


@dataclass
class Task:
    target_agent: str
    task_type: str
    context: Dict[str, Any] = field(default_factory=dict)
    feature_id: Optional[str] = None
    priority: int = 1
    max_retries: int = 3
    id: Optional[int] = None
    status: str = "queued"
    retry_count: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class Queue:
    """SQLite-backed task queue with both sync and async APIs."""

    VALID_STATUSES = ("queued", "running", "completed", "failed", "dead_letter")
    VALID_TASK_TYPES = tuple(REGISTRY.list_task_types())

    _DDL = """
    CREATE TABLE IF NOT EXISTS task_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_agent TEXT NOT NULL,
        task_type TEXT NOT NULL,
        feature_id TEXT,
        context_json TEXT NOT NULL DEFAULT '{}',
        priority INTEGER NOT NULL DEFAULT 1 CHECK(priority IN (0,1,2)),
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','dead_letter')),
        retry_count INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 3,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT,
        result_json TEXT,
        error_message TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_task_queue_status_priority ON task_queue(status, priority DESC, created_at ASC);
    CREATE INDEX IF NOT EXISTS idx_task_queue_target_agent ON task_queue(target_agent);
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            cfg = PipelineConfig()
            db_path = str(Path(cfg.base_dir) / "task_queue.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        parent = Path(db_path).parent
        if parent:
            parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_tables(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(self._DDL)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _row_to_task(cls, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            target_agent=row["target_agent"],
            task_type=row["task_type"],
            feature_id=row["feature_id"],
            context=json.loads(row["context_json"] or "{}"),
            priority=row["priority"],
            status=row["status"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_message=row["error_message"],
        )

    # Synchronous API
    def push_sync(self, task: Task) -> int:
        if task.task_type not in self.VALID_TASK_TYPES:
            raise ValueError(f"Invalid task_type {task.task_type!r}")
        now = self._now()
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """INSERT INTO task_queue
                       (target_agent, task_type, feature_id, context_json, priority, max_retries, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (task.target_agent, task.task_type, task.feature_id,
                     json.dumps(task.context, ensure_ascii=False), task.priority, task.max_retries, now),
                )
                conn.commit()
                task.id = cur.lastrowid
                task.created_at = now
                return task.id or 0
            finally:
                conn.close()

    def pull_sync(self, agent_id: str) -> Optional[Task]:
        now = self._now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """UPDATE task_queue SET status='running', started_at=?
                       WHERE id = (
                           SELECT id FROM task_queue
                           WHERE target_agent=? AND status='queued'
                           ORDER BY priority DESC, created_at ASC LIMIT 1
                       )
                       RETURNING *""",
                    (now, agent_id),
                ).fetchone()
                conn.commit()
                return self._row_to_task(row) if row else None
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    def complete_sync(self, task_id: int, result: Optional[Dict[str, Any]] = None) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE task_queue SET status='completed', completed_at=?, result_json=? WHERE id=? AND status='running'",
                    (self._now(), json.dumps(result, ensure_ascii=False) if result else None, task_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def fail_sync(self, task_id: int, error: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT retry_count, max_retries FROM task_queue WHERE id=? AND status='running'",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return False
                if row["retry_count"] < row["max_retries"]:
                    conn.execute(
                        "UPDATE task_queue SET status='queued', retry_count=retry_count+1, error_message=?, started_at=NULL WHERE id=?",
                        (error, task_id),
                    )
                else:
                    conn.execute(
                        "UPDATE task_queue SET status='dead_letter', error_message=?, completed_at=? WHERE id=?",
                        (error, self._now(), task_id),
                    )
                conn.commit()
                return True
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    def recover_orphaned_sync(self) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """UPDATE task_queue
                       SET status='queued', started_at=NULL,
                           error_message=COALESCE(error_message, 'Orphaned after restart')
                       WHERE status='running'"""
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def stats_sync(self) -> Dict[str, int]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT status, COUNT(*) AS cnt FROM task_queue GROUP BY status").fetchall()
                return {r["status"]: r["cnt"] for r in rows}
            finally:
                conn.close()

    # Asynchronous wrappers
    async def push(self, task: Task) -> int:
        return await asyncio.to_thread(self.push_sync, task)

    async def pull(self, agent_id: str) -> Optional[Task]:
        return await asyncio.to_thread(self.pull_sync, agent_id)

    async def complete(self, task_id: int, result: Optional[Dict[str, Any]] = None) -> bool:
        return await asyncio.to_thread(self.complete_sync, task_id, result)

    async def fail(self, task_id: int, error: str) -> bool:
        return await asyncio.to_thread(self.fail_sync, task_id, error)

    async def recover_orphaned(self) -> int:
        return await asyncio.to_thread(self.recover_orphaned_sync)

    async def stats(self) -> Dict[str, int]:
        return await asyncio.to_thread(self.stats_sync)
```

- [ ] **Step 2: 修改 `src/pipeline_executor.py`，改为通过 `Queue` 派发任务**

将 `dispatch_and_wait` 改为：先 `push` 任务到队列，再 `pull` 同一 agent，执行 CLI，最后 `complete`/`fail`。核心逻辑示例：

```python
from queue import Queue, Task

class PipelineExecutor:
    def __init__(self, queue: Queue, work_dir: str):
        self.queue = queue
        self.work_dir = work_dir

    def dispatch_and_wait(self, adapter: str, task_type: str, payload: dict, timeout_sec: int = 600) -> AgentResult:
        task = Task(target_agent=adapter, task_type=task_type, context=payload)
        task_id = self.queue.push_sync(task)
        task = self.queue.pull_sync(adapter)  # should return the same task
        if task is None or task.id != task_id:
            return AgentResult(success=False, error="Failed to claim queued task", status=AgentStatus.FAILED)
        result = self._run_cli(adapter, task_type, payload, timeout_sec)
        if result.success:
            self.queue.complete_sync(task_id, {"output": result.output})
        else:
            self.queue.fail_sync(task_id, result.error or "Unknown error")
        return result
```

- [ ] **Step 3: 修改 `src/event_engine.py` 的事件链改为基于 Queue**

`chain()` 将每一步转为 `Task` 入队，`chain_async()` 在后台线程循环 `pull` 并触发下一步。保留条件判断与重试模板。

- [ ] **Step 4: 删除 `src/message_queue.py` 和 `src/task_queue.py`**

物理删除，并全局替换导入 `from message_queue import ...` / `from task_queue import ...` 为 `from queue import Queue, Task`。

- [ ] **Step 5: 写队列测试并运行**

```python
def test_queue_push_pull_complete(tmp_path):
    from queue import Queue, Task
    q = Queue(db_path=str(tmp_path / "tq.db"))
    tid = q.push_sync(Task(target_agent="claude-code", task_type="code", context={"x": 1}))
    claimed = q.pull_sync("claude-code")
    assert claimed and claimed.id == tid
    assert q.complete_sync(tid, {"out": "ok"})
    assert q.stats_sync()["completed"] == 1
```

Run: `pytest tests/test_queue.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git rm src/message_queue.py src/task_queue.py
git add src/queue.py src/pipeline_executor.py src/event_engine.py tests/test_queue.py
git commit -m "refactor: unify message/task queues into single queue.py"
```

---

### Task 3: 编排层轻量化（phase_flow / system_constraint / suggestion_engine）

**Files:**
- Modify: `src/phase_flow.py`
- Modify: `src/system_constraint.py`
- Modify: `src/suggestion_engine.py`
- Modify: `src/phase_checks.py`
- Modify: `src/workflow.py`（由 workflow_registry + template 合并而来）
- Test: `tests/test_phase_flow.py`, `tests/test_system_constraint.py`

- [ ] **Step 1: 新建 `src/workflow.py`，从注册表派生模板**

**原则：** brownfield 先统一使用现有 `config.py` 的 7-phase 流程（`discover/benchmark/analyze/plan/execute/verify/deliver`），不引入 `brownfield_feature/fix/audit` 子模式；子模式后续独立设计，避免与注册表 phase 冲突。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List
from registry import REGISTRY


@dataclass
class WorkflowTemplate:
    name: str
    phases: List[str]
    conditions: List[Callable[[Dict], bool]] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> None:
        missing = [p for p in self.phases if p not in REGISTRY.phases]
        if missing:
            raise ValueError(f"Workflow {self.name} references unknown phases: {missing}")


def build_workflows() -> Dict[str, WorkflowTemplate]:
    return {
        "greenfield": WorkflowTemplate(
            name="greenfield",
            phases=[p for p in [
                "init", "prd", "research", "design", "decompose",
                "journey", "develop", "integrate", "test", "evaluate", "accept", "deploy"
            ] if p in REGISTRY.phases],
        ),
        "brownfield": WorkflowTemplate(
            name="brownfield",
            phases=[p for p in [
                "discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"
            ] if p in REGISTRY.phases],
        ),
    }
```

**debate 子系统说明：** `src/debate/` 在本次重构中保持现状，不纳入 workflow 模板；`bridge_cli.py debate` 命令不改动。

- [ ] **Step 2: 修改 `src/phase_flow.py` 从注册表读取 phase 顺序**

将 `PHASE_ORDER` 常量替换为：

```python
from workflow import build_workflows
from config import get_config

def get_current_workflow(state: ProjectState) -> WorkflowTemplate:
    cfg = get_config()
    mode = cfg.pipeline_mode
    return build_workflows().get(mode, build_workflows()["greenfield"])
```

`advance()` 使用 `Phase.next(workflow.phases)` 计算下一 phase。

- [ ] **Step 3: 修改 `src/system_constraint.py`，移除硬编码任务映射，改为注册表驱动**

```python
def route_task(self, task_type: str, spec: dict) -> dict:
    task_def = REGISTRY.get_task_type(task_type)
    if task_def is None:
        raise ConstraintViolation(f"Unknown task type: {task_type}")
    if task_def.default_agent:
        return {"target_adapter": task_def.default_agent}
    # fallback: Hermes handles orchestration tasks
    return {"target_adapter": "hermes"}
```

- [ ] **Step 4: 修改 `src/phase_checks.py`，注册检查函数到注册表/字典**

在 `phase_checks.py` 末尾增加：

```python
CHECK_REGISTRY: Dict[str, Callable] = {
    "init": check_init,
    "design": check_design,
    "decompose": check_decompose,
    "research": check_research,
    "prd": check_prd,
    "journey": check_journey,
    "develop": check_develop,
    "integrate": check_integrate,
    "test": check_test,
    "evaluate": check_evaluate,
    "accept": check_accept,
    "deploy": check_deploy,
    # brownfield
    "discover": check_discover,
    "benchmark": check_benchmark,
    "analyze": check_analyze,
    "plan": check_plan,
    "execute": check_execute,
    "verify": check_verify,
    "deliver": check_deliver,
}


def run_check(phase_name: str, project_name: str, base_dir: Path) -> Dict[str, Any]:
    fn = CHECK_REGISTRY.get(phase_name)
    if fn is None:
        return {"passed": False, "reason": f"No check function registered for {phase_name}", "details": {}}
    return fn(project_name, base_dir)
```

然后 `phase_flow.py` 的 `check()` 调用 `phase_checks.run_check(...)` 而非动态反射。

- [ ] **Step 5: 修改 `src/suggestion_engine.py` 使用注册表 workflow**

将阶段映射列表替换为 `build_workflows()[mode].phases`。

- [ ] **Step 6: 运行编排层测试**

Run: `pytest tests/test_phase_flow.py tests/test_system_constraint.py tests/test_workflow.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git rm src/workflow_registry.py src/workflow_template.py
git add src/workflow.py src/phase_flow.py src/system_constraint.py src/phase_checks.py src/suggestion_engine.py tests/test_workflow.py
git commit -m "refactor: registry-driven orchestration and workflow templates"
```

---

### Task 3.5: 独立审计员 Inspector / Veto 机制

**Files:**
- Create: `src/inspector.py`
- Modify: `src/phase_flow.py`（在阶段检查通过后、实际推进前调用 Inspector）
- Modify: `src/state_store.py`（写入 `audit_logs`，记录 veto 与审计结论）
- Modify: `src/bridge_cli.py`（增加 `inspect` / `audit-report` 子命令）
- Test: `tests/test_inspector.py`

- [ ] **Step 1: 新建 `src/inspector.py`，实现独立审计逻辑**

Inspector 不执行具体任务，只读取项目文档并判断当前阶段产出是否偏离目标。

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class AuditVerdict(str, Enum):
    PASS = "pass"
    VETO = "veto"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass
class AuditReport:
    phase: str
    verdict: AuditVerdict
    evidence_files: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    human_can_override: bool = True

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "verdict": self.verdict.value,
            "evidence_files": self.evidence_files,
            "findings": self.findings,
            "risks": self.risks,
            "suggestions": self.suggestions,
            "human_can_override": self.human_can_override,
        }


class Inspector:
    """Independent auditor that checks phase outputs against PRD, architecture,
    journey, and acceptance criteria. Can veto advance if inconsistencies found.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.docs_dir = project_dir / "docs"

    def _read_doc(self, name: str) -> str:
        path = self.docs_dir / name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def audit(self, phase: str, evidence: Optional[Dict] = None) -> AuditReport:
        """Audit the given phase before it is allowed to advance."""
        evidence = evidence or {}
        prd = self._read_doc("prd.md")
        architecture = self._read_doc("architecture.md")
        journey = self._read_doc("journey.md")
        acceptance = self._read_doc("acceptance.md")

        report = AuditReport(phase=phase)

        # Collect evidence files actually present
        for f in ["prd.md", "architecture.md", "journey.md", "acceptance.md"]:
            if (self.docs_dir / f).exists():
                report.evidence_files.append(f)

        # Example: plan phase must reference PRD goals
        if phase == "plan":
            plan_doc = self._read_doc("plan.md")
            if not plan_doc:
                report.verdict = AuditVerdict.VETO
                report.findings.append("plan.md 缺失，无法判断优化方案是否基于目标制定。")
            elif prd and "响应时间" in prd and "响应时间" not in plan_doc:
                report.verdict = AuditVerdict.VETO
                report.findings.append("PRD 明确要求优化响应时间，但 plan.md 未提及该目标。")

        # Example: execute phase must not violate architecture boundaries
        if phase == "execute":
            changed_summary = evidence.get("changed_files", [])
            if architecture and "外部 API 接口" in architecture:
                if any("api" in f.lower() for f in changed_summary):
                    report.risks.append("检测到 api 相关文件改动，请确认未修改外部接口契约。")

        # Default pass if no veto findings
        if report.verdict != AuditVerdict.VETO:
            report.verdict = AuditVerdict.PASS

        return report
```

- [ ] **Step 2: 修改 `src/phase_flow.py`，在 advance 前嵌入 Inspector**

```python
from inspector import Inspector, AuditVerdict

class PhaseFlow:
    def advance(self) -> bool:
        # 1. Existing phase check
        check_result = run_check(self.current_phase, self.project_name, self.base_dir)
        if not check_result["passed"]:
            raise PhaseBlockedError(check_result["reason"])

        # 2. Independent audit (NEW)
        inspector = Inspector(self.project_dir)
        report = inspector.audit(self.current_phase)
        self._store_audit_report(report)

        if report.verdict == AuditVerdict.VETO:
            raise PhaseBlockedError(
                f"Inspector veto on phase {self.current_phase}: {report.findings}"
            )

        # 3. Advance to next phase
        next_phase = Phase(self.current_phase).next(self.pipeline_mode)
        if next_phase is None:
            return False
        self._transition_to(next_phase)
        return True

    def _store_audit_report(self, report) -> None:
        from state_store import StateStore
        store = StateStore()
        store.log_audit(
            project=self.project_name,
            phase=report.phase,
            event="inspector_audit",
            details=report.to_dict(),
        )
```

- [ ] **Step 3: 修改 `src/state_store.py` 的 `log_audit`，支持结构化 audit 记录**

确保 `audit_logs` 表至少包含：`project_id`、`phase`、`event`、`details_json`、`created_at`。`log_audit` 方法已存在则补充 `details_json` 序列化。

- [ ] **Step 4: 在 `src/bridge_cli.py` 增加审计查看命令**

```python
def cmd_inspect(project_name: str, phase: str = "") -> dict:
    from inspector import Inspector

    base_dir = get_base_dir()
    project_dir = base_dir / project_name
    inspector = Inspector(project_dir)
    target_phase = phase or get_current_phase(project_name)
    report = inspector.audit(target_phase)
    return {"command": "inspect", "report": report.to_dict()}


def cmd_audit_report(project_name: str) -> dict:
    from state_store import StateStore
    store = StateStore()
    logs = store.list_audit_logs(project_name, event="inspector_audit", limit=50)
    return {"command": "audit-report", "logs": logs}
```

在 `build_parser()` 中新增子命令：

```python
inspect_parser = subparsers.add_parser("inspect", help="Run independent audit on current or given phase")
inspect_parser.add_argument("--project", required=True)
inspect_parser.add_argument("--phase", default="", help="Phase to audit (default: current)")

audit_report_parser = subparsers.add_parser("audit-report", help="Show inspector audit history")
audit_report_parser.add_argument("--project", required=True)
```

- [ ] **Step 5: 编写 Inspector 测试**

```python
def test_inspector_veto_when_plan_missing_target(tmp_path):
    from inspector import Inspector, AuditVerdict

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")
    (tmp_path / "docs" / "plan.md").write_text("我们将优化内存占用。", encoding="utf-8")

    inspector = Inspector(tmp_path)
    report = inspector.audit("plan")

    assert report.verdict == AuditVerdict.VETO
    assert any("响应时间" in f for f in report.findings)
```

Run: `pytest tests/test_inspector.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/inspector.py src/phase_flow.py src/state_store.py src/bridge_cli.py tests/test_inspector.py
git commit -m "feat: independent Inspector with veto power before phase advance"
```

---

### Task 4: 入口层统一与 Windows 本地运行脚本

**Files:**
- Modify: `src/pipeline.py`
- Modify: `src/bridge_cli.py`
- Modify: `src/main.py`
- Create: `scripts/start-windows.ps1`
- Create: `scripts/start-api.ps1`
- Create: `scripts/env.example.ps1`
- Test: `tests/test_pipeline.py`, `tests/test_bridge_cli.py`, `tests/test_main.py`

- [ ] **Step 1: 修改 `src/pipeline.py`，删除旧 3-state 兼容，help 从注册表生成**

移除 `Phase.next()` 旧逻辑调用，改用 `phase_model.Phase.next(pipeline_mode)`。子命令帮助中的 phase choices 从 `REGISTRY.list_phases()` 动态生成：

```python
parser.add_argument("--to", choices=REGISTRY.list_phases(), help="Target phase")
```

- [ ] **Step 2: 修改 `src/bridge_cli.py`，移除重复命令代理，统一调用核心 API**

`init/advance/status/resume/rollback/approve/mark-tests` 不再通过构造 `argparse.Namespace` 调用 `pipeline.py` 的命令函数，而是直接调用 `phase_flow.PhaseFlow` 与 `state_store.StateStore`：

```python
from phase_flow import PhaseFlow
from state_store import StateStore

def cmd_init(project_name: str, description: str, stack: str, force: bool) -> dict:
    store = StateStore()
    if not force and store.project_exists(project_name):
        return {"error": f"Project {project_name} already exists"}
    flow = PhaseFlow(project_name)
    flow.init(description=description, stack=stack)
    return {"project": project_name, "phase": flow.current_phase}
```

保留 `load/route/suggest/full/check-hermes/dispatch/debate` 作为 Bridge 特有命令。

- [ ] **Step 3: 修改 `src/main.py`，删除城策通残留 mock 端点，保留核心端点并改为真实查询**

**删除以下与 multi-agent-pipeline 核心无关的路由及对应 Pydantic 模型：**
- `/finance/*`（`FinancialInput`、`BudgetRequest` 等）
- `/knowledge/*`（`KnowledgeItem` 等）
- `/documents/*`（`DocumentRequest` 等）

**保留并改为真实查询的端点：**
- `/health`、`/status`
- `/agents`：返回 `REGISTRY.list_agents()`
- `/queue/stats`：返回 `Queue().stats_sync()`
- `/projects/{name}`：从 `StateStore` 读取项目状态
- `/projects/{name}/advance`：调用 `PhaseFlow.advance()`

示例：

```python
from fastapi import FastAPI
from registry import REGISTRY
from queue import Queue
from state_store import StateStore
from phase_flow import PhaseFlow

app = FastAPI()

@app.get("/agents")
def list_agents():
    return {"agents": REGISTRY.list_agents()}

@app.get("/queue/stats")
def queue_stats():
    return Queue().stats_sync()

@app.get("/projects/{name}")
def get_project(name: str):
    state = StateStore().load_project(name)
    return state.to_dict()

@app.post("/projects/{name}/advance")
def advance_project(name: str):
    flow = PhaseFlow(name)
    result = flow.advance()
    return {"success": result, "phase": flow.current_phase}
```

删除对应的 pydantic 模型：`FinancialInput`、`BudgetRequest`、`KnowledgeItem`、`DocumentRequest` 等共约 200 行。

- [ ] **Step 4: 创建 `scripts/env.example.ps1`**

```powershell
# Copy to scripts/env.ps1 and fill in your own paths
$env:MULTI_AGENT_PIPELINE_BASE_DIR = "D:\\pipeline-projects"
$env:AGENT_CLI_PATH_CLAUDE_CODE = "C:\\Users\\$env:USERNAME\\AppData\\Roaming\\npm\\claude.cmd"
$env:AGENT_CLI_PATH_CODEWHALE = "C:\\Users\\$env:USERNAME\\AppData\\Roaming\\npm\\codewhale-tui.exe"
$env:AGENT_CLI_PATH_QWEN_CODE = "C:\\Users\\$env:USERNAME\\AppData\\Roaming\\npm\\qwen.cmd"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:AGENT_MOCK = "true"   # set false for real agent calls
```

- [ ] **Step 5: 创建 `scripts/start-windows.ps1`**

```powershell
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\env.ps1"
Set-Location $root
python -m venv .venv
& .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$root\src"
python -c "from registry import REGISTRY; assert REGISTRY.is_ready(), 'Registry not ready'"
python src/pipeline.py --help
```

- [ ] **Step 6: 创建 `scripts/start-api.ps1`**

```powershell
$root = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\env.ps1"
Set-Location $root
& .venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$root\src"
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

- [ ] **Step 7: 运行入口层测试**

Run: `pytest tests/test_pipeline.py tests/test_bridge_cli.py tests/test_main.py -v`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add src/pipeline.py src/bridge_cli.py src/main.py scripts/ tests/test_main.py
git commit -m "feat: unified CLI/API entry points and Windows launch scripts"
```

---

### Task 5: 状态持久层与检查函数精简

**Files:**
- Modify: `src/state_store.py`
- Modify: `src/phase_checks.py`
- Create: `config/thresholds.yaml`
- Test: `tests/test_state_store.py`, `tests/test_phase_checks.py`

- [ ] **Step 1: 简化 `src/state_store.py` 索引与 schema**

保留 `projects`、`features`、`checkpoints`、`traces`、`audit_logs`、`model_health`、`approval_records`、`dispatch_history` 8 表，但移除 v1→v2 兼容代码；提供一次性迁移脚本 `scripts/migrate_v1_to_v2.py`，用于遗留项目。

为 `projects.name`、`features.project_id`、`checkpoints.project_id`、`dispatch_history.project_id`、`audit_logs.project_id` 增加索引。

- [ ] **Step 2: 创建 `config/thresholds.yaml`**

```yaml
# config/thresholds.yaml
checks:
  init:
    min_description_length: 10
  design:
    required_files: ["docs/design.md"]
  develop:
    min_source_files: 1
  test:
    min_test_files: 1
    required_pass_rate: 0.9
  accept:
    require_verified: true
  evaluate:
    llm_judge_min_score: 0.7
budget:
  warning_pct: 0.8
  hard_stop_pct: 0.95
```

- [ ] **Step 3: 修改 `src/phase_checks.py`，读取 `thresholds.yaml`**

新增加载函数：

```python
import yaml
from pathlib import Path

_THRESHOLDS: Optional[dict] = None

def load_thresholds() -> dict:
    global _THRESHOLDS
    if _THRESHOLDS is None:
        path = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"
        with path.open("r", encoding="utf-8") as f:
            _THRESHOLDS = yaml.safe_load(f)
    return _THRESHOLDS
```

`check_develop`、`check_test`、`check_evaluate` 等均使用 `load_thresholds()["checks"][...]` 替代硬编码数值。

- [ ] **Step 4: 运行状态与检查测试**

Run: `pytest tests/test_state_store.py tests/test_phase_checks.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add config/thresholds.yaml src/state_store.py src/phase_checks.py scripts/migrate_v1_to_v2.py
git commit -m "refactor: simplify state store and externalize thresholds"
```

---

### Task 6: Agent 适配器与 health check 固化

**Files:**
- Modify: `src/adapters.py`
- Modify: `src/pipeline_executor.py`
- Modify: `src/bridge_cli.py` 的 `check_endpoint_availability`
- Create: `tests/conftest.py` 官方 mock fixture
- Test: `tests/test_adapters.py`, `tests/test_pipeline_executor.py`

- [ ] **Step 1: 修改 `src/adapters.py` 增加 `version()` 与 `health()`**

```python
@dataclass
class AgentAdapter:
    name: str
    cli_path: str
    cli_command: str
    env_vars: Dict[str, str]

    def version(self, timeout: int = 10) -> Tuple[bool, str]:
        if not Path(self.cli_path).exists():
            return False, f"CLI not found: {self.cli_path}"
        try:
            result = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, **self.env_vars},
            )
            return result.returncode == 0, (result.stdout + result.stderr).strip()[:200]
        except Exception as e:
            return False, str(e)[:200]

    def run(self, task_type: str, payload: dict, work_dir: str, timeout: int = 600) -> AgentResult:
        """Agent 执行入口：CLI 调用 → 解析 → 容错。mock 仅短路 subprocess 调用。"""
        raw_stdout, raw_stderr, exit_code = self._execute_cli(
            task_type, payload, work_dir, timeout
        )
        # 解析层与容错层始终运行，mock 下也能被测试覆盖
        return self._parse_and_validate(raw_stdout, raw_stderr, exit_code)

    def _execute_cli(
        self, task_type: str, payload: dict, work_dir: str, timeout: int
    ) -> Tuple[str, str, int]:
        """执行 CLI 进程。mock 模式下返回模拟原始输出，不触发真实 Agent 调用。"""
        if os.environ.get("AGENT_MOCK", "false").lower() == "true":
            return self._mock_raw_output(task_type, payload), "", 0

        cmd = self._build_command(task_type, payload)
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **self.env_vars},
        )
        return result.stdout, result.stderr, result.returncode

    def _mock_raw_output(self, task_type: str, payload: dict) -> str:
        """返回模拟的原始 CLI 输出字符串（不含任何解析），保持与真实输出格式相似。"""
        return (
            f"[MOCK] {self.name} simulated output for task_type={task_type}\n"
            "Task completed successfully.\n"
            "Generated code/files as requested."
        )

    def _parse_and_validate(
        self, raw_stdout: str, raw_stderr: str, exit_code: int
    ) -> AgentResult:
        """解析层 + 容错层：从原始输出提取结构化结果，并处理失败/截断/超时痕迹。"""
        # 1. 解析（正则 + 启发式规则）—— 始终运行
        parsed = self._parser.extract(raw_stdout, raw_stderr)

        # 2. 容错（非零退出码 / stderr 异常 / 截断检测）—— 始终运行
        if exit_code != 0:
            return self._tolerance.handle_failure(raw_stderr, exit_code)
        if self._tolerance.is_truncated(raw_stdout):
            return AgentResult(
                success=False,
                error="Output appears truncated",
                output=parsed,
                status=AgentStatus.FAILED,
            )

        return AgentResult(
            success=True,
            output=parsed,
            status=AgentStatus.COMPLETED,
        )
```

**关键改动：**
- `run()` 不再在入口处判断 `AGENT_MOCK`，而是统一调用 `_execute_cli()` + `_parse_and_validate()`。
- `_execute_cli()` 是唯一能短路到 mock 的位置，且只返回原始字符串/退出码，不返回 `AgentResult`。
- `_parse_and_validate()` 中的解析层（`self._parser.extract`）和容错层（`self._tolerance.handle_failure` / `is_truncated`）在 mock 模式下同样被调用，确保测试覆盖。

- [ ] **Step 2: 修改 `src/pipeline_executor.py`，集成 `AgentAdapter`**

`dispatch_and_wait` 不再直接 `subprocess.run`，而是通过 `AgentAdapter(name=adapter, ...).run(...)`，失败时走 fallback（队列重新入队 / 下一 agent）。

- [ ] **Step 3: 简化 `src/bridge_cli.py` 的 health check**

`check_endpoint_availability` 直接复用 `AgentAdapter.version()`：

```python
from adapters import AgentAdapter

def check_endpoint_availability(adapter_name: str) -> dict:
    agent_def = REGISTRY.get_agent(adapter_name)
    if not agent_def:
        return {"ok": False, "error": f"Unknown agent {adapter_name}"}
    adapter = AgentAdapter(
        name=agent_def.name,
        cli_path=agent_def.cli_path,
        cli_command=agent_def.cli_command,
        env_vars=agent_def.env_vars,
    )
    ok, msg = adapter.version()
    return {"ok": ok, "version": msg, "cli_path": agent_def.cli_path}
```

- [ ] **Step 4: 固化 `tests/conftest.py` 的 mock fixture**

```python
import pytest

@pytest.fixture(scope="session", autouse=True)
def set_mock_mode():
    import os
    os.environ.setdefault("AGENT_MOCK", "true")

@pytest.fixture
def fresh_queue(tmp_path):
    from queue import Queue
    db = tmp_path / "queue.db"
    return Queue(db_path=str(db))
```

- [ ] **Step 5: 运行适配器与执行器测试**

Run: `pytest tests/test_adapters.py tests/test_pipeline_executor.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/adapters.py src/pipeline_executor.py src/bridge_cli.py tests/conftest.py tests/test_adapters.py
git commit -m "refactor: agent adapter with health check and mock fixture"
```

---

### Task 7: 文档与 Windows 部署手册重写

**Files:**
- Rewrite: `README.md`
- Rewrite: `progress.md`
- Rewrite: `DEPLOY.md`
- Rewrite: `AGENTS.md`
- Create: `docs/superpowers/runbooks/windows-setup.md`
- Create: `docs/superpowers/runbooks/agent-setup.md`
- Delete: `docker-compose.yml`

- [ ] **Step 1: 重写 `README.md`**

内容要点：
- 项目定位：纯 Windows 本地运行的 multi-agent 编排框架。
- 快速开始：复制 `scripts/env.example.ps1` → `scripts/env.ps1`，执行 `scripts/start-windows.ps1`。
- 核心命令：`python src/pipeline.py init my-project --description "..."` → `advance` → `status`。
- 架构图：指向 `docs/architecture_v2.md` 或本节目标架构。
- 测试：`pytest tests/ -q`（给出实际测试命令，不写具体数量）。

- [ ] **Step 2: 重写 `progress.md`**

删除虚假完成度。改为：

```markdown
# Progress

## Current Phase
架构清理与注册表驱动重构（进行中）。

## Completed
- 统一注册表 `registry.py`
- SQLite 状态持久化
- 基础 phase 检查框架

## In Progress
- 队列合并
- CLI/API 入口统一
- 文档重写

## Not Started
- 真实 agent 端到端验证
- 性能基准
```

- [ ] **Step 3: 重写 `DEPLOY.md` 为 Windows-only**

仅包含：
1. 环境要求：Windows 10/11、Python 3.11、PowerShell 7+、Git。
2. 安装：clone → `python -m venv .venv` → `pip install -r requirements.txt`。
3. Agent CLI 安装：claude.cmd / qwen.cmd / codewhale-tui.exe 安装指引。
4. 启动：`. scripts/start-windows.ps1` 与 `. scripts/start-api.ps1`。
5. 健康检查：`python src/bridge_cli.py check-hermes --task-type code`。

- [ ] **Step 4: 删除 `docker-compose.yml`**

```bash
git rm docker-compose.yml
```

- [ ] **Step 5: Commit**

```bash
git add README.md progress.md DEPLOY.md AGENTS.md docs/superpowers/runbooks/
git commit -m "docs: rewrite docs for Windows-only deployment and current architecture"
```

---

### Task 8: 全局测试、可观测性与 9 维度验收

**Files:**
- Modify: `tests/...`（补测试）
- Modify: `src/observability.py`（在现有 Dashboard + AlertManager 基础上新增 `trace()` 与 JSON 结构化日志，不删除现有功能）
- Create: `tests/integration/test_full_flow.py`
- Create: `scripts/run-checks.ps1`

- [ ] **Step 1: 增强现有 `src/observability.py`，添加结构化日志与 trace**

**注意：** 现有 `src/observability.py` 已包含 Dashboard + AlertManager + Markdown 报告（约 737 行），本次仅新增 `trace()` 函数与 JSON 日志器，不改动现有功能。

```python
import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict

_pipeline_logger = logging.getLogger("pipeline")


def trace(event: str, project: str, details: Dict[str, Any]) -> None:
    """Emit a structured JSON trace record for key pipeline events."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "project": project,
        "details": details,
    }
    _pipeline_logger.info(json.dumps(record, ensure_ascii=False, default=str))
```

在 `phase_flow.advance()`、`pipeline_executor.dispatch_and_wait()`、`queue.push_sync()` 关键路径调用 `trace(...)`。

- [ ] **Step 2: 编写集成测试 `tests/integration/test_full_flow.py`**

```python
def test_full_flow_init_advance(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
    from phase_flow import PhaseFlow
    from state_store import StateStore

    flow = PhaseFlow("demo")
    flow.init(description="demo project", stack="python")
    assert flow.current_phase == "init"

    flow.advance()
    assert flow.current_phase == "prd"

    store = StateStore()
    state = store.load_project("demo")
    assert state.phase.name == "prd"
```

- [ ] **Step 3: 创建 `scripts/run-checks.ps1` 一键质量门禁**

```powershell
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$root\src"
pytest tests/ -q --tb=short
python -m ruff check src tests
python -m mypy src --ignore-missing-imports
python src/bridge_cli.py check-hermes --task-type code
```

- [ ] **Step 4: 运行全量测试并修复回归**

Run: `pytest tests/ -q --tb=short`
Expected: 全量通过（目标：所有既有测试不回归，新增测试覆盖重构点）。

- [ ] **Step 5: Commit**

```bash
git add src/observability.py tests/integration/test_full_flow.py scripts/run-checks.ps1
git commit -m "feat: observability trace, integration tests, and Windows quality gate"
```

---

## 6. 验收标准（最终门禁）

- [ ] `pytest tests/ -q` 全部通过，无新增警告。
- [ ] `python src/pipeline.py --help` 显示的 phase choices 来自 `REGISTRY.list_phases()`。
- [ ] `python src/bridge_cli.py check-hermes --task-type code` 在无真实 agent 环境下因 `AGENT_MOCK=true` 或通过配置正常返回。
- [ ] `python -c "from registry import REGISTRY; print(REGISTRY.list_phases())"` 输出 19 个 phase 且包含 greenfield 与 brownfield。
- [ ] `python src/main.py` 启动后 `/health` 与 `/agents` 返回真实数据，无 mock；`/finance/*`、`/knowledge/*`、`/documents/*` 路由已删除。
- [ ] `python src/bridge_cli.py inspect --project chengcetong2` 能输出审计报告；当阶段产出偏离 PRD 时，`advance` 被 veto 阻止。
- [ ] 在全新 Windows 机器上按 `docs/superpowers/runbooks/windows-setup.md` 可在 30 分钟内跑通 `init → advance → status`。
- [ ] `docker-compose.yml`、`message_queue.py`、`task_queue.py`、`workflow_registry.py`、`workflow_template.py` 已物理删除。
- [ ] 现有 `src/observability.py` 的 Dashboard/AlertManager/Markdown 报告功能未被覆盖。

---

## 7. 9 质量维度验收清单

| 维度 | 验收动作 |
|------|---------|
| 正确性 | 集成测试 `test_full_flow_init_advance` 通过；Phase 模型与注册表一致 |
| 可靠性 | `Queue.recover_orphaned()` 测试通过；`AgentAdapter.version()` 健康检查可用 |
| 安全性 | 无硬编码 API key；`thresholds.yaml` 与 agent 路径外置 |
| 性能 | `pytest-benchmark` 或简单 `time` 显示 `pull_sync` 在 10ms 内 |
| 可维护性 | `flake8`/`ruff` 无重复导入；注册表驱动新增 phase 只需改一处 |
| 可用性 | Windows 启动脚本与 `--help` 完整可用 |
| 可测试性 | `AGENT_MOCK=true` 下所有测试不依赖外部 CLI |
| 可扩展性 | 新增 agent 只需 `registry.py` + env 路径 |
| 可观测性 | `logs/pipeline.log` 包含 JSON trace 记录；现有 Dashboard/AlertManager 不被覆盖 |
| 治理/一致性 | Inspector 审计报告覆盖每个阶段转换；veto 能有效阻止方向偏离 |
| 实用性 | 新用户按 Windows 手册 30 分钟跑通；brownfield 使用单一 7-phase 流程；debate 模块保持现状 |

---

## 8. Self-Review

**1. Spec coverage:** 用户 5 大约束已全部映射到任务：
- Windows-only / 无 Docker：Task 4、Task 7。
- 统筹修复非 patchwork：先注册表，再队列，再编排，再入口，再持久化，再适配器，再文档，最后验收。
- 9 维度 + 实用性：Task 1/3/3.5/5/6/8 覆盖，表格在第 7 节明确。
- 架构轻量化：Task 1/2/3 合并 4 个文件、删除 5 个文件、统一真相源；新增 `inspector.py` 作为治理层。
- 模块集成：每个 Task 都给出集成测试与 commit，Task 8 全量验收。

**2. Placeholder scan:** 无 “TBD/TODO/待实现”；所有代码块给出具体实现；命令给出具体路径。

**3. Type consistency:**
- `Phase` 类替代 enum 后在所有任务中统一使用 `phase.name`（字符串）与 `Phase.from_name()`；新增 `is_init()`/`is_start()`/`is_terminal()` 兼容旧习惯用法。
- `Queue` 类替代 `MessageQueue`/`TaskQueue` 后统一使用 `Queue(db_path=...)`、`push_sync`/`pull_sync`/`complete_sync`/`fail_sync`；DDL 不再拼接 task_type 字符串，依赖 `Registry` 注册时正则校验。
- brownfield workflow 统一为现有 7-phase，避免与注册表 phase 名冲突；debate 子系统本次不涉及。
- `AGENT_MOCK` 仅短路真实 subprocess，解析层/容错层保持可测。
- `AgentAdapter` 在 Task 6 定义，Task 4 health check 复用。
- `Inspector` 在 Task 3.5 定义，`phase_flow.py` 在 advance 前调用，`bridge_cli.py` 提供 `inspect` / `audit-report` 查询。

---

## 9. Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-02-multi-agent-pipeline-repair-plan.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for this multi-file refactor because each task is self-contained and needs focused implementation + test.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints. Faster but requires you to review larger diffs at once.

Which approach would you like?
