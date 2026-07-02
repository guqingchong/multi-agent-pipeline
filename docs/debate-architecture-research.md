# 多Agent辩论架构深度调研

> 基于 D3 (Debate/Deliberate/Decide) + MAD协议比较 + Multi-Agent Memory 三大方向

---

## 一、核心发现

### 1.1 当前CLI dispatch式辩论的根本缺陷

每次dispatch = 新进程 = 零记忆。Claude第2轮不知道第1轮说了什么。**这不是"辩论"，是三轮互不认识的独立审查。** 唯一的信息桥梁是Hermes手动写的辩护摘要。

学术上这叫**No-Interaction (NI) baseline**——各Agent独立响应，互相看不见对方的输出(Marandi, 2026)。我们的实际做法比NI还差，因为NI至少知道同一个问题，而我们的Agent连上一轮的输出都看不到。

### 1.2 学术界验证的最佳实践

**关键结论（多篇论文收敛）**：辩论协议设计比模型能力更重要。在匹配的prompt和解码设置下，协议选择直接影响：
- 交互质量（peer-reference rate）
- 收敛行为（consensus formation）
- 论证多样性（argument diversity）

---

## 二、三大框架对比

### 2.1 D3：法庭式架构（Harrasse et al., 2024/2026）

**角色专业化**：Advocate（辩护方）/ Judge（裁判评分）/ Juror（陪审团裁决）

**两种协议**：
| 协议 | 模式 | 适用场景 |
|------|------|---------|
| MORE | k个Advocate并行→1轮汇总 | 客观任务/差距明显的比较 |
| SAMRE | 1个Advocate串行→多轮迭代→预算停止 | 微妙差异/高风险决策 |

**关键数据**：评分差距在4-5轮时趋于稳定，大多数判决在第2轮就已确定。Coding任务峰值差距可达20分（第2轮）。

**预算停止**：当评分差距不再显著变化时自动终止，节省token。有理论证明（概率收敛模型+正式分离证明）。

### 2.2 MAD协议比较（Marandi, 2026）

三种协议在匹配prompt/解码下的对比：

| 协议 | 上下文 | 收敛速度 | 交互质量 | 
|------|--------|---------|---------|
| Within-Round (WR) | 只看当轮 | 慢 | 高（peer-referencing更多） |
| Cross-Round (CR) | 全历史 | 中 | 中 |
| Rank-Adaptive CR (RA-CR) | 全历史+动态排序 | **最快** | 中 |

**核心权衡**：交互质量 ↔ 收敛速度。RA-CR在需要快速达成共识时最优。

**关键洞察**：辩论协议的大部分收益来自**集成（ensembling/voting）**，而非辩论本身。稀疏通信可减少94.5%的token成本，同时保持精度在2%以内。

### 2.3 Multi-Agent Memory（Mem0, Letta, multi-agent memory survey）

两种基本架构：

| 架构 | 机制 | 优点 | 缺点 |
|------|------|------|------|
| Shared Memory | 所有Agent读写同一记忆库 | 简单，一致性高 | 单点故障，冲突难解决 |
| Distributed Memory | 每个Agent独立记忆+协议通信 | 隔离性好，可扩展 | 一致性协议复杂 |

**对pipeline的启示**：Shared Memory模式更适合小规模(3-5 Agent)场景。每个辩论session在共享文件中持久化全部上下文。

---

## 三、Pipeline应采用的架构

### 3.1 核心设计：Debate Session + Context File

```
辩论session = 一个共享的markdown文件，记录全部轮次

session-001.md:
  [Round 1 — Claude攻击]
  ...
  [Round 1 — Hermes辩护]
  ...
  [Round 2 — Claude再挑战]
  ...
  [Round 2 — Hermes再辩]
  ...
  [Round 3 — Qwen裁判]
  裁判: ...
```

每个Agent dispatch的prompt = "这是辩论session-001的当前状态（附完整文件）。你的角色是X。请根据已有上下文进行第Y轮回应。"

### 3.2 两种协议适配

| 场景 | 协议 | 实现 |
|------|------|------|
| 日常code review | MORE | k=2并行，1轮裁判 |
| 架构方案对抗 | SAMRE + RA-CR | 串行多轮，预算停止（最多3轮或差距<5%停止） |
| 快速决策 | NI fallback | 单Agent独立输出 |

### 3.3 Pipeline需要新增的模块

```
src/debate/
├── session.py        # 管理辩论session的创建/读写/状态
├── context.py        # 构建带完整上下文的dispatch prompt
├── protocols.py      # MORE / SAMRE / NI 三种协议
└── convergence.py    # 预算停止检测：评分差距是否收敛
```

### 3.4 与现有pipeline的集成

`bridge_cli.py debate` 新增子命令：

```bash
# 启动一场3轮辩论
python bridge_cli.py debate \
    --session-id debate-001 \
    --protocol SAMRE \
    --agents claude-code:adversarial,qwen-code:inspector \
    --judge qwen-code \
    --context "C:/tmp/.../fix-plan-v3.md" \      # 背景文件
    --max-rounds 3 \
    --output docs/debate-result.md
```

每轮dispatch自动把session文件注入prompt。Agent看到的不是"一个孤立任务"而是"一段持续对话中的当前轮次"。

---

## 四、推翻了什么假设

| 我们的做法 | 学术结论 | 应改为 |
|-----------|---------|--------|
| 传给Agent的任务描述就是上下文 | NI baseline，无Agent间交互 | Shared context file + full debate history |
| Hermes手动写摘要连接各轮 | 单一信息瓶颈，丢失细节 | Context file是唯一真相源，Hermes不加工 |
| 固定3轮辩论 | 大多数判决在2轮确定，4-5轮收敛 | 预算停止：差距不变即终止 |
| 全部串行 | 客观任务MORE并行更高效 | 按任务类型选协议 |
| 没有形式化裁判标准 | 陪审团+评分准则 | Judge评分 + Jury独立投票 |

---

## 五、对修复方案的影响

在 `fix-plan-v3-final.md` 基础上，增加 **Phase 7：辩论协议层**：

- `src/debate/` 四个模块
- `bridge_cli debate` 子命令
- Shared context file机制
- MORE/SAMRE/NI三协议
- 收敛检测

如果将此视为P1优先级，可与Phase 4(智能化)同期实现。

---

*调研完成。建议：Phase 1-6按fix-plan-v3执行，Phase 7作为独立增项。*
