# Pipeline 双模式设计 — Greenfield + Brownfield

## 模式总览

```
bridge_cli.py full <project>
  → 自动检测模式:
      features.json 存在 && any passed ? brownfield : greenfield
  → 加载对应 PHASE_ORDER
  → PhaseFlow 按当前模式的阶段链推进
```

## 两种模式

### Greenfield（新建项目）— 12 Phase

```
init → design → decompose → research → prd → journey →
develop → integrate → test → evaluate → accept → deploy
```

从零开始，先设计再开发。PhaseFlow 原封不动。

### Brownfield（存量优化）— 7 Phase

```
discover → benchmark → analyze → plan → execute → verify → deliver
```

已有项目，先摸底再对标再改。新增于同目录 `brownfield_phases.py`。

---

## Brownfield 7 Phase 详解

### Phase 1: DISCOVER（发现）— "我们有什么"

目标：多维度审计，建立现状全貌。不问"好不好"，只记录事实。

| Agent | 任务 | 产出 |
|-------|------|------|
| codewhale | 代码审计：lint、复杂度、死代码、模块耦合 | 代码审计报告 |
| codewhale | 架构审计：模块依赖图、设计偏离、循环引用 | 架构审计报告 |
| qwen-code | 测试审计：覆盖率、缺口、质量 | 测试审计报告 |
| qwen-code | 文档审计：PRD/架构/API与代码一致性 | 文档审计报告 |

**check**: 四份审计报告全部生成

---

### Phase 2: BENCHMARK（对标）— "好的标准是什么"

目标：从外部建立标杆。这是 Brownfield 独有的核心环节。

| 对标维度 | 数据来源 | Agent |
|----------|----------|-------|
| 功能完整性 | 竞品分析、同类产品能力矩阵 | qwen-code（全网调研） |
| 用户体验 | 行业标杆产品的交互模式 | qwen-code（体验分析） |
| 代码质量 | 开源同类项目的架构和测试标准 | codewhale（GitHub分析） |
| 输出质量 | 过审案例、最佳实践文档 | claude-code（案例提炼） |
| 领域知识 | 最新政策、学术论文、行业报告 | qwen-code（深度调研） |

工作方式：
```
1. 根据项目领域确定对标范围（如：专项债一案两书编制）
2. 全网搜索同类产品、过审案例、行业标准
3. 提取标杆指标：功能完整度、输出精度、响应速度等
4. 生成标杆文档：每个维度的"优秀"标准
```

**check**: 标杆文档生成，每个维度有≥3个可量化指标

---

### Phase 3: ANALYZE（分析）— "差距在哪，优先改什么"

目标：DISCOVER vs BENCHMARK → 差距矩阵

```
差距 = 标杆标准 - 现状水平
优先级 = 影响度 × 紧迫度 / 实现成本
```

输出：

| ID | 维度 | 现状 | 标杆 | 差距 | 优先级 |
|----|------|------|------|------|--------|
| G01 | 意图识别 | 关键词匹配 85% | LLM语义 95% | -10% | P0 |
| G02 | 政策来源 | 仅本地知识库 | 本地+全网+来源链接 | 缺全链路 | P0 |
| G03 | 侧边栏 | 静态清单 | 5Tab实时动态 | 结构差距 | P1 |

**check**: 差距矩阵生成，P0项≤5个

---

### Phase 4: PLAN（规划）— "怎么改"

目标：为每个P0/P1差距制定优化方案

- 编写优化PRD（增量，标注与原有PRD的diff）
- 更新架构设计
- 更新用户旅程
- 更新 features.json

**check**: PRD + 架构 + 旅程 + features.json 四件套完整

---

### Phase 5: EXECUTE（执行）— "改"

目标：逐个gap执行 review_fix_test 闭环

每个gap：
```
codewhale 审查 → claude-code 修复 → qwen-code 测试
    ↑                                            │
    └──────── 不通过 ← 回归检查 ←────────────────┘
```

- 每完成一个gap，全量回归测试
- 全部通过才进入下一Phase

**check**: features.json 全部 passed + 回归测试全绿

---

### Phase 6: VERIFY（验证）— "改对了吗"

目标：E2E用户旅程验证 + 性能对比

- E2E测试（覆盖优化前后对比）
- 性能基准对比
- 标杆达成度检查（DISCOVER → ANALYZE → 现在的差距）

**check**: E2E全通过 + 标杆达成率 ≥ 目标值

---

### Phase 7: DELIVER（交付）

目标：文档更新 + 部署验证

- DEPLOY.md 更新
- CHANGELOG 生成
- 优化前后对比报告

---

## 实现计划

### 文件变更清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `config.py` | +30行 | 双模式 phase_order 字典 + auto_detect 逻辑 |
| `phase_checks.py` | +200行 | 7个 brownfield check 函数 |
| `event_engine.py` | +15行 | 新增 brownfield CHAIN_TEMPLATES |
| `bridge_cli.py` | +20行 | mode 检测 + `--mode` 参数 |
| `brownfield_phases.py` | 新建 | discover/benchmark/analyze agent调度逻辑 |

### PhaseFlow：零改动

PhaseFlow 只读 `PHASE_ORDER`，不关心是哪个模式。改 config 就行。

### 插件化兼容

未来加第三种模式（如"安全加固"、"性能优化"）：

```
AVAILABLE_MODES = {
    "greenfield": {...},
    "brownfield": {...},
    "security_hardening": {   ← 新增只需加一条
        "phases": ["threat_model","audit","harden","pentest","verify","deliver"],
        "trigger": "manual",  # 不自动检测，需显式 --mode
    },
}
```

三步即插即用：①config加定义 ②phase_checks加check ③完成。
