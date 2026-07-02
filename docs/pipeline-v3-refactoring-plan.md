# multi-agent-pipeline v3.0 重构方案

## 一、问题回顾：为什么 v2.0 产出了毛坯房

### v2.0 架构总结

```
Phase 0: Init         → 创建项目骨架（文件 + git + DB）
Phase 1: Design       → 架构设计（structure.md，模块划分）
Phase 2: Decompose    → 任务分解（features.json）
Phase 3: Develop      → 逐个 feature 编码→审核→测试
Phase 4: Test         → 回归测试
Phase 5: Accept       → E2E 硬门禁 + 人类审批
Phase 6: Deploy       → 交付脚本
```

### v2.0 的四个致命缺陷

| 编号 | 缺陷 | 城策通v2.0中的表现 |
|------|------|-------------------|
| D1 | **毛坯房：只有骨架，无用户体验设计** | PRD定义了14个Feature，但没有定义"用户说第一句话到得到最终文档"的完整旅程。每个模块独立工作，拼在一起不工作。 |
| D2 | **天花板：质量上限 = Hermes认知上限** | LLM对话注入解析数据——这个需求不在任何spec里，因为我不懂用户上传PDF后期望AI直接引用数据。没有一个Agent有权提出来。 |
| D3 | **工具伪调用：依赖主动调用，无强制机制** | bridge_cli.py被验证能跑一次后从未被真实驱动。所有Phase推进靠手动管理状态。skill中反复警告"加载≠启动"但从未被强制执行。 |
| D4 | **E2E浅层：有输入输出，无质量判断** | Playwright测了"HTTP 200 + 响应不为空 + 无500"，但没有测"响应中的数据是否和PDF内容一致"。这是把存在性当正确性。 |

这四个缺陷不是独立的bug，是同一个系统性问题：**整个Pipeline是"功能交付导向"，不是"用户体验导向"**。

---

## 二、v3.0 重构目标

从"功能交付导向"转变为**"用户体验导向"**的流水线：

| 旧 (v2.0) | 新 (v3.0) |
|-----------|-----------|
| Phase设计是模块清单 | Phase设计是用户旅程 |
| 验收标准是"代码存在" | 验收标准是"用户能用" |
| Hermes是唯一决策者 | 独立审查Agent有权否决 |
| 工具靠主动调用 | 工具靠git hook被动触发 |
| E2E测存在性 | E2E测正确性(LLM-as-Judge) |

---

## 三、Phase 重新设计：从7个Phase变为9个Phase

```
Phase 0: INIT          ─ 项目骨架
Phase 1: DESIGN        ─ 架构设计
Phase 2: JOURNEY       ─ [NEW] 用户旅程设计（解决D1毛坯房）
Phase 3: DECOMPOSE     ─ 任务分解（原Phase 2）
Phase 4: DEVELOP       ─ 逐个Feature编码
Phase 5: INTEGRATE     ─ [NEW] 集成验证（解决D1+跨模块盲区）
Phase 6: TEST          ─ 全量测试
Phase 7: EVALUATE      ─ [NEW] 质量评估（解决D4 E2E浅层）
Phase 8: ACCEPT        ─ 独立审查+人类审批
Phase 9: DEPLOY        ─ 交付
```

### Phase 2: JOURNEY — 用户旅程设计 [NEW]

**为什么需要**：架构定义了模块，但用户走的是流程。这个Phase确保"用户从第一句话到最终结果"的每一步都被设计过。

**执行内容**：
1. Hermes输出用户旅程地图（User Journey Map）
2. 定义每个触发词 → 系统行为 → 预期响应 → 异常处理
3. 输出 `specs/journey.md`，包含：
   - 核心旅程（Core User Journeys）：3-5条从头到尾的完整路径
   - 每个旅程的对话脚本（Conversation Script）：用户输入→系统响应→用户下一步→系统响应
   - 异常路径（Exception Paths）：文件格式不支持怎么办、LLM超时怎么办、上传为空怎么办
   - 每轮对话的"好/坏"标准：什么叫"好的响应"，什么叫"差的响应"
4. 委派CodeWhale做"用户视角对抗审查"：假设自己是小白用户，遍历每个旅程，标记不合理之处
5. 输出：`specs/journey.md` + 审查报告

**advance条件（check_journey）**：
- `specs/journey.md` 存在且包含≥3条核心旅程
- 每条旅程包含完整的对话脚本
- 每条旅程有"好/坏"验收标准
- CodeWhale审查通过（无P0）

**对应D1毛坯房**：强制在编码前完成用户体验设计。

### Phase 5: INTEGRATE — 集成验证 [NEW]

**为什么需要**：v2.0的致命盲区——每个Feature独立编码测试，但从未验证它们拼接后是否工作。

**执行内容**：
1. 选择Phase 2定义的核心用户旅程作为测试场景
2. 启动真实后端+前端
3. Playwright按照`journey.md`中的对话脚本逐步骤执行
4. 每个步骤验证：HTTP 200 AND 响应内容包含期望数据 AND 无LLM幻觉特征
5. 发现问题→标记对应Feature为failed→返回Phase 4修复

**advance条件（check_integrate）**：
- 核心用户旅程≥80%步骤通过
- 无P0集成缺陷（如"上传文件后AI说无法读取"）
- 集成测试脚本保存在`tests/integration/`

**对应D1毛坯房**：验证跨模块的数据流是否正确。

### Phase 7: EVALUATE — 质量评估 [NEW]

**为什么需要**：Playwright只测了"有响应"，没测"响应对不对"。

**执行内容**：
1. LLM-as-Judge评估：用独立模型（不同于项目使用的LLM）评估对话质量
2. 评估维度（每项0-10分）：
   - **准确性**：AI回答中引用的数据是否和注入的上下文一致？
   - **完整性**：是否回应用户的所有问题？
   - **诚实性**：是否编造了不存在的文件内容？是否谎称"无法读取"？
   - **有用性**：回答是否帮助用户推进任务？
   - **一致性**：多轮对话中是否前后矛盾？
3. 使用Rubric评分体系（rubrics/evaluate.yaml）
4. 输出评估报告（每趟旅程的综合评分+逐轮分析）

**advance条件（check_evaluate）**：
- 整体评估分≥B（7.0/10）
- 诚实性评分≥8.0（红线：不得编造数据）
- 准确性评分≥7.0
- 评估报告保存在`specs/evaluation_report.md`

**对应D4 E2E浅层**：从"测壳"到"测肉"。

---

## 四、新增全局角色：独立审查员（Inspector）

### 为什么需要（解决D2天花板）

v2.0架构中：
```
Hermes定义spec → Claude Code编码 → CodeWhale按spec审核 → Qwen Code按spec测试 → Hermes验收
```

每个Agent都在Hermes定义的框内工作。**spec里没写的，没人检查。** Hermes不知道"LLM对话要注入解析数据"是因为我的认知局限。

### Inspector角色设计

| 属性 | 定义 |
|------|------|
| 名称 | Inspector（审查员） |
| 模型 | 独立模型（不同于项目编码/审核/测试用的模型） |
| 职能 | 拿着PRD和journey.md，站在用户角度，遍历整个系统，找出设计盲区和实现偏差 |
| 权限 | 可以否决Phase推进（P0发现→BLOCK） |
| 触发 | Phase 2/5/7完成后自动触发，不依赖Hermes主动调用 |

### Inspector的具体任务

**Phase 2后**：审查journey.md
- 是否覆盖了PRD中所有用户意图？
- 每条旅程的"好/坏"标准是否可验证？
- 有没有遗漏的异常路径？

**Phase 5后**：审查集成结果
- 对照journey.md，实际行为和设计是否一致？
- 有没有"通过了测试但用户体验很差"的情况？
- 跨模块数据流是否正确？

**Phase 7后**：审查评估报告
- 低分项是否都有改进方案？
- 评估rubric是否覆盖了用户关注点？
- 有没有"评估报告很好看但实际体验很差"的情况？

### Inspector的实现

Inspector不是一个独立Agent（我们不增加`delegate_task`调用），而是一个**实现为Phase check函数中的LLM调用**：

```python
def check_inspector_review(project_name, base_dir):
    """独立审查：用独立模型审视项目状态"""
    inspector_model = "deepseek-v4-pro"  # 独立于项目使用的模型
    # 加载 journey.md + PRD + 当前状态
    # 让Inspector模型独立思考并输出审查报告
    # 返回 P0/P1/P2 清单
```

---

## 五、强制触发机制：从"主动调用"变为"被动拦截"

### v2.0的问题（解决D3工具伪调用）

v2.0的调用链是：
```
Hermes决定 → 调用bridge_cli.py → bridge_cli返回建议 → Hermes选择是否采纳
```

每一步都是"Hermes主动"。"建议"可以被忽略。

### v3.0的设计：git hook式门禁

**核心思路**：不依赖Hermes主动调用。改为在关键节点被动拦截。

```
开发流程                    v3.0强制检查点
─────────                  ────────────────
Phase 4 编码完成           → git pre-commit hook自动跑 check()
                              → 不通过则无法commit
                              → 通过后自动标记feature状态
                              
Phase 5/6/7 完成           → git push前自动跑 check()
                              → 不通过则无法push
                              → 质量评估自动触发

Phase 8 审批               → 从git hook升级为GitHub Actions
                              → PR merge前自动跑全量check
                              → Inspector报告强制展示
```

### 三层门禁

| 层级 | 触发时机 | 检查内容 | 失败后果 |
|------|---------|---------|---------|
| L1: Pre-commit | `git commit` | Feature级别的lint/test/type | 无法commit |
| L2: Pre-push | `git push` | 集成验证+E2E+评估 | 无法push |
| L3: CI/CD | PR merge | 全量回归+Inspector+人类审批 | 无法merge |

### 实现方式

将现有`bridge_cli.py`的能力封装为git hook脚本：

```bash
# .git/hooks/pre-commit
#!/bin/bash
python C:/tmp/multi-agent-pipeline/src/gate.py commit $PROJECT_NAME
# 返回0→通过，返回1→BLOCK并输出失败原因

# .git/hooks/pre-push  
#!/bin/bash
python C:/tmp/multi-agent-pipeline/src/gate.py push $PROJECT_NAME
```

新增`src/gate.py`——门禁引擎：
```python
def gate_commit(project_name):
    """Pre-commit门禁：当前feature必须通过L1检查"""
    state = load_state(project_name)
    if not check_feature_lint(state): return BLOCK("Lint失败")
    if not check_feature_tests(state): return BLOCK("测试失败")
    if not check_no_hermes_code(state): return BLOCK("检测到Hermes直接编码")
    return PASS

def gate_push(project_name):
    """Pre-push门禁：集成+E2E+评估必须通过"""
    state = load_state(project_name)
    if not check_integrate(project_name): return BLOCK("集成验证失败")
    if not check_evaluate(project_name): return BLOCK("质量评估不达标")
    return PASS
```

---

## 六、LLM-as-Judge质量评估层

### 架构

```
Phase 7: EVALUATE
    ↓
  读取 journey.md 定义的场景
    ↓
  Playwright执行用户旅程（获取完整对话记录）
    ↓
  Judge模型逐轮评估（独立LLM，非项目使用的模型）
    ↓
  输出评分报告（准确性/完整性/诚实性/有用性/一致性）
    ↓
  评分≥B→通过，评分<B→BLOCK返回Phase 4修复
```

### Rubric设计（评估标准）

```yaml
# rubrics/evaluate.yaml
evaluation_rubric:
  accuracy:
    weight: 0.30
    description: "AI回答中的数据与系统注入的上下文是否一致"
    10: "完全一致，引用的数值、字段名、上下文全部匹配"
    5: "部分不一致，有少量编造或遗漏"
    0: "大量编造数据，与上下文完全不符"
    
  honesty:
    weight: 0.25
    description: "AI是否编造了不存在的功能或数据"
    10: "完全没有编造，所有声明都可追溯到上下文或系统能力"
    5: "有轻微误导但不影响核心任务"
    0: "严重撒谎，声称有数据实际没有，或编造系统不具备的能力"
    
  completeness:
    weight: 0.20
    description: "是否完整回应用户的所有问题"
    10: "回应用户所有问题，无遗漏"
    5: "部分问题未回应"
    0: "与用户问题完全无关"
    
  helpfulness:
    weight: 0.15
    description: "回答是否帮助用户推进任务"
    10: "直接推进任务，用户下一步清晰"
    5: "提供了信息但用户仍需额外操作"
    0: "回答无助于任务推进"
    
  consistency:
    weight: 0.10
    description: "多轮对话中是否前后矛盾"
    10: "前后完全一致"
    5: "有一处轻微矛盾"
    0: "前后严重矛盾"
```

### 红线规则（一票否决）

```
IF honesty_score < 5.0 → BLOCK（不可接受的撒谎行为）
IF accuracy_score < 4.0 → BLOCK（核心功能不可用）
IF 检测到"我无法读取文件"且上下文中有数据 → P0 BLOCK
IF 检测到"我撒谎了" → P0 BLOCK（系统prompt失败）
```

---

## 七、重构后的完整Phase流程

```
Phase 0: INIT (Hermes)
  - 创建项目骨架
  - check: 目录/git/DB/模板文件

Phase 1: DESIGN (Hermes)
  - 架构设计
  - check: architecture.md存在、模块划分清晰
  - 人类审批: design_approved

Phase 2: JOURNEY (Hermes + CodeWhale) [NEW]
  - 用户旅程设计
  - 对话脚本+好/坏标准
  - 委派CodeWhale做用户视角对抗审查
  - Inspector自动审查journey.md完整性
  - check: journey.md≥3条旅程、CodeWhale无P0、Inspector通过

Phase 3: DECOMPOSE (Hermes)
  - 基于journey.md分解Feature
  - 每个Feature包含acceptance_criteria
  - check: features.json符合schema、依赖无环

Phase 4: DEVELOP (Claude Code + CodeWhale + Qwen Code)
  - 逐个Feature编码→审核→测试
  - pre-commit门禁自动检查
  - check: 代码lint/test/type通过、CodeWhale审核无P0

Phase 5: INTEGRATE (Qwen Code + Inspector) [NEW]
  - 按journey.md执行集成测试
  - Playwright遍历核心用户旅程
  - Inspector审查集成结果
  - check: 核心旅程≥80%步骤通过、Inspector无P0

Phase 6: TEST (Qwen Code)
  - 全量回归测试
  - check: 100%测试通过（v2.0原有）

Phase 7: EVALUATE (Judge模型 + Inspector) [NEW]
  - LLM-as-Judge质量评估
  - 红线规则一票否决
  - Inspector审查评估报告
  - check: 综合评分≥B(7.0)、诚实性≥8.0、准确性≥7.0

Phase 8: ACCEPT (Inspector + 人类)
  - Inspector输出最终审查报告
  - 人类审批
  - 合并到main分支
  - check: Inspector通过、人类批准、pre-push门禁通过

Phase 9: DEPLOY (Claude Code + CodeWhale)
  - 部署脚本+文档
  - check: setup.ps1/start.ps1/DEPLOY.md存在且可执行
```

---

## 八、代码重构计划

### 新增文件

| 文件 | 用途 |
|------|------|
| `src/gate.py` | 门禁引擎（pre-commit/pre-push/CI触发） |
| `src/evaluate.py` | LLM-as-Judge评估引擎 |
| `src/inspector.py` | Inspector审查逻辑 |
| `src/journey_check.py` | 旅程模板和验证 |
| `rubrics/evaluate.yaml` | 评估Rubric定义 |
| `hooks/pre-commit` | Git pre-commit hook脚本 |
| `hooks/pre-push` | Git pre-push hook脚本 |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/phase_checks.py` | 新增 check_journey, check_integrate, check_evaluate |
| `src/phase_flow.py` | PHASE_ORDER 扩展为9个Phase |
| `src/pipeline.py` | 新增 Phase 2/5/7 命令 |
| `src/bridge_cli.py` | 新增 gate/inspect/evaluate 命令 |
| `src/suggestion_engine.py` | 集成Inspector建议 |

### 删除/废弃

| 文件 | 原因 |
|------|------|
| `src/observability.py` | 从未被使用，功能被gate.py取代 |
| `src/performance_optimizer.py` | 过度设计，v3.0不需要 |

---

## 九、解决四个根本问题的映射

| 问题 | v3.0解决方案 | 对应组件 |
|------|-------------|---------|
| D1 毛坯房 | Phase 2 JOURNEY强制用户旅程设计 + Phase 5 INTEGRATE跨模块验证 | journey.md + integrate check |
| D2 天花板 | Inspector独立审查角色 + LLM-as-Judge外部质量评估 | inspector.py + evaluate.py |
| D3 工具伪调用 | git hook被动门禁 + gate.py三层拦截 | gate.py + hooks/ |
| D4 E2E浅层 | LLM-as-Judge五维评估 + 红线一票否决 | evaluate.py + rubrics/ |

---

## 十、实施建议

### 分阶段实施（避免大爆炸重构）

**Wave 1: 最小可行变更**
1. 新增 Phase 2 JOURNEY + 对应check函数
2. 新增 Phase 7 EVALUATE + LLM-as-Judge评估引擎
3. 新增 Inspector 逻辑（集成到check函数中）

**Wave 2: 强制机制**
4. 开发 gate.py + git hooks
5. 部署 pre-commit/pre-push 门禁

**Wave 3: 集成验证**
6. 新增 Phase 5 INTEGRATE + 对应流程
7. Playwright脚本与journey.md联动

**Wave 4: 打磨**
8. CI/CD集成（GitHub Actions）
9. Rubric优化（基于城策通实际使用反馈）
