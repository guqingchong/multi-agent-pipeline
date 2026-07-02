# multi-agent-pipeline v3.0 详细实施计划 + 用户旅程（修正版）

> 修正：1) 对抗审查=每个争议点3轮+第三方裁决 2) JOURNEY=基于PRD的全功能遍历 3) 新增PRD编制Phase

---

## 一、最终 Phase 序列：12 个 Phase

```
Phase 0:  INIT          ─ 项目骨架 + 选择工作流模板
Phase 1:  RESEARCH      ─ 四级知识图谱构建（全网深度调研）
Phase 2:  PRD           ─ [NEW] PRD编制 + 人机协同 + 逐点3轮对抗
Phase 3:  DESIGN        ─ 知识驱动的架构设计 + 逐点3轮对抗
Phase 4:  JOURNEY       ─ 全功能用户旅程设计（基于PRD遍历）
Phase 5:  DECOMPOSE     ─ 任务分解（DAG调度器）
Phase 6:  DEVELOP       ─ Feature编码（Agent Daemon并行 + Auto-Fix Loop）
Phase 7:  INTEGRATE     ─ 跨模块集成 + Spec Conformance Check
Phase 8:  TEST          ─ 全量回归（真实Playwright E2E）
Phase 9:  EVALUATE      ─ LLM-as-Judge + Evidence-First
Phase 10: ACCEPT        ─ Inspector审查 + 人类审批
Phase 11: DEPLOY        ─ 交付
```

---

## 二、对抗审查机制（修正）：逐点3轮 + 第三方裁决

### 旧版错误理解

```
第1轮：审查Agent提所有问题 → 第2轮：补充问题 → 第3轮：收敛定稿
这是3轮总共覆盖所有争议点。
```

### 修正版正确机制

```
对 PRD/DESIGN/JOURNEY 中的每一个争议点：

  争议点 X：
    第1轮对抗 → Agent A 观点 vs Agent B 观点
    第2轮对抗 → 各自补充论据，修正原观点
    第3轮对抗 → 最后陈述，无法一致
    ↓
    第三方裁决 Agent（独立LLM，与A/B均不同模型）做出裁决
    裁决结果：A胜 / B胜 / 折中方案
    ↓
    进入下一个争议点

不是"3轮讨论"，是"每个争议点的3轮辩论 + 独立裁决"。
```

### 实现

```python
@dataclass
class DebatePoint:
    id: str
    topic: str              # 争议主题
    position_a: str         # Agent A 的核心观点
    position_b: str         # Agent B 的核心观点
    rounds: List[DebateRound]  # 每个争议点可能有1-3轮

@dataclass
class DebateRound:
    round_number: int       # 1, 2, or 3
    argument_a: str         # Agent A 本轮论点
    evidence_a: List[str]   # Agent A 的本轮证据
    argument_b: str         # Agent B 本轮论点  
    evidence_b: List[str]   # Agent B 的本轮证据
    convergence: bool       # 是否已达成一致

@dataclass  
class ArbitrationResult:
    point_id: str
    winner: str             # "A" / "B" / "COMPROMISE"
    final_decision: str     # 裁决结果
    rationale: str          # 裁决理由
    arbiter_model: str      # 裁决使用的模型

class AdversarialReview:
    def debate_point(self, point: DebatePoint, 
                     agent_a: str, agent_b: str,
                     arbiter: str) -> ArbitrationResult:
        """
        对单个争议点执行最多3轮辩论，无法一致时由第三方裁决。
        
        流程：
          for round in 1..3:
            agent_a.argue(point, prev_rounds)
            agent_b.argue(point, prev_rounds)
            if agent_a 与 agent_b 达成一致:
              return 一致结果
          # 3轮后仍不一致
          return arbiter.arbitrate(point, all_rounds)
        """
        ...
    
    def debate_all_points(self, document: str, 
                          author_agent: str, reviewer_agent: str,
                          arbiter_agent: str) -> List[ArbitrationResult]:
        """
        对文档中的所有争议点逐一执行辩论+裁决。
        
        争议点识别：审查Agent标记文档中每一个有异议的位置。
        每个位置独立辩论。
        """
        points = self._extract_debate_points(document)
        results = []
        for point in points:
            result = self.debate_point(point, author_agent, 
                                       reviewer_agent, arbiter_agent)
            results.append(result)
            if result.winner != "COMPROMISE":
                # 修改文档，应用裁决
                self._apply_arbitration(document, point, result)
        return results
```

---

## 三、Phase 2: PRD 编制 [NEW]

### 为什么需要 PRD 在 RESEARCH 之后、DESIGN 之前

RESEARCH 产生的知识图谱是"这个领域有什么"。PRD 是"我们要做什么"。
不能跳过 PRD 直接做架构——PRD 定义了需求边界，架构是需求的实现方案。

### 执行流程

```
Step 1: 加载产品经理技能
  - skill_view("product-manager-skills")
  - 确保 PM 方法论到位：JTBD、用户故事、功能优先级

Step 2: 人机协同 PRD 编制
  Hermes (加载 PM 技能) 与用户对话：
    Q1: "这个系统的核心用户是谁？主要解决他们的什么问题？"
    Q2: "用户完成一次完整操作的关键步骤是什么？"
    Q3: "有没有竞品可以参考？它们做得好的地方、不好的地方？"
    Q4: "哪些功能是 MVP 必须有的？哪些可以延后？"
    Q5: "有没有特殊的合规/法规约束？"
  
  每次一问，渐进式深化。
  最终输出: specs/prd.md (初版)

Step 3: 逐点 3 轮对抗审查
  审查 Agent: CodeWhale（独立加载 PM 技能 + 独立深度调研）
  
  争议点示例：
    争议点 P1: "PRD 定义的功能范围是否过大？MVP 应该缩小到什么程度？"
    争议点 P2: "对话流程设计中，用户上传 PDF 后是否应该自动触发解析？"
    争议点 P3: "财务测算的精度要求——万元级还是元级？"
    ...
  
  每个争议点: 3 轮辩论 → 无法一致 → 第三方裁决 Agent 裁决

Step 4: 人类决策定稿
  Hermes 汇总所有裁决结果，呈现给用户：
    "PRD 编制完毕。共识别 12 个争议点，已全部裁决。
     以下 3 个裁决结果需要你最终确认（涉及重大决策）：
     1. MVP 范围：裁决为缩小到 6 个核心功能...
     2. 对话触发：裁决为上传后自动解析...
     3. 精度要求：裁决为万元级...
     是否批准？或需要调整？"

Step 5: 用户批准 → PRD 定稿 → 进入 Phase 3 (DESIGN)
```

### advance 条件（check_prd）

```
- specs/prd.md 存在
- PRD 包含：用户画像、核心旅程、功能清单、MVP范围、非功能需求
- 至少识别出 ≥8 个争议点
- 所有争议点有完整的 3 轮辩论记录 + 第三方裁决结果
- 人类已审批（prd_approved=true）
```

---

## 四、Phase 4: JOURNEY — 全功能用户旅程

### 修正：不是 5 条核心旅程，而是基于 PRD 的完整遍历

```
旧版: 选择 3-5 条最核心的用户旅程
修正: 遍历 PRD 中定义的每一个用户功能，为每个功能设计旅程

覆盖规则:
  1. PRD 中的每个用户故事 → 至少 1 条旅程
  2. 每个功能入口 → 至少 1 条旅程（含异常路径）
  3. 每个意图路由 → 至少 1 条旅程
  4. 跨功能组合场景 → 至少组合旅程

示例（供热管网系统）:
  旅程 1: 创建项目 + 上传PDF + 确认字段 + 补充信息 + 审核 + 导出
  旅程 2: 仅对话提供信息（不上传文件）+ 财政自平衡测算
  旅程 3: 上传损坏PDF → 系统提示 + 上传正确PDF → 正常流程
  旅程 4: 知识库查询 → 查看政策原文 → 追问细节
  旅程 5: 修改参数 → 自动重算 → 对比前后结果
  旅程 6: 导出实施方案 + 导出财务评价报告 → 对比两种文档
  旅程 7: 多项目切换 + 项目进度对比
  ...（遍历 PRD 中所有功能定义）
```

### 验收标准

```
- specs/journey.md 中旅程数量 = PRD 中功能定义数量 × 覆盖系数
  （每个功能 ≥1 条正常路径 + 至少 1 条异常路径）
- 每条旅程有完整的对话脚本 + "好/坏"标准
- 异常路径覆盖率 ≥ 80%（PRD 中定义的异常场景）
```

---

## 五、修正后的用户旅程

### 步骤 2：知识图谱构建（Phase 1）

```
Hermes: 知识图谱已构建完毕。
        - Level 1 业务概念层: 23 个核心概念
        - Level 2 约束规则层: 15 条可执行业务规则
        - Level 3 组件映射层: 18 个概念→组件映射
        - Level 4 代码生成规则层: 12 条模板规则
        
        进入 Phase 2: PRD 编制。
```

### 步骤 3：PRD 编制（Phase 2）[NEW]

```
Hermes: [加载 product-manager-skills]
        现在开始 PRD 编制。我会与你协同讨论，逐步明确需求。
        
        Q1: 这个系统的核心用户是谁？主要解决什么问题？
        
用户:  核心用户是县级政府的财政局工作人员。
        他们需要快速编制专项债申报材料（一案两书），
        主要痛点是：不会做财务测算、不熟悉政策法规、
        编制周期长（通常要1个月）。

Hermes: 明白了。Q2: 用户完成一次完整操作的关键步骤是什么？

用户:  1) 上传可研报告PDF 
        2) 系统自动提取项目信息
        3) 补充缺失的财务参数
        4) 系统自动生成财务测算
        5) 审核合规性
        6) 导出实施方案+财务评价报告+法律意见书

... [5轮对话后 PRD 初版完成]

Hermes: PRD 初版完成。现委派审查 Agent 进行逐点对抗审查。
        
        [委派 CodeWhale 审查...]
        
        审查 Agent 识别出 12 个争议点。开始逐点3轮辩论：
        
        争议点 P1: MVP 范围是否过大？
          第1轮: CodeWhale 认为应缩小到6个核心功能...
          第2轮: Hermes 补充论据... CodeWhale 修正观点...
          第3轮: 双方仍不一致 → 第三方裁决 Agent 裁决: CodeWhale 胜
          裁决结果: MVP 范围为 6 个功能
        
        争议点 P2: PDF 上传后是否自动触发解析？
          第1轮: ... 
          第2轮: 双方在第2轮达成一致: 自动触发
          无需裁决
        
        ... [12个争议点全部处理完毕]
        
        12个争议点已全部处理: 3个达成一致, 9个第三方裁决。
        
        以下 3 个裁决结果需要你最终确认：
        1. MVP 范围: 6 个核心功能（详情见裁决记录）
        2. 数据精度: 万元级
        3. 合规策略: 白名单+红线，不做全量合规
        
        是否批准？
```

### 步骤 4：架构设计（Phase 3）

与 PRD 相同的逐点 3 轮对抗机制。
设计 Agent 基于知识图谱+PRD 编写架构，审查 Agent 逐点质疑。

### 步骤 5：全功能旅程（Phase 4）

基于 PRD 的完整功能清单，Hermes 遍历设计每条旅程。
审查 Agent 同样逐点质疑每条旅程的合理性。

---

## 六、`adversarial_review.py` 完整接口

```python
class AdversarialReview:
    """
    逐点3轮对抗 + 第三方裁决引擎。
    
    用于: PRD / DESIGN / JOURNEY 三个 Phase 的审查环节。
    """
    
    def __init__(self, arbiter_model: str = "deepseek-v4-pro"):
        self.arbiter_model = arbiter_model
    
    def review_document(self, 
                        document_path: Path,
                        author_agent: str,      # 编写者
                        reviewer_agent: str,    # 审查者
                        reviewer_context: str = "",  # 审查者的独立调研上下文
    ) -> ReviewReport:
        """
        完整审查流程：
        
        1. 审查 Agent 独立加载文档 + 独立深度调研
        2. 标记所有争议点（每个异议位置 = 1 个争议点）
        3. 对每个争议点执行逐点3轮辩论
        4. 无法一致时，第三方裁决 Agent 作出裁决
        5. 应用所有裁决结果，生成定稿文档
        6. 输出完整审查报告（含所有辩论记录+裁决记录）
        """
        ...
    
    def _extract_debate_points(self, document: str) -> List[DebatePoint]:
        """
        从审查 Agent 的反馈中提取争议点。
        
        争议点 = 审查 Agent 标记的每个异议位置。
        不是按主题分组，而是按具体位置。
        
        例如 PRD 中一个段落被标记了 3 处异议 → 3 个争议点。
        """
        ...
    
    def debate_point(self, point: DebatePoint, ...) -> ArbitrationResult:
        """对单个争议点执行最多3轮辩论"""
        ...
    
    def _arbitrate(self, point: DebatePoint, rounds: List[DebateRound]) -> ArbitrationResult:
        """
        第三方裁决。
        
        裁决 Agent:
          - 使用独立模型（与作者、审查者均不同）
          - 输入: 全部辩论记录 + 双方证据
          - 输出: A胜 / B胜 / 折中方案 + 理由
        """
        ...
```

---

## 七、完整文件索引

`C:/tmp/multi-agent-pipeline/docs/` 下的全部文档：

| 文档 | 内容 |
|------|------|
| **pipeline-v3-implementation-plan.md** | ⭐ 本文件 — 详细实施计划 + 用户旅程 |
| pipeline-v3-final-merged-plan.md | 三方合并方案（Hermes + 调研 + Claude Code） |
| pipeline-v3-complete-refactoring-plan.md | 前期方案（已被最终版取代） |
| code-review-v2.md | Hermes 代码审查（40 项） |
| research-report.md | 全网调研报告（1023 行） |
