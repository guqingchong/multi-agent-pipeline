# 对抗审查 2/3：智能化程度审查

> **审查日期**：2026-07-02  
> **审查范围**：`src/system_constraint.py`、`src/suggestion_engine.py`、`src/evaluate.py`、`src/gate.py`、`src/approval.py`、`src/phase_checks.py`、`src/phase_flow.py`、`src/config.py`  
> **审查视角**：最坏假设（hostile review）—— 把代码中自称“自动”“智能”“Judge”的部分当成广告词，逐行检验其决策逻辑到底是“基于证据的推理”还是“硬编码查表/计数”。  
> **基准事实**：progress.md 记录 28/28 features passed、1206/1206 tests PASS；本报告默认单元测试不能证明“智能化”，只能证明“分支覆盖”。

---

## 摘要

| 审查对象 | 代码声称 | 实际实现的决策机制 | 智能化评级 | 风险 |
|---|---|---|---|---|
| `system_constraint.route_task` | “自动约束任务路由” | `task_type → adapter` 硬编码字典 + 请求代理名校验 | 🔴 伪智能 | 高 |
| `suggestion_engine` | “生成建议等待用户确认” | `run_check().passed` 布尔判断 + 字符串 `split(" | ")` | 🔴 计数器 | 高 |
| `evaluate.py` | “LLM-as-Judge Evidence-First” | 规则化证据收集 + 计数加减分 + 无真实 LLM 调用（默认 fallback） | 🟡 半智能/易误导 | 中高 |
| `gate.py` | “4-level quality gate engine” | 文件存在性、正则扫描、阈值启发式；几乎不执行测试 | 🟡 形式门禁 | 中高 |
| `approval.py` | “Human-in-the-loop approval system” | 人类审批/超时自动放行/ blanket 自动通过；无内容风险评估 | 🟡 流程框架 | 中 |
| `config.py` Phase 顺序 | “双模式支持” | greenfield/brownfield 两个写死列表，无科学依据与反馈校准 | 🔴 教条式顺序 | 高 |

**核心结论**：本系统在当前代码层面实现的“智能”主要是**硬编码映射、存在性检查、计数器和超时器**。它能在 happy path 上给出看似合理的建议，但不具备动态调度、语义质量判断、自适应阈值或从反馈中学习的能力。

---

## 1. `system_constraint.route_task`：只是查表，不是路由

### 1.1 最坏假设
用户说“帮我 review 这段代码”，系统能否像真正的调度器一样，根据当前各 Agent 的负载、成本、历史准确率、任务复杂度，选择最合适的审查 Agent？

### 1.2 实际实现
```python
# src/system_constraint.py:98-110
TASK_ADAPTER_MAP: Dict[TaskType, str] = {
    TaskType.CODE: ADAPTER_CLAUDE,
    TaskType.REVIEW: ADAPTER_CODEWHALE,
    TaskType.TEST: ADAPTER_QWEN,
    TaskType.DOC: ADAPTER_QWEN,
    TaskType.E2E: ADAPTER_QWEN,
    TaskType.INSPECTOR: ADAPTER_QWEN,
    TaskType.ADVERSARIAL: ADAPTER_CLAUDE,
    TaskType.ORCHESTRATE: "",
    TaskType.DEPLOY: "",
    TaskType.ANALYZE: "",
}
```
`route_task()`（`src/system_constraint.py:215-295`）的逻辑是：
1. 把字符串转成 `TaskType` 枚举；
2. 从 `TASK_ADAPTER_MAP` 取目标 adapter；
3. 如果调用方传了 `requested_agent`，校验它是否等于字典里的值；
4. 紧急模式检查；
5. 返回路由结果。

这里没有任何：
- **负载感知**：不知道 `claude-code` 当前是否忙、队列多长；
- **成本感知**：不会比较 3 个 adapter 的 token 价格；
- **质量感知**：不会根据历史成功率选择“今天状态更好”的模型；
- **任务复杂度感知**：不会把大任务拆分或路由给 specialist；
- **能力推理**：`ADAPTER_CAPABILITIES`（`src/system_constraint.py:113-117`）只支持 3 个 adapter，反向查询也是硬编码。

### 1.3 Hermes 权限检查：关键词黑名单，容易被绕过
```python
# src/system_constraint.py:318-323
forbidden_actions = {
    "code", "write", "implement", "develop", "program",
    "review", "audit", "inspect", "check_code",
    "test", "run_test", "e2e_test", "playwright",
    "doc", "document", "generate_doc",
}
```
检查方式：`action_lower in forbidden_actions` 或子串匹配（`src/system_constraint.py:341-352`）。这属于**字符串黑名单**，既无法处理同义词（如 “craft implementation”），也无法理解复合语境（如 “review the orchestration plan” 里的 review 不一定违规）。同义改写即可绕过。

### 1.4 结论
`SystemConstraint` 实现的是**静态 RBAC + 字典路由**，与“智能调度”相距甚远。把它叫成“自动约束任务路由”是一种能力透支。

---

## 2. `suggestion_engine`：只是数 `passed` 数量

### 2.1 最坏假设
系统能否在复杂项目状态下，综合分析多 phase 阻塞、资源约束、历史失败模式，给出“下一步最该做什么”的明智建议？

### 2.2 实际实现
核心函数 `suggest_next_phase()`（`src/suggestion_engine.py:138-211`）几乎等价于：
```python
complete, check_details = self.check_phase_complete(state)
if not complete:
    blockers = self.check_blockers(state)
    return BLOCKER(blockers)
return ADVANCE(next_phase)
```

- `check_phase_complete()`（`src/suggestion_engine.py:217-243`）直接转发 `run_check()` 的 `passed` 布尔值；
- `check_blockers()`（`src/suggestion_engine.py:249-277`）只是把 `result["reason"]` 按 `" | "` 切分；
- `get_next_phase()`（`src/suggestion_engine.py:283-307`）就是 `PHASE_ORDER.index(phase) + 1`。

### 2.3 约束检查同样形式化
```python
# src/suggestion_engine.py:382-390
phase_task_map = {
    "init": TaskType.ORCHESTRATE,
    "design": TaskType.ANALYZE,
    "decompose": TaskType.ANALYZE,
    "develop": TaskType.CODE,
    "test": TaskType.TEST,
    "accept": TaskType.REVIEW,
    "deploy": TaskType.DEPLOY,
}
```
`_check_constraints()` 只检查“该 phase 有没有对应 adapter”，不检查：
- 该 adapter 是否在线；
- 任务规格是否合法；
- 当前状态是否允许委派；
- brownfield 的 `discover/benchmark/...` 等 phase 完全不在 map 中，直接跳过。

### 2.4 没有学习、没有不确定性、没有优先级
- 不会记录“上次 design 阶段因为 acceptance_criteria 缺失阻塞了 3 次”，因此无法建议“先补 acceptance criteria”；
- 不会区分阻塞项的优先级：所有 blocker 平铺成字符串列表；
- 不会给出置信度或替代路径；
- 不会结合 `budget_guard` 的剩余预算调整建议。

### 2.5 结论
`SuggestionEngine` 是一个**状态机包装器 + reason 字符串拆分器**。称其“智能化”属于误导。

---

## 3. `evaluate.py`：Evidence-First 是真的，Judge 是假的

### 3.1 最坏假设
系统能否像一名独立的质量评审员，自动阅读代码、文档、测试结果，判断项目输出的真实质量？

### 3.2 EvidenceCollector：规则化、表层、无 LLM
`EvidenceCollector.collect_all()`（`src/evaluate.py:484-498`）跑 5 类收集器，但全部是确定性正则/文件统计：
- 静态分析：`py_compile` 语法检查 + 正则检查 imports + 模块 docstring 是否存在；
- 测试结果：数 `test_*.py` 文件和 `def test_` 数量，**不运行 pytest**；
- Lint： trailing whitespace、行长度 >150、空白行空格数；
- Spec 一致性：数 specs/ 下文件数量；
- 代码审查：bare except、TODO/FIXME 数量、函数行数 >100、`eval/exec` 调用；
- 依赖审计：requirements 文件里带 `==`/`>=` 的数量。

这些都是**间接指标**。一段代码可以没有语法错误、没有 trailing whitespace、函数 80 行，却完全实现错误。

### 3.3 LLMJudge：默认走规则 fallback，不是 LLM
```python
# src/evaluate.py:929-962
dimensions = self._score_dimensions(evidence, project_output)  # 规则打分
lie_findings = self._detect_lies(evidence, project_output)     # 正则
```
`_score_dimensions()`（`src/evaluate.py:964-1065`）的算法：
```python
score = 8.0
score -= len(criticals) * 2.0
score -= len(errors) * 0.5
score -= len(warnings) * 0.2
score += min(len(infos), 3) * 0.1
score = max(1.0, min(10.0, score))
```
权重 `8.0 / 2.0 / 0.5 / 0.2 / 0.1` 没有任何文献或本地校准支持。红线条目：
```python
RED_LINE_HONESTY_THRESHOLD = 5.0
RED_LINE_ACCURACY_THRESHOLD = 4.0
```
这两个阈值同样是拍脑袋。

### 3.4 LieDetection 存在严重缺陷
```python
# src/evaluate.py:1136-1139
def _find_file_in_project(self, filename: str) -> bool:
    return False  # Default: assume not found without project dir context
```
`_detect_lies()` 用正则从 `project_output` 中提取提到的文件、测试数、库版本，然后调用 `_find_file_in_project()` 判断文件是否存在。**该函数恒返回 `False`**。这意味着：只要输出中提到任何 `.py`/`.md` 文件，都会被系统标记为“可能编造”。这是一个系统性的假阳性 bug。

### 3.5 结论
`evaluate.py` 的“Judge”在默认路径下是**基于计数和正则的规则引擎**，不是 LLM。它收集的 evidence 是浅层代理指标，评分公式和红线条款未经校准，lie detection 还有功能缺陷。它不能可靠地判断代码质量。

---

## 4. `gate.py`：形式门禁，不是质量门禁

### 4.1 最坏假设
四层门禁能否在代码进入仓库前，真实拦截低质量、高风险、与 spec 不一致的产出？

### 4.2 各层检查内容
- **POST_GEN**（`src/gate.py:778-797`）：AI origin marker、硬编码 secrets 正则、危险模式正则（eval/exec/os.system）、路径穿越正则。这些是必要但不充分的**安全检查**。
- **COMMIT**（`src/gate.py:800-818`）：
  - `_check_feature_lint`：trailing whitespace、长行、空白行空格；
  - `_check_feature_tests`：**检查测试文件命名和 `def test_` 是否存在**，不执行测试；
  - `_check_no_hermes_code`：正则扫描内部关键词；
  - `_check_phase_requirements`：文件存在性。
- **PUSH**（`src/gate.py:821-841`）：
  - `_check_integrate`：import 语句与本地文件匹配；
  - `_check_spec_conformance`：用正则从 `architecture.md` 提取 module 名，再检查 src/ 下是否有同名文件；
  - `_check_evaluate`：代码/测试行数比例、docstring 数量；
  - `_check_inspector_review`：检查 `REVIEW*` 文件或 checkpoint 表里 phase 含 review；
  - `_check_all_features_passed`：读 `features.json` 的 `status` 字段；
  - `_check_no_p0_remaining`：扫描 `P0/FIXME/HACK/CRITICAL` 字符串。

### 4.3 关键缺失
- **不运行实际测试**：`pytest` 未被调用；
- **不检查 spec 语义**：只检查 module 名是否存在；
- **不评估架构合理性**：只检查文件存在；
- **不评估 LLM 输出真伪**：依赖 `evaluate.py` 的缺陷实现；
- **CI 层与 PUSH 层相同**（`src/gate.py:844-860`），没有更全面的检查。

### 4.4 结论
`gate.py` 实现的是**流程合规性检查 + 正则安全扫描**。它能拦住明显的危险模式和文件缺失，但无法判断“这个功能是否正确实现了需求”。称其为“quality gate engine”过于夸大。

---

## 5. `approval.py`：审批流程框架，不会自动判断风险

### 5.1 最坏假设
系统能否根据操作内容、项目状态、预算、历史风险，自动决定需要人工审批还是自动放行？

### 5.2 实际实现
- `BlockingApproval` / `AsyncApproval` / `AutoApproval` 只是三种**超时策略**（30 分钟 / 2 小时 / 5 分钟），然后保存状态；
- `AutoApproval.check_and_timeout()`（`src/approval.py:465-482`）在超时后自动把状态改为 `AUTO_PASSED`，**没有内容判断**；
- `ApprovalSystem` 的 `BLANKET` 模式（`src/approval.py:576-697`）只要用户授权一次，后续所有操作都自动通过；
- `generate_summary()`（`src/approval.py:90-136`）根据传入的 `risk` 字符串和 `cost` 生成模板化摘要，推荐语完全由风险等级决定，没有分析操作本身；
- `request()` 不会自动选择 `ApprovalLevel`，需要调用方显式传入。

### 5.3 结论
`approval.py` 是一个**状态机 + 超时器 + SQLite 持久化**。它能执行“30 分钟后保存状态”“2 小时后跳过”等策略，但**不会自动判断操作是否危险**。风险等级、成本、审批级别都来自外部输入，系统本身不做推理。

---

## 6. Phase 顺序有没有科学依据？

### 6.1 greenfield 顺序
```python
# src/config.py:50-54
"greenfield": {
    "phases": ["init", "design", "decompose", "research", "prd", "journey",
                "develop", "integrate", "test", "evaluate", "accept", "deploy"],
}
```
这个顺序存在明显可质疑之处：
- 先做 `design`，再 `decompose`，然后才 `research`？通常 research 应在 design 之前支撑架构决策；
- `prd` 在 `research` 之后，但 `journey` 又在 `prd` 之后，没有解释为何不是 `prd → journey → design`；
- 12 个 phase 是**写死列表**，没有引用任何软件生命周期模型（V-Model、Incremental Commitment Spiral、DORA 能力模型等），也没有本地实验数据支持。

### 6.2 brownfield 顺序
```python
# src/config.py:57-59
"brownfield": {
    "phases": ["discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"],
}
```
同样是一个列表，没有解释为什么不是 `benchmark → discover → analyze` 或其他顺序，没有从任何优化方法论（如 DMAIC、Theory of Constraints）映射。

### 6.3 Phase check 本身也不深入
- `check_research`（`src/phase_checks.py:968-995`）：只要 `docs/` 下有 `research*.md` 就 pass；
- `check_prd`（`src/phase_checks.py:1002-1013`）：只要 `PRD*.md` 存在就 pass，再跑一个 1 轮对抗（未实现 3 轮）；
- `check_journey`（`src/phase_checks.py:1020-1040`）：只要 `*journey*.md` 存在就 pass；
- `check_evaluate`（`src/phase_checks.py:1063-1089`）：只要 `tests/e2e/test_*.py` 存在就 pass，再调用有缺陷的 `evaluate()`。

这些 check 无法验证每个 phase 的**内容质量**，只能验证**文件存在**。因此即使 Phase 顺序本身有科学依据，执行层也无法保证每个 phase 真正产出合格交付物。

### 6.4 没有自适应与反馈
- `PhaseFlow.advance()`（`src/phase_flow.py:126-152`）只会顺序推进；
- 除了人工 `rollback`，没有基于检查结果的自动循环（如 test 失败后自动插入 fix_loop）；
- `condition_engine.py` 虽然能插入 phase，但插入位置写死在绿色field顺序中（见 review-adversarial.md 第 5 节），不是从当前模板动态推导。

### 6.5 结论
Phase 顺序是**工程直觉 + PRD 教条**，没有文献映射、没有对照实验、没有反馈校准。12 phase 的合理性无法被当前代码验证。

---

## 7. 综合判断：当前系统的“智能”到底是什么？

### 7.1 它做到了什么
- 用一个统一状态机和注册表把各 phase 的检查串起来；
- 用正则和文件存在性检查实现了最低限度的质量护栏；
- 用超时和 SQLite 实现了审批生命周期；
- 用硬编码映射实现了 3 个 adapter 的任务分发。

这些是有价值的**流程自动化**，但不是“智能决策”。

### 7.2 它没有做到什么
| 真正智能化能力 | 当前状态 |
|---|---|
| 基于实时负载/成本/质量的动态调度 | ❌ 只有硬编码字典 |
| 语义层面的代码/文档质量判断 | ❌ 只有正则和文件统计 |
| 运行真实测试并自动判定是否通过 | ❌ `mark-tests` 需要人工打标 |
| 从项目历史中学习并优化阈值 | ❌ 阈值全部写死 |
| 自动选择审批级别并解释风险 | ❌ 级别和风险由调用方传入 |
| 根据反馈自适应调整 Phase 顺序 | ❌ 顺序写死 |
| 不确定性/置信度量化 | ❌ 所有判断都是布尔值 |

---

## 8. 反方案：如果要真正提升智能化

### 8.1 路由层：能力注册 + 多目标评分
- 把 `ADAPTER_REGISTRY`、`TASK_ADAPTER_MAP`、`VALID_TASK_TYPES` 合并成**统一 AgentRegistry**；
- 每个 Agent 声明 capabilities、成本、延迟、健康状态、历史成功率；
- `route_task()` 用加权评分函数或多臂老虎机动态选择，而不是字典查找。

### 8.2 建议层：语义检查 + 行动计划
- `check_phase_complete()` 不再只看 `passed`，而是返回**结构化根因**（需求缺失、测试失败、覆盖率不足、spec 不一致等）；
- `suggest_next_phase()` 根据阻塞根因推荐**具体动作**（如“补 F012 的 acceptance criteria”“运行 pytest”），并为每个动作给出置信度；
- 低风险动作（如运行测试）可自动执行，高风险动作（如部署）才请求确认。

### 8.3 评估层：真正调用 LLM + 指标校准
- 默认路径调用真实 LLM Judge，而不是规则 fallback；
- 修复 `_find_file_in_project()`，让 lie detection 不再系统性假阳性；
- 引入真实测试覆盖率、lint、类型检查作为 evidence；
- 用人工评分与 LLM 评分的相关性（Kendall τ / Pearson）校准权重和红线阈值。

### 8.4 门禁层：从形式检查到真实质量
- COMMIT gate 调用 `pytest --cov`；
- PUSH gate 调用 spec 一致性 LLM 审查、架构漂移检测、回归测试；
- CI gate 不再只是重复 PUSH，而是增加性能基准、安全扫描、依赖漏洞扫描。

### 8.5 审批层：风险模型驱动
- 根据操作类型、影响范围、预算消耗、历史失败率自动选择 `ApprovalLevel`；
- `generate_summary()` 分析操作内容，给出具体风险点，而不是只根据 `risk` 字符串模板化输出；
- 取消“超时自动通过”作为默认策略，改为“超时升级为阻塞并通知人类”。

### 8.6 Phase 顺序：可解释 + 可校准
- 在 PRD/specs 中增加“设计原理”附录，把 Phase 顺序映射到已知生命周期模型；
- 让 Phase 顺序由 `workflow_template` 驱动，支持项目级自定义；
- 建立反馈闭环：记录每个 phase 的实际耗时、返工次数、人工覆盖次数，用贝叶斯优化或简单 A/B 调整顺序和阈值。

---

## 9. 总体结论

multi-agent-pipeline v3.0 的“智能化”在代码层面主要是：
- **`system_constraint.route_task` → 硬编码查表；**
- **`suggestion_engine` → 数 `passed` 和切分 reason；**
- **`evaluate.py` → 规则化计数，不是真正的 LLM Judge；**
- **`gate.py` → 形式合规与正则安全扫描；**
- **`approval.py` → 人工审批 + 超时自动放行；**
- **Phase 顺序 → 写死列表，无科学依据。**

这些模块能够支撑一条固定、 happy-path 的流水线，并确保流程不偏离 PRD 定义。但在真实工程压力下，它们无法自主判断质量、无法自适应调度、无法从经验中学习。如果把这些模块当成“智能核心”，会让 Hermes 和用户误以为系统在做高质量决策，实际上关键判断仍然依赖人工 flag（`design_approved`、`accept_approved`、`tests_passed`、`features.json status`）。

**智能化程度审查结论：未通过。** 当前实现距离“智能”还有至少一个数量级的差距，需要在路由、评估、门禁、审批、Phase 规划五个层面引入真正的推理、反馈与校准机制。

---

*报告完成。文件位置：C:/tmp/multi-agent-pipeline/docs/review-adversarial-2.md*

