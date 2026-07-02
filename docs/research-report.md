# 多智能体软件开发流水线深度研究报告

> 调研日期：2026年6月30日  
> 目的：为 v2.0 多智能体开发流水线工具的重设计提供业界实战参考  
> 语言：中文  

---

## 目录

1. [生产级 AI 编码流水线的质量保障机制](#1-生产级-ai-编码流水线的质量保障机制)
2. [知识图谱驱动的软件设计](#2-知识图谱驱动的软件设计)
3. [LLM-as-Judge 评估量规最佳实践](#3-llm-as-judge-评估量规最佳实践)
4. [Git Hooks 对 AI 生成代码的质量门禁](#4-git-hooks-对-ai-生成代码的质量门禁)
5. [AI 智能体的 Spec-Driven Development](#5-ai-智能体的-spec-driven-development)
6. [总结：对 v2.0 重设计的启示](#6-总结对-v20-重设计的启示)

---

## 1. 生产级 AI 编码流水线的质量保障机制

### 1.1 SWE-bench / SWE-agent（Princeton NLP）

**核心架构：**
- **Agent-Computer Interface (ACI)**：智能体通过 bash 终端与代码仓库交互，而不是直接操作文件。这模拟了人类开发者的真实工作流。
- **三个命令：** `find`（定位）、`edit`（修改）、`submit`（提交）。极简接口减少智能体的误操作空间。
- **沙箱执行：** 每个任务在隔离的 Docker 容器中运行，防止环境污染和级联故障。

**质量保障的三层机制：**

| 层级 | 机制 | 具体实现 |
|------|------|----------|
| **L1: 执行验证** | 测试驱动判定 | 通过仓库自带的单元测试/回归测试来判定修复是否成功（pass@1） |
| **L2: 行为约束** | ACI 限制 | 只暴露必要命令，智能体无法执行任意破坏性操作 |
| **L3: 评估基准** | SWE-bench Verified | 人工过滤的 500 个实例，排除不可靠的测试用例，确保评估公正 |

**关键教训：**
- ⚠️ SWE-bench 的原始测试有大量假阳性，**Verified 子集是人工清洗后的结果**。你的流水线也需要"可信测试集"的概念。
- ✅ **"先跑测试，再改代码"** 的模式确保了回归安全。智能体必须确认当前测试状态后才能开始修改。
- ✅ 极简 ACI 设计（只有 3 个命令）反而提高了成功率——**少即是多**。

### 1.2 Aider（Paul Gauthier）

**核心创新：Repo Map（仓库地图）**

Aider 的质量核心不在于事后检查，而在于**事前上下文供给**：

```
1. 自动生成 repo map → 识别哪些文件与当前任务相关
2. 将 map 注入 prompt → LLM 获得全局视角
3. 用 Tree-sitter AST 理解代码结构 → 精准定位修改点
```

**质量保障流水线：**

```
┌─────────────┐    ┌──────────────┐    ┌───────────┐    ┌──────────┐
│ Repo Map    │ →  │ Architect    │ →  │ Editor    │ →  │ Lint +   │
│ 上下文注入   │    │ 模式: 架构设计 │    │ 模式: 编码  │    │ Test     │
└─────────────┘    └──────────────┘    └───────────┘    └──────────┘
                                                              │
                                                     ┌────────▼────────┐
                                                     │ 自动修复循环      │
                                                     │ lint/test 失败   │
                                                     │ → LLM 分析错误   │
                                                     │ → 自动修正       │
                                                     └─────────────────┘
```

**关键机制：**

1. **Lint/Test 自动循环：** 每次代码修改后自动运行 linter 和测试套件。如果失败，将错误输出反馈给 LLM 进行自动修复。这是一个**闭环自愈系统**。

2. **Architect/Editor 分离模式：**
   - Architect 模式：LLM 只负责架构决策、设计思路
   - Editor 模式：另一个 LLM（或同一 LLM 的不同角色）负责代码实现
   - 分离关注点，防止"边写边设计"导致的架构漂移

3. **Git 原生集成：** 每次修改自动生成清晰、原子化的 commit。Aider 强制使用 Conventional Commits 格式。

4. **Map 刷新策略：** `--map-refresh` 控制仓库地图刷新频率（auto/always/files/manual），在大型仓库中平衡上下文质量和 token 消耗。

**关键教训：**
- ✅ **"Lint + Test + 自动修复循环"** 是最低成本的质量保障。你的 v2.0 必须内置此循环。
- ✅ **Architect/Editor 分离** 是防止代码质量退化的关键模式。一个智能体想架构，另一个只写代码。
- ⚠️ Aider 在处理 50+ 文件的修改时成功率急剧下降——**任务粒度控制**（每次只改 1-5 个文件）是实战硬道理。

### 1.3 OpenHands（All Hands AI）

**核心架构：**

OpenHands 的架构是**事件驱动的多智能体协作**：

```
┌──────────────────────────────────────────────┐
│              Agent Controller                  │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ CodeAct  │  │ Browsing │  │ CLI Agent  │  │
│  │ Agent    │  │ Agent    │  │            │  │
│  └──────────┘  └──────────┘  └────────────┘  │
│        │             │              │          │
│        └─────────────┼──────────────┘          │
│                      │                         │
│            ┌─────────▼─────────┐               │
│            │   Sandbox (Docker) │               │
│            │   - 文件系统        │               │
│            │   - Shell 环境      │               │
│            │   - Jupyter        │               │
│            └───────────────────┘               │
└──────────────────────────────────────────────┘
```

**质量保障特点：**

1. **沙箱隔离：** 所有代码执行在 Docker 容器内，绝对的执行安全。
2. **状态管理：** 通过 Event Stream 记录智能体的每一步操作，支持回溯和审计。
3. **人类反馈环（Human-in-the-Loop）：** 关键操作（如 git push、执行危险命令）需要人工确认。
4. **SDK 化：** 最新的 software-agent-sdk 将 Agent 和 Agent Server 分离，支持可编程的工作流定义。

**关键教训：**
- ✅ **Event Stream 审计日志** 是调试 AI 流水线的生命线。你的 v2.0 如果没有完整的事件追踪，排障会极其痛苦。
- ✅ **沙箱不是可选的，是必需的。** 7 个 P0 中有几个可能就是因为缺少沙箱隔离。
- ⚠️ OpenHands 的复杂度很高，但它的插件架构值得学习——让每个质量检查变成可插拔的插件。

### 1.4 行业通用模式总结

| 模式 | 描述 | 适用场景 |
|------|------|----------|
| **Test-First Gate** | 代码合并前必须通过所有测试 | 所有流水线 |
| **Auto-Fix Loop** | lint/test 失败 → AI 自动修复 → 重新验证 | 迭代开发 |
| **Architect/Editor Split** | 架构和实现由不同智能体完成 | 复杂功能 |
| **Sandbox Execution** | 隔离环境中运行所有 AI 生成的代码 | 安全关键场景 |
| **Human-in-the-Loop** | 高风险操作需要人工确认 | 生产环境 |
| **Event Audit Trail** | 记录每个智能体的每步操作 | 调试&合规 |

---

## 2. 知识图谱驱动的软件设计

### 2.1 核心理念

**知识图谱驱动软件设计（Knowledge Graph Driven Software Design, KGDSD）** 是一种在编码前将领域知识结构化为图数据库的方法论。它不是"先画图再编码"，而是**让领域知识本身成为可查询、可推理、可验证的编码基础**。

### 2.2 知识图谱的结构层次

```
                    ┌──────────────────┐
                    │   业务概念层        │  ← 领域术语、实体、关系
                    │   (Business       │
                    │    Concepts)      │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   约束规则层        │  ← 业务规则、不变量、验证逻辑
                    │   (Constraints    │
                    │    & Rules)       │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   组件映射层        │  ← 概念→模块→文件的映射
                    │   (Component      │
                    │    Mapping)       │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   代码生成层        │  ← 从图谱生成代码骨架
                    │   (Code Gen)      │
                    └──────────────────┘
```

### 2.3 实战实现模式

#### 模式 A：基于 Neo4j/GraphDB 的领域建模

```cypher
// 示例：电商领域的知识图谱
CREATE (order:Entity {name: "Order", module: "order-service"})
CREATE (item:Entity {name: "LineItem", module: "order-service"})
CREATE (product:Entity {name: "Product", module: "catalog-service"})

CREATE (order)-[:CONTAINS {cardinality: "1..*"}]->(item)
CREATE (item)-[:REFERENCES]->(product)

CREATE (constraint:Rule {name: "OrderTotalMustBePositive", 
                         expression: "order.total > 0"})
CREATE (order)-[:CONSTRAINED_BY]->(constraint)
```

**实战工具链：**
- **本体定义：** OWL/RDF 或简化版 YAML schema
- **图谱存储：** Neo4j、ArangoDB、或嵌入式 SQLite + JSON
- **代码生成：** 从图谱遍历生成：DTO 类、API 端点、验证器、数据库迁移脚本
- **一致性检查：** CI 中运行图谱校验，确保代码实现不偏离领域模型

#### 模式 B：基于 YAML/Markdown 的轻量级知识建模

适合中小型项目，不需要完整的图数据库：

```yaml
# domain-knowledge.yml
entities:
  Order:
    fields:
      - name: id
        type: UUID
        required: true
      - name: total
        type: Decimal
        constraints: [">0"]
    relations:
      - target: LineItem
        type: one_to_many
        cascade: true

  LineItem:
    fields:
      - name: product_id
        type: UUID
      - name: quantity
        type: Integer
        constraints: [">0", "<=100"]

business_rules:
  - name: "订单总额必须等于明细总和"
    check: "order.total == sum(item.subtotal for item in order.line_items)"
    severity: error

  - name: "库存不足时订单状态应为PENDING"
    check: "order.status == 'PENDING' if any item out of stock"
    severity: warning
```

**AI 智能体如何使用此建模：**

1. **需求分析阶段：** AI 将 user story 转化为实体和关系
2. **设计审查阶段：** AI 检查 proposed code 是否违反图谱中的约束
3. **代码生成阶段：** AI 从图谱自动生成 TypeScript interface、Python dataclass、SQL schema
4. **测试生成阶段：** AI 从 business_rules 自动生成对应的测试用例

### 2.4 关键教训

- ✅ **图谱必须在代码之前存在**，否则就沦为文档（而且通常是过时的文档）
- ✅ **约束规则应该是可执行的**（不仅是描述性文字），这样 AI 和 CI 都能验证
- ⚠️ 图谱维护成本高——**从简单开始**（YAML 建模），不要一上来就 Neo4j
- ✅ **双向同步**：代码变了 → 更新图谱；图谱变了 → 标记相关代码为"待更新"
- ✅ 图谱中的每个 entity 应该直接映射到一个**文件路径**或**模块**，做到可追溯

### 2.5 与 AI 编码流水线的集成点

```
User Story → [KG Extractor Agent] → 领域图谱
                                          │
                    ┌─────────────────────┤
                    ▼                     ▼
           [Architect Agent]      [Validator Agent]
           根据图谱设计架构          检查代码是否符合图谱约束
                    │                     │
                    └─────────┬───────────┘
                              ▼
                    [Code Generator Agent]
                    根据图谱+架构生成代码
                              │
                              ▼
                    [Test Generator Agent]
                    从 business_rules 生成测试
```

---

## 3. LLM-as-Judge 评估量规最佳实践

### 3.1 核心挑战

根据 Microsoft 的 **llm-as-judge** 框架研究和行业实践：

| 挑战 | 表现 | 缓解策略 |
|------|------|----------|
| **流畅性偏差（Fluency Bias）** | LLM 倾向于给"看起来漂亮"的代码高分，即使有逻辑错误 | 强制要求执行验证，不只看文本 |
| **立场偏差（Position Bias）** | 评估结果受 prompt 中候选顺序影响 | 随机化顺序、多次评估取平均 |
| **自我增强偏差** | LLM 更喜欢自己生成的代码风格 | 使用不同模型做 Judge |
| **过度纠正** | LLM 审查者会过度纠正，引入新的 bug | 限制建议范围，要求最小改动 |

### 3.2 实战评估量规设计

#### 维度一：代码正确性（权重 40%）

| 分数 | 标准 | 验证方式 |
|------|------|----------|
| 5 | 通过所有测试，无逻辑错误 | `pytest --strict` 全部通过 |
| 4 | 通过所有测试，有 1-2 个非关键边界情况未处理 | 测试通过但有 TODO |
| 3 | 核心功能测试通过，但边缘情况有 bug | 部分测试失败 |
| 2 | 核心功能有逻辑错误 | 主要测试失败 |
| 1 | 无法运行，语法错误或运行时崩溃 | `SyntaxError` / `ImportError` |

**现实检测：** 此维度必须有**实际执行结果**支撑，不能仅凭 LLM 判断。

#### 维度二：代码质量（权重 25%）

```python
# LLM Judge 评分提示词模板
"""
请按以下标准评分（1-5分）：

1. 命名清晰度：变量/函数/类名是否自解释？
2. 函数职责单一：每个函数是否只做一件事？
3. 代码复杂度：是否存在深层嵌套（>3层）、过长函数（>50行）？
4. 错误处理：是否有适当的 try/except、输入验证？
5. 类型安全：是否有类型注解？（Python/TS）

输出格式：JSON
{
  "naming": <1-5>,
  "single_responsibility": <1-5>,
  "complexity": <1-5>,
  "error_handling": <1-5>,
  "type_safety": <1-5>,
  "overall": <1-5>,
  "specific_issues": ["问题1", "问题2"]
}
"""
```

#### 维度三：架构合规性（权重 20%）

检查生成的代码是否遵循项目的架构规则：
- 是否在正确的模块/层级中？（如：业务逻辑不在 Controller 层）
- 是否正确使用了项目的依赖注入模式？
- 是否引入了未授权的依赖？

**实战技巧：** 不要用 LLM 单独判断架构合规性——用 **ArchUnit（Java）、import-linter（Python）、dependency-cruiser（JS）** 等静态分析工具。

#### 维度四：安全性（权重 15%）

| 检查项 | 检测方式 |
|--------|----------|
| SQL 注入 | 使用参数化查询？ |
| XSS | 输出是否转义？ |
| 硬编码密钥 | 正则扫描 `password\s*=\s*["']` |
| 路径遍历 | 是否拼接用户输入到文件路径？ |

```yaml
# Bandit / Semgrep 集成配置
security_rules:
  - id: no-hardcoded-secrets
    pattern: '(password|secret|api_key)\s*=\s*["\x27][^"\x27]{3,}'
    severity: CRITICAL
    
  - id: no-eval
    pattern: 'eval\s*\('
    severity: CRITICAL
    
  - id: no-exec
    pattern: 'exec\s*\('
    severity: HIGH
```

### 3.3 Microsoft llm-as-judge 框架的核心模式

```
┌──────────────────────────────────────────────────────┐
│                  LLM-as-Judge Pipeline                │
│                                                       │
│  ┌─────────┐   ┌──────────┐   ┌──────────────────┐  │
│  │ Criteria│ → │ Evidence │ → │ Scoring +        │  │
│  │ Config  │   │ Collector │   │ Justification    │  │
│  └─────────┘   └──────────┘   └──────────────────┘  │
│       │              │                  │             │
│       │     ┌────────▼────────┐         │             │
│       │     │  Static Analysis│         │             │
│       │     │  + Test Runner  │         │             │
│       │     └────────┬────────┘         │             │
│       │              │                  │             │
│       └──────────────┼──────────────────┘             │
│                      ▼                                │
│            ┌─────────────────┐                        │
│            │ Judge LLM       │                        │
│            │ (不同模型)       │                        │
│            └────────┬────────┘                        │
│                     ▼                                 │
│            ┌─────────────────┐                        │
│            │ 评审报告         │                        │
│            │ - 总分           │                        │
│            │ - 分维度评分      │                        │
│            │ - 具体问题列表     │                        │
│            │ - 修复建议        │                        │
│            └─────────────────┘                        │
└──────────────────────────────────────────────────────┘
```

**关键原则：**

1. **Judge 模型必须与生成模型不同** —— 防止自我偏好偏差
2. **证据先行** —— 先收集静态分析、测试结果、lint 输出等客观证据，再让 LLM 评判
3. **结构化输出** —— Judge 必须输出 JSON，包含评分 + 理由 + 具体行号
4. **多轮评判** —— 对关键代码进行 2-3 轮独立评判取平均值

### 3.4 反模式⚠️

| 反模式 | 后果 | 正确做法 |
|--------|------|----------|
| 仅凭 LLM 判断正确性 | 流畅但错误的代码得高分 | 必须先执行测试 |
| Judge 提示词太模糊 | 评分不稳定、无法复现 | 使用精确的评分标准+示例 |
| 一次性评判大量代码 | 评分质量断崖下降 | 每次评判不超过 200 行 |
| 忽略静态分析结果 | 漏掉硬编码密钥等机械问题 | 静态分析作为 Judge 的输入 |

---

## 4. Git Hooks 对 AI 生成代码的质量门禁

### 4.1 业界实战架构

基于 **phoenix-assistant/ai-code-quality-gate** 和 **Antigravity Lab** 的实践：

```
┌─────────────────────────────────────────────────────────────┐
│                  Git Hook Quality Gates                       │
│                                                              │
│  pre-commit ──────► pre-push ──────► post-merge ────► CI    │
│      │                  │                │             │     │
│      ▼                  ▼                ▼             ▼     │
│  ┌────────┐      ┌──────────┐    ┌───────────┐  ┌────────┐ │
│  │AI标记   │      │集成测试    │    │回归测试    │  │完整流水 │ │
│  │检测     │      │必须通过    │    │必须通过    │  │线       │ │
│  │Lint    │      │安全扫描    │    │覆盖率检查  │  │         │ │
│  │单元测试 │      │           │    │           │  │         │ │
│  └────────┘      └──────────┘    └───────────┘  └────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Pre-commit Hook：AI 代码检测与标记

```bash
#!/bin/bash
# .git/hooks/pre-commit

# 1. 检测 AI 生成的代码模式
AI_PATTERNS=(
    "# Generated by AI"           # 注释标记
    "AI-generated code"            # 文档标记
    "\\.ai-generated\\."           # 文件扩展名
)

for file in $(git diff --cached --name-only); do
    # 检查 .ai-metadata.json 是否存在
    if [[ -f "$(dirname "$file")/.ai-metadata.json" ]]; then
        echo "⚠️  AI 生成的代码: $file"
        echo "   请在 commit message 中添加 'REVIEWED: <reviewer>' "
        
        # 检查是否已被审查
        REVIEWED=$(git log -1 --pretty=%B | grep "REVIEWED:")
        if [[ -z "$REVIEWED" ]]; then
            echo "❌ 阻止提交: AI 代码未经人工审查"
            exit 1
        fi
    fi
done

# 2. 运行 lint
echo "🔍 运行 lint..."
npm run lint -- --fix || exit 1

# 3. 运行相关单元测试
echo "🧪 运行测试..."
npm test -- --related $(git diff --cached --name-only | tr '\n' ' ') || exit 1
```

### 4.3 Pre-push Hook：完整质量门禁

这是**最关键的关卡**——代码可以 commit，但不能 push 到远程：

```bash
#!/bin/bash
# .agent/hooks/pre-push-gate.sh

echo "🚦 运行 pre-push 质量门禁..."

# 1. 完整测试套件
echo "  [1/5] 运行完整单元测试..."
pytest --strict -x --tb=short || {
    echo "❌ 单元测试失败，修复后再 push"
    exit 1
}

# 2. 安全扫描
echo "  [2/5] 安全扫描..."
bandit -r src/ -ll -f json > security_report.json
CRITICAL_COUNT=$(jq '.results | map(select(.issue_severity == "HIGH")) | length' security_report.json)
if [[ "$CRITICAL_COUNT" -gt 0 ]]; then
    echo "❌ 发现 $CRITICAL_COUNT 个高危安全问题"
    exit 1
fi

# 3. AI 代码质量评分
echo "  [3/5] AI 代码质量评估..."
python scripts/ai_quality_score.py --threshold 0.7 || {
    echo "❌ AI 代码质量评分低于阈值 (0.7)"
    exit 1
}

# 4. 覆盖率检查
echo "  [4/5] 测试覆盖率检查..."
coverage run -m pytest
coverage report --fail-under=80 || {
    echo "❌ 覆盖率低于 80%"
    exit 1
}

# 5. 依赖检查
echo "  [5/5] 依赖审计..."
npm audit --audit-level=high || {
    echo "⚠️  存在高危依赖漏洞，请检查"
}

echo "✅ 所有质量门禁通过！"
```

### 4.4 AI 代码质量评分工具 (ai_quality_score.py)

```python
"""AI 代码质量评分脚本 - 集成到 pre-push hook"""
import re
import sys
import json
from pathlib import Path

# AI 生成代码的典型坏味道模式
AI_SMELLS = [
    (r'#\s*TODO:.*implement', 0.5, "未实现的 TODO 占位"),
    (r'pass\s*#.*placeholder', 0.3, "placeholder 注释"),
    (r'print\(.*\)', 0.1, "调试 print 语句"),
    (r'except\s*:', 0.4, "裸 except（无异常类型）"),
    (r'\.\.\.\s*#', 0.3, "省略号占位"),
    (r'import\s+\w+\s*#.*unused', 0.2, "未使用的导入"),
]

def scan_file(filepath: str) -> dict:
    """扫描单个文件的 AI 代码质量"""
    issues = []
    score = 1.0
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        lines = content.split('\n')
    
    for line_num, line in enumerate(lines, 1):
        for pattern, penalty, description in AI_SMELLS:
            if re.search(pattern, line):
                issues.append({
                    "file": filepath,
                    "line": line_num,
                    "pattern": pattern,
                    "description": description,
                    "penalty": penalty
                })
                score -= penalty
    
    return {
        "file": filepath,
        "score": max(0.0, score),
        "issues": issues
    }

def main():
    threshold = float(sys.argv[sys.argv.index('--threshold') + 1]) if '--threshold' in sys.argv else 0.7
    results = []
    
    for py_file in Path('src').rglob('*.py'):
        result = scan_file(str(py_file))
        results.append(result)
    
    overall_score = sum(r['score'] for r in results) / len(results) if results else 1.0
    all_issues = [i for r in results for i in r['issues']]
    
    print(json.dumps({
        "overall_score": round(overall_score, 2),
        "threshold": threshold,
        "passed": overall_score >= threshold,
        "total_files": len(results),
        "total_issues": len(all_issues),
        "issues": all_issues[:20]  # 只展示前 20 个
    }, indent=2))
    
    sys.exit(0 if overall_score >= threshold else 1)

if __name__ == '__main__':
    main()
```

### 4.5 Antigravity CLI 的 Hooks 设计（重要参考）

Antigravity Lab 的 `.agent/hooks/` 模式：

```
.agent/
├── hooks/
│   ├── pre-commit.sh        # 轻量级检查（< 5秒）
│   ├── pre-push-gate.sh     # 重量级门禁（可 5+ 分钟）
│   ├── post-generate.sh     # AI 生成代码后的即时检查
│   └── config.yml           # Hook 配置
├── rules/
│   ├── architecture.md      # 架构规则（markdown，LLM 可读）
│   ├── coding-standards.md  # 编码规范
│   └── security.md          # 安全规则
└── memories/
    └── decisions.json       # 架构决策记录
```

**关键设计：**
- **`post-generate` hook** —— AI 每生成一个文件后立即运行，不等 commit。这是最快的反馈环。
- **Markdown 规则文件** —— 不仅给人看，也给 LLM 看。在生成代码前，将规则文件注入 prompt。
- **可配置的 severity** —— 不是所有规则都是 blocker。分为 `error`（阻止）/ `warn`（警告）/ `info`（建议）。

### 4.6 关键教训

| 教训 | 细节 |
|------|------|
| ✅ pre-push > pre-commit | 重量级检查放 pre-push（可以 commit 但不让 push），轻量级放 pre-commit |
| ✅ post-generate hook | AI 生成后立即检查，反馈环最短 |
| ✅ 规则 LLM 可读 | 用 Markdown 写规则，直接注入 AI prompt |
| ⚠️ hook 超时设计 | 每个 hook 设置超时（如 5 分钟），防止堵塞开发流程 |
| ✅ 渐进式采纳 | 先用 warn 模式运行 2 周，团队适应后再升级为 error |
| ⚠️ 不要完全依赖 hook | Hook 可被 `--no-verify` 绕过。CI 是最终防线 |

---

## 5. AI 智能体的 Spec-Driven Development

### 5.1 核心理念

**Spec-Driven Development (SDD)** 是一种让 AI 智能体**先写规范、再写代码**的方法论。核心原则：

> 规范是唯一的真相源（Single Source of Truth），代码是对规范的实现。

### 5.2 五步工作流

```
┌─────────────────────────────────────────────────────────────┐
│              Spec-Driven Development for AI Agents           │
│                                                              │
│  Step 1          Step 2          Step 3          Step 4     │
│  ┌────────┐     ┌────────┐     ┌────────┐     ┌────────┐   │
│  │ 需求    │ →   │ 规范    │ →   │ 代码    │ →   │ 验证    │   │
│  │ 收集    │     │ 编写    │     │ 生成    │     │ 一致性   │   │
│  └────────┘     └────────┘     └────────┘     └────────┘   │
│       │              │              │              │         │
│       ▼              ▼              ▼              ▼         │
│  User Story     API Spec       Generated      Spec ↔ Code   │
│  自然语言       OpenAPI/       Code           Conformance   │
│                GraphQL/       (from spec)     Test           │
│                JSON Schema                                   │
│                                                              │
│  Step 5: 迭代循环（规范 ↔ 代码双向同步）                      │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 实战规范格式

#### 格式 A：OpenAPI / GraphQL Schema（API 开发）

```yaml
# spec/order-api.yaml
openapi: 3.0.0
info:
  title: Order Service API
  version: 1.0.0
paths:
  /orders:
    post:
      summary: 创建订单
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateOrderRequest'
      responses:
        '201':
          description: 订单创建成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '400':
          description: 参数错误
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '409':
          description: 库存不足
          
components:
  schemas:
    CreateOrderRequest:
      type: object
      required: [items]
      properties:
        items:
          type: array
          items:
            $ref: '#/components/schemas/OrderItemInput'
          minItems: 1
          maxItems: 50
    OrderItemInput:
      type: object
      required: [product_id, quantity]
      properties:
        product_id:
          type: string
          format: uuid
        quantity:
          type: integer
          minimum: 1
          maximum: 100
    ErrorResponse:
      type: object
      properties:
        error:
          type: string
        code:
          type: string
        details:
          type: object
```

**AI 智能体使用此规范的方式：**

1. 读取 `spec/order-api.yaml`
2. 自动生成：
   - FastAPI/Express 路由
   - Pydantic/Zod 验证模型
   - API 测试用例（基于 response schema）
   - API 文档

#### 格式 B：Gherkin BDD 规范（业务逻辑）

```gherkin
# spec/order.feature
Feature: 订单创建
  作为买家
  我想创建订单
  以便购买商品

  Scenario: 成功创建订单
    Given 商品 "MacBook Pro" 库存为 10 件
    And 商品价格为 $2000
    When 我创建订单包含 1 件 "MacBook Pro"
    Then 订单状态应为 "CONFIRMED"
    And 订单总额应为 $2000
    And 商品 "MacBook Pro" 库存应为 9 件

  Scenario: 库存不足时创建订单失败
    Given 商品 "iPhone" 库存为 0 件
    When 我创建订单包含 1 件 "iPhone"
    Then 应返回错误 "INSUFFICIENT_STOCK"
    And 订单不应被创建

  Scenario: 超过最大购买数量
    Given 商品 "AirPods" 库存为 200 件
    When 我创建订单包含 101 件 "AirPods"
    Then 应返回错误 "MAX_QUANTITY_EXCEEDED"
```

**测试自动生成：** 每个 Scenario 对应一个测试用例。AI 从 spec 生成测试骨架，开发者只需实现 Given/When/Then 的步骤定义。

#### 格式 C：Spec.md（通用功能规范）

适合没有现成 DSL 的场景：

```markdown
# spec/payment-gateway.md

## 功能：支付网关集成

### 输入
- `amount`: Decimal, > 0, 最大 999999.99
- `currency`: String, ISO 4217 三位码
- `payment_method`: Enum {CREDIT_CARD, DEBIT_CARD, WALLET}

### 输出
- `transaction_id`: UUID
- `status`: Enum {SUCCESS, FAILED, PENDING}
- `error`: Optional<String>

### 业务规则
1. 金额必须 > 0 且 ≤ 999999.99
2. 支付失败 → 自动重试 3 次，间隔 1 秒
3. 3 次重试后仍失败 → status = FAILED, error = "PAYMENT_FAILED"
4. 成功 → 发送确认通知（异步，不阻塞返回）

### 边界条件
- 并发支付同一订单 → 只处理第一笔
- 网络超时（>5秒）→ 视为 PENDING，后续轮询确认
- 金额为 0 → 直接返回 SUCCESS（免费订单）

### 错误码
| Code | 含义 |
|------|------|
| INVALID_AMOUNT | 金额无效 |
| PAYMENT_FAILED | 支付失败 |
| DUPLICATE | 重复支付 |
```

### 5.4 规范 ↔ 代码一致性验证

**这是 SDD 最关键的部分**——如何确保生成的代码真正符合规范？

```python
# scripts/spec_conformance.py
"""规范一致性检查工具"""

import yaml
import ast
from pathlib import Path

def check_api_conformance(spec_path: str, code_path: str) -> list[dict]:
    """检查 API 实现是否与 OpenAPI 规范一致"""
    spec = yaml.safe_load(Path(spec_path).read_text())
    violations = []
    
    for path, methods in spec['paths'].items():
        for method, details in methods.items():
            # 检查路由是否存在
            route_file = Path(code_path) / f"{path.replace('/', '_')}.py"
            if not route_file.exists():
                violations.append({
                    "severity": "error",
                    "spec": f"{method.upper()} {path}",
                    "issue": f"路由实现缺失: {route_file}"
                })
                continue
            
            # 检查请求/响应模型是否匹配
            if 'requestBody' in details:
                schema_name = details['requestBody']['content']['application/json']['schema']['$ref']
                # 在代码中搜索对应的 Pydantic 模型
                code = route_file.read_text()
                model_name = schema_name.split('/')[-1]
                if model_name not in code:
                    violations.append({
                        "severity": "error",
                        "spec": f"{method.upper()} {path}",
                        "issue": f"请求模型 {model_name} 在代码中未定义"
                    })
    
    return violations


def check_bdd_conformance(feature_path: str, test_path: str) -> list[dict]:
    """检查 BDD 测试是否覆盖所有 Scenario"""
    feature = Path(feature_path).read_text()
    tests = Path(test_path).read_text() if Path(test_path).exists() else ""
    
    violations = []
    
    # 解析 Scenario 名称
    import re
    scenarios = re.findall(r'Scenario:\s*(.+)', feature)
    
    for scenario in scenarios:
        if scenario.strip() not in tests:
            violations.append({
                "severity": "warning",
                "scenario": scenario,
                "issue": f"BDD 测试缺失: {scenario}"
            })
    
    return violations
```

### 5.5 GPT-Pilot / Pythagora 模式

**GPT-Pilot** 是最早实践 "spec-first" 的 AI 编码工具之一：

1. **Step 1 - 需求澄清：** AI 与用户对话，消除歧义
2. **Step 2 - 技术规范：** AI 输出结构化的技术规范文档（含数据模型、API 设计、文件结构）
3. **Step 3 - 用户审批：** 用户审查并批准规范（这是关键的人类把关点）
4. **Step 4 - 代码生成：** AI 严格按规范生成代码
5. **Step 5 - 自我审查：** AI 生成代码后自行审查是否偏离规范

**关键教训：**
- ✅ **用户审批规范这一环节**必不可少。省掉这步会导致 AI 在错误方向上狂奔。
- ✅ 规范是"合同"——生成代码后，AI 还可以用它来**自我审查**。
- ⚠️ GPT-Pilot 在复杂项目（>20 个文件）中成功率和一致性急剧下降。

### 5.6 Claude Code 的 CLAUDE.md / 规则文件模式

Anthropic 的 Claude Code 引入了项目级指导文件：

```markdown
# CLAUDE.md
## 项目架构
- 前端: React + TypeScript, src/client/
- 后端: Python FastAPI, src/server/
- 测试: pytest, tests/

## 编码规范
- 所有 Python 函数必须有类型注解
- API 端点返回 Pydantic 模型，不返回 dict
- 数据库操作通过 Repository 模式

## 不被允许的操作
- 不要修改数据库迁移文件，只能新增
- 不要直接 import 内部模块，使用公共 API
```

这种模式本质上就是**轻量级 spec-driven**——规则文件指导 AI 的每一步。

### 5.7 关键教训总结

| 原则 | 说明 |
|------|------|
| **规范在代码前** | 用 OpenAPI/Gherkin/Markdown 写规范，然后让 AI 生成代码 |
| **规范可验证** | 规范中的约束必须是可自动检查的（类型、范围、格式） |
| **双向同步** | 代码变了 → 更新规范；规范变了 → 标记代码"待更新" |
| **人类审批关卡** | 规范写完后必须经过人类审批，再让 AI 生成代码 |
| **从简单开始** | 不要追求完美的规范——从 OpenAPI + Gherkin 最关键的部分开始 |

---

## 6. 总结：对 v2.0 重设计的启示

### 6.1 必须修复的架构缺陷（对应 7 P0 + 14 P1）

基于上述研究，以下是 v2.0 必须内置的能力：

| 优先级 | 能力 | 来源 | 实现方式 |
|--------|------|------|----------|
| **P0** | Sandbox 执行隔离 | SWE-bench, OpenHands | Docker 容器，每个 Agent 独立环境 |
| **P0** | Event Audit Trail | OpenHands | 记录每个 Agent 的每一步操作和决策 |
| **P0** | Auto-Fix Loop | Aider | lint/test 失败 → AI 自动修复 → 重新验证 |
| **P0** | 测试驱动门禁 | SWE-bench | 先跑测试，改代码后再跑，不通过不合并 |
| **P0** | Human-in-the-Loop | OpenHands | 高风险操作需人工确认 |
| **P0** | Git Hook 质量门禁 | Antigravity, phoenix | pre-commit / pre-push / post-generate |
| **P0** | 规范 ↔ 代码一致性检查 | SDD 实践 | CI 中验证代码是否符合 spec |
| **P1** | Architect/Editor 分离 | Aider | 不同 Agent 负责架构 vs 实现 |
| **P1** | 知识图谱领域建模 | KGDSD | YAML 建模 → 约束验证 → 代码生成 |
| **P1** | LLM-as-Judge 评估 | Microsoft | 多维度评分 + 客观证据 + 不同 Judge 模型 |
| **P1** | Repo Map 上下文 | Aider | 自动生成仓库地图注入 Agent prompt |

### 6.2 推荐的流水线架构

```
┌──────────────────────────────────────────────────────────────┐
│                    v2.0 Pipeline Architecture                  │
│                                                               │
│  User Story                                                    │
│      │                                                        │
│      ▼                                                        │
│  ┌──────────────┐                                             │
│  │ Spec Writer   │ ← 产出 OpenAPI/Gherkin/spec.md              │
│  │ Agent         │                                            │
│  └──────┬───────┘                                             │
│         │                                                     │
│         ▼  [Human Approval Gate]                              │
│         │                                                     │
│  ┌──────▼───────┐                                             │
│  │ Architect    │ ← 产出架构设计（基于知识图谱）                │
│  │ Agent        │                                            │
│  └──────┬───────┘                                             │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐      │
│  │ Code Gen     │ → │ Test Gen     │ → │ Auto-Fix     │      │
│  │ Agent        │   │ Agent        │   │ Loop         │      │
│  └──────┬───────┘   └──────────────┘   └──────┬───────┘      │
│         │                                      │              │
│         ▼                                      ▼              │
│  ┌──────────────┐   ┌──────────────┐                         │
│  │ LLM Judge    │   │ Spec Conform │  ← 客观检查 + AI 评分    │
│  │ (Quality)    │   │ Check        │                         │
│  └──────┬───────┘   └──────┬───────┘                         │
│         │                  │                                  │
│         └────────┬─────────┘                                  │
│                  ▼                                            │
│  ┌──────────────────────────────────────┐                     │
│  │        Git Hook Quality Gates        │                     │
│  │  pre-commit → pre-push → CI          │                     │
│  └──────────────────────────────────────┘                     │
│                  │                                            │
│                  ▼                                            │
│           ┌──────────┐                                       │
│           │ Merge ✓   │                                       │
│           └──────────┘                                       │
└──────────────────────────────────────────────────────────────┘
```

### 6.3 立即行动项

1. **Week 1:** 实现 Sandbox 执行隔离 + Event Audit Trail（P0 最大）
2. **Week 2:** 实现 Auto-Fix Loop（lint + test 失败自动修复）
3. **Week 3:** 实现 Git Hook 质量门禁（pre-commit + pre-push）
4. **Week 4:** 实现 LLM-as-Judge 评估 + Spec Conformance Check
5. **Week 5+:** 知识图谱建模 + Architect/Editor 分离

### 6.4 关键指标

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| 测试通过率 | ≥ 95% | CI 统计 |
| AI 代码自动修复成功率 | ≥ 80% | Auto-Fix Loop 统计 |
| 规范一致性 | ≥ 90% | Spec Conformance Check |
| LLM Judge 评分 | ≥ 4.0/5.0 | 每次 PR 评估 |
| 安全漏洞 | 0 个 CRITICAL | Bandit/Semgrep |
| 人工审批率 | < 20% 操作需审批 | Event Audit 统计 |

---

## 参考资料

1. SWE-bench: https://www.swebench.com/ — Princeton NLP 的软件工程基准
2. Aider: https://github.com/Aider-AI/aider — AI 结对编程工具
3. OpenHands: https://github.com/OpenHands/OpenHands — 开源 AI 编码智能体平台
4. Microsoft llm-as-judge: https://github.com/microsoft/llm-as-judge — LLM 评判框架
5. ai-code-quality-gate: https://github.com/phoenix-assistant/ai-code-quality-gate — AI 代码质量检测
6. Antigravity Lab: https://antigravitylab.net/ — Agent hooks 设计实践
7. Spec-driven development (Wikipedia): https://en.wikipedia.org/wiki/Spec-driven_development
8. Specification-Driven Development: https://blog.rezvov.com/specification-driven-development-four-pillars

---

> 报告生成时间：2026-06-30  
> 工具：Hermes Agent (Nous Research) + DeepSeek V4 Pro
