# multi-agent-pipeline v3.0 彻底重构方案

> 基于 v2.0 全量代码审查（7 P0 / 14 P1 / 19 P2）+ 城策通 v2.0 实战教训 + Superpowers 方法论 + 全网调研

---

## 第一部分：v2.0 必须修复的致命缺陷（P0 修复清单）

### 安全漏洞（必须逐项修复）
| ID | 文件 | 问题 | 修复方案 |
|----|------|------|---------|
| PIPE-01 | pipeline.py:228 | os.system() 命令注入 | subprocess.run(list, cwd=) |
| WKTR-01 | worktree.py:79 | feature名注入git命令 | 白名单校验 [a-z0-9-]+ |
| PHCK-01 | phase_checks.py | 路径遍历攻击 | path.resolve() + 必须在base_dir内验证 |
| SYSC-01 | system_constraint.py | SHA-256做密码哈希 | 改用 bcrypt/pbkdf2_hmac |
| STOR-01 | state_store.py | project_id 注入 | 统一参数化查询 |

### 数据安全（必须修复）
| ID | 文件 | 问题 | 修复方案 |
|----|------|------|---------|
| APPR-01 | approval.py | 审批记录纯内存，重启丢失 | 持久化到 SQLite |
| PCST-01 | prompt_cache_store.py | save_entry 重置 access_count=0 | 改为 access_count+1 |

---

## 第二部分：Phase 重新设计 — 从 7 个变为 10 个

```
Phase 0: INIT          ─ 项目骨架
Phase 1: RESEARCH      ─ [NEW] 知识图谱构建（解决毛坯房根因）
Phase 2: DESIGN        ─ 架构设计（含多 LLM 对抗审查）
Phase 3: JOURNEY       ─ [NEW] 用户旅程设计（解决毛坯房）
Phase 4: DECOMPOSE     ─ 任务分解
Phase 5: DEVELOP       ─ 逐个 Feature 编码
Phase 6: INTEGRATE     ─ [NEW] 跨模块集成验证（解决毛坯房盲区）
Phase 7: TEST          ─ 全量回归测试
Phase 8: EVALUATE      ─ [NEW] LLM-as-Judge 质量评估（解决 E2E 浅层）
Phase 9: ACCEPT        ─ Inspector 审查 + 人类审批
Phase 10: DEPLOY       ─ 交付
```

### Phase 1: RESEARCH — 三级知识图谱构建 [NEW]

**为什么需要**：城策通 v2.0 的 PRD 和架构设计没有经过任何外部知识调研。设计者凭自己的认知写 spec，认知盲区直接变成系统缺陷。

**这个 Phase 解决你的要求 #2a 和 #2b**。

**三级知识图谱结构**：

```
Level 1: 领域概念层（Domain Concepts）
  - 该领域有哪些核心概念？
  - 概念之间的关系是什么？
  - 业界标准术语是什么？

Level 2: 模式与实践层（Patterns & Practices）
  - 业界解决同类问题的主流方案有哪些？
  - 各方案的优缺点对比？
  - 有哪些已知的陷阱和反模式？

Level 3: 实现决策层（Implementation Decisions）
  - 基于 L1/L2，本项目的技术选型是什么？
  - 为什么不选其它方案？
  - 有哪些假设需要验证？
```

**执行流程**：

```
Step 1: Hermes 定义调研范围
  - 从用户需求中提取调研主题
  - 定义每个主题的调研深度（L1/L2/L3）

Step 2: 并行委派多个研究 Agent
  - Agent A: 领域知识调研（学术论文、行业报告、技术文档）
  - Agent B: 竞品/同类项目调研（GitHub 开源项目、商业产品）
  - Agent C: 最佳实践调研（Stack Overflow、技术博客、社区讨论）

Step 3: Hermes 汇总构建知识图谱
  - 去重、合并、结构化
  - 标注置信度（验证来源 vs 推测）
  - 输出 specs/knowledge_graph.md

Step 4: Inspector 审查知识图谱完整性
  - 是否有遗漏的关键概念？
  - 调研来源是否可靠？
  - 是否有未被覆盖的盲区？

Step 5: 知识图谱达标后，才能进入 Phase 2 (DESIGN)
```

**advance 条件（check_research）**：
- specs/knowledge_graph.md 存在
- 至少覆盖 3 个一级领域 + 每个领域 ≥5 个核心概念
- 每条知识点标注来源（URL/论文/项目名）
- Inspector 审查通过（无 P0 遗漏）

### Phase 2: DESIGN — 知识驱动的架构设计 + 多 LLM 对抗审查

**这个 Phase 解决你的要求 #2c 和 #2d**。

**执行流程**：

```
Step 1: 设计 Agent（Hermes 或委派的架构 Agent）深度研究
  - 加载 Phase 1 的知识图谱
  - 补充针对性调研（如果知识图谱有缺口）
  - 输出初版架构设计

Step 2: 审查 + 补充 Agent 也进行深度调研
  - 独立搜索和构建自己的知识图谱
  - 不与设计 Agent 共享搜索过程
  - 形成独立的认知基础

Step 3: 3 轮对抗讨论（逐渐收敛）
  
  第 1 轮：结构性质疑
    审查 Agent: "这个架构缺少 X 模块，因为业界实践中 Y"
    设计 Agent: 回应或修改
  
  第 2 轮：细节挑战
    审查 Agent: "这个接口设计在 Z 场景下会失败"
    设计 Agent: 修正接口或论证合理性
  
  第 3 轮：收敛定稿
    审查 Agent: "经过前两轮修改，剩余问题为 P2 级别，可接受"
    设计 Agent: 最终修订
    输出: specs/architecture.md + specs/design_review_log.md

Step 4: Inspector 最终审查
  - 验证所有 P0/P1 问题已解决
  - 验证知识图谱中的关键概念都已映射到架构中
  - 验证没有遗漏的用户需求

Step 5: 人类审批
```

**每轮讨论的输出格式**：
```yaml
round: 2
reviewer_findings:
  - id: R2-01
    severity: P0
    category: missing_module
    description: "缺少数据持久化层，所有状态仅存内存"
    evidence: "pipeline.py L163-195 使用内存字典，无 SQLite 写入"
    suggestion: "增加 StateStore 模块，所有状态变更必须持久化"
author_response:
  - id: R2-01
    action: accepted
    change: "新增 src/persistence.py，替换所有内存字典为 SQLite 写入"
```

**advance 条件（check_design）**：
- specs/architecture.md 存在
- specs/design_review_log.md 存在，包含 ≥3 轮讨论记录
- 无未解决的 P0/P1
- Inspector 审查通过
- 人类审批通过

### Phase 3: JOURNEY — 用户旅程设计 [NEW]

与 v3 初版方案中的 JOURNEY Phase 一致，但增加：

**审查 Agent 独立调研**：审查 Agent 搜索类似产品的 UX 设计，作为审查依据。

**advance 条件（check_journey）**：
- specs/journey.md 存在，包含 ≥3 条核心旅程
- 每条旅程有完整的对话脚本 + "好/坏"标准
- 审查 Agent 完成 3 轮审查（日志记录）
- Inspector 审查通过

### Phase 5: DEVELOP — Feature 编码（增强）

原来的 Phase 3/4 合并。增加：

1. **Feature 编码前，编码 Agent 必须先阅读知识图谱**（由 Hermes 注入到 delegate_task context 中）
2. **Pre-commit 门禁自动触发**（见第五部分）

### Phase 6: INTEGRATE — 跨模块集成验证 [NEW]

与 v3 初版方案一致，但增加：
- **集成测试脚本必须引用 journey.md 中的对话脚本**
- **Inspector 验证 journey.md 与实际行为的偏差**

### Phase 8: EVALUATE — LLM-as-Judge 质量评估 [NEW]

与 v3 初版方案一致。Rubric 五维度 + 红线一票否决。

### Phase 9: ACCEPT — Inspector 审查 + 人类审批

**Inspector 的角色定位（解决你的要求 #3）**：
- Inspector = 审查 + 补充 Agent（同一实体）
- 与 Hermes 一样具备**全局记忆**（读取完整 context、知识图谱、journey.md、所有 review 日志）
- 站在**用户角度**遍历整个系统
- 审查内容：
  - 知识图谱中的所有概念是否都在系统中实现？
  - journey.md 中的所有步骤是否都能正常执行？
  - EVALUATE 评分是否真实可信？
  - 有没有遗漏的异常路径？

---

## 第三部分：Inspector — 统一审查角色

### 设计原则

Inspector 不是一个独立的外部 Agent 进程，而是一个**在关键 Phase check 函数中嵌入的审查逻辑**。

```
Inspector = 独立 LLM 调用 + 全局上下文注入 + 用户视角审查模板
```

### 全局记忆机制

```
Inspector 在每次被调用时，注入以下上下文：
  1. 项目 specs/knowledge_graph.md（领域知识）
  2. 项目 specs/architecture.md（架构设计）
  3. 项目 specs/journey.md（用户旅程）
  4. 当前 Phase 的所有产出物
  5. 所有历史 review_log.md（之前的审查记录）
  6. 项目 features.json（当前状态）
```

### 审查模板

```yaml
inspector_review_template:
  user_perspective:
    - "如果我是用户，这个阶段结束后我能否完成任务 X？"
    - "还有哪些边界情况我作为用户会遇到？"
    - "这个设计有没有过度设计的部分（用户不需要的）？"
  
  knowledge_completeness:
    - "知识图谱中的核心概念是否都已映射到设计中？"
    - "有没有知识图谱中标注为'关键'但设计未覆盖的概念？"
  
  journey_fidelity:
    - "journey.md 中的每个步骤，当前系统是否都能执行？"
    - "实际行为和 journey.md 的描述是否有偏差？"
  
  cross_phase_coherence:
    - "前一个 Phase 的决策是否在本 Phase 被正确继承？"
    - "有没有被后续 Phase 悄悄推翻的设计决定？"
```

---

## 第四部分：业界最佳实践融入（全网调研成果）

### 4.1 Auto-Fix Loop（源自 Aider）

Phase 5 (DEVELOP) 和 Phase 8 (EVALUATE) 之间增加自动修复循环：

```
Feature 编码 → Lint/Test → 失败 → LLM 分析错误 → 自动修正 → 重新验证
                                                              ↓
                                                         通过 → 继续
```

**关键设计**：
- 最多 3 轮自动修复
- 3 轮后仍失败 → 标记 Feature failed，由人类介入
- 使用与编码 Agent 不同的 LLM 来分析错误（防止自我偏好）

### 4.2 Repo Map（源自 Aider）

Phase 5 编码 Agent 在开始工作前，由 Hermes 注入 Repo Map：

```
项目文件结构 → 依赖关系 → 受影响文件列表 → 注入到 Agent 上下文
```

**好处**：Agent 不需要盲目遍历整个代码库，精准修改。

### 4.3 Event Audit Trail（源自 OpenHands）

每个 Agent 的每一步操作都要记录到审计日志：

```python
# 新增 src/audit_trail.py
class AuditEvent:
    agent_id: str
    phase: str
    action: str        # "code_write", "test_run", "review_submit"
    input_summary: str  # 输入摘要（不是完整上下文）
    output_summary: str # 输出摘要
    timestamp: datetime
    duration_ms: int
    result: str         # "pass" / "fail" / "timeout"
```

**存储**：SQLite（已有 StateStore，扩展 audit_logs 表）

### 4.4 Evidence-First LLM-as-Judge（源自 Microsoft）

Phase 8 EVALUATE 的 Judge 流程改进：

```
Step 1: 收集客观证据
  - 静态分析结果（Bandit/Semgrep）
  - 测试运行结果（pytest 输出）
  - Lint 输出
  - 规范一致性检查结果

Step 2: Judge LLM 评分
  - 使用与项目编码不同的模型
  - 输入：代码 + 证据 + Rubric
  - 输出：JSON（评分 + 理由 + 具体行号）

Step 3: 多轮取平均值（可选，用于关键模块）
  - 2-3 轮独立评判取平均值
```

### 4.5 post-generate Hook（源自 Antigravity）

在 gate.py 中新增 post-generate 级别的检查：

```
AI 生成代码后 → post-generate hook（<5 秒）→ 通过则继续 → 失败则立即反馈
```

**检查项**（post-generate 级别，必须极快）：
- AI 生成标记检测（防止 AI 代码遗漏审查标记）
- 安全模式扫描（硬编码密钥、eval、exec）
- 文件路径安全检查

### 4.6 Spec Conformance Check（源自 SDD）

Phase 6 INTEGRATE 增加规范一致性检查：

```
journey.md 中的每个步骤 → 实际执行 → 对比期望输出
specs/architecture.md 中的每个模块 → 代码中是否存在 → 接口是否匹配
```

### 4.7 四层知识图谱模型补充

基于调研，将知识图谱从三层扩展为四层：

```
Level 1: 业务概念层（Business Concepts）
Level 2: 约束规则层（Constraint Rules）—— 可执行的业务规则
Level 3: 组件映射层（Component Mapping）—— 概念 → 代码组件的映射
Level 4: 代码生成规则层（Code Generation Rules）—— 从映射到代码的模板
```

**关键增强**：L2 的约束规则必须是**可自动验证的**，而不仅仅是描述性的。

---

## 第五部分：强制触发机制 — 从"主动调用"变为"被动拦截"

### 架构

```
AI生成代码 → post-generate hook → gate.py post-gen → 通过 ✅ / 立即反馈 ❌
git commit → .git/hooks/pre-commit → gate.py commit → 通过 ✅ / BLOCK ❌
git push   → .git/hooks/pre-push   → gate.py push   → 通过 ✅ / BLOCK ❌
```

### gate.py 四层门禁

```python
# src/gate.py

def gate_post_generate(file_path: str) -> GateResult:
    """AI 生成代码后立即检查（<5 秒）"""
    checks = [
        CheckAIOriginMark(),       # 检测 AI 生成标记
        CheckHardcodedSecrets(),   # 扫描 api_key/password
        CheckDangerousPatterns(),  # eval/exec/os.system
        CheckPathSafety(),         # 路径遍历检查
    ]
    return run_checks(checks, level=GateLevel.POST_GEN, timeout=5)

def gate_commit(project_name: str) -> GateResult:
    """Pre-commit 门禁（<30 秒）"""
    checks = [
        CheckFeatureLint(),
        CheckFeatureTests(),
        CheckNoHermesCode(),       # 检测 Hermes 是否直接编码
        CheckPhaseRequirements(),  # 当前 Phase 的所有要求
    ]
    return run_checks(checks, level=GateLevel.COMMIT, timeout=30)

def gate_push(project_name: str) -> GateResult:
    """Pre-push 门禁（可 5+ 分钟）"""
    checks = [
        CheckIntegrate(),          # 跨模块集成验证
        CheckSpecConformance(),    # 规范一致性检查
        CheckEvaluate(),           # LLM-as-Judge 质量评估
        CheckInspectorReview(),    # Inspector 审查通过
        CheckAllFeaturesPassed(),  # 所有 Feature passed
        CheckNoP0Remaining(),      # 无未解决 P0
    ]
    return run_checks(checks, level=GateLevel.PUSH, timeout=600)
```

### git hook 安装

```bash
# setup.sh 中自动安装
cp hooks/pre-commit .git/hooks/pre-commit
cp hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-commit .git/hooks/pre-push
```

---

## 第六部分：代码重构计划

### 修改文件（Bug 修复 + 功能增强）

| 文件 | 修改内容 |
|------|---------|
| pipeline.py | 修复 os.system → subprocess (PIPE-01); 去除重复 check 函数 (PIPE-05); 修复 --mark-tests (PIPE-03) |
| phase_checks.py | 新增 check_research, check_journey, check_integrate, check_evaluate; 修复路径遍历 (PHCK-01); 修复 E2E 解析 (PHCK-03); 修复 _load_state 绕过 StateStore (PHCK-06) |
| phase_flow.py | PHASE_ORDER 扩展为 10 个 Phase (PHFL-02); 修复 _load_state 异常吞噬 (PHFL-01) |
| models.py | Phase enum 新增 RESEARCH/JOURNEY/INTEGRATE/EVALUATE; 移除 LEGACY REVIEW (MOD-02) |
| state_store.py | SQLite WAL 模式 (STOR-06); 连接池 (STOR-03); 修复 load() 忽略 name (STOR-04) |
| system_constraint.py | bcrypt 替换 SHA-256 (SYSC-01) |
| approval.py | SQLite 持久化 (APPR-01) |
| worktree.py | 命令注入修复 (WKTR-01); 硬编码路径支持环境变量 (WKTR-04) |
| sandbox.py | 修复 DENY/ALLOW 顺序 (SNDB-01) |
| circuit_breaker.py | 修复 half-open 竞态 (CBRK-01) |
| suggestion_engine.py | 修复重复 blocker (SUGG-02); 缓存 run_check 结果 (SUGG-03) |
| e2e_framework.py | 删除 PlaywrightDriver stub; 改为集成真实 Playwright (E2EF-01) |
| prompt_cache_store.py | 修复 access_count 重置 (PCST-01) |

### 新增文件

| 文件 | 用途 |
|------|------|
| src/gate.py | 门禁引擎（post-generate / pre-commit / pre-push） |
| src/evaluate.py | LLM-as-Judge 评估引擎（Evidence-First + Microsoft 框架） |
| src/inspector.py | Inspector 审查逻辑（含审查模板 + 全局上下文注入） |
| src/knowledge_graph.py | 四层知识图谱数据结构 |
| src/research_agent.py | 研究 Agent 调度器（并行委派 + 汇总去重） |
| src/adversarial_review.py | 多轮对抗讨论引擎（3 轮收敛定稿） |
| src/audit_trail.py | Event Audit Trail（Agent 操作全量记录） |
| src/repo_map.py | Repo Map 生成器（文件结构 → 依赖关系 → Agent 上下文） |
| rubrics/evaluate.yaml | 五维评估 Rubric（正确性/质量/架构/安全/完整性） |
| hooks/pre-commit | git pre-commit hook（调用 gate.py commit） |
| hooks/pre-push | git pre-push hook（调用 gate.py push） |
| hooks/post-generate | AI 生成后即时检查 hook（调用 gate.py post-gen） |

### 删除文件

| 文件 | 原因 |
|------|------|
| observability.py | 功能与 entry.py 高度重叠 (OBSV-01)，由 gate.py + inspector.py 替代 |
| performance_optimizer.py | 过度设计，v3.0 不需要（benchmark 无意义 PERF-01） |
| architecture_review.py | 静态报告，由 adversarial_review.py 动态引擎替代 (ARCH-01) |

---

## 第七部分：实施路线图

### Wave 1: 止血（P0 修复）
修复全部 7 个 P0：命令注入、路径遍历、密码哈希、审批丢失、access_count 重置

### Wave 2: 重构核心
- Phase enum 重新定义（10 个 Phase）
- 新增 check_research、check_journey、check_integrate、check_evaluate
- gate.py + git hooks

### Wave 3: 知识驱动设计引擎
- knowledge_graph.py
- research_agent.py
- adversarial_review.py

### Wave 4: 质量评估层
- evaluate.py + rubrics/evaluate.yaml
- inspector.py

### Wave 5: 打磨与文档
- 修复 P1/P2 剩余问题
- 补充集成/E2E 测试
- 更新 docs/
