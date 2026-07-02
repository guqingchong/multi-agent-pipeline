# Multi-Agent Pipeline

> 纯 Windows 本地运行的轻量 multi-agent 编排框架。
> 以注册表为单一真相源，通过 SQLite 持久化状态，用统一队列派发真实 CLI Agent。

---

## 项目定位

Multi-Agent Pipeline 是一个面向 Windows 开发者的本地 agent 编排框架。它不依赖 Docker，不依赖远程服务，核心由 Python 3.11+ 编写，通过调用本地安装的 CLI Agent（Claude Code / Qwen Code / CodeWhale）完成代码、审查、测试、文档等任务。

默认启用 `AGENT_MOCK=true`，因此即使尚未安装任何真实 Agent，也可以安全地运行测试和体验完整流程。

---

## 快速开始

### 1. 准备环境

- Windows 10/11
- Python 3.11+
- PowerShell 7+
- Git

### 2. 克隆项目并配置环境

```powershell
git clone <repository-url> C:\path\to\multi-agent-pipeline
Set-Location C:\path\to\multi-agent-pipeline
Copy-Item scripts\env.example.ps1 scripts\env.ps1
```

按需编辑 `scripts\env.ps1`，至少确认：

```powershell
$env:MULTI_AGENT_PIPELINE_BASE_DIR = "C:\path\to\multi-agent-pipeline"
$env:AGENT_MOCK = "true"
```

### 3. 一键启动

```powershell
. .\scripts\start-windows.ps1
```

该脚本会自动创建 `.venv`、安装依赖、检查注册表就绪并打印 CLI 帮助。

### 4. 跑通第一个项目

```powershell
. .\scripts\env.ps1
python src\pipeline.py init my-project --description "一个示例项目" --stack python
python src\pipeline.py advance my-project
python src\pipeline.py status my-project
```

---

## 核心命令

### `pipeline.py` — 状态机主入口

| 命令 | 说明 |
|------|------|
| `init <project> --description "..." --stack python` | 初始化项目骨架 |
| `check <project>` | 检查当前 phase 是否满足推进条件 |
| `advance <project>` | 推进到下一 phase（自动检查，未通过则阻塞） |
| `status <project>` | 查看项目当前状态 |
| `resume <project> [--checkpoint-id N]` | 从 checkpoint 恢复 |
| `rollback <project> --checkpoint-id N` | 回退到指定 checkpoint |
| `rollback-phase <project> --to <phase> --approved` | 回退到指定 phase |
| `approve <project> --phase design\|accept` | 人工审批 |
| `mark-tests <project> --passed\|--failed` | 标记端到端测试状态 |

### `bridge_cli.py` — Hermes / 外部 Agent 桥

| 命令 | 说明 |
|------|------|
| `load --project <project>` | 加载项目状态与仪表盘 |
| `route --task-type <type> [--feature-id <id>]` | 任务路由到目标 Agent |
| `suggest --project <project>` | 生成下一步建议 |
| `full --project <project>` | load + suggest |
| `check-hermes --task-type <type>` | 检查 Hermes 是否有权执行某类任务 |
| `dispatch --adapter <agent> --task-type <type> --prompt "..."` | 派发任务到真实 Agent |
| `inspect --project <project> [--phase <phase>]` | 独立审计当前或指定 phase |
| `audit-report --project <project>` | 查看审计历史 |

### FastAPI 服务（可选）

```powershell
. .\scripts\start-api.ps1
```

启动后访问：

- `http://127.0.0.1:8000/health` — 健康检查
- `http://127.0.0.1:8000/agents` — 已注册 Agent 列表
- `http://127.0.0.1:8000/queue/stats` — 队列统计
- `http://127.0.0.1:8000/projects/{name}` — 项目状态
- `POST http://127.0.0.1:8000/projects/{name}/advance` — 推进项目

---

## 架构

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
│  inspector.py       — 独立审计员，持 PRD/架构/旅程，可 veto     │
├─────────────────────────────────────────────────────────────────┤
│  Dispatch & Queue Layer                                          │
│  pipeline_queue.py  — 统一 SQLite 任务队列（sync + async API）    │
│  adapters.py        — Agent 适配器与 AgentResult                 │
│  pipeline_executor.py — 任务派发与等待                           │
│  event_engine.py    — 基于 queue 的事件链                         │
├─────────────────────────────────────────────────────────────────┤
│  Persistence Layer                                               │
│  state_store.py     — 项目/特性/检查点/审计/调度历史持久化        │
│  registry.py        — Agent/Phase/TaskType 单一注册表             │
│  config.py          — Pydantic-settings 环境配置                  │
└─────────────────────────────────────────────────────────────────┘
```

更详细的设计背景与历史文档见 `docs/architecture_v2.md`。

---

## 运行测试

默认在 `AGENT_MOCK=true` 下运行，不依赖任何真实 Agent CLI：

```powershell
. .\scripts\env.ps1
pytest tests/ -q
```

---

## 文档索引

| 文档 | 内容 |
|------|------|
| `DEPLOY.md` | Windows 部署总览 |
| `AGENTS.md` | Agent 分工与配置 |
| `docs/superpowers/runbooks/windows-setup.md` | 逐步 Windows 部署手册 |
| `docs/superpowers/runbooks/agent-setup.md` | Agent CLI 安装手册 |
| `docs/architecture_v2.md` | 历史架构设计文档 |
| `progress.md` | 项目当前进度 |

---

## 重要约定

- **Windows only**：本项目不支持 Docker，也不支持 Linux/macOS 部署脚本。
- **AGENT_MOCK=true 为默认**：安全本地测试，不触发真实 Agent 调用。
- **无硬编码路径**：Agent CLI 路径通过 `AGENT_CLI_PATH_*` 环境变量或 `scripts/env.ps1` 配置。
- **注册表驱动**：所有 phase、agent、task_type 均来自 `src/registry.py`。

