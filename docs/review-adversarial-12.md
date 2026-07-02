# 对抗性审查报告 #12：扩展性与智能化

> **审查日期**：2026-07-02  
> **审查范围**：`src/adapters.py`、`src/system_constraint.py`、`src/suggestion_engine.py`、`src/message_queue.py`、`src/pipeline_executor.py`、`src/evaluate.py`、`src/gate.py`、`src/approval.py`、`src/phase_checks.py`  
> **审查视角**：hostile review —— 把当前实现当成“刚好跑通 3-Agent demo 的纸面架构”，在“3 → 10 Agent”和“自动判定质量”两个真实压力下检验其脆性。

---

## 摘要

| 维度 | 核心结论 | 风险等级 |
|------|----------|----------|
| 3 → 10 Agents 扩展性 | **不会瞬间崩溃，但会在注册表、任务类型白名单、端点配置、命名一致性、降级链五处硬阻塞**；每新增一个 Agent 必须改多处源码。 | 🟠 高 |
| “智能化”含金量 | `system_constraint` 是死映射，`suggestion_engine` 是 `passed` 布尔转发；**没有基于能力、负载、历史表现、成本的动态决策**，离真正的智能调度还差一个策略引擎。 | 🟡 中高 |
| 自动质量判定 | `evaluate` / `gate` / `approval` 基本靠**正则启发式、文件存在性、状态标志位**；没有真正调用 LLM Judge，也没有执行测试或做语义审查，**不能可靠地自动判定质量**。 | 🟠 高 |

---

## 挑战 1：Agent 从 3 个扩展到 10 个，TASK_ADAPTER_MAP / 端点注册 / message_queue 会不会崩？

### 最坏假设
在不修改源码的前提下，新增 7 个专业 Agent（如安全审计、UI/UX、DevOps、数据、需求澄清、回归测试、架构治理）。系统能否平滑接入它们？

### 核心结论
**不会“崩”，但会被多处硬编码注册表“拒之门外”，事实不可扩展。** 当前不是“Agent 可插拔”的开放平台，而是“3 个固定角色 + 若干写死任务类型”的闭合系统。

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
   `create_adapter()` 在 `src/adapters.py:1674-1675` 对未知 adapter 直接抛 `ValueError`。任何第 4 个 Agent 必须改源码。

2. **CLI 端点列表同样是 3 个写死**  
   `src/pipeline_executor.py:197-223`：
   ```python
   DEFAULT_ENDPOINTS: List[CLIEndpoint] = [
       CLIEndpoint(adapter_name="claude-code", cli_path=_resolve_cli_path("claude"), ...),
       CLIEndpoint(adapter_name="codewhale", cli_path=_resolve_cli_path("codewhale-tui"), ...),
       CLIEndpoint(adapter_name="qwen-code", cli_path=_resolve_cli_path("qwen"), ...),
   ]
   ```
   `dispatch()` 在 `src/pipeline_executor.py:326-330` 检查 `adapter_name not in self._cli_endpoints` 即抛错。新增 Agent 必须同时加端点。

3. **命名体系不统一，已经出现“同名不同指”**  
   - `adapters.py` 用 `"claude"`、`"qwen"`、`"codewhale"`；  
   - `system_constraint.py` 用 `"claude-code"`、`"qwen-code"`、`"codewhale"`；  
   - `pipeline_executor.py` 端点用 `"claude-code"`、`"qwen-code"`、`"codewhale"`。  
   两套名字如果不经过显式映射，直接混用会导致路由失败。一个 10-Agent 系统如果沿用这种命名习惯，维护成本会指数级放大。

4. **任务类型白名单分散且互相不一致**  
   - `src/message_queue.py:36`：
     ```python
     VALID_TASK_TYPES = ("code", "review", "test", "shutdown", "inspector", "adversarial", "doc", "e2e")
     ```
     并在 `src/message_queue.py:193` 拒绝未注册类型。  
   - `src/system_constraint.py:69-81`：`TaskType` 枚举是封闭集合，没有动态扩展入口。  
   - `src/agent_daemon.py` 里的 `VALID_TASK_TYPES`  reportedly 只有 4 个（`code`, `review`, `test`, `shutdown`），比 MQ 还少。  
   新增 Agent 时，必须同时改 MQ、daemon、constraint 三个白名单，否则任务连队列都进不去。

5. **SystemConstraint 是“任务 → 角色”死映射，没有基于能力的匹配**  
   `src/system_constraint.py:98-118`：
   ```python
   TASK_ADAPTER_MAP: Dict[TaskType, str] = {
       TaskType.CODE: ADAPTER_CLAUDE,
       TaskType.REVIEW: ADAPTER_CODEWHALE,
       TaskType.TEST: ADAPTER_QWEN,
       TaskType.DOC: ADAPTER_QWEN,
       TaskType.E2E: ADAPTER_QWEN,
       TaskType.INSPECTOR: ADAPTER_QWEN,
       TaskType.ADVERSARIAL: ADAPTER_CLAUDE,
       ...
   }
   ADAPTER_CAPABILITIES: Dict[str, List[TaskType]] = {
       ADAPTER_CLAUDE: [TaskType.CODE],
       ADAPTER_CODEWHALE: [TaskType.REVIEW],
       ADAPTER_QWEN: [TaskType.TEST, TaskType.DOC, TaskType.E2E],
   }
   ```
   注意 `INSPECTOR`/`ADVERSARIAL` 在 `TASK_ADAPTER_MAP` 中有映射，但在 `ADAPTER_CAPABILITIES` 中却没声明；`route_task` 只查正向表，`can_adapter_execute` 查反向表时会给出不一致结果。

6. **降级链只认识 Claude → Qwen → CodeWhale**  
   `src/fallback_manager.py`（根据已有审查）的 `fallback_chain` 写死为 `["qwen", "codewhale"]`。新增 Agent 无法自动被纳入降级路径。

7. **MessageQueue 的 SQLite 并发不是主要瓶颈，但语义扩展是**  
   `src/message_queue.py:109-139` 使用 WAL + `threading.RLock` + `BEGIN IMMEDIATE` 原子拉取。10 个进程轻量任务不会把 SQLite 压垮，但：  
   - 所有 Agent 的任务混在同一张表，没有按能力/优先级分片；  
   - `target_agent` 是自由字符串，没有认证，任何知道 `agent_id` 的进程都能以该身份消费任务；  
   - 没有队列深度、消费延迟、死信队列的监控，扩展后难以定位“谁卡住了”。

### 反方案

1. **统一的 AgentRegistry 元数据驱动**：每个 Agent 通过配置文件/代码声明 `name`、`capabilities`、`task_types`、`cli_path`、`endpoint`、`cost_profile`、`fallback_chain`、`max_workers`、`health_endpoint`。所有硬编码 map 改为从注册表读取。
2. **开放任务类型注册**：`MessageQueue`、`AgentDaemon`、`SystemConstraint` 统一使用注册表的 `capabilities`，而不是各自维护 tuple/enum。
3. **能力评分路由**：`SystemConstraint.route_task` 不再返回唯一 adapter，而是返回候选列表；调度器根据当前负载、历史成功率、成本选择最优实例。
4. **队列分片或异步 Broker 可插拔**：保留 SQLite 作为默认实现，但抽象出 `MessageBackend` 接口，允许替换为 Redis/RabbitMQ/Kafka；大型团队或 10+ Agent 时启用外部 Broker。
5. **自动扩缩容**：`WorkerPool` 根据 queue depth / 平均 latency 自动扩容某类 Agent 实例数，并在实例失活时自动重启。
6. **命名与版本治理**：所有模块统一使用 `agent_id`（如 `claude-code`），`adapters.py` 的工厂也改为按 `agent_id` 查找；引入 `aliases` 字段兼容旧名。
7. **服务发现与健康检查**：每个 Agent daemon 启动时向 registry 注册心跳；调度器只把任务派发给健康实例。

---

## 挑战 2：`system_constraint` 只是查表，`suggestion_engine` 只是数 passed——这算智能化吗？

### 最坏假设
把“智能化”定义为：系统能根据上下文（Agent 能力、当前负载、历史表现、任务紧急度、风险）自动做出合理调度/推进/拦截决策。当前实现满足这个定义吗？

### 核心结论
**不算。** 当前两层都是“静态规则 + 布尔转发”，没有学习、没有推理、没有优化目标。它们能防止最明显的越权，但不能显著减轻 Hermes 的决策负担。

### 论据

1. **SystemConstraint：纯查表 + 关键词匹配**  
   - 路由：`route_task()`（`src/system_constraint.py:215-295`）唯一动作是 `TASK_ADAPTER_MAP.get(tt)`，然后校验 `requested_agent` 是否等于这个写死的值。  
   - Hermes 权限：`hermes_only_orchestration()`（`src/system_constraint.py:301-370`）维护两个关键词集合：
     ```python
     allowed_actions = {"orchestrate", "route", "delegate", ...}
     forbidden_actions = {"code", "write", "review", "test", ...}
     ```
     判断逻辑是“字符串是否在集合中”或“字符串是否包含禁用词”。这种实现会误伤（如 `coordinate_code_review` 包含 `code` 被禁）也会漏过（如 `implement_via_orchestrate` 包含 `orchestrate` 被放行）。  
   - 没有上下文：不看 Agent 是否在线、队列是否积压、历史成功率、成本预算。

2. **ADAPTER_CAPABILITIES 残缺且没有优先级**  
   `src/system_constraint.py:113-117` 只列出 3 个 adapter 的 6 项能力；`INSPECTOR`、`ADVERSARIAL`、`ANALYZE`、`DEPLOY` 等任务类型都没有反向声明。系统无法回答“哪个 Agent 最适合做这件事”，只能回答“这件事应该给谁”。

3. **SuggestionEngine：把 `passed` 当决策**  
   - `check_phase_complete()`（`src/suggestion_engine.py:217-243`）直接返回 `run_check(...).get("passed")`：
     ```python
     result = run_check(current_phase, self.project_name, self.base_dir)
     is_complete = result.get("passed", False)
     ```
     没有分析“为什么没过”“哪些阻塞项是硬阻塞、哪些是警告”“修复代价多大”。  
   - `check_blockers()`（`src/suggestion_engine.py:249-277`）把 `reason` 字符串按 `" | "` split，再拼接 `_check_state_blockers()` 里写死的 3 条状态检查。本质上是“把失败原因列出来”，而不是“判断阻塞路径”。  

4. **审批触发条件极其粗糙**  
   `src/suggestion_engine.py:365-373`：
   ```python
   def _requires_approval(self, phase: str, state: Dict[str, Any]) -> bool:
       if phase == "design":
           return not state.get("design_approved", False)
       if phase == "accept":
           return not state.get("accept_approved", False)
       return False
   ```
   只有 design/accept 两个阶段需要审批；风险、成本、影响范围都不参与决策。

5. **约束检查只验证“有没有映射”，不验证“能不能跑”**  
   `src/suggestion_engine.py:375-397`：
   ```python
   phase_task_map = {
       "init": TaskType.ORCHESTRATE,
       "design": TaskType.ANALYZE,
       ...
   }
   target_agent = self.constraint.get_agent_for_task(task_type.value)
   if target_agent is None:
       return False, f"phase '{phase}' 没有对应的路由 Agent"
   ```
   它不关心该 Agent 是否健康、队列是否已满、该任务是否需要高优先级。

6. **没有反馈闭环**  
   系统没有记录“上一次把任务派给 A Agent 花了多久、质量如何、成本多少”，因此无法调整未来调度策略。

### 反方案

1. **把 SystemConstraint 升级为策略引擎**：使用 Rego / CEL / 自定义 DSL 表达路由规则，规则可读取 `agent_health`、`queue_depth`、`cost_budget`、`historical_accuracy` 等上下文。
2. **能力模型化**：每个 Agent 声明多维能力向量（`code_quality`、`security_review`、`test_coverage`、`doc_quality`、`cost_per_1k_tokens` 等），调度问题变成带约束的优化问题。
3. **SuggestionEngine 引入阻塞分类与关键路径分析**：把 `run_check` 的结果拆分为 `HARD_BLOCKER`、`SOFT_BLOCKER`、`WARNING`、`INFO`；结合 feature 依赖图判断哪些阻塞项位于关键路径上，哪些可以并行处理。
4. **预测式建议**：基于历史数据估计“完成当前 phase 还需多少时间/成本”，给出 `advance now` / `wait` / `rollback` / `escalate` 等带置信区间的建议。
5. **风险驱动的审批升级**：审批级别不由 phase 唯一决定，而是由操作风险评分（涉及生产环境、修改核心模块、高成本 API 调用、陌生代码路径）动态决定。
6. **人在回路反馈学习**：记录 Hermes 对每次建议的采纳/忽略/修正，定期微调调度权重。

---

## 挑战 3：`evaluate` / `gate` / `approval` 真能自动判断质量吗？

### 最坏假设
让系统自主判定一次交付是否“质量好到可以进入下一阶段”。它能否像人类 reviewer 一样发现语义错误、设计缺陷、测试不足、幻觉引用、安全风险？

### 核心结论
**当前不能。** 三个模块都停留在“存在性检查 + 正则匹配 + 状态标志”层面，没有真正执行测试、没有做语义审查、没有调用 LLM Judge；在真实场景下会大量漏报和误报。

### 论据

#### A. `evaluate.py`：LLM-as-Judge 还停留在“数据结构 + 启发式算术”

1. **EvidenceCollector 是规则收集器，不是证据推理器**  
   `src/evaluate.py:464-495` 收集：语法是否通过 `py_compile`、导入是否可解析、测试文件数量、是否有 docstring、是否有 trailing whitespace、是否有 `eval()`、依赖是否 pinned 等。  
   这些是有价值的**必要检查**，但远远不够：它不看函数逻辑正确性、不看测试是否真正覆盖分支、不看需求是否实现。

2. **LLMJudge 默认走确定性 fallback，没有真正调用 LLM**  
   `src/evaluate.py:911-962` 的 `evaluate()` 调用 `_score_dimensions()`，而 `_score_dimensions()` 在 `src/evaluate.py:964-1066` 使用纯算术：
   ```python
   score = 8.0
   score -= len(criticals) * 2.0
   score -= len(errors) * 0.5
   score -= len(warnings) * 0.2
   score += min(len(infos), 3) * 0.1
   ```
   `judge_model` 只是标签，代码里没有发起任何 LLM API 调用。所谓“不同模型评分避免自评偏差”并未落地。

3. **谎言检测会误杀正常引用**  
   `src/evaluate.py:1067-1147` 通过正则查找输出中引用的文件：
   ```python
   mentioned_files = re.findall(r'`([^`]+\.(?:py|md|json|yaml|toml))`', project_output)
   for mf in mentioned_files:
       if not (self._find_file_in_project(mf) or self._find_file_in_evidence(mf, evidence)):
           findings.append(LieFinding(...))
   ```
   而 `_find_file_in_project()` 在 `src/evaluate.py:1136-1139` 直接返回 `False`：
   ```python
   def _find_file_in_project(self, filename: str) -> bool:
       return False  # Default: assume not found without project dir context
   ```
   这意味着**任何带文件名的引用都会被判定为疑似谎言**。

4. **Red-line 阈值是拍脑袋的**  
   `src/evaluate.py:892-896`：
   ```python
   RED_LINE_HONESTY_THRESHOLD: float = 5.0
   RED_LINE_ACCURACY_THRESHOLD: float = 4.0
   ```
   没有基于历史数据校准，也没有解释为什么 honesty < 5 就一定 BLOCK。

#### B. `gate.py`：门禁更像是“静态扫描脚本合集”

1. **Level 0-3 都依赖正则和文件存在性**  
   - `POST_GEN`：检查 AI-origin 注释、硬编码密钥正则、`eval()` / `shell=True` 正则、路径遍历正则。  
   - `COMMIT`：检查 trailing whitespace、长行、测试文件存在性、`features.json` 格式。  
   - `PUSH`：检查内部导入是否可解析、`architecture.md` 提到的模块是否存在、features 是否全部 `passed`、是否有 REVIEW 文件。  
   这些能拦住低级错误，但**无法判断代码是否满足需求、测试是否有效、架构是否合理**。

2. **`CheckAllFeaturesPassed` 只看 status 字符串**  
   `src/gate.py:687-724`：
   ```python
   for feat in features:
       status = feat.get("status", "unknown")
       if status != "passed":
           not_passed.append(...)
   ```
   feature 的 `status` 是人工或流程设置的字符串，系统没有独立验证每个 acceptance_criteria 是否真正被满足。

3. **不执行测试**  
   `gate.py` 里没有任何地方真正运行 `pytest` 或解析 JUnit/Coverage 报告。`_check_feature_tests` 只数 `def test_` 的数量。

#### C. `approval.py`：审批是状态机，不是质量判定

1. **审批只管理超时和状态**  
   `src/approval.py` 实现了 `PENDING / APPROVED / REJECTED / EXPIRED / AUTO_PASSED / SKIPPED` 状态机和三种超时策略。  
   但它**不审查申请内容**：不检查 diff、不评估风险、不引用 gate/evaluate 结果。

2. **AutoApproval 5 分钟后自动放行**  
   `src/approval.py:438-494`：只要没有人工拒绝，5 分钟后自动变成 `AUTO_PASSED`。这意味着高风险操作如果 5 分钟内没人看，就会默认通过。

3. **Blanket mode 一键放行所有后续操作**  `src/approval.py:597-599` 的 `authorize_blanket()` 开启后，所有后续请求自动 `AUTO_PASSED`。没有按操作类型、风险、成本再做细分授权。

### 反方案

1. **真正的 LLM-as-Judge**：`LLMJudge.evaluate()` 默认调用外部 Judge LLM，传入 evidence bundle 和 rubric；确定性 fallback 仅用于离线测试或 LLM 不可用时。
2. **AST 与专业 linter 接入**：集成 `ruff`、`bandit`、`mypy`、`pylint`、`semgrep`，把真实静态分析结果纳入 evidence，而不是手写正则。
3. **测试真实执行与覆盖率解析**：运行 `pytest --junitxml --cov`，解析测试通过率、覆盖率、最慢用例，把结果写入 evidence；gate 的 `CheckFeatureTests` 应检查实际通过的 assertions。
4. **谎言/幻觉 grounding**：把项目输出中的每个声明（文件、函数、版本、指标）与代码库、依赖清单、测试报告进行检索对比；必要时调用搜索/文件系统 API，而不是正则 + `return False`。
5. **多 Judge ensemble 与分歧仲裁**：至少两个不同模型独立评分；差异大时自动触发人工仲裁，而不是取平均。
6. **审批内容化**：审批请求必须附带 gate 结果、evaluate 评分、变更 diff、预估成本、风险评估；`BlockingApproval` 不应在没有看到这些材料的情况下进入等待。
7. **阈值校准与可解释性**：Red-line 阈值应从历史 P0/P1 事件中学习，并在报告里给出“为什么这个分数会 BLOCK”的逐条解释。
8. **审批分级授权**：Blanket mode 应支持按操作类型、风险等级、成本上限授权；高风险操作永远走 Granular。

---

## 结论

当前 multi-agent-pipeline 在“3-Agent 固定剧本”下可以稳定运行，但其扩展性与智能化存在明显天花板：

- **扩展性**：从 3 → 10 Agent 不会在 SQLite 层面立刻崩掉，但注册表、任务类型、端点配置、命名一致性、降级链都会成为硬阻塞；系统需要一次以“Agent Registry + 能力模型 + 可插拔后端”为核心的重构。
- **智能化**：`system_constraint` 和 `suggestion_engine` 目前只是静态规则与布尔转发，尚未达到“根据上下文自主决策”的智能化水平；建议引入策略 DSL、阻塞分类、关键路径分析与预测式建议。
- **自动质量判定**：`evaluate` / `gate` / `approval` 目前主要依赖存在性检查、正则启发式和状态标志，不能可靠地自动判定代码质量；必须接入真正的 LLM Judge、AST 工具链、测试执行与 grounding 机制，才能把“自动判定”从纸面落到实地。

一句话：**这不是“会不会崩”的问题，而是“每加一个 Agent、每做一次自动判定，都要人工改源码和猜阈值”的问题。**

