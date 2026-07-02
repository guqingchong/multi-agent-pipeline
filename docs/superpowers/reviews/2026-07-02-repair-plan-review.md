# Multi-Agent Pipeline 修复方案 — 审查意见

> 审查对象：`docs/superpowers/plans/2026-07-02-multi-agent-pipeline-repair-plan.md`
> 审查日期：2026-07-02
> 基于代码库分析：63 个 src 模块、37,793 行源码、42 个测试文件

---

## 一、总体评价

**综合评级：B+ → A-（良好偏优）**

方案核心理念（注册表单一真相源、队列合并、Phase 模型注册表驱动、Windows 原生定位）完全正确。架构四层划分（Entry → Orchestration → Dispatch & Queue → Persistence）清晰合理。8 个 Task 的依赖顺序和 commit 边界设计良好。

但存在 8 个具体问题，其中 P0（孤儿模块遗漏、observability.py 覆盖）和 P1（Phase enum 全局替换风险、brownfield Phase 不一致）在实施前必须修正。

---

## 二、必须关注的问题

### P0-1（已解决 — 本人撤回）: ~~22+ 孤儿模块完全被忽略~~

**更正**：7月1日已完成孤儿模块接入，状态如下：

| 状态 | 数量 | 模块 |
|------|------|------|
| 已接入 phase_checks | 11 | evaluate, gate, approval, inspector, adversarial, research_agent, journey_designer, architecture_review, workflow_registry, condition_engine, subtask_chunker |
| 已知 gap（patch 多匹配未接入） | 4 | delivery, github_sync, performance_optimizer, budget_guard |
| 独立工具（无需接入流程） | 5 | skill_injector, prompt_cache, prompt_cache_store 等 |
| 已接入（更早批） | 4 | 原 guardrails 清单中的 inspector, adversarial_review, evaluate, gate（更早接入，含 approval） |

**总计：24 个孤儿 → 处理完毕。**

审查文档中此条不成立，予以撤回。修复方案 Task 3.5（"孤儿模块清理"）不再需要。

---

### P0-2: observability.py 重复定义

方案 Task 8 说"新建 `src/observability.py`"，但**现有代码已有** `src/observability.py`（737 行，涵盖 Dashboard + AlertManager + Markdown 报告）。直接新建会**覆盖现有模块**，丢失所有仪表盘和告警功能。

**修正**：Task 8 改为"增强现有 `src/observability.py`，添加 `trace()` 函数和结构化 JSON 日志"。trace() 作为新方法，不删除现有代码。

---

### P1-1: `Phase` enum 删除 → 全项目 import 崩溃

`models.py.Phase` enum（8 个值，含 `REVIEW` 兼容别名）被至少 **15 个模块** 直接引用：

- pipeline.py：`Phase.INIT` / `Phase.DEVELOP` / `.next()` / `.value`
- phase_flow.py：`isinstance(state.phase, Phase)` 类型检查
- entry.py：`ProjectState.phase` 字段类型
- state_store.py：`Phase.from_name()` 序列化/反序列化
- bridge_cli.py：pipeline 命令代理
- phase_checks.py：check 函数中的 Phase 引用
- 以及 10+ 个测试文件

删除后改为 `from phase_model import Phase`，行为不兼容：

```python
# 旧代码（models.py enum）
state.phase == Phase.INIT       # → True
state.phase.value               # → 0
state.phase.next()              # → Phase.DEVELOP
isinstance(state.phase, Phase)  # → True

# 新代码（phase_model.py class）
state.phase == Phase("init")    # → True
state.phase.name                # → "init"
state.phase.next("greenfield")  # → Phase("design") or None
isinstance(state.phase, Phase)  # → 仍然 True（但语义不同）
```

**修正**：
1. Task 1 必须包含**全局查找替换脚本**（`rg "Phase\." src/ tests/` → 列出所有引用点）
2. 所有涉及模块必须在**同一个 Task 内**修改并通过测试，不能跨 Task
3. 建议增加 `Phase.is_init()` / `Phase.is_start()` 等便捷方法以兼容习惯用法
4. 测试修复量预估：至少 10 个测试文件需要更新

---

### P1-2: brownfield Phase 命名不一致

方案 Task 1 的 `build_workflows()` 设计了 **4 种 brownfield 子模式**：

```python
"brownfield_feature": ["prd_update", "research", "design", ...]
"brownfield_fix":      ["triage", "fix", "verify", "deploy"]
"brownfield_audit":    ["audit", "report", "review"]
```

但现有 `config.py` 只有**单一 7-phase brownfield**：

```python
"brownfield": ["discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"]
```

冲突点：
- `config.detect_mode()` 返回 `"brownfield"`，但找不到名为 `"brownfield"` 的 workflow template
- Phase 名 `prd_update` / `triage` / `audit` 在 REGISTRY（19 个 phase）中**不保证存在**
- 两种方案的设计理念不同：方案是"按任务类型分子模式"，现有是"统一 7-step 优化流程"

**修正**：先统一 brownfield 定义。建议保留现有 `config.py` 单一 7-phase brownfield（已实现且经测试），后续再渐进引入子模式。如果坚持合并两套，则必须：
1. 在 REGISTRY 中注册 `prd_update` / `triage` / `audit` 等新 phase
2. 在 phase_checks.py 中补充对应的 check 函数
3. 更新 `config.detect_mode()` 返回子模式标识

---

### P1-3: Queue 类 DDL SQL 注入风险

```python
"CHECK(task_type IN (" + ", ".join(f"'{t}'" for t in VALID_TASK_TYPES) + ")"
```

如果 task type 名称包含单引号，会导致 SQL 注入。在代码完全控制 task type 名称的情况下风险较低，但不符合安全最佳实践。

**修正**：在 REGISTRY 注册 task type 时加名称正则校验：

```python
import re
_TASK_TYPE_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]*$')

def register_task_type(self, task_def: TaskTypeDef) -> None:
    if not _TASK_TYPE_NAME_RE.match(task_def.name):
        raise ValueError(f"Invalid task type name: {task_def.name!r}")
    self._task_types[task_def.name] = task_def
```

---

### P2-1: debate 子系统完全缺位

`src/debate/` 4 个模块（session.py / context.py / protocols.py / convergence.py）在以下位置**完全未出现**：
- 目标架构图
- 全部 8 个 Task 的 Files 清单
- 11 个待修改文件的列表
- 6 个待删除文件的范围

但 debate 是 `bridge_cli.py` 的重要功能入口：

```bash
bridge_cli debate --session xxx --protocol SAMRE
```

**修正**：两个选择：
1. **标注本次不涉及**：在方案开头声明"debate 模块本次重构暂不涉及，保持现状"
2. **纳入架构图**：在目标架构中补充 debate 层（Orchestration Layer 的子层），并在 Task 3 中说明"debate 模块保持不变，后续独立优化"

---

### P2-2: main.py 的城策通残留处理过于暧昧

方案说"删除或禁用 mock 端点，若业务确实需要则移到独立 `src/api_*.py`"。

但 main.py 的以下三个路由域（共约 400 行）**与 multi-agent-pipeline 核心功能完全无关**：

```python
/finance/calculate   # 财务 NPV/IRR/ROI 计算（城策通业务逻辑）
/finance/budget      # 预算设置（城策通业务逻辑）
/knowledge/search    # 知识库搜索模拟
/knowledge/add       # 知识条目添加模拟
/documents/generate  # 文档生成模拟
/documents/template  # 模板列表模拟
/projects/create     # 项目创建（有模拟但可保留改造）
```

其中 `/projects/create` 可改造为真实端点，其余应**直接删除**。

**修正**：在 Task 4 Step 3 中明确：
1. 直接删除 `/finance/*`、`/knowledge/*`、`/documents/*` 路由
2. 保留并改造 `/projects/*`、`/health`、`/status`、`/agents`、`/queue/stats`
3. 删除对应的 pydantic 模型（FinancialInput / BudgetRequest / KnowledgeItem / DocumentRequest 等共约 200 行）

---

### P2-3: AGENT_MOCK 强制化会削弱测试价值

方案建议 `AGENT_MOCK=true` 作为测试默认值：

```python
if os.environ.get("AGENT_MOCK", "false").lower() == "true":
    return self._mock_run(task_type, payload)
```

这会导致 adapters.py 的**核心三层逻辑完全不被测试覆盖**：

| 层 | 行数 | 内容 | mock 下是否可测 |
|---|------|------|----------------|
| 适配层 | ~600 | Agent 启动/通信/CLI 调用 | **不可测**（跳过） |
| 解析层 | ~500 | 正则 + 启发式规则解析输出 | **不可测**（跳过） |
| 容错层 | ~400 | Timeout/崩溃/截断恢复 | **不可测**（跳过） |

实际上解析层和容错层**完全可以在 mock 下测试**——只需提供模拟的 CLI 输出文本即可。

**修正**：双层 mock 设计：
1. `AGENT_MOCK=true`：跳过真实 CLI 调用，但**保留解析层和容错层**的完整路径
2. `_mock_run()` 返回模拟的原始 CLI 输出，解析层和容错层正常工作
3. 仅在 `_run_real_cli()` 方法内检查 `AGENT_MOCK`，不在 `run()` 入口处就短路

---

## 三、正确且值得肯定的部分

1. **修复顺序合理**：Phase 模型 → 队列合并 → 编排层 → 入口层 → 持久化 → 适配器 → 文档 → 验收。依赖链清晰。

2. **truth-source 统一化具体可行**：`Phase` 类替代 enum、`Queue` 替代双队列、`build_workflows()` 替代硬编码。代码示例具体且有可操作性。

3. **Windows-only 定位明确**：PowerShell 启动脚本、env 环境变量、移除 Docker，与用户环境匹配。

4. **progress.md 重写为诚实状态**：正确识别当前"1206 测试 / 28 功能 / 100% 完成"是虚假数据，重写为真实的进行中状态。

5. **每个 Task 的 commit 边界清晰**：一个 Task 一个 commit，回滚粒度合理。

6. **CLI 路径移除硬编码**：registry.py 改为 `AGENT_CLI_PATH_*` 环境变量 + `shutil.which` fallback，解决了 Windows 用户名硬编码问题。

7. **thresholds.yaml 外置**：30+ 硬编码阈值集中管理。

---

## 四、修正建议汇总

| 优先级 | 问题 | 修正 |
|--------|------|------|
| ~~**P0**~~ | ~~22+ 孤儿模块未处理~~ | **撤回**——7月1日已完成，24个孤儿 → 15已接入 + 4 gap + 5独立工具 |
| **P0** | observability.py 重复定义 | Task 8 改为"增强现有 observability.py"，添加 trace() 而非新建 |
| **P1** | Phase enum 删除影响 15+ 模块 | Task 1 全局替换 + 同 Task 内全测试通过；增加 `is_init()` 等便捷方法 |
| **P1** | brownfield Phase 命名不一致 | 先统一用 config.py 的 7-phase brownfield，后续再扩展子模式 |
| **P1** | Queue DDL SQL 注入风险 | REGISTRY 注册时加 task_type 名称正则校验 `[a-z][a-z0-9_-]*` |
| **P2** | debate 系统零提及 | 补充架构图或在方案开头标注"debate 本次不涉及" |
| **P2** | main.py 城策通残留暧昧 | 明确删除全部 3 个无关路由域 + 对应 pydantic 模型 |
| **P2** | AGENT_MOCK 削弱测试 | mock 仅针对 subprocess 调用，保留适配层/解析层/容错层完整测试路径 |

---

## 五、建议的修订后 Task 结构

```
Task 0:   前置诊断（影响面评估 + 4 个已知 gap 补接入）            [修正]
Task 1:   注册表单真相源 + Phase 模型（含全局替换）                [扩大范围]
Task 2:   队列合并                                                  [不变]
Task 3:   编排层轻量化                                              [不变]
Task 4:   入口层统一 + 删除城策通残留 + Windows 脚本                [扩大范围]
Task 5:   状态持久层 + thresholds.yaml                              [不变]
Task 6:   Agent 适配器 + health check + 双层 mock                    [修正 mock]
Task 7:   文档重写                                                  [不变]
Task 8:   全局测试 + 可观测性增强 + 9 维度验收                      [修正 observability]
```

---

*审查完成。方案架构方向正确，建议在实施前按上述 P0/P1 项修正后执行。*
