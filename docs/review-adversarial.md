# 对抗性审查报告：multi-agent-pipeline 的智能化、通用性与科学性

> **审查日期**：2026-07-02  
> **审查范围**：src/ 全部源码、specs/、features.json、progress.md、SOUL.md、AGENTS.md  
> **审查视角**：最坏假设（hostile review）—— 把当前实现当作“刚好能通过测试的纸面架构”，验证其在真实工程压力下的脆性。  
> **基准事实**：progress.md 记录 28/28 features passed、1206/1206 tests PASS；本报告默认这些测试不能覆盖扩展性、跨模式复用性、自主决策与理论可解释性。

---

## 摘要

| 维度 | 核心结论 | 风险等级 |
|------|----------|----------|
| 3 → 10 Agents 扩展性 | 不会“立刻崩溃”，但会在 **任务类型校验、Adapter 注册表、调度映射、降级链** 四处硬阻塞；必须改源码才能新增 Agent。 | 🟠 高 |
| Brownfield ↔ Greenfield 复用性 | 底层基础设施（MQ、State、Observability、Adapter factory 等）可复用；**模式感知层**（init、phase_checks、workflow 选择、condition 插入、suggestion 映射）与当前模式硬耦合，跨模式基本不可用。 | 🟠 高 |
| 减轻 Hermes 决策负担 | 当前智能化不足以显著减负。入口意图、约束路由、Phase 推进均为 **静态规则 + 存在性检查**，缺少自主闭环；大量审批/标记仍靠 Hermes 手工完成。 | 🟡 中高 |
| 科学依据 | Dispatch、Phase 顺序、约束规则、评分权重多为 **PRD 教条与启发式阈值**，缺少文献映射、本地对照实验与阈值校准；部分设计（3 轮对抗+第三方裁决）并未真正落地。 | 🟡 中高 |

---

## 挑战 1：Agent 数量从 3 个扩展到 10 个，pipeline 会不会崩溃？

### 最坏假设
新增 7 个专业 Agent（例如：安全审计 Agent、UI/UX Agent、文档 Agent、DevOps Agent、数据 Agent、需求澄清 Agent、回归测试 Agent）。在不做任何人工修改的情况下，系统能否平滑容纳它们？

### 核心结论
**不会瞬间崩溃，但会在多处“硬编码注册表”处被拦截，等于事实上的不可扩展。** 当前实现不是一个“Agent 可插拔”的系统，而是一个“3 个固定角色 + 几个预留任务类型”的系统。

### 论据

1. **Adapter 注册表只认识 3 个人**  
   `src/adapters.py:1661-1665`：
   ```python
   ADAPTER_REGISTRY: Dict[str, Callable[..., BaseAdapter]] = {
       "claude": ClaudeCodeAdapter,
       "codewhale": CodeWhaleAdapter,
       "qwen": QwenCodeAdapter,
   }
   ```
   `create_adapter()` 在 `src/adapters.py:1674-1675` 直接对未知 adapter 抛 `ValueError`。任何第 4 个 CLI Agent 必须修改源码才能被识别。

2. **任务类型白名单四处硬编码且互不统一**  
   - `src/message_queue.py:36`：`VALID_TASK_TYPES = ("code", "review", "test", "shutdown", "inspector", "adversarial", "doc", "e2e")`，并在 `src/message_queue.py:193` 拒绝未注册类型。  
   - `src/agent_daemon.py:48`：`VALID_TASK_TYPES = ("code", "review", "test", "shutdown")`，比 MQ 还少 4 个。  
   - `src/system_constraint.py:69-81`：`TaskType` 枚举是封闭集合，没有动态扩展机制。  
   这意味着新增 Agent 时，必须同时改 MQ、daemon、constraint 三个白名单，否则任务连队列都进不去。

3. **调度映射写死在建议引擎里**  
   `src/suggestion_engine.py:382-390`：
   ```python
   phase_task_map = {
       "init": TaskType.ORCHESTRATE,
       "design": TaskType.ANALYZE,
       "decompose": TaskType.ANALYZE,
       "develop": TaskType.CODE,
       ...
   }
   ```
   新增的 Phase 或 Agent 类型没有对应入口，约束检查直接跳过，无法生成 actionable 调度建议。

4. **降级链只认识 Claude → Qwen → CodeWhale**  
   `src/fallback_manager.py:65`：`fallback_chain: List[str] = field(default_factory=lambda: ["qwen", "codewhale"])`。新增 Agent 无法被纳入自动降级路径，除非扩展配置。

5. **SystemConstraint 是“角色→任务”死映射**  
   `src/system_constraint.py:98-110` 的 `TASK_ADAPTER_MAP` 把 8 种任务固定路由到 3 个 adapter；`ADAPTER_CAPABILITIES` 反向查询也只支持这 3 个。没有基于能力声明的动态匹配。

6. **WorkerPool 能启动任意 agent_id，但缺少发现机制**  
   `src/worker_pool.py:189` 的 `start_agent(agent_id, count)` 支持任意 id，但它依赖调用方传入 `cli_path`，且 daemon 只识别上述 4 种 task_type。新增 Agent 的启动、心跳、任务消费链路需要手动拼接。

7. **SQLite MQ 的并发不是主要瓶颈，但语义扩展是**  
   MessageQueue 使用 WAL + `threading.RLock`，10 个 Agent 轻量级任务不会把数据库压垮；真正压垮的是“每新增一个 Agent 都要改 5 处源码”的维护模式。

### 反方案

1. **引入统一的 `AgentRegistry` 元数据驱动**：每个 Agent 通过配置文件/代码声明 `name`、`capabilities`、`task_types`、`cli_path`、`cost_profile`、`fallback_chain`、`max_workers`。所有硬编码 map 改为从注册表读取。
2. **开放任务类型注册**：`MessageQueue`、`AgentDaemon`、`SystemConstraint` 统一使用注册表的 capabilities，而不是各自维护 tuple/enum。
3. **分层调度器**：把 Agent 分成 `coder_group`、`reviewer_group`、`test_group`、`specialist_group`，组内按负载/成本/历史质量动态选择实例，组间通过 pipeline 编排。
4. **工作队列自动伸缩**：`WorkerPool` 根据 queue depth / 平均 latency 自动扩容/缩容某类 Agent 的实例数。
5. **Worktree 合并自动化**：当前 `worktree.py` 有重叠检测但缺少自动 merge/rebase 与冲突升级；10 Agent 并行必须有自动化合并或人工仲裁入口。

---

## 挑战 2：项目从 brownfield 变成 greenfield，哪些模块无法复用？

### 最坏假设
用户先在一个存量项目（brownfield）上用了本系统，随后要把它用于一个全新的 greenfield 项目；或者反过来。要求系统在不重写核心代码的前提下切换模式。

### 核心结论
**底层基础设施跨模式可复用，但所有“模式感知层”都假设了单一模式。** 当前 greenfield/brownfield 更像是两套平行的 Phase 列表，而不是一个统一的、可配置的工作流引擎。

### 论据

1. **`pipeline.py init` 永远只创建 greenfield 骨架**  
   `src/pipeline.py:202-259` 的 `cmd_init` 无条件生成 `SOUL.md`、`AGENTS.md`、`progress.md`、`features.json`、`src/`、`tests/`、`specs/`、`git init`。它既不读取 `config.pipeline_mode`，也不调用 `workflow_registry.detect_project_type()`。对 brownfield 存量项目，这会生成一套与存量代码并行的空骨架。

2. **`check_init` 的检查项是 greenfield 专属**  
   `src/phase_checks.py:114`：
   ```python
   required_files = ["SOUL.md", "AGENTS.md", "progress.md", "features.json"]
   ```
   brownfield 场景下这些文件可能不存在或不应是入口；但 check 失败会阻塞任何推进。

3. **模式检测存在但未被调度层使用**  
   - `src/config.py:81-110` 提供了 `detect_mode()`，依据 `src/`、`features.json` 中 passed feature、`docs/audit-*.md` 判断 brownfield。  
   - `src/workflow_registry.py:192-237` 提供了 `detect_project_type()`，依据 `.audit`、`.hotfix`、`src/` 等哨兵文件判断。  
   但这两个检测函数都没有在 `pipeline.py init`、`PhaseFlow.__init__` 或 `SuggestionEngine` 里被调用以切换实际 Phase 链；它们只是 check_init 里被 try/except 包起来跑一下，失败也不影响结果。

4. **PhaseFlow 的 Phase 链依赖 config，但 check registry 没有按模式隔离**  
   `src/phase_flow.py:50`：`PHASE_ORDER = get_config().phase_order`。虽然 brownfield 模式会切换为 `discover→benchmark→analyze→plan→execute→verify→deliver`，但 `src/phase_checks.py:1092-1113` 把 greenfield 与 brownfield 的 check 函数混在一个 registry 里，没有运行时按模式加载的机制。若当前模式是 brownfield，`check_init`、`check_design` 等 greenfield 检查仍然可用，会产生语义混乱。

5. **条件引擎的 Phase 插入逻辑写死了 greenfield 顺序**  
   `src/condition_engine.py:567-572`：
   ```python
   phase_order = [
       "INIT", "PRD", "RESEARCH", "DESIGN", "DESIGN_REVIEW",
       "DECOMPOSE", "DEVELOP", "CODE_REVIEW", "TEST",
       "FIX_LOOP", "ACCEPT", "DEPLOY",
   ]
   ```
   若当前模板是 brownfield，`trigger_deep_review`、`insert_fix_loop` 的插入位置会指向不存在的 Phase。

6. **建议引擎的 phase→task 映射只覆盖 greenfield**  
   `src/suggestion_engine.py:382-390` 的 `phase_task_map` 包含 `init/design/decompose/develop/test/accept/deploy`。对 brownfield 的 `discover/benchmark/analyze/plan/execute/verify/deliver`，返回 `None`，约束检查被跳过，无法给出任务路由建议。

7. **`SystemConstraint` 没有 brownfield 任务类型**  
   `src/system_constraint.py:69-81` 的 `TaskType` 枚举没有 `DISCOVER`、`BENCHMARK`、`ANALYZE`、`PLAN`、`AUDIT` 等类型；路由层无法为 brownfield 任务选择 Agent。

8. **SOUL.md / AGENTS.md 内容空泛，无法承载模式差异**  
   实际文件 `AGENTS.md:1` 只有 `(TBD)`；`SOUL.md` 只有项目元数据。模式特定的角色、规则、入口都没有落地。

### 可复用 vs 不可复用矩阵

| 层级 | 模块 | 跨模式可复用？ | 原因 |
|------|------|----------------|------|
| 基础设施 | `message_queue.py` | ✅ 是 | 通用 SQLite 任务队列，与模式无关 |
| 基础设施 | `state_store.py` | ✅ 是 | 通用 projects/checkpoints/features 持久化 |
| 基础设施 | `observability.py` | ✅ 是 | 指标、告警、dashboard 不依赖 Phase 语义 |
| 基础设施 | `circuit_breaker.py` | ✅ 是 | 通用熔断/降级 |
| 基础设施 | `prompt_cache.py` | ✅ 是 | 通用缓存 |
| 基础设施 | `audit_trail.py` | ✅ 是 | 通用操作审计 |
| 基础设施 | `budget_guard.py` | ✅ 是 | 通用预算监控 |
| 执行层 | `adapters.py`（工厂） | ⚠️ 需扩展 | 当前只注册 3 个 adapter，但工厂机制可复用 |
| 执行层 | `worker_pool.py` | ⚠️ 需扩展 | 能启动任意 agent_id，但 daemon 任务类型需开放 |
| 编排层 | `pipeline.py init` | ❌ 否 | 只生成 greenfield 骨架 |
| 编排层 | `phase_checks.check_init` | ❌ 否 | 强制要求 greenfield 元数据文件 |
| 编排层 | `workflow_registry.py` | ⚠️ 需打通 | 有检测函数，但未被 init/PhaseFlow 使用 |
| 编排层 | `condition_engine.py` | ❌ 否 | Phase 插入逻辑硬编码 greenfield 顺序 |
| 编排层 | `suggestion_engine.py` | ❌ 否 | phase_task_map 只覆盖 greenfield |
| 编排层 | `system_constraint.py` | ❌ 否 | TaskType 缺少 brownfield 类型 |

### 反方案

1. **`init` 命令模式感知**：调用 `config.detect_mode()` / `workflow_registry.detect_project_type()`，根据 greenfield/brownfield 生成不同骨架（greenfield：SOUL/AGENTS/features；brownfield：audit 计划、benchmark 模板、gap-matrix、存量代码导入）。
2. **按模式加载 Phase check registry**：`PhaseFlow` 初始化时根据 `pipeline_mode` 从 `workflow_registry` 拿到模板，再加载该模板对应的 check 集合，而不是把 greenfield/brownfield 混在一个 dict。
3. **条件引擎从模板推导插入点**：`determine_phase_insertions` 应读取当前 workflow template 的 `phases` 列表，而不是写死 12-phase 常量。
4. **建议引擎按模板生成 phase-task map**：每个 workflow template 自带 `phase_roles` 元数据，`SuggestionEngine` 根据当前模板动态映射。
5. **`SystemConstraint` 支持任务类型注册**：brownfield 类型（discover/audit/analyze 等）作为模板元数据注入，而非写死在枚举里。

---

## 挑战 3：智能化程度是否足以减少 Hermes 的决策负担？

### 最坏假设
Hermes 作为 Orchestrator 本应“开口即协作”，但系统实际上只是把 Hermes 要做的判断提前列成清单。它能否真正减少决策次数与认知负荷？

### 核心结论
**当前智能化水平不足以显著减负。** 入口层是关键词匹配；调度层是“建议模式”而非“执行模式”；约束层是静态角色映射；Phase 检查多数是“文件是否存在”的形式检查。Hermes 仍需要手动审批、手动标记测试通过、手动推进 Phase、手动处理歧义。

### 论据

1. **入口意图解析过于简陋**  
   `src/entry.py:49`：
   ```python
   class UserIntent(Enum):
       DEVELOP = "develop"
       MODIFY = "modify"
       QUERY = "query"
       UNKNOWN = "unknown"
   ```
   只有 4 种意图，且 `src/entry.py:291-309` 用硬编码关键词 + 正则匹配；没有 LLM 兜底、没有对话历史、没有歧义澄清。例如“把 F012 的测试覆盖率提到 80%”会被简单归类为 `MODIFY`，无法解析出 feature、指标、阈值。

2. **建议引擎只建议、不执行**  
   `src/suggestion_engine.py:138-211` 的 `suggest_next_phase()` 返回 `SuggestionType.ADVANCE/BLOCKER/INFO`，`can_advance=True` 也只是标志位。它不会自动调用 `PhaseFlow.advance()`、不会自动派发 Agent、不会自动处理 blocker。Hermes 仍需读取建议后自己决定并执行。

3. **约束路由没有成本/负载/质量感知**  
   `src/system_constraint.py:215-295` 的 `route_task()` 只看 task_type → adapter 的固定映射，不感知：当前队列长度、各 adapter 历史成功率、token 成本、响应延迟、模型健康度。这导致 Hermes 仍需在关键节点做“用谁更划算”的决策。

4. **条件阈值是静态启发式**  
   `src/condition_engine.py:253-272`：
   ```python
   DEFAULT_RULES = [
       ConditionRule("code_lines>500", ..., "trigger_deep_review"),
       ConditionRule("test_failures>3", ..., "insert_fix_loop"),
       ConditionRule("budget_80pct", ..., "pause"),
   ]
   ```
   500 行、3 次失败、80% 预算、80% 覆盖率均为硬编码，没有根据项目历史或语言特性自适应。对 Python 脚本和 Java 企业项目使用同一阈值显然不科学。

5. **Phase 检查多数是存在性/标记检查**  
   - `src/phase_checks.py:426`：`progress_updated = bool(progress_content.strip()) and "develop" in progress_content.lower()`。  
   - `src/phase_checks.py:493`：`tests_passed = state.get("tests_passed", False)`，需要人工调用 `pipeline.py mark-tests`。  
   - `src/phase_checks.py:199`：`design_approved = state.get("design_approved", False)`，需要人工 `pipeline.py approve --phase design`。  
   - `src/phase_checks.py:542`：`accept_approved` 同样需要人工审批。  
   这些检查验证的是“人类有没有在正确的时间点打勾”，而不是“工作成果是否真正满足目标”。

6. **角色协作规则未编码**  
   `AGENTS.md:1` 只有 `(TBD)`；`SOUL.md` 只有项目元数据。系统无法基于细粒度协作规则做决策，因为规则本身不存在。

7. **评估权重与模型路由缺少反馈闭环**  
   `src/evaluate.py:10-15` 的 30/20/25/15/10 权重是固定值；`src/research_agent.py` 的 3 路并行研究没有基于置信度或来源质量做加权聚合；`inspector.py` 的 4 个审查维度同样未经本地验证。

### 反方案

1. **LLM + 规则的混合意图解析**：保留关键词快速路径，增加 LLM fallback 解析 feature/指标/阈值/期望完成时间；低置信度时主动澄清而不是猜。
2. **从“建议模式”升级到“带确认的执行模式”**：`SuggestionEngine` 生成 action plan 后，约束层校验通过即可自动执行低风险动作（如派发编码任务、运行测试），仅在高成本/高风险动作处请求一次性确认。
3. **基于多目标优化的调度策略**：把队列长度、模型健康度、历史成功率、成本、延迟纳入评分函数，使用加权轮询或多臂老虎机动态选择 Agent/模型。
4. **语义化 Phase check**：结合静态分析、测试覆盖率、spec 一致性、LLM-as-Judge 判断工作成果是否真正满足 acceptance criteria；`tests_passed` 由 `gate.py` 自动判定，而不是人工 flag。
5. **元认知/反馈闭环**：记录每次决策（选谁、阈值触发、人工覆盖）与结果，定期 replay 优化条件阈值、模型路由权重、评估维度权重。

---

## 挑战 4：科学依据在哪？——dispatch 策略、Phase 顺序、约束规则有没有理论基础？

### 最坏假设
PRD 里写的“硬约束”“12 Phase”“角色分离”“5 维评分”都是作者的主观工程直觉，没有引用任何软件工程、多智能体系统或决策科学的文献，也没有经过本地对照实验验证。

### 核心结论
**多数设计是工程直觉与 PRD 教条，缺乏可验证的科学基础。** 尤其值得警惕的是：3 轮对抗审查 + 第三方裁决在 features.json 中标记为 passed，但代码层面只是占位实现；模型路由依据的是公开 benchmark，而非本地任务上的实际表现。

### 论据

1. **Dispatch 策略是固定角色映射，无理论支撑**  
   `src/system_constraint.py:98-110` 的 `TASK_ADAPTER_MAP` 把 `CODE → claude-code`、`REVIEW → codewhale`、`TEST → qwen-code` 写死。`specs/prd.md:0.3` 称之为“角色分离硬约束”，但没有引用任何：  
   - 组织设计理论（如 Belbin team roles、separation of duties）  
   - 多智能体任务分配算法（如 contract net、market-based allocation）  
   - 本地 A/B 实验（单 Agent vs 2-Agent vs 4-Agent 在相同任务上的成功率/成本/延迟）  
   因此无法证明“Claude 编码 + CodeWhale 审查 + Qwen 测试”这一组合优于其他组合。

2. **Phase 顺序是 PRD 自定义，未映射到已知生命周期模型**  
   `src/config.py:50-58` 的 greenfield 顺序：
   ```python
   ["init", "design", "decompose", "research", "prd", "journey",
    "develop", "integrate", "test", "evaluate", "accept", "deploy"]
   ```
   其中 `design → decompose → research → prd → journey` 的排列既没有对应 V-Model、Incremental Commitment Spiral Model，也没有对应 DORA/DevOps 研究中的高绩效实践。文档里没有说明为何 research 在 decompose 之后、prd 在 research 之后，而不是更常见的 `prd → design → decompose → develop`。

3. **条件阈值是未经校准的启发式**  
   `src/condition_engine.py:82-99` 的谓词：
   - `code_lines > 500`
   - `test_failures > 3`
   - `budget_consumed >= 80%`
   - `test_coverage < 80%`
   这些数字在 `specs/prd.md:1673-1678` 中被明确标注为 **“初始估值，必须通过 Week 1 基线实验校准”**。但 `progress.md` 只记录测试通过，没有提供校准方法或结果。不同语言、不同项目规模下 500 行是否合理的证据缺失。

4. **“逐点 3 轮对抗 + 第三方裁决”未真正落地**  
   - `features.json` W4-K03 验收标准：*“每争议点独立 3 轮辩论；第三方裁决正确输出 A 胜/B 胜/折中”*。  
   - `src/adversarial_review.py:633-693` 的 `_agent_argue()` 明确标注：*“Placeholder: in a real implementation, this would call the actual LLM agents.”*，且默认返回模板字符串。  
   - `src/phase_checks.py:811-862` 的 `_run_adversarial_review()` 只执行了 **1 轮挑战 + 1 轮辩护**，没有 3 轮循环，也没有独立裁决 Agent；并且角色分配还与 PRD 相反（挑战方是 `claude-code`，辩护方是 `codewhale`）。  
   因此该 feature 的“科学依据”声称无法被代码验证。

5. **模型路由基于公开 benchmark，缺少本地验证**  
   `docs/research_report.md:88-103` 与 `specs/prd.md:847-860` 引用 SWE-bench Pro / LiveCodeBench / Codeforces 分数来分配模型：Claude Code → Kimi、CodeWhale → DeepSeek V4 Pro、Qwen Code → Qwen3-Coder-Plus。但没有：  
   - 在本代码库上的实际修复率/审查命中率对比  
   - 不同模型审查成本 vs 发现 bug 数量的 ROC 曲线  
   - 双模型路由上下文损耗的实测（PRD 只设定 20% 阈值，未给出测量方法）  
   公开 benchmark 不一定迁移到当前 Windows 11 Home + 中文项目场景。

6. **评估维度权重是主观设定**  
   `src/evaluate.py:10-15`：
   ```python
   # accuracy 30%, completeness 20%, honesty 25%, helpfulness 15%, consistency 10%
   ```
   没有引用任何可解释性/幻觉检测文献，也没有与人工评分做相关性校验。红线规则 `honesty < 5 → BLOCK`、`accuracy < 4 → BLOCK` 同样是阈值直觉。

7. **Inspector 的 4 维度来自 PRD 设计，未经验证**  
   `src/inspector.py:67-75` 定义 `user_perspective / knowledge_completeness / journey_fidelity / cross_phase_coherence`。这些维度合理，但：没有人类审查标签数据集、没有维度间相关性分析、没有证明它们能预测最终验收成功率。

### 反方案

1. **在 PRD/specs 增加“设计原理”附录**：把 Phase 顺序映射到 Incremental Commitment Spiral Model / V-Model / DORA 能力模型，并给出引用；把角色分离映射到 separation of duties / Belbin roles。
2. **用历史数据校准条件阈值**：收集项目级数据（代码行 vs 审查发现问题数、测试失败分布、预算 burn rate），用分位数回归或简单决策树确定触发深度审查/修复循环/暂停的阈值，而不是写死 500/3/80。
3. **真正落地 3 轮对抗 + 裁决**：接入 LLM backend 实现 `_agent_argue()` 与 `_arbitrate()`，并用人工审查标签评估 precision/recall/F1；否则不应在 features.json 中标记为 passed。
4. **本地对照实验**：至少运行单 Agent（Kimi）vs 2-Agent vs 4-Agent 的基线实验，测量相同任务集的成功率、token 成本、人工干预次数；用结果证明 dispatch 策略与模型路由的有效性。
5. **验证评估权重**：让人类专家对若干输出打分，计算各维度与总体评分的 Kendall τ 或 Pearson 相关性，必要时用项目特定权重替代全局权重。
6. **引入可证伪的假设清单**：每个“硬约束”“最佳实践”都应写成一个可测试假设（例如“异构审查比同构审查多发现 15% 的 P0”），并规定实验方式。

---

## 总体结论与行动优先级

### 核心结论
multi-agent-pipeline v3.0 在 **“把 3 个固定 Agent 跑通一条固定 Phase 链”** 这一有限场景下是成功的（28 features passed / 1206 tests PASS）。但在最坏假设下：
- **扩展性**：它不是 10-Agent 可插拔系统，而是 3-Agent 硬编码系统。
- **通用性**：greenfield/brownfield 双模式只做到了 config 里的两个列表，真正的 init、check、condition、suggestion 都没有模式感知。
- **智能化**：它把 Hermes 需要做的很多决策变成了“清单”，但没有形成自主决策闭环。
- **科学性**：dispatch、phase 顺序、约束规则、评分权重多为工程直觉与 PRD 教条，缺少文献映射、本地实验与阈值校准；部分核心设计（3 轮对抗）与代码实现不符。

### 建议优先级

| 优先级 | 行动 | 影响 | 工作量 |
|--------|------|------|--------|
| P0 | 统一 Agent/Task/Adapter 注册表，消除 `TASK_ADAPTER_MAP`、`VALID_TASK_TYPES`、`ADAPTER_REGISTRY` 中的硬编码 | 扩展性、通用性 | 中 |
| P0 | 让 `pipeline.py init`、PhaseFlow、SuggestionEngine、ConditionEngine 读取当前 workflow template，实现真正的 greenfield/brownfield 模式切换 | 通用性 | 中 |
| P1 | 把 suggestion_engine 从“建议模式”升级为“带确认的执行模式”，并引入语义化 Phase check | 减轻 Hermes 负担 | 高 |
| P1 | 接入真实 LLM 实现 3 轮对抗 + 裁决，或在 features.json 中诚实标注为 partial | 科学依据 | 中 |
| P2 | 建立本地基线实验与阈值校准流程，为 dispatch、phase 顺序、条件阈值、评估权重提供可验证依据 | 科学依据 | 高 |
| P2 | 在 PRD/specs 中补充设计原理与文献引用，把“工程直觉”转化为“可证伪假设” | 科学依据 | 低 |

---

*报告完成。文件位置：C:/tmp/multi-agent-pipeline/docs/review-adversarial.md*
