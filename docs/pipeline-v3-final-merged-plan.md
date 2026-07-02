# multi-agent-pipeline v3.0 — 最终合并重构方案

> 来源：Hermes 代码审查（40 项）+ 全网调研（SWE-bench/Aider/OpenHands/Microsoft LLM-as-Judge/Antigravity/SDD）+ Claude Code 审计（5 P0 + 6 P1 + 6 P2 + 4 目标差距分析）

---

## 一、完整缺陷清单（三方合并）

### P0 — 阻塞生产可用（共 10 项）

| # | 来源 | 文件:行 | 问题 |
|---|------|--------|------|
| 1 | Hermes | pipeline.py:228 | os.system 命令注入 |
| 2 | Hermes | worktree.py:79 | feature 名注入 git 命令 |
| 3 | Hermes | phase_checks.py | 路径遍历 |
| 4 | Hermes | system_constraint.py | SHA-256 做密码哈希 |
| 5 | Hermes | approval.py:163 | 审批记录纯内存，重启丢失 |
| 6 | Hermes | prompt_cache_store.py | access_count 重置为 0 |
| 7 | **Claude** | **adapters.py:935/1065/1228** | **三个 Adapter 全部返回 mock，核心流程不可运行** |
| 8 | **Claude** | **adapters.py:1469** | **Windows rename() 在目标已存在时崩溃（WinError 183）** |
| 9 | **Claude** | **adapters.py:636** | **伪超时：超时只在 callable 返回后检查，无法打断阻塞调用** |
| 10 | **Claude** | **config 分散** | **DB_FILENAME / PHASE_ORDER / 超时阈值 / 模型名分散在 10+ 模块硬编码** |

### P1 — 高优先级（共 20 项）

合并自 Hermes（14 项 P1）+ Claude Code（6 项 P1）：
- 无持久化异步任务队列（绕过 delegate_task 600s 限制）
- MCP 通道是空 stub
- 无 src/delivery.py 交付层
- 无 GitHub 双向同步
- 无角色能力矩阵
- BlanketApproval 与 v2 设计不符
- ...以及 Hermes 代码审查中的 14 项

### P2 — 体验与维护（共 25 项）

合并自 Hermes（19 项 P2）+ Claude Code（6 项 P2）

---

## 二、四个目标的真实差距与修正方案

Claude Code 提出的四个目标分析是本次合并最有价值的部分：

### 目标 1：定制化图编排 —— 原方案覆盖 60%，需补充 40%

**缺失**：任务类型→图模板注册机制、条件边引擎、效率基线验证

**新增模块**：
- `src/workflow_registry.py` —— 任务类型到图模板的注册
- `src/workflow_template.py` —— GraphTemplate 数据类
- `src/condition_engine.py` —— 条件边求值（基于 feature 元数据、代码量、测试结果）

**DB 新增**：`workflow_templates` 表

**关键改动**：
- pipeline.py init 时根据任务类型选择模板
- phase_flow.py 从固定 PHASE_ORDER 改为 GraphExecutor
- suggestion_engine.py 根据图模板给出不同建议

### 目标 2：不用 delegate_task + CLI 控制 + 并行 + 无超时 —— 原方案覆盖 50%，需补充 50%（最大的差距）

**这是四个目标中差距最大的**。需要从根本上改变执行架构：

```
当前架构（同步调用）：
  Hermes → delegate_task → Agent 子进程 → 600s 超时/50 次迭代硬限制

目标架构（异步 Agent Daemon）：
  Hermes Orchestrator（长期运行）
    ↓ dispatch task via SQLite MQ / MCP
  Agent Worker Pool
    ├── claude-agent --task-id <id>
    ├── codewhale-agent --task-id <id>
    └── qwen-agent --task-id <id>
    每个 Agent 独立进程/daemon
    子任务有自己的 checkpoint
    超时只影响当前子任务，不影响整体
```

**新增核心模块**（均提升到 P0）：
- `src/agent_daemon.py` —— Agent 守护进程入口
- `src/worker_pool.py` —— 进程池管理
- `src/subtask_chunker.py` —— 长任务切片 + checkpoint
- `src/message_queue.py` —— SQLite-based async MQ（替代 delegate_task）
- `src/checkpointer.py` —— 统一 checkpoint 写入/恢复

**关键设计**：
- 整体任务无全局 timeout，只有子任务 timeout
- 子任务结果写入 checkpoint，失败自动重试
- Hermes 每轮只决定"下一步调用哪个 Agent daemon 的哪个子任务"

### 目标 3：真实 Playwright E2E —— 原方案覆盖 80%，需补充 20%

**缺失**：Playwright 依赖管理、E2E 与沙箱 Profile 冲突、测试环境隔离

**新增/修改**：
- `src/e2e_framework.py`：将 mock PlaywrightDriver 替换为真实 playwright.sync_api
- `src/e2e_server_fixture.py`：启动/停止被测服务的 fixture
- `src/sandbox.py`：增加 E2E_PROFILE，放行浏览器进程和网络白名单域名

### 目标 4：长任务持久化运行 —— 目标修正为"持久化可续跑"

"一次性跑通"是不切实际的。真正目标是：**任何时刻崩溃都能续跑**。

**新增模块**：
- `src/watchdog.py` —— 定时快照
- `src/budget_guard.py` —— 预算监控与熔断
- `src/checkpointer.py` —— 子任务级 checkpoint（与目标 2 共用）

**关键设计**：
- 子任务级 checkpoint（每完成一个子任务就写）
- resume 时读取最后一个成功子任务，跳过已完成部分
- 全局 watchdog：定时保存状态快照
- 预算熔断：per-feature token 上限、per-project USD 上限
- 人工接管流程：当所有 Agent 不可用时，生成当前状态摘要并暂停等待用户

---

## 三、最终 Phase 设计：10 个 Phase + 图编排

```
Phase 0: INIT          ─ 项目骨架 + 选择工作流模板
Phase 1: RESEARCH      ─ 三级知识图谱构建（全网调研）
Phase 2: DESIGN        ─ 知识驱动的架构设计 + 3 轮对抗审查
Phase 3: JOURNEY       ─ 用户旅程设计 + 对话脚本
Phase 4: DECOMPOSE     ─ 任务分解（DAG 调度器）
Phase 5: DEVELOP       ─ Feature 编码（Agent Daemon 并行）+
                          Auto-Fix Loop + Repo Map
Phase 6: INTEGRATE     ─ 跨模块集成 + Spec Conformance Check
Phase 7: TEST          ─ 全量回归（真实 Playwright E2E）
Phase 8: EVALUATE      ─ LLM-as-Judge + Evidence-First
Phase 9: ACCEPT        ─ Inspector 审查 + 人类审批
Phase 10: DEPLOY       ─ 交付
```

### 图编排支持

不在 Phase 10 个中硬编码，而是在 workflow_templates 表中定义：

```yaml
# 轻量工具模板（4 阶段）
lightweight_tool:
  phases: [INIT, RESEARCH, DEVELOP, EVALUATE]
  skip: [DESIGN, JOURNEY, DECOMPOSE, INTEGRATE, TEST, ACCEPT, DEPLOY]
  condition: task_complexity == "simple"

# 企业级应用模板（10 阶段 + 多级审批）  
enterprise_app:
  phases: [全部 10 个]
  conditions:
    - code_lines > 500 → 触发 CodeWhale 深度审查
    - test_failures > 3 → 自动插入修复循环
    - budget_consumed > 80% → 暂停并通知用户
```

---

## 四、完整新增文件清单（合并后）

### 核心架构（P0）

| 文件 | 用途 |
|------|------|
| `src/agent_daemon.py` | Agent 守护进程入口（替代 delegate_task） |
| `src/worker_pool.py` | 进程池管理 |
| `src/subtask_chunker.py` | 长任务切片 + 子任务 checkpoint |
| `src/message_queue.py` | SQLite-based 异步消息队列 |
| `src/checkpointer.py` | 统一 checkpoint 写入/恢复/续跑 |
| `src/gate.py` | 4 层门禁引擎（post-gen / commit / push / CI） |

### 流程引擎（P1）

| 文件 | 用途 |
|------|------|
| `src/workflow_registry.py` | 任务类型 → 图模板注册 |
| `src/workflow_template.py` | GraphTemplate 数据类 |
| `src/condition_engine.py` | 条件边求值引擎 |
| `src/task_queue.py` | 持久化异步任务队列 |
| `src/task_decomposer.py` | LLM + 模板自动生成 features.json |
| `src/repo_map.py` | Aider 式 Repo Map 生成器 |

### 知识驱动设计

| 文件 | 用途 |
|------|------|
| `src/knowledge_graph.py` | 四层知识图谱数据结构 |
| `src/research_agent.py` | 并行研究 Agent 调度器 |
| `src/adversarial_review.py` | 多轮对抗讨论引擎（3 轮收敛） |

### 质量保障

| 文件 | 用途 |
|------|------|
| `src/evaluate.py` | LLM-as-Judge 评估引擎（Evidence-First） |
| `src/inspector.py` | Inspector 审查逻辑（全局记忆 + 用户视角） |
| `src/e2e_server_fixture.py` | Playwright E2E 服务 fixture |
| `rubrics/evaluate.yaml` | 五维评估 Rubric |

### 运维与可观测性

| 文件 | 用途 |
|------|------|
| `src/audit_trail.py` | Event Audit Trail（Agent 全量操作记录） |
| `src/watchdog.py` | 定时状态快照 |
| `src/budget_guard.py` | 预算监控与熔断 |
| `src/delivery.py` | 交付层（setup/start/verify） |
| `src/github_sync.py` | GitHub 双向同步 |

### Hooks

| 文件 | 用途 |
|------|------|
| `hooks/pre-commit` | git pre-commit hook（gate.py） |
| `hooks/pre-push` | git pre-push hook（gate.py） |
| `hooks/post-generate` | AI 生成后即时检查 hook |

---

## 五、执行架构对比

```
v2.0 执行模型（同步，有硬限制）：
  Hermes → delegate_task(task) → Agent 子进程
    → 600s 超时（硬编码）
    → 50 次迭代限制（硬编码）
    → 超时 = 任务失败

v3.0 执行模型（异步，可续跑）：
  Hermes Orchestrator（长期运行）
    ├── 读取 task_queue
    ├── 调用 GraphExecutor
    └── dispatch sub-task → message_queue
  
  Agent Worker Pool（独立进程）
    ├── claude-agent --task-id <id>
    ├── codewhale-agent --task-id <id>
    └── qwen-agent --task-id <id>
  
  每个子任务：
    → 独立 timeout（短，可配置）
    → 完成 → 写入 checkpoint
    → 失败/超时 → 自动重试（最多 N 次）
    → N 次后仍失败 → 进入 dead_letter 队列 → 通知用户
  
  整体任务：
    → 无全局 timeout
    → 通过 checkpoint 续跑
    → 预算熔断保护
```

---

## 六、实施路线图（修正后）

### Phase 1：止血 + 架构原型验证（P0，2-3 周）

1. 修复 pipeline.py:228 命令注入
2. 修复 adapters.py:1469 Windows rename() 崩溃
3. 修复 adapters.py:636 伪超时（concurrent.futures 真实超时）
4. 三个 Adapter 真实 CLI 调用（增加 MOCK 开关）
5. approval.py 全量持久化到 SQLite
6. 统一使用 config.py，消除硬编码
7. **架构原型验证**：选一个中等复杂度任务，用 "Agent daemon + SQLite MQ + 子任务 checkpoint + 真实 Playwright" 跑通一次

### Phase 2：核心架构重建（P1，3-4 周）

1. agent_daemon.py + worker_pool.py + message_queue.py（替代 delegate_task）
2. subtask_chunker.py + checkpointer.py（子任务切片 + 续跑）
3. task_queue.py 持久化任务队列
4. phase_flow.py 从线性改为 GraphExecutor
5. workflow_registry.py + workflow_template.py + condition_engine.py
6. gate.py + git hooks（post-generate / pre-commit / pre-push）
7. 新增 10 个 Phase check 函数

### Phase 3：知识驱动 + 质量保障（P2，2-3 周）

1. knowledge_graph.py + research_agent.py + adversarial_review.py
2. evaluate.py + inspector.py + evidence-first Judge pipeline
3. repo_map.py + audit_trail.py
4. e2e_framework.py 真实 Playwright 替换
5. delivery.py + github_sync.py
6. budget_guard.py + watchdog.py
7. WebSocket 实时仪表盘 + OpenTelemetry

---

## 七、四个目标 → 最终落地映射

| 目标 | 落地状态 | 核心机制 |
|------|---------|---------|
| 1. 定制化图编排 | ✅ 完整方案 | workflow_registry + condition_engine + GraphExecutor |
| 2. 不用 delegate_task + 并行 + 无超时 | ✅ 完整方案 | agent_daemon + worker_pool + subtask_chunker + message_queue + checkpointer |
| 3. 真实 Playwright E2E | ✅ 完整方案 | 替换 mock driver + e2e_server_fixture + E2E_PROFILE |
| 4. 长任务持久化可续跑 | ✅ 完整方案 | 子任务级 checkpoint + budget_guard + watchdog |

---

## 文档索引

所有文档在 `C:/tmp/multi-agent-pipeline/docs/`：
- `pipeline-v3-complete-refactoring-plan.md` — 主方案
- `code-review-v2.md` — Hermes 代码审查
- `research-report.md` — 全网调研
- 本文件 — 最终合并方案
