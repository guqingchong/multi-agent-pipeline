# 多 Agent Pipeline — 三层架构设计文档（PRD）

> 版本: v1.0
> 日期: 2026-06-19
> 项目: multi-agent-pipeline
> 作者: Hermes-Research
> 状态: 设计完成，待评审

---

## 1. 背景与问题

### 1.1 现状

当前系统已交付 17 个 src 模块、773 个测试、22 个 features，能运行但不会自动协作。每次对话需要用户手动：
- 告诉 Hermes 当前项目路径
- 告诉 Hermes 当前做到哪个 Phase
- 告诉 Hermes 想做什么（"继续"、"查看状态"、"推进到 test"）
- 等待 Hermes 理解后再手动分派任务

### 1.2 核心问题

| 问题 | 影响 | 根因 |
|------|------|------|
| 每次对话从零开始 | 用户重复输入上下文 | 没有入口层自动加载 |
| Hermes 可能越界编码 | 违反 SOUL.md 角色约束 | 没有约束层拦截 |
| Phase 推进靠用户指令 | 协作不自动 | 没有调度层编排 |
| Agent 超时无人处理 | 任务挂死 | 没有超时自动恢复 |
| 用户意图识别靠猜 | 误操作多 | 没有意图解析器 |

### 1.3 设计目标

构建**入口层 + 约束层 + 调度层**三层架构，让系统：
1. **用户开口即协作** — 无需手动同步状态
2. **违规即时拦截** — 硬约束不可绕过
3. **Phase 自动推进** — 检查通过即前进
4. **超时自动处理** — 不挂死、不丢状态

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户（聊天窗口）                          │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  入口层 (Entry Layer)                                         │
│  ├── SessionLoader    — 自动加载项目状态                       │
│  ├── IntentParser     — 自动识别用户意图                       │
│  ├── ContextBuilder   — 构建本轮对话上下文                     │
│  └── EntryGate        — 入口路由分发                          │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  约束层 (Constraint Layer)                                    │
│  ├── RoleGuard        — 角色越界拦截                         │
│  ├── ActionFilter     — 动作白名单过滤                       │
│  ├── GoalValidator    — 目标对齐验证                         │
│  ├── SafetyEnforcer   — 安全策略执行                         │
│  └── ViolationLogger  — 违规记录与上报                       │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  调度层 (Orchestration Layer)                                 │
│  ├── PhaseEngine      — Phase 自动推进引擎                   │
│  ├── AgentDispatcher  — Agent 自动委派器                     │
│  ├── TimeoutHandler   — 超时自动处理                         │
│  ├── RecoveryManager  — 故障自动恢复                         │
│  └── CheckpointSync   — 状态同步与持久化                     │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  现有 17 个 src 模块（pipeline / adapters / approval / ...） │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 入口层（Entry Layer）

### 3.1 设计原则

- **零配置启动**：用户无需手动输入项目路径或当前状态
- **一次对话即状态**：每次用户开口，系统自动完成状态恢复
- **意图即指令**：自然语言直接映射到系统动作

### 3.2 组件设计

#### 3.2.1 SessionLoader — 会话状态加载器

**职责**：每次对话开始时，自动从持久化存储加载项目完整状态。

**输入**：无（自动触发）
**输出**：`SessionContext` 对象

**加载顺序**：
1. 检测当前工作目录是否有项目标记（`.hermes-project` 或 `pipeline_state.db`）
2. 读取 `pipeline_state.db` → 当前 Phase、features 状态、审批记录
3. 读取 `progress.md` → 人类可读进度摘要
4. 读取 `features.json` → 22 个 features 的当前状态
5. 读取 `SOUL.md` / `AGENTS.md` → 角色定义与协作规则
6. 读取最近 checkpoint → 可恢复状态
7. 构建 `SessionContext` 并注入到本轮对话系统提示

**数据模型**：
```python
@dataclass
class SessionContext:
    project_name: str                    # "multi-agent-pipeline"
    project_path: Path                   # C:\tmp\multi-agent-pipeline
    current_phase: str                   # "develop"
    current_wave: int                  # 5
    active_features: List[str]           # ["F012", "F013"]
    completed_features: List[str]        # ["F001", ...]
    pending_approvals: List[str]         # 等待审批的ID
    last_agent_action: str               # "Claude Code 编码 F012"
    last_checkpoint_id: int              # 42
    session_start_time: datetime
    context_summary: str                 # 200字摘要，用于系统提示
```

**与现有代码整合**：
- 复用 `state_store.StateStore` 读取 projects / checkpoints / traces 表
- 复用 `phase_flow.PhaseFlow.current_phase()` 获取当前 Phase
- 复用 `config_loader` 读取项目配置

**新增文件**：`src/entry/session_loader.py`

---

#### 3.2.2 IntentParser — 用户意图解析器

**职责**：将用户自然语言输入解析为结构化意图，映射到系统动作。

**输入**：用户原始文本
**输出**：`UserIntent` 对象

**意图分类**：

| 意图类型 | 示例输入 | 映射动作 | 置信度阈值 |
|----------|----------|----------|-----------|
| CONTINUE | "继续" / "go on" / "下一步" | phase_advance() | 0.9 |
| STATUS | "查看状态" / "进度如何" | 返回 SessionContext 摘要 | 0.8 |
| CODE_REVIEW | "查看代码" / "看看 F012 的代码" | 读取对应文件并展示 | 0.8 |
| ROLLBACK | "回退到 design" / "退回" | phase_rollback() | 0.85 |
| MODIFY_REQ | "修改需求：增加..." | 更新 prd.md，重新分解 | 0.8 |
| PAUSE | "暂停" / "stop" | 保存 checkpoint，暂停 | 0.9 |
| RESUME | "恢复" / "resume" | 从 checkpoint 恢复 | 0.9 |
| DELEGATE | "让 Claude 写..." / "派 Qwen 测试..." | 直接委派指定 Agent | 0.85 |
| CHAT | "你好" / "在吗" / 闲聊 | 普通对话，不触发动作 | 0.7 |
| AMBIGUOUS | 无法识别 | 请求用户澄清 | — |

**解析策略**（三层级联）：
1. **规则匹配层**：关键词 + 正则快速匹配（低延迟，高置信度意图）
2. **模式匹配层**：基于历史对话模式的启发式匹配（如用户上次说"继续"后 30 秒内说"ok" → CONTINUE）
3. **LLM 解析层**：仅当规则/模式均无法识别时，调用轻量模型解析（兜底，增加 200ms 延迟）

**数据模型**：
```python
@dataclass
class UserIntent:
    intent_type: str           # "CONTINUE" / "STATUS" / ...
    confidence: float          # 0.0-1.0
    raw_text: str              # 原始输入
    extracted_params: dict     # 提取的参数
    suggested_action: str      # 建议执行的动作
    requires_confirmation: bool # 是否需要用户确认
```

**与现有代码整合**：
- 无需改动现有模块
- 解析结果直接传递给约束层和调度层

**新增文件**：`src/entry/intent_parser.py`

---

#### 3.2.3 ContextBuilder — 上下文构建器

**职责**：将 SessionLoader + IntentParser 的结果整合为系统提示上下文。

**构建内容**：
```
[系统提示上下文]

=== 项目状态 ===
项目: multi-agent-pipeline
当前 Phase: develop (Wave 5)
活跃 Features: F012 (Agent Adapter), F013 (Phase 编排)
已完成: F001-F011
等待审批: 无
上次动作: Claude Code 完成 F012 编码，CodeWhale 审核通过

=== 本轮意图 ===
用户意图: CONTINUE (置信度 0.95)
解析: 用户希望推进到下一阶段

=== 可用指令 ===
- 继续: 推进 Phase
- 查看状态: 显示进度
- 暂停: 保存状态并暂停
- 修改需求: 调整 PRD

=== 当前约束 ===
- 禁止 Hermes 直接编码
- 禁止 Hermes 直接测试
- 禁止 Hermes 直接审核
```

**与现有代码整合**：
- 复用 `context_manager.ContextManager` 的分层压缩策略
- 安全指令（角色约束）标记为 `LayerPriority.SAFETY`，永不压缩

**新增文件**：`src/entry/context_builder.py`

---

#### 3.2.4 EntryGate — 入口路由

**职责**：根据 Intent 路由到正确的处理路径。

**路由表**：

```
用户输入
    │
    ├─→ IntentParser 解析
    │
    ├─→ CHAT ─────────────→ 直接回复（不触发约束/调度层）
    │
    ├─→ STATUS ───────────→ SessionLoader 返回状态摘要
    │
    ├─→ CONTINUE/ROLLBACK/PAUSE/RESUME
    │   └─→ 约束层 (RoleGuard/ActionFilter) ──→ 调度层 (PhaseEngine)
    │
    ├─→ DELEGATE ─────────→ 约束层 ──→ 调度层 (AgentDispatcher)
    │
    ├─→ MODIFY_REQ ───────→ 约束层 ──→ 调度层 (PhaseEngine.rollback_to_design)
    │
    └─→ AMBIGUOUS ────────→ 请求澄清
```

**新增文件**：`src/entry/entry_gate.py`

---

### 3.3 入口层与现有代码的整合点

| 现有模块 | 整合方式 | 说明 |
|----------|----------|------|
| `state_store.py` | 读取 projects / checkpoints / traces | SessionLoader 数据源 |
| `phase_flow.py` | 调用 `current_phase()` | 获取当前 Phase |
| `features.json` | 直接读取 | 获取 features 状态 |
| `progress.md` | 直接读取 | 获取人类可读进度 |
| `context_manager.py` | 复用 `ContextLayer` | 构建系统提示上下文 |
| `config_loader.py` | 读取项目配置 | 获取项目元数据 |

---

## 4. 约束层（Constraint Layer）

### 4.1 设计原则

- **硬约束**：违反即拦截，不可绕过，不可关闭
- **实时检测**：在动作执行前检测，不在事后审计
- **明确反馈**：拦截时告知用户原因和正确路径
- **日志留痕**：所有违规尝试记录到 audit_logs

### 4.2 核心约束定义

基于 SOUL.md / AGENTS.md / PRD 第 0 章，定义以下硬约束：

#### 约束 1：角色分离硬约束（RoleGuard）

| 角色 | 允许动作 | 禁止动作 | 违规后果 |
|------|----------|----------|----------|
| Hermes | orchestrate, dispatch, research_org, gather_results | code_write, code_fix, test_write, test_run, code_review | 拦截，记录违规 |
| Hermes-Research | deep_research, prd_write, arch_design, task_decompose, final_accept | code_write, code_fix, test_run | 拦截，驳回任务 |
| Claude Code | code_write, code_fix | arch_design, code_review, final_accept | 拦截，重新派发 |
| CodeWhale | code_review, risk_alert, improvement_suggest | code_write, code_fix, arch_design | 拦截，重新派发 |
| Qwen Code | test_write, test_run, coverage_check, bug_report | code_write, arch_design, code_review | 拦截，重新派发 |

**检测时机**：
- AgentDispatcher 派发任务前
- EntryGate 处理 DELEGATE 意图时
- 任何 Agent 尝试通过 pipeline 执行动作时

**检测逻辑**：
```python
def check_role_constraint(agent_name: str, action: str) -> Tuple[bool, str]:
    allowed = ROLE_TASKS.get(agent_name, [])
    if action not in allowed:
        return False, (
            f"【角色约束拦截】{agent_name} 无权执行 '{action}'。"
            f"允许动作: {', '.join(allowed)}。"
            f"如需此动作，请委派给正确 Agent。"
        )
    return True, "PASS"
```

#### 约束 2：目标对齐硬约束（GoalValidator）

PRD 0.1 节已定义，当前已有 `goal_validator` 概念，需实体化：

- 编码前：验证目标是否已分解为可执行任务
- 审核前：验证代码是否实现目标功能
- 测试前：验证测试用例是否覆盖目标场景
- 验收前：验证所有目标验证是否通过
- 推进前：验证 GoalValidator 是否全通过

**检测时机**：PhaseEngine 推进 Phase 前自动调用。

#### 约束 3：动作白名单硬约束（ActionFilter）

定义全局危险动作白名单，任何 Agent 执行前必须通过：

| 动作类别 | 白名单 | 说明 |
|----------|--------|------|
| 文件操作 | read, write, append, delete | delete 需额外审批 |
| 网络操作 | api_call, git_clone, pip_install | 需 sandbox 授权 |
| 系统操作 | subprocess_run, os_system | 严格限制，需 sandbox Profile |
| 数据库操作 | query, insert, update | 仅限项目 DB |
| 模型操作 | chat_completion, embedding | 需预算检查 |

**检测时机**：Agent 执行任何工具调用前。

#### 约束 4：安全策略硬约束（SafetyEnforcer）

- 沙箱 Profile 检查：`sandbox.py` 的 Profile 必须在执行前确认
- 命令白名单检查：`sandbox.py` 的 command_whitelist 必须匹配
- 绕过检测：`sandbox.py` 的 bypass detection 必须在执行前运行
- 预算检查：`circuit_breaker.py` 的预算熔断必须在执行前检查

### 4.3 组件设计

#### 4.3.1 RoleGuard — 角色守卫

**职责**：拦截任何 Agent 越界动作。

**输入**：`(agent_name, action)`
**输出**：`(allowed: bool, reason: str)`

**状态**：无状态，纯函数判断

**与现有代码整合**：
- 复用 `SOUL.md` 中的 `ROLE_TASKS` 定义
- 复用 `AGENTS.md` 中的协作规则

**新增文件**：`src/constraint/role_guard.py`

---

#### 4.3.2 ActionFilter — 动作过滤器

**职责**：过滤危险动作，确保所有执行动作在白名单中。

**输入**：`(agent_name, action, params)`
**输出**：`(allowed: bool, reason: str, sanitized_params: dict)`

**与现有代码整合**：
- 复用 `sandbox.py` 的 `ProfileConfig` 和命令白名单
- 复用 `circuit_breaker.py` 的预算检查

**新增文件**：`src/constraint/action_filter.py`

---

#### 4.3.3 GoalValidator — 目标对齐验证器

**职责**：验证当前工作是否与 PRD 目标对齐。

**输入**：`(feature_id, phase, artifacts)`
**输出**：`(aligned: bool, report: GoalAlignmentReport)`

**与现有代码整合**：
- 复用 `phase_checks.py` 的 check 函数
- 复用 `features.json` 的 acceptance_criteria

**新增文件**：`src/constraint/goal_validator.py`

---

#### 4.3.4 SafetyEnforcer — 安全策略执行器

**职责**：执行沙箱、预算、熔断等安全策略。

**输入**：`(action, profile, budget)`
**输出**：`(allowed: bool, reason: str)`

**与现有代码整合**：
- 复用 `sandbox.py` 的 `Profile` 和 `InteractionLevel`
- 复用 `circuit_breaker.py` 的 `CircuitBreaker`
- 复用 `approval.py` 的审批分级

**新增文件**：`src/constraint/safety_enforcer.py`

---

#### 4.3.5 ViolationLogger — 违规记录器

**职责**：记录所有违规尝试，用于审计和熔断。

**输入**：违规事件 `(timestamp, agent, action, constraint, reason)`
**输出**：写入 `audit_logs` 表 + 触发告警

**与现有代码整合**：
- 复用 `state_store.py` 的 `AuditLogRecord`
- 复用 `observability.py` 的 `AlertManager`

**新增文件**：`src/constraint/violation_logger.py`

---

### 4.4 约束层执行流程

```
动作请求 (来自调度层或入口层)
    │
    ├─→ RoleGuard.check(agent, action)
    │   └─× 违规 → ViolationLogger.record() → 返回拦截
    │
    ├─→ ActionFilter.check(agent, action, params)
    │   └─× 违规 → ViolationLogger.record() → 返回拦截
    │
    ├─→ GoalValidator.check(feature, phase, artifacts)
    │   └─× 未对齐 → ViolationLogger.record() → 返回拦截
    │
    ├─→ SafetyEnforcer.check(action, profile, budget)
    │   └─× 不安全 → ViolationLogger.record() → 返回拦截
    │
    └─→ 全部通过 → 允许执行 → 调度层继续
```

**关键原则**：任何一层拦截，后续层不再执行。拦截结果立即返回给调用方。

---

### 4.5 约束层与现有代码的整合点

| 现有模块 | 整合方式 | 说明 |
|----------|----------|------|
| `SOUL.md` | 读取 ROLE_TASKS | RoleGuard 的数据源 |
| `AGENTS.md` | 读取协作规则 | RoleGuard 的补充规则 |
| `sandbox.py` | 调用 Profile / 白名单 | ActionFilter / SafetyEnforcer |
| `circuit_breaker.py` | 调用预算检查 | SafetyEnforcer |
| `approval.py` | 调用审批分级 | SafetyEnforcer |
| `state_store.py` | 写入 audit_logs | ViolationLogger |
| `observability.py` | 触发告警 | ViolationLogger |
| `phase_checks.py` | 复用 check 逻辑 | GoalValidator |
| `features.json` | 读取 acceptance_criteria | GoalValidator |

---

## 5. 调度层（Orchestration Layer）

### 5.1 设计原则

- **自动推进**：Phase 检查通过即自动推进，无需用户指令
- **自动委派**：根据当前 Phase 和 feature 自动选择 Agent
- **超时自愈**：Agent 超时自动降级、重试、或升级人工
- **状态不丢**：任何中断可从 checkpoint 恢复

### 5.2 组件设计

#### 5.2.1 PhaseEngine — Phase 自动推进引擎

**职责**：管理 Phase 0-6 的自动流转。

**当前问题**：`phase_flow.py` 的 `advance()` 需要显式调用，不会自动推进。

**改进设计**：

```python
class PhaseEngine:
    """Phase 自动推进引擎

    职责：
      1. 监控当前 Phase 的完成状态
      2. 当 check 通过时自动推进到下一 Phase
      3. 支持人工暂停/恢复
      4. 推进前自动通过约束层验证
    """

    def __init__(self, project_name: str, base_dir: Path):
        self.flow = PhaseFlow(project_name, base_dir)
        self.constraint = ConstraintLayer()  # 约束层引用
        self.auto_advance = True             # 是否自动推进
        self.paused = False                  # 是否暂停

    def tick(self) -> PhaseTickResult:
        """心跳检查：当前 Phase 是否可推进

        返回:
            - can_advance: bool
            - next_phase: str (如果可推进)
            - reason: str
            - actions: List[AgentAction] (推进后需要执行的动作)
        """
        if self.paused:
            return PhaseTickResult(can_advance=False, reason="已暂停")

        current = self.flow.current_phase()

        # 1. 执行 check
        passed, msg = self.flow.check()
        if not passed:
            return PhaseTickResult(can_advance=False, reason=f"check 未通过: {msg}")

        # 2. 约束层验证
        ok, reason = self.constraint.validate_phase_advance(current)
        if not ok:
            return PhaseTickResult(can_advance=False, reason=f"约束拦截: {reason}")

        # 3. 自动推进
        if self.auto_advance:
            advance_ok, advance_msg = self.flow.advance()
            if advance_ok:
                next_phase = self.flow.current_phase()
                actions = self._generate_phase_actions(next_phase)
                return PhaseTickResult(
                    can_advance=True,
                    next_phase=next_phase,
                    reason=advance_msg,
                    actions=actions,
                )

        return PhaseTickResult(can_advance=True, reason="check 通过，等待用户指令推进")

    def _generate_phase_actions(self, phase: str) -> List[AgentAction]:
        """根据新 Phase 生成需要自动委派的动作"""
        actions = []
        if phase == "design":
            actions.append(AgentAction("Hermes-Research", "arch_design", {}))
        elif phase == "decompose":
            actions.append(AgentAction("Hermes-Research", "task_decompose", {}))
        elif phase == "develop":
            # 获取当前 wave 的 features，委派给 Claude Code
            features = self._get_wave_features()
            for f in features:
                actions.append(AgentAction("Claude Code", "code_write", {"feature": f}))
        elif phase == "test":
            features = self._get_wave_features()
            for f in features:
                actions.append(AgentAction("Qwen Code", "test_write", {"feature": f}))
                actions.append(AgentAction("CodeWhale", "code_review", {"feature": f}))
        elif phase == "accept":
            actions.append(AgentAction("Hermes-Research", "final_accept", {}))
        return actions
```

**与现有代码整合**：
- 复用 `phase_flow.py` 的 `PhaseFlow` 类
- 复用 `phase_checks.py` 的 check 函数
- 新增自动推进逻辑

**新增文件**：`src/orchestration/phase_engine.py`

---

#### 5.2.2 AgentDispatcher — Agent 自动委派器

**职责**：根据当前任务自动选择 Agent 并委派。

**当前问题**：委派靠用户指令（"让 Claude 写..."）或 Hermes 手动判断。

**改进设计**：

```python
class AgentDispatcher:
    """Agent 自动委派器

    职责：
      1. 根据任务类型选择最佳 Agent
      2. 处理 Agent 不可用时的降级
      3. 管理并行任务（worktree）
      4. 收集 Agent 结果并反馈
    """

    TASK_AGENT_MAP = {
        "arch_design": "Hermes-Research",
        "prd_write": "Hermes-Research",
        "task_decompose": "Hermes-Research",
        "code_write": "Claude Code",
        "code_fix": "Claude Code",
        "code_review": "CodeWhale",
        "test_write": "Qwen Code",
        "test_run": "Qwen Code",
        "coverage_check": "Qwen Code",
        "final_accept": "Hermes-Research",
        "orchestrate": "Hermes",
        "dispatch": "Hermes",
    }

    def dispatch(self, task: AgentTask) -> DispatchResult:
        """委派任务给合适的 Agent

        流程：
          1. 约束层检查（RoleGuard / ActionFilter）
          2. 选择 Agent（主选 + 备选）
          3. 创建 worktree（如果需要并行）
          4. 执行 Agent（通过 Adapter）
          5. 收集结果
          6. 超时处理（TimeoutHandler）
        """
        # 1. 约束层检查
        ok, reason = self.constraint.validate_task(task)
        if not ok:
            return DispatchResult(success=False, error=reason, violated=True)

        # 2. 选择 Agent
        primary_agent = self.TASK_AGENT_MAP.get(task.type)
        if not primary_agent:
            return DispatchResult(success=False, error=f"未知任务类型: {task.type}")

        # 3. 检查 Agent 可用性（CircuitBreaker）
        adapter = self._get_adapter(primary_agent)
        if not adapter.can_execute():
            # 降级到备选 Agent
            fallback = self.fallback_manager.get_fallback(primary_agent)
            adapter = self._get_adapter(fallback)
            task.fallback_from = primary_agent

        # 4. 创建 worktree（并行开发时）
        if task.feature_id and self._should_use_worktree(task):
            worktree_path = self.worktree_manager.create_worktree(task.project, task.feature_id)
            task.worktree_path = worktree_path

        # 5. 执行（带超时）
        result = self.timeout_handler.run_with_timeout(
            adapter.execute,
            task=task,
            timeout_seconds=task.timeout or 600,
        )

        # 6. 收集结果
        return DispatchResult(
            success=result.success,
            agent=adapter.agent_name,
            result=result,
            worktree_path=task.worktree_path,
        )
```

**与现有代码整合**：
- 复用 `adapters.py` 的 `ClaudeCodeAdapter` / `QwenCodeAdapter` / `CodeWhaleAdapter`
- 复用 `fallback_manager.py` 的 `FallbackManager`
- 复用 `worktree.py` 的 `WorktreeManager`
- 复用 `circuit_breaker.py` 的 `CircuitBreaker`

**新增文件**：`src/orchestration/agent_dispatcher.py`

---

#### 5.2.3 TimeoutHandler — 超时处理器

**职责**：处理 Agent 执行超时，自动降级或升级。

**超时策略**：

| 超时类型 | 默认超时 | 处理策略 | 升级条件 |
|----------|----------|----------|----------|
| 编码任务 | 600s | 保存 checkpoint → 重试 1 次 → 降级 Agent | 重试仍超时 |
| 审核任务 | 300s | 保存 checkpoint → 标记为 pending → 异步等待 | 2 小时仍无结果 |
| 测试任务 | 300s | 保存 checkpoint → 重试 1 次 → 检查环境 | 重试仍失败 |
| 审批等待 | 1800s | 标记超时 → 保存状态 → 通知用户 | — |
| 整体 Wave | 7200s | 保存 checkpoint → 通知用户 → 询问是否继续 | — |

**数据模型**：
```python
@dataclass
class TimeoutPolicy:
    task_type: str
    timeout_seconds: int
    retry_count: int
    retry_delay_seconds: int
    fallback_on_timeout: bool
    escalate_on_retry_exhausted: bool
    checkpoint_before_retry: bool

@dataclass
class TimeoutResult:
    task: AgentTask
    outcome: str  # "success" / "retry" / "fallback" / "escalate" / "checkpoint_saved"
    retry_count: int
    final_agent: str
    error: Optional[str]
```

**与现有代码整合**：
- 复用 `state_store.py` 的 checkpoint 写入
- 复用 `fallback_manager.py` 的降级逻辑
- 复用 `approval.py` 的异步审批超时

**新增文件**：`src/orchestration/timeout_handler.py`

---

#### 5.2.4 RecoveryManager — 故障恢复管理器

**职责**：处理 Agent 崩溃、网络中断、模型不可用等故障。

**恢复策略**：

| 故障类型 | 检测方式 | 恢复策略 |
|----------|----------|----------|
| Agent 崩溃 | Adapter 返回 CRASHED | 重启 Adapter → 重试 → 降级 |
| 网络中断 | API 调用超时 | 指数退避重试 → 切换 endpoint |
| 模型不可用 | CircuitBreaker OPEN | 自动降级到备选模型 |
| 上下文溢出 | token 超限 | 压缩上下文 → 分段执行 |
| 文件锁冲突 | portalocker 超时 | 等待 5s → 强制释放 → 重试 |
| 数据库损坏 | SQLite 异常 | 从最新 checkpoint 恢复 |

**与现有代码整合**：
- 复用 `adapters.py` 的 `ToleranceLayer`
- 复用 `circuit_breaker.py` 的 `ResilienceManager`
- 复用 `context_manager.py` 的压缩策略
- 复用 `state_store.py` 的 checkpoint 恢复

**新增文件**：`src/orchestration/recovery_manager.py`

---

#### 5.2.5 CheckpointSync — 状态同步器

**职责**：确保所有状态变更原子写入，支持中断恢复。

**同步策略**：
1. **原子写入**：先写 `.tmp` 文件，再 `os.replace`
2. **文件锁**：写 `features.json` / `progress.md` 前获取 `portalocker` 锁
3. **双写**：关键状态同时写入 SQLite + 文本文件（人类可读）
4. **checkpoint 链**：每次状态变更写入 checkpoint，保留最近 50 个
5. **恢复扫描**：启动时扫描未完成的 checkpoint，询问用户是否恢复

**与现有代码整合**：
- 复用 `state_store.py` 的 `CheckpointRecord`
- 复用 `phase_flow.py` 的 checkpoint 写入
- 复用 `portalocker` 文件锁

**新增文件**：`src/orchestration/checkpoint_sync.py`

---

### 5.3 调度层执行流程

```
PhaseEngine.tick() 或 AgentDispatcher.dispatch()
    │
    ├─→ 约束层验证 (RoleGuard / ActionFilter / GoalValidator / SafetyEnforcer)
    │   └─× 拦截 → 返回违规
    │
    ├─→ TimeoutHandler 包装执行
    │   ├─→ 正常完成 → 返回结果
    │   ├─→ 超时 → 重试 / 降级 / 升级
    │   └─→ 崩溃 → RecoveryManager 处理
    │
    ├─→ CheckpointSync 保存状态
    │
    ├─→ PhaseEngine 检查是否可推进
    │   ├─→ 可推进 → 自动推进 → 生成新 actions
    │   └─→ 不可推进 → 等待
    │
    └─→ 返回结果给用户
```

---

### 5.4 调度层与现有代码的整合点

| 现有模块 | 整合方式 | 说明 |
|----------|----------|------|
| `phase_flow.py` | 封装 `PhaseFlow` | PhaseEngine 的核心 |
| `phase_checks.py` | 复用 check 函数 | PhaseEngine 推进前检查 |
| `adapters.py` | 复用 `ClaudeCodeAdapter` 等 | AgentDispatcher 的执行器 |
| `fallback_manager.py` | 复用 `FallbackManager` | AgentDispatcher 降级 |
| `worktree.py` | 复用 `WorktreeManager` | AgentDispatcher 并行 |
| `circuit_breaker.py` | 复用 `CircuitBreaker` | TimeoutHandler / RecoveryManager |
| `state_store.py` | 复用 `StateStore` | CheckpointSync 持久化 |
| `approval.py` | 复用 `BaseApproval` | PhaseEngine 审批节点 |
| `observability.py` | 复用 `AlertManager` | RecoveryManager 告警 |
| `context_manager.py` | 复用 `ContextManager` | RecoveryManager 上下文压缩 |

---

## 6. 三层交互流程（完整示例）

### 场景：用户说"继续"

```
用户: "继续"
    │
    ▼
┌─────────────────────────────────────────┐
│ 入口层                                   │
│ 1. SessionLoader: 加载项目状态            │
│    → 当前 Phase: develop, Wave: 5       │
│    → 活跃 Features: [F012, F013]         │
│ 2. IntentParser: 解析为 CONTINUE (0.95)    │
│ 3. ContextBuilder: 构建系统提示          │
│ 4. EntryGate: 路由到调度层               │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 约束层                                   │
│ 1. RoleGuard: 检查通过（用户不是 Agent）   │
│ 2. ActionFilter: 检查通过（CONTINUE 安全）│
│ 3. GoalValidator: 检查当前 wave 目标      │
│ 4. SafetyEnforcer: 检查通过             │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 调度层                                   │
│ 1. PhaseEngine.tick():                  │
│    → check develop: 通过                │
│    → 自动推进到 test                    │
│ 2. 生成 actions:                        │
│    → Qwen Code: test_write(F012)        │
│    → CodeWhale: code_review(F012)       │
│    → Qwen Code: test_write(F013)        │
│    → CodeWhale: code_review(F013)       │
│ 3. AgentDispatcher.dispatch():          │
│    → 创建 worktree（并行）              │
│    → TimeoutHandler 包装执行            │
│    → CheckpointSync 保存状态             │
│ 4. 收集结果                             │
│ 5. PhaseEngine.tick():                  │
│    → check test: 通过 → 推进到 accept   │
│    → 或 check test: 失败 → 停留 test    │
└─────────────────────────────────────────┘
    │
    ▼
用户: "Wave 5 完成，F012/F013 测试通过，已推进到 accept。是否继续？"
```

---

### 场景：Hermes 尝试编码（违规拦截）

```
Hermes 内部: "我来修复这个 bug..."
    │
    ▼
┌─────────────────────────────────────────┐
│ 约束层                                   │
│ 1. RoleGuard.check("Hermes", "code_fix")  │
│    → ❌ 违规！Hermes 禁止编码            │
│ 2. ViolationLogger.record()             │
│ 3. 触发 observability.AlertManager      │
│ 4. 返回拦截信息                           │
└─────────────────────────────────────────┘
    │
    ▼
Hermes: "【角色约束拦截】Hermes 无权执行 'code_fix'。"
        "允许动作: orchestrate, dispatch, research_org, gather_results。"
        "如需修复代码，请委派给 Claude Code。"
        "已记录违规日志。"
```

---

## 7. 文件结构

### 7.1 新增文件

```
src/
├── entry/                          # 入口层
│   ├── __init__.py
│   ├── session_loader.py           # SessionLoader
│   ├── intent_parser.py            # IntentParser
│   ├── context_builder.py          # ContextBuilder
│   └── entry_gate.py               # EntryGate
│
├── constraint/                     # 约束层
│   ├── __init__.py
│   ├── role_guard.py               # RoleGuard
│   ├── action_filter.py            # ActionFilter
│   ├── goal_validator.py           # GoalValidator
│   ├── safety_enforcer.py          # SafetyEnforcer
│   └── violation_logger.py         # ViolationLogger
│
├── orchestration/                  # 调度层
│   ├── __init__.py
│   ├── phase_engine.py             # PhaseEngine
│   ├── agent_dispatcher.py         # AgentDispatcher
│   ├── timeout_handler.py          # TimeoutHandler
│   ├── recovery_manager.py         # RecoveryManager
│   └── checkpoint_sync.py          # CheckpointSync
│
└── integration.py                  # 三层集成入口（对外统一接口）
```

### 7.2 修改文件

```
src/
├── pipeline.py                     # 集成 EntryGate + ConstraintLayer + OrchestrationLayer
│                                   # 新增命令: auto-tick, auto-dispatch, status-full
├── phase_flow.py                   # 新增 auto_advance 参数支持
└── context_manager.py              # 新增 SessionContext 作为 ContextLayer
```

### 7.3 不修改的现有文件

以下模块保持原样，通过导入复用：
- `state_store.py` — 持久化
- `phase_checks.py` — Phase 检查
- `adapters.py` — Agent 适配器
- `fallback_manager.py` — 降级管理
- `worktree.py` — Worktree 管理
- `circuit_breaker.py` — 熔断器
- `approval.py` — 审批系统
- `observability.py` — 可观测性
- `sandbox.py` — 沙箱
- `prompt_cache.py` — 缓存
- `config_loader.py` — 配置

---

## 8. 测试策略

### 8.1 入口层测试

| 测试 | 内容 | 预期 |
|------|------|------|
| test_session_loader | 从空目录/完整目录加载 | 正确识别项目/非项目 |
| test_intent_parser | 输入 50 种典型用户语句 | 正确分类，置信度 > 0.8 |
| test_context_builder | 构建系统提示 | 包含所有必要信息，< 2000 tokens |
| test_entry_gate | 路由各种意图 | 正确分发到约束层/直接回复 |

### 8.2 约束层测试

| 测试 | 内容 | 预期 |
|------|------|------|
| test_role_guard | 每个 Agent 执行允许/禁止动作 | 允许通过，禁止拦截 |
| test_action_filter | 执行白名单外动作 | 拦截并记录 |
| test_goal_validator | 未对齐目标提交 | 拦截并报告 |
| test_safety_enforcer | 沙箱/预算违规 | 拦截 |
| test_violation_logger | 记录违规 | 写入 audit_logs，触发告警 |

### 8.3 调度层测试

| 测试 | 内容 | 预期 |
|------|------|------|
| test_phase_engine_tick | 各 Phase check 通过/失败 | 正确推进/停留 |
| test_phase_engine_auto_advance | 开启/关闭自动推进 | 自动/手动行为正确 |
| test_agent_dispatcher | 派发各类型任务 | 正确选择 Agent，降级正常 |
| test_timeout_handler | 模拟超时 | 重试/降级/升级正确 |
| test_recovery_manager | 模拟崩溃/网络中断 | 恢复成功 |
| test_checkpoint_sync | 中断后恢复 | 状态不丢 |

### 8.4 集成测试

| 测试 | 内容 | 预期 |
|------|------|------|
| test_full_pipeline_continue | 用户说"继续"完整流程 | 自动推进，委派 Agent，返回结果 |
| test_violation_interception | Hermes 尝试编码 | 拦截，记录，正确反馈 |
| test_timeout_recovery | Agent 超时 | 自动降级，状态保存 |
| test_checkpoint_resume | 中断后重启 | 从 checkpoint 恢复，继续执行 |

---

## 9. 验收标准

### 9.1 入口层验收

- [ ] 用户说"继续"，系统自动加载项目状态并推进（无需手动输入路径/Phase）
- [ ] 用户说"查看状态"，系统返回 200 字以内摘要（包含当前 Phase/活跃 features/等待审批）
- [ ] 用户说"让 Claude 写 F014"，系统自动识别 DELEGATE 意图并委派
- [ ] 系统提示上下文 < 2000 tokens（中文字符）

### 9.2 约束层验收

- [ ] Hermes 尝试编码 → 被拦截，返回明确原因
- [ ] Claude Code 尝试做架构设计 → 被拦截，重新派发给 Hermes-Research
- [ ] CodeWhale 尝试修复代码 → 被拦截，重新派发给 Claude Code
- [ ] 所有违规记录到 audit_logs，可在 observability 仪表盘查看
- [ ] 拦截延迟 < 10ms（纯本地检查）

### 9.3 调度层验收

- [ ] Phase check 通过 → 10 秒内自动推进（无需用户指令）
- [ ] Agent 超时 600s → 自动保存 checkpoint → 重试 1 次 → 降级 Agent
- [ ] 4 个 Agent 并行任务 → 自动创建 worktree，无文件冲突
- [ ] 系统崩溃后重启 → 从最新 checkpoint 恢复，询问用户是否继续
- [ ] 整体 Wave 超时 2 小时 → 保存状态，通知用户，询问是否继续

---

## 10. 实施计划

### Phase 1: 约束层（最高优先级，安全基础）

1. 实现 `RoleGuard` — 角色越界拦截
2. 实现 `ActionFilter` — 动作白名单
3. 实现 `ViolationLogger` — 违规记录
4. 集成到 `pipeline.py` — 所有动作执行前通过约束层

**预计**：1 个 Wave，1 个 feature（F023: 约束层）

### Phase 2: 入口层（用户体验）

1. 实现 `SessionLoader` — 自动加载状态
2. 实现 `IntentParser` — 意图解析
3. 实现 `ContextBuilder` — 上下文构建
4. 实现 `EntryGate` — 入口路由
5. 集成到 `pipeline.py` — 新增命令

**预计**：1 个 Wave，1 个 feature（F024: 入口层）

### Phase 3: 调度层（自动化核心）

1. 实现 `PhaseEngine` — 自动推进
2. 实现 `AgentDispatcher` — 自动委派
3. 实现 `TimeoutHandler` — 超时处理
4. 实现 `RecoveryManager` — 故障恢复
5. 实现 `CheckpointSync` — 状态同步
6. 集成到 `pipeline.py` — 完整自动化流程

**预计**：2 个 Wave，2 个 features（F025: 调度层上，F026: 调度层下）

### Phase 4: 集成与验收

1. 三层集成测试
2. E2E 测试（完整流程）
3. 性能测试（延迟 < 10ms 约束检查）
4. 文档更新

**预计**：1 个 Wave，1 个 feature（F027: 三层集成）

---

## 11. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 意图解析准确率不足 | 中 | 三层级联（规则→模式→LLM），低置信度时请求澄清 |
| 约束层误拦截正常操作 | 中 | 白名单可配置，拦截日志可审计，支持人工覆盖 |
| 自动推进导致错误前进 | 高 | Phase check 严格，支持 pause 模式，推进前通知用户 |
| 超时处理过于激进 | 中 | 超时策略可配置，支持人工覆盖 |
| 状态同步失败导致数据丢失 | 高 | 双写策略（SQLite + 文本），checkpoint 链保留 50 个 |
| 与现有代码冲突 | 中 | 新增文件独立，现有文件最小修改，充分测试 |

---

## 12. 附录

### 12.1 术语表

| 术语 | 说明 |
|------|------|
| 入口层 | 自动加载状态、解析意图、构建上下文的层 |
| 约束层 | 拦截违规动作（角色越界、危险操作）的层 |
| 调度层 | 自动推进 Phase、委派 Agent、处理超时的层 |
| SessionContext | 单次对话的完整项目状态快照 |
| UserIntent | 用户自然语言解析后的结构化意图 |
| PhaseEngine | Phase 自动推进引擎 |
| AgentDispatcher | Agent 自动委派器 |
| TimeoutHandler | 超时自动处理器 |
| RecoveryManager | 故障恢复管理器 |
| CheckpointSync | 状态同步与持久化器 |

### 12.2 参考文档

- `SOUL.md` — Agent 角色定义
- `AGENTS.md` — 协作规则
- `PRD v2.3` — 产品需求（第 0 章硬约束、第 3 章架构、第 20 章实施路线图）
- `progress.md` — 当前进度
- `features.json` — 任务分解

---

> 本文档为设计文档（PRD 格式），非编码任务。
> 实施时按 Phase 1-4 顺序执行，每个 Phase 通过 check 后推进。
