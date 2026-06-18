# 多 Agent 协作构建方案（v2.2 修正版）

> 版本：v2.3
> 基于：v2.2 + 目标对齐硬约束 + Agent 角色硬约束
> 日期：2026-06-18
> 目标：构建可落地的多 Agent 协作系统，适配复杂工程级项目开发
> 原则：先硬约束、后智能；先 2-Agent 跑通、再扩展全团队；先基线对照、再架构决策；**先诚实声明能力边界，再承诺安全等级**

---

## 零、硬约束体系（v2.3 新增）

### 0.0 核心原则：三重硬约束

本方案实施**三重硬约束**，任何违反都将被系统拦截：

1. **目标对齐约束**：所有代码、审核、测试、验收必须与 PRD 目标、架构设计目标、分阶段任务目标严格对齐
2. **角色分离约束**：Agent 必须严格执行角色分工，禁止越界
3. **流程推进约束**：Phase 推进必须通过前置检查点

### 0.1 目标对齐硬约束

#### 0.1.1 原则

> **没有实现目标的写作、审核、测试、验收都是无用的。**

每一个代码写作、每一个代码审核、每一个测试、每一个验收都必须与以下目标严格对齐：
- **PRD 目标**：Week 1-12 的阶段性目标
- **架构设计目标**：五层约束模型、安全边界、性能指标
- **分阶段任务目标**：当前 Feature 的验收标准

#### 0.1.2 目标对齐验证机制

```python
# src/goal_validator.py — 目标对齐硬约束
class GoalValidator:
    """Validate project alignment with PRD goals.
    
    Hard constraint: No feature can advance to 'deploy' phase without
    passing goal alignment verification.
    """
    
    WEEK1_GOALS = {
        "W1G1": {
            "description": "单 Agent（Kimi）跑通编码→测试→验证流程",
            "required": True,
            "check_files": ["src/agents/kimi_adapter.py", "pipeline.py"],
            "check_functions": ["single_agent_mode", "kimi_execute"]
        },
        "W1G2": {
            "description": "基线对照实验：单 Agent vs 多 Agent（2-Agent）",
            "required": True,
            "check_files": ["src/baseline_experiment.py"],
            "check_functions": ["run_baseline", "compare_results"]
        },
        # ... 其他目标
    }
    
    def validate_all(self) -> Tuple[bool, List[GoalCheck]]:
        """验证所有 Week 1 MVP 目标。
        
        硬约束：所有 required 目标必须 COMPLETE 才能推进。
        """
        # ... 实现
```

#### 0.1.3 目标对齐检查点

| 检查点 | 触发时机 | 检查内容 | 失败后果 |
|--------|----------|----------|----------|
| 编码前检查 | `pipeline.py dispatch` | 目标是否已分解为可执行任务 | BLOCK，重新分解 |
| 审核前检查 | `pipeline.py review` | 代码是否实现目标功能 | BLOCK，返回修复 |
| 测试前检查 | `pipeline.py test` | 测试用例是否覆盖目标场景 | BLOCK，补充测试 |
| 验收前检查 | `pipeline.py accept` | 所有目标验证是否通过 | BLOCK，继续开发 |
| 推进前检查 | `pipeline.py advance` | GoalValidator 是否全通过 | BLOCK，无法推进 |

#### 0.1.4 目标对齐报告

每个 Phase 结束必须生成目标对齐报告：

```
reports/{feature_id}_goal_alignment.json
{
  "feature_id": "F001",
  "phase": "develop",
  "goals": {
    "W1G1": {"status": "complete", "evidence": "src/agents/kimi_adapter.py:45"},
    "W1G2": {"status": "not_implemented", "evidence": "missing baseline_experiment.py"}
  },
  "all_passed": false,
  "blocker": "W1G2 not implemented"
}
```

### 0.2 Agent 角色与职能硬约束

### 0.1 核心原则：角色分离是硬约束，不是建议

**任何 Agent 不得越界执行其他 Agent 的职能。违反角色约束的任务派发将被 GoalValidator 拦截。**

### 0.2 Agent 角色定义

| Agent | 模型 | 核心职能 | 禁止行为 |
|-------|------|----------|----------|
| **Hermes (Orchestrator)** | Kimi K2.6 | 统筹协调、任务派发、组织调研、成果归拢、进度跟踪 | ❌ 禁止直接编写代码、❌ 禁止直接修复代码、❌ 禁止代替其他 Agent 执行其职能 |
| **Hermes-Research (深度研究)** | Qwen 3.7 Max | 深度研究、PRD 编制、架构设计、任务分解规划、最终验收 | ❌ 禁止编写代码、❌ 禁止直接修复代码、❌ 禁止执行测试 |
| **Claude Code (主 Coder)** | Kimi K2.6 | 代码编写、代码修复、功能实现 | ❌ 禁止做架构设计、❌ 禁止做代码审核（自审除外）、❌ 禁止做最终验收 |
| **CodeWhale (审核专家)** | DeepSeek V4 Pro | 代码审核、风险提示、修改意见、提升意见、质量评估 | ❌ 禁止编写代码、❌ 禁止直接修复代码、❌ 禁止做架构设计 |
| **Qwen Code (测试专家)** | Qwen 3.7 Max | 测试任务、测试用例编写、覆盖率检查、缺陷报告 | ❌ 禁止编写生产代码、❌ 禁止做架构设计、❌ 禁止做代码审核 |

### 0.3 任务派发规则

```python
# 硬约束：任务派发必须通过 GoalValidator 检查角色合规性
def dispatch_task(agent: str, task_type: str) -> bool:
    """Dispatch task to agent with role validation."""
    
    # Role-task mapping (hard constraint)
    ROLE_TASKS = {
        "Hermes": ["orchestrate", "dispatch", "research_org", "gather_results"],
        "Hermes-Research": ["deep_research", "prd_write", "arch_design", "task_decompose", "final_accept"],
        "Claude Code": ["code_write", "code_fix"],
        "CodeWhale": ["code_review", "risk_alert", "improvement_suggest"],
        "Qwen Code": ["test_write", "test_run", "coverage_check", "bug_report"]
    }
    
    if task_type not in ROLE_TASKS.get(agent, []):
        raise RuntimeError(
            f"ROLE_VIOLATION: {agent} cannot execute {task_type}. "
            f"Allowed tasks: {ROLE_TASKS.get(agent, [])}"
        )
    
    return True
```

### 0.4 违反角色约束的后果

| 违反类型 | 检测机制 | 后果 |
|----------|----------|------|
| Hermes 直接编码 | GoalValidator + 代码审查 | 任务被拦截，记录违规日志 |
| Claude Code 做架构设计 | GoalValidator + PRD 审查 | 架构设计被驳回，重新派发给 Hermes-Research |
| CodeWhale 直接修复代码 | GoalValidator + 代码 diff 检查 | 修复被回滚，重新派发给 Claude Code |
| Qwen Code 做代码审核 | GoalValidator + 审查报告检查 | 审核报告被驳回，重新派发给 CodeWhale |

### 0.5 角色协作流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Hermes    │────▶│ Hermes-Res  │────▶│  Claude Code │
│ (统筹派发)   │     │ (架构设计)   │     │  (编码实现)  │
└─────────────┘     └─────────────┘     └─────────────┘
      │                                       │
      │                                       ▼
      │                                ┌─────────────┐
      │                                │  CodeWhale  │
      │                                │  (代码审核)  │
      │                                └─────────────┘
      │                                       │
      ▼                                       ▼
┌─────────────┐                        ┌─────────────┐
│  Qwen Code  │◀───────────────────────│  (审核反馈)  │
│  (测试验证)  │                        └─────────────┘
└─────────────┘
      │
      ▼
┌─────────────┐
│   Hermes    │
│ (验收归拢)   │
└─────────────┘
```

---

---

## 一、项目目标与范围

### 1.1 目标

搭建一个以 Hermes 为 Orchestrator、Claude Code 为主 Coder、CodeWhale 为审查员、Qwen Code 为辅助/测试的多 Agent 协作系统，通过**硬基础设施（沙箱、验证、预算、可观测性）+ 确定性流程约束 + LLM 智能决策**的三层结合，解决单 Agent 在复杂工程级项目中的能力瓶颈。

### 1.2 核心认知前提

> **2024-2026年学术研究反复证明：mini-SWE-agent 用 100 行 Python 在 SWE-bench Verified 上达到 65% 解决率，击败了绝大多数复杂的多Agent编排系统。** 这意味着当前的核心瓶颈不在编排层，而在底层模型的推理能力和上下文理解能力。多Agent的真正价值在于**管理真实生产环境的复杂性**（并行、分工、验证），而非提升模型本身的编码能力。

因此，本方案的设计原则是：**多 Agent 不提升单次编码能力，它提升的是流程可靠性。** 每一层架构都必须证明其存在价值大于引入的协调成本。

### 1.3 范围

**本期范围内：**
- 单个项目级别的多 Agent 开发流水线
- 默认 2-Agent 模式（Claude 编码 + CodeWhale 审查）
- 复杂工程可扩展到 4-Agent 全团队模式
- Windows 11 Home 环境（不依赖 Docker Desktop、不依赖自研 Web UI）
- 轻量级沙箱（权限隔离 + 网络白名单 + 进程监控 + 命令审计）
- 终端 Markdown 报告 + SQLite 可观测性 + 实时告警

**本期范围外（后续迭代）：**
- 跨项目 Agent 调度
- 8+ Agent 层级 Supervisor
- 自研 Web UI
- Docker Desktop 级容器隔离
- 多租户/企业级权限系统

### 1.4 不适用多 Agent 的场景

以下场景**只派单 Agent**，不启动多 Agent 流水线：
- 修改少于 50 行、涉及文件 ≤2 个的简单修复
- 纯文档/注释/变量名修改
- 已有明确单点任务（如格式化、lint 修复）
- 临时探索/调研任务

**Golden Path（快速通道）：**
触发条件：修改 <5 个文件、<30 行代码、无依赖变更、无 API 变更。
流程：单 Agent 编码 → 自动化验证 → 直接合并。跳过 CodeWhale 审查、Phase 1 架构设计、Phase 5 人工验收。

---

## 二、核心原则

### 2.1 原则一：简单优先

从单 Agent 开始，仅在任务可明确分解、需要独立验证时才引入多 Agent。80% 的任务用 2-Agent，20% 用 4-Agent。

### 2.2 原则二：硬约束优先于 Prompt 约束

所有关键规则必须写成代码，不能依赖 Agent "记得"或"遵守"。例如：
- feature passing 必须测试通过
- token 超预算必须熔断
- 高风险命令必须拦截

### 2.3 原则三：确定性验证优先于 LLM 判断

LLM 可以生成、审查、建议，但不能决定"完成"。完成的唯一标准是可执行检查通过。

### 2.4 原则四：可观测性是第一天工程

没有 trace、cost、quality 数据，系统无法调试、无法优化、无法信任。可观测性必须包含实时告警，不能只做事后报告。

### 2.5 原则五：失败可恢复

每个重要状态必须持久化，每个 Agent action 必须可重试，每次失败必须可回滚。Agent 工具调用链必须保证原子性。

### 2.6 原则六：人机边界清晰

Agent 负责执行和初步判断，人类在关键决策点介入。审批必须分级（阻塞式/异步式/默认放行式），审批必须有超时机制，系统提供清晰的阻塞原因和推荐操作。

### 2.7 原则七：异构审查（v2 新增）

编码和审查**必须使用不同模型**。不同模型有不同的"思维盲区"，交叉审查能发现更多问题。审查者不看编码者的思考过程，避免确认偏差。

### 2.8 原则八：先验证再扩展（v2 新增）

在全面构建多 Agent 架构之前，必须通过单 Agent 基线对照实验验证多 Agent 的必要性。只有当多 Agent 方案在至少 2/3 的对照任务上显著优于单 Agent 时，才继续推进。

---

## 三、总体架构：五层约束模型

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Agent 智能决策层                                    │
│   Hermes / Claude / CodeWhale / Qwen                         │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: 编排与流程约束层                                    │
│   pipeline.py 状态机 / Budget Enforcer / Circuit Breaker     │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: 状态持久化与检查点层                                │
│   SQLite State DB / Checkpoint / Resume                      │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: 代码级确定性验证层                                  │
│   Lint / Test / Build / Diff / Feature Assertion             │
├─────────────────────────────────────────────────────────────┤
│ Layer 0: 沙箱与执行隔离层（分层信任模型）                     │
│   5种Profile / 4级交互等级 / 5级风险等级 / 动态切换          │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Layer 0：沙箱与执行隔离层（分层信任模型）

**目标：** 根据使用场景动态调整安全约束的严格程度，在保障安全的同时不阻碍正常使用。

**核心设计原则：沙箱严格程度 ∝ Agent自主程度 × 操作不可逆性 ÷ 用户在场程度**

> v1/v2 原设计的问题：假设所有场景都是"多Agent流水线项目开发"，把最严格的约束当默认值。
> 导致独立任务、调研协作、系统维护等场景被无差别拦截。
> 本设计引入分层信任模型，让沙箱约束按场景动态调整。

**方案选型结论：最小可行沙箱 + 分层信任模型**

> 选型理由（基于实测踩坑）：
> - **WSL2**：Hermes 向 Claude Code 注入长提示时出现**长提示静默问题**，移到 Windows 侧后问题消失。❌
> - **Docker Desktop**：占用 8-16GB 内存，导致电脑性能疲劳。❌
> - **Hyper-V 容器**：Windows 11 Home 不支持。❌
> - **Windows Sandbox**：每次重启清空，不支持持久化。❌
> - **AppLocker**：Windows 11 Home 不支持，仅 Pro/Enterprise 可用。❌
> - **WFP 驱动开发**：用户层 API 可用，但完整网络内容审计需驱动开发。⚠️
> - **NTFS ACL**：可用，但**可被 Junction Point / 符号链接绕过**（已实测验证）。⚠️

> ⚠️ **诚实声明（v2.1 新增）**：Week 1-3 实施阶段，以下企业级安全功能**尚未启用或不可行**：
> - 多用户隔离（每个 Agent 独立 Windows 用户）：可行但 CLI 工具链切换复杂，延迟到 Week 4-5 评估
> - AppLocker 进程白名单：Home 版不支持，改用软件限制策略 (SRP) 或基于路径的进程限制
> - WFP 网络内容审计：用户层可用，驱动级需额外开发
> - NTFS ACL 隔离：已验证可被 Junction Point 绕过，需配合符号链接创建权限限制
>
> **当前最小可行沙箱**：NTFS ACL + 命令白名单 + 本地代理层网络过滤 + 人工审批（L3/L4 操作）

#### 3.1.1 交互等级体系（T0-T3）

| 等级 | 名称 | 用户在场程度 | 沙箱角色 | 适用场景 |
|------|------|------------|---------|---------|
| T0 | 监督式 | 用户实时在场，逐条指挥 | 提醒风险+审计，不主动拦截 | 单Agent独立任务 |
| T1 | 指挥式 | 用户下达目标，Agent自主执行 | 高风险操作前暂停确认 | 单Agent任务、多Agent调研 |
| T2 | 审批式 | Agent自主，关键节点等审批 | 完整约束+审批流+超时暂停 | 多Agent项目开发 |
| T3 | 全自主 | 用户完全离线 | 最严格约束+硬熔断+自动回滚 | 过夜批量任务 |

#### 3.1.2 操作风险等级（L0-L4）

| 等级 | 描述 | 示例 |
|------|------|------|
| L0 | 只读操作 | 读文件、网络搜索、git log、运行测试 |
| L1 | 项目内可逆写入 | 修改代码、git commit、创建文件 |
| L2 | 项目外可逆写入 | 安装依赖、创建配置、修改项目设置 |
| L3 | 系统级修改 | 修改系统配置、环境变量、全局设置 |
| L4 | 不可逆/高影响 | git push、部署到生产、删除重要数据 |

#### 3.1.3 风险 × 交互交叉矩阵

```
              L0 只读    L1 项目写入    L2 项目外写入    L3 系统修改    L4 不可逆
T0 监督式      ✅自动     ✅自动         ✅自动          ⚠️确认          ⚠️确认
T1 指挥式      ✅自动     ✅自动         ⚠️确认          ⚠️确认          ⚠️确认
T2 审批式      ✅自动     ✅自动         🔒审批          🔒审批+人工     🔒审批+人工
T3 全自主      ✅自动     ✅自动         ⛔禁止          ⛔禁止+暂停     ⛔禁止+暂停
```

#### 3.1.4 五个 Profile 预设

```
╔════════════════════════════════════════════════════════════════════════╗
║  Profile: LOCKDOWN（保险箱）                                          ║
║  场景：高敏感项目（金融、医疗、安全关键）                              ║
║  网络：仅 API 白名单 + 内容审计 │ 目录：仅项目目录 │ 默认：T2        ║
╠════════════════════════════════════════════════════════════════════════╣
║  Profile: PIPELINE（流水线）                                          ║
║  场景：标准多 Agent 项目开发                                           ║
║  网络：API白名单 │ 目录：项目目录 │ 通信：走pipeline │ 默认：T2      ║
╠════════════════════════════════════════════════════════════════════════╣
║  Profile: ASSISTANT（助手）← 系统默认                                 ║
║  场景：用户直接指挥的独立任务（编码、调研、系统操作）                   ║
║  网络：开放(审计) │ 目录：用户授权 │ 进程：不限制 │ 默认：T0          ║
╠════════════════════════════════════════════════════════════════════════╣
║  Profile: RESEARCH（调研）                                            ║
║  场景：网络调研、数据分析、爬虫、多Agent讨论                          ║
║  网络：完全开放 │ 目录：用户指定+临时 │ Agent通信：可调度 │ 默认：T1  ║
╠════════════════════════════════════════════════════════════════════════╣
║  Profile: FREE（自由）                                                ║
║  场景：完全信任 Agent，快速执行                                        ║
║  网络：完全开放 │ 目录：全部 │ 通信：不限制 │ 默认：T1               ║
║  ⚠️ 仅审计不拦截，L4操作通知但不阻塞                                 ║
╚════════════════════════════════════════════════════════════════════════╝
```

> **系统默认 Profile 为 ASSISTANT**，因为 80% 的日常使用是单 Agent + 用户在场。
> `pipeline` 仅在明确启动多 Agent 项目开发时使用。

#### 3.1.5 场景到 Profile 自动映射

| 场景 | 推荐Profile | 交互等级 | 关键约束差异 |
|------|------------|---------|-------------|
| 多Agent流水线项目开发 | PIPELINE | T2 | API白名单、项目目录、走pipeline |
| 单Agent独立编码 | ASSISTANT | T0 | 开放网络、用户指定目录 |
| 快速修复（<5文件） | ASSISTANT | T0 | 最小权限 |
| 修改系统配置文件 | ASSISTANT | T0 | L3操作需确认 |
| 单Agent网络调研 | RESEARCH | T0 | 网络完全开放 |
| 多Agent深度调研+讨论 | RESEARCH | T1 | Agent间通信开放 |
| 数据爬取与处理 | RESEARCH | T1 | 网络开放、限制写入范围 |
| 写文档/翻译 | ASSISTANT | T0 | 不限目录 |
| 数据分析与可视化 | ASSISTANT | T0 | 数据目录读取 |
| 本地部署 | ASSISTANT | T1 | API+包管理网络 |
| 云端部署 | ASSISTANT | T0 | L4操作每步确认 |
| 运维诊断修复 | ASSISTANT | T0 | 系统目录、L3确认 |
| 配置开发环境 | ASSISTANT | T0 | 系统目录、L3确认 |
| Git工作流管理 | ASSISTANT | T0 | 项目目录 |
| CI/CD配置 | ASSISTANT | T1 | 广泛网络 |
| 跨项目Agent协调 | PIPELINE | T2 | 多项目目录、Agent通信 |

#### 3.1.6 用户控制系统（零学习成本设计）

> **设计原则：用户不需要记任何命令，系统应该"懂"用户在做什么**
> 
> ⚠️ **v2.2 修正**：全局快捷键（Ctrl+Shift+1~4）在 Windows 11 Home + git-bash 环境下**不可行**（pynput 无法注册系统级热键）。改为 CLI 命令切换 + 终端内快捷键方案。

**控制方式：**

1. **CLI 命令切换（主方案）**
   ```bash
   hermes mode assistant    # 切换到 ASSISTANT 模式
   hermes mode pipeline     # 切换到 PIPELINE 模式
   hermes mode research     # 切换到 RESEARCH 模式
   hermes mode lockdown     # 切换到 LOCKDOWN 模式
   ```

2. **终端内快捷键（备选）**
   - 依赖终端模拟器的键位转发（如 Windows Terminal 的自定义键绑定）
   - 不承诺全局热键，仅作为终端内快捷操作

3. **自然语言切换（实验性）**
   - 用户说"让我自由点" → 系统理解并切换
   - ⚠️ **准确率 88%**，有误判风险（如"审查一下"被误判为"research"）
   - 建议：仅作为辅助，不依赖为唯一切换方式

**A. 快捷键切换（最简单的方式）**

| 快捷键 | 功能 | 说明 |
|--------|------|------|
| `Ctrl+Shift+1` | 切换到 ASSISTANT 模式 | 日常助手，网络开放 |
| `Ctrl+Shift+2` | 切换到 RESEARCH 模式 | 调研模式，Agent可讨论 |
| `Ctrl+Shift+3` | 切换到 PIPELINE 模式 | 项目开发，严格隔离 |
| `Ctrl+Shift+4` | 切换到 FREE 模式 | 完全自由，仅审计 |
| `Ctrl+Shift+0` | 查看当前模式 | 弹出当前模式+权限详情 |
| `Ctrl+Shift+N` | 临时开放网络 | 当前会话有效 |
| `Ctrl+Shift+D` | 临时开放目录 | 弹出目录选择器 |
| `Ctrl+Shift+H` | 临时提升权限 1 小时 | 切换到 FREE 模式 |
| `Ctrl+Shift+R` | 恢复默认模式 | 撤销所有临时变更 |

> 快捷键可自定义，存储在 `~/.hermes/config/hotkeys.yaml`

**B. 自然语言命令（用户说人话，系统自动理解）**

用户直接在对话中说中文，系统自动识别意图并切换模式：

| 用户说的话 | 系统识别意图 | 执行动作 |
|-----------|-------------|---------|
| "帮我调研一下 XXX" | 需要调研 | 自动切换到 RESEARCH 模式 |
| "搜索一下 XXX" | 需要网络 | 临时开放网络 30 分钟 |
| "让我自由点" / "别管我" | 放宽限制 | 切换到 FREE 模式 |
| "严格点" / "安全模式" | 收紧限制 | 切换到 LOCKDOWN 模式 |
| "开始项目" / "启动流水线" | 项目开发 | 切换到 PIPELINE 模式 |
| "回到日常" / "正常模式" | 恢复默认 | 切换到 ASSISTANT 模式 |
| "允许它 push" / "让它提交" | 授权特定操作 | 临时授权 30 分钟 |
| "允许它上网" / "给它网络权限" | 开放网络 | 临时开放网络 1 小时 |
| "允许它改系统文件" | 开放系统目录 | 临时开放目录 30 分钟 |
| "当前什么模式" / "我现在安全吗" | 查询状态 | 显示当前模式+权限详情 |

> 自然语言识别由 LLM 内置能力实现，无需额外 NLP 模块。

**C. 系统自动推断（最智能的方式）**

系统根据用户的任务描述自动推荐并切换模式：

```python
class AutoModeInferrer:
    """根据用户任务自动推断并切换模式"""

    def infer_and_switch(self, user_request: str) -> str:
        # 调研类任务
        if any(kw in user_request for kw in ["调研", "搜索", "查找资料", "研究", "爬虫"]):
            return "research"

        # 项目开发类
        if any(kw in user_request for kw in ["开始项目", "开发", "编码", "实现功能"]):
            return "pipeline"

        # 系统操作类
        if any(kw in user_request for kw in ["修改配置", "改系统", "装软件", "配环境"]):
            return "assistant"  # 但 L3 操作仍需确认

        # 文档/翻译类
        if any(kw in user_request for kw in ["写文档", "翻译", "总结", "报告"]):
            return "assistant"

        # 默认助手模式
        return "assistant"

    def confirm_with_user(self, suggested_mode: str, reason: str):
        """切换前简单确认（可关闭）"""
        # 弹出轻量级确认框：
        # "检测到你在做调研，建议切换到调研模式（网络开放、Agent可讨论）。是否切换？"
        # [切换] [保持当前] [不再提示]
        pass
```

**自动切换触发示例：**

```
用户: "帮我调研一下 2026 年最火的 AI Agent 框架"
系统: [自动切换到 RESEARCH 模式]
      已切换到调研模式，网络已开放，Agent 可以互相讨论。

用户: "开始开发一个电商网站"
系统: [自动切换到 PIPELINE 模式]
      已切换到流水线模式，开始多 Agent 项目开发。

用户: "帮我改一下 .gitconfig"
系统: [保持 ASSISTANT 模式]
      当前已是助手模式，可以修改配置文件（需要你确认）。
```

**D. Agent 请求权限（弹出式确认）**

当 Agent 需要超出当前权限的操作时，自动弹出对话框让用户点选：

```
┌────────────────────────────────────────────────────────────────┐
│  🔔 Agent 需要你的授权                                          │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  谁: Claude Code (F002)                                        │
│  想做什么: git push origin feature/f002                        │
│  风险: ⚠️ 高（不可逆操作）                                     │
│  为什么: 推送代码到远程仓库以便 CI 测试                         │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ ① 允许这次                                              │  │
│  │ ② 允许 30 分钟                                          │  │
│  │ ③ 开放权限 1 小时                                       │  │
│  │ ④ 拒绝                                                  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  [快捷键] 1=允许这次  2=允许30分钟  3=开放1小时  Esc=拒绝      │
└────────────────────────────────────────────────────────────────┘
```

**E. 状态栏实时显示（始终可见）**

终端底部常驻状态栏，显示当前模式和关键权限：

```
┌────────────────────────────────────────────────────────────────┐
│ 🟢 ASSISTANT │ 网络:✅开放 │ 目录:用户授权 │ 通信:✅允许      │
└────────────────────────────────────────────────────────────────┘
```

模式切换时状态栏高亮闪烁 2 秒，提醒用户模式已变更。

**F. 一键恢复（防止忘记）**

| 场景 | 操作 | 效果 |
|------|------|------|
| 忘了当前什么模式 | `Ctrl+Shift+0` 或问"现在什么模式" | 弹出详情 |
| 忘了恢复默认 | `Ctrl+Shift+R` 或说"回到正常" | 撤销所有临时变更 |
| 临时授权到期 | 自动回退 | 系统自动恢复到原模式 |
| 长时间无操作 | 30 分钟无操作自动回退 | 防止忘记收紧权限 |

**G. 配置持久化（记住用户偏好）**

```yaml
# ~/.hermes/config/user-preferences.yaml
user_preferences:
  # 默认模式（非项目场景）
  default_mode: "assistant"

  # 项目默认模式
  project_default_mode: "pipeline"

  # 自动推断开关
  auto_infer_mode: true

  # 自动确认切换（关闭后需手动确认）
  auto_confirm_switch: true

  # 临时授权默认时长
  temp_auth_duration_minutes: 60

  # 无操作自动回退时长
  idle_revert_minutes: 30

  # 快捷键自定义（可选）
  custom_hotkeys:
    assistant: "Ctrl+Shift+1"
    research: "Ctrl+Shift+2"
    pipeline: "Ctrl+Shift+3"
    free: "Ctrl+Shift+4"
    status: "Ctrl+Shift+0"
```

**H. 使用流程示例**

```
场景 1: 用户想调研 AI 框架
─────────────────────────────
用户: "帮我调研一下 LangChain 和 LlamaIndex 哪个更好"
系统: [自动推断] 检测到调研意图
      [自动切换] RESEARCH 模式
      已切换到调研模式，网络已开放，Agent 可以互相讨论。
      [Agent 开始搜索、阅读、讨论、生成报告]

场景 2: 用户想开发一个项目
─────────────────────────────
用户: "开始开发一个待办事项应用"
系统: [自动推断] 检测到项目开发意图
      [自动切换] PIPELINE 模式
      已切换到流水线模式，启动多 Agent 项目开发。
      [pipeline.py init 自动执行]

场景 3: Agent 需要 push 代码
─────────────────────────────
Agent: [检测到 L4 操作]
系统: [弹出确认框]
      🔔 Agent 需要你的授权
      谁: Claude Code
      想做什么: git push origin main
      风险: ⚠️ 高
      [1] 允许这次  [2] 允许30分钟  [3] 开放1小时  [Esc] 拒绝
用户: [按 1]
系统: 已授权，30 分钟后自动收回。

场景 4: 用户想自由操作
─────────────────────────────
用户: "让我自由点，别管我"
系统: [自然语言识别] 意图：放宽限制
      [自动切换] FREE 模式
      已切换到自由模式，仅审计不拦截。
      [用户开始随意修改系统文件、安装软件等]

场景 5: 用户想恢复安全
─────────────────────────────
用户: "回到正常模式"
系统: [自然语言识别] 意图：恢复默认
      [自动切换] ASSISTANT 模式
      已恢复到助手模式。
```

#### 3.1.7 硬性限制（不可关闭）

无论使用什么 Profile，以下安全措施**永远生效**：

```yaml
hard_limits:
  never_allow_commands:
    - "rm -rf /"              # 禁止删除根目录
    - "format C:"             # 禁止格式化磁盘
    - "reg delete HKLM"       # 禁止删除注册表关键项
    - "shutdown /s"           # 禁止关机
    - "curl.*| bash"          # 禁止管道执行远程脚本
    - "net user /delete"      # 禁止删除系统用户
  always_audit: true           # 始终记录审计日志
  l4_always_confirm_in_T2_T3: true  # T2/T3下L4操作始终需确认
  auto_revert_temp_auth: true  # 临时授权到期自动回退
  budget_circuit_breaker: true # 预算熔断不可关闭
  log_mode_changes: true       # 所有模式切换记录到审计日志
```

#### 3.1.8 安全增强措施（PIPELINE/LOCKDOWN 模式启用，v2.1 修正）

**已验证可行的措施（Week 1-3 启用）：**
1. **NTFS ACL 隔离**：项目目录授权、敏感目录拒绝 ⚠️ 但需配合符号链接创建权限限制（已实测 Junction Point 可绕过）
2. **Agent 间通信隔离**：必须通过 pipeline.py，禁止直接进程通信
3. **Secrets 隔离**：Agent 环境不注入 API key，由编排层代理
4. **命令白名单**：仅允许已知安全命令，不在白名单中的命令一律拦截（替代正则黑名单，v2.1 修正）
   <!-- v2.2 修改来源：安全审查辩论 P0-6 / 辩论轮次：Round 1-3 / 证据：推理 / 假设验证：辩论结论-正方胜 / 修改：安全声明从"防恶意"降级为"防误操作" -->
   > ⚠️ **安全声明**：命令白名单对解释器类命令（python、node、powershell）的防御效果有限。解释器脚本可在白名单放行后执行任意系统调用。本机制的核心目标是**防误操作**（阻止用户意外执行危险命令），而非**防恶意**（阻止决心攻击者）。在 PIPELINE/LOCKDOWN 模式下，解释器脚本必须来自版本控制（Git 追踪路径）。
5. **本地代理层网络过滤**：HTTP 代理过滤出站请求，阻止 Base64 大负载（替代 WFP 驱动级审计）
   
   <!-- v2.2 修改来源：技术验证 / 验证方法：实测 HTTP_PROXY 环境变量 / 结果：部分工具不支持 / 修改：从"系统级透明代理"降级为"CLI 单独配置" -->
   > ⚠️ **技术声明**：系统级透明代理在 Windows 11 Home 上不可行（HTTP_PROXY 环境变量不被所有 CLI 工具尊重）。改为每个 Agent CLI 单独配置代理参数。
   > 
   > **替代方案**：
   > 1. Claude Code: `claude --proxy http://localhost:8080`
   > 2. CodeWhale: `codewhale --http-proxy http://localhost:8080`
   > 3. Qwen Code: `qwen --proxy http://localhost:8080`
   > 4. 或统一配置：在 `pipeline.yaml` 中设置 `proxy_url`，由编排层注入到每个 Agent 的启动参数
   > 
   > **限制**：此方案仅过滤 Agent 主动发起的网络请求，无法拦截系统级流量。
6. **文件完整性校验**：关键目录定期 hash 校验、**检测符号链接和连接点攻击**（v2.1 新增）

**延迟到 Week 4-5 评估的措施：**
7. **多用户隔离**：每个 Agent 独立 Windows 用户 — 可行但 CLI 工具链切换复杂，需评估实际收益

**不可行的措施（Windows 11 Home）：**
8. ~~进程白名单（AppLocker）~~ → 改用软件限制策略 (SRP) 或基于路径的进程限制
9. ~~WFP 驱动级网络内容审计~~ → 改用本地代理层过滤

> ⚠️ **诚实声明**：以上增强措施在 PIPELINE/LOCKDOWN 模式下**尽可能启用**，但受 Windows 11 Home 功能限制。
> ASSISTANT/RESEARCH/FREE 模式下按需部分启用。
> **Week 1-3 实际安全边界**：NTFS ACL（有绕过风险）+ 命令白名单 + 代理层网络过滤 + 人工审批。

#### 3.1.9 动态模式切换

```python
class DynamicModeSwitch:
    """运行时自动建议模式切换"""
    triggers = {
        "agent_requests_web_search": {
            "from": ["pipeline", "lockdown"],
            "suggest": "research",
            "message": "Agent 需要网络搜索，建议切换到调研模式"
        },
        "agent_requests_system_write": {
            "from": ["pipeline", "lockdown"],
            "suggest": "assistant",
            "message": "Agent 需要修改系统文件，建议切换到助手模式"
        },
        "orchestrator_needs_discussion": {
            "from": ["pipeline", "lockdown"],
            "suggest": "research",
            "message": "Orchestrator 需要多 Agent 讨论，建议开放通信"
        },
        "task_completed": {
            "from": ["research", "free"],
            "suggest": "pipeline",
            "message": "任务已完成，建议切回流水线模式"
        },
    }
```

### 3.2 Layer 1：代码级确定性验证层

**目标：** 所有"完成"判断必须由代码执行验证，不能由 LLM 自评。

**验证项目：**

| 验证项 | 触发时机 | 失败处理 |
|--------|---------|---------|
| `git diff --stat` 非空 | feature 编码后 | BLOCK，标记假完成 |
| `git diff --stat` 范围检查 | feature 编码后 | BLOCK，修改范围超出声明 |
| `git log` 有对应 commit | feature 编码后 | BLOCK |
| Lint 通过（eslint/ruff/flake8/black） | 每次提交前 | BLOCK，自动修复或返修 |
| Type Check 通过（mypy/tsc） | 每次提交前 | BLOCK |
| 冒烟测试通过（v2 新增） | feature 编码后、审查前 | BLOCK，避免审查跑不起来的代码 |
| 单元测试通过 | feature 审查前 | BLOCK |
| 集成测试通过 | feature 审查前 | BLOCK（可选） |
| E2E 测试通过 | Phase 4 | BLOCK |
| Feature assertion 通过 | Phase 5 | BLOCK |
| 依赖 lockfile 一致 | 提交前 | BLOCK |
| `import` 验证（v2 新增） | 编码后 | BLOCK，检测 API 幻觉 |

**反幻觉验证措施（v2 新增）：**

| 幻觉类型 | 表现 | 对策 |
|---------|------|------|
| API 幻觉 | 调用不存在的函数/库 | 编码后自动 `import` 验证 + `pip list` 交叉检查 |
| 路径幻觉 | 使用不存在的文件路径 | 工具层强制使用绝对路径 + 存在性检查 |
| 逻辑幻觉 | 代码看似合理但逻辑错误 | 独立测试生成 + mutation testing（可选） |
| 类型幻觉 | 使用错误的类型/签名 | 强制 type check (`mypy --strict` / `tsc --strict`) |
| 范围幻觉 | 修改了 feature 范围外的代码 | `git diff --stat` 检查修改范围是否超出声明 |

### 3.3 Layer 2：状态持久化与检查点层

**目标：** 崩溃后可恢复，长期任务不丢失进度。

**实现：**
- 使用 SQLite 数据库：`~/.hermes/pipelines/<project>.db`
- 每个有意义 action 后 checkpoint
- 支持 `pipeline.py resume <project>`
- 状态模式版本控制（schema v1、v2...）

**核心表结构：**

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    schema_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE features (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT CHECK(status IN ('pending','in_progress','review','test','passed','failed','needs_rework')),
    owner_agent TEXT,
    token_cost INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    phase TEXT NOT NULL,
    feature_id TEXT,
    agent TEXT,
    action TEXT,
    result TEXT,
    state_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    feature_id TEXT,
    agent TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    status TEXT,
    cache_hit BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    agent TEXT,
    command TEXT,
    allowed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE model_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    response_time_ms INTEGER,
    success BOOLEAN,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.4 Layer 3：编排与流程约束层

**目标：** 控制阶段流转、预算、熔断、派发。

**核心组件：**
- `pipeline.py`：状态机 + CLI
- `BudgetEnforcer`：per-task / per-step / per-project 预算硬上限
- `CircuitBreaker`：连续失败熔断
- `Dispatcher`：根据任务类型选择 Agent
- `CommandGate`：命令分级拦截
- `ContextManager`：上下文窗口管理（v2 新增）
- `ModelHealthMonitor`：模型健康度监控（v2 新增）
- `AlertManager`：实时告警系统（v2 新增）

### 3.5 Layer 4：Agent 智能决策层

**目标：** 在硬约束内做智能决策。

**Agent 清单：**
- **Hermes（Orchestrator + Judge）**：任务派发、架构决策、phase 推进、最终验收判断。使用双模型路由（Kimi K2.6 日常 + Qwen 3.7 Max 决策）
- **Claude Code（主 Coder + Tech Lead）**：编码、重构、技术方案实现。使用 Kimi K2.6 包月
- **CodeWhale（审查员 + Shell 专家）**：代码审查、安全问题、Shell/DevOps 任务。使用 DeepSeek V4 Pro 异构审查
- **Qwen Code（辅助 Coder + 浏览器测试）**：备用编码、E2E 测试、中文文档、简单任务。使用 Qwen3-Coder-Plus 原生适配

---

## 四、模型选型与路由（v2 定稿）

### 4.1 编程能力基准对比（2026-06 数据）

| 模型 | SWE-bench Pro | SWE-bench Verified | LiveCodeBench | Codeforces | Arena Coding |
|------|-------------|-------------------|-------------|-----------|-------------|
| **Qwen 3.7 Max** | **60.6%** 🏆 | 80.4% | 91.6% | — | **全球第10** |
| **Kimi K2.6** | 58.6% | 80.2% | 89.6% | — | — |
| **DeepSeek V4 Pro** | 55.4% | **80.6%** | **93.5%** 🏆 | **3206** 🏆 | — |
| Claude Opus 4.8 | 51.9%/69.2% | **88.6%** 🏆 | ~76.0% | — | — |

**关键洞察：**
- Qwen 3.7 Max 在 **SWE-bench Pro（工程实战）全球第一**，最适合做架构决策和工程判断
- DeepSeek V4 Pro 在 **LiveCodeBench + Codeforces 全球第一**，算法推理能力最强，最适合做代码审查（发现逻辑漏洞）
- Kimi K2.6 **综合均衡**，且有 **包月 Coding Plan**，最适合做高频编码主力

### 4.2 定价与成本对比

| 模型 | 计费模式 | 输入价格（¥/百万token） | 输出价格（¥/百万token） | 包月选项 |
|------|---------|----------------------|----------------------|---------|
| **Kimi K2.6** | 包月为主 | ¥6.50（缓存未命中） | ¥27.00 | **¥49-699/月 Coding Plan** |
| **DeepSeek V4 Pro** | 按量计费 | ¥3.00 | ¥6.00 | ❌ 无包月 |
| **Qwen 3.7 Max** | 按量计费 | ¥12.00 | ¥36.00 | ❌（百炼有¥40-200/月套餐） |
| **Qwen3-Coder-Plus** | 按量计费 | ¥2.00 | ¥8.00 | 百炼 ¥40/月起 |

### 4.3 技术规格对比

| 模型 | 总参数 | 激活参数 | 上下文窗口 | 最大输出 | 多模态 |
|------|--------|---------|-----------|---------|-------|
| **DeepSeek V4 Pro** | 1.6T | 49B | **1M** | **384K** | ✅（开源版视觉 Q3 释放） |
| **Kimi K2.6** | 1T | — | 256K | 128K+ | ✅ 图片+视频 |
| **Qwen 3.7 Max** | — | — | 1M | 65K | ✅ 成熟 |
| **Qwen3-Coder-Plus** | — | — | 1M | — | ✅ 成熟 |

### 4.4 最终模型适配方案

```
╔══════════════════════════════════════════════════════════════════╗
║              最终模型适配方案（已确认）                            ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  🧠 Hermes (Orchestrator) ── 双模型路由                         ║
║  ├── 主模型：Kimi K2.6 (kimi-for-coding, 包月)                  ║
║  │   → 日常交互、任务派发、状态查询、非开发长任务                 ║
║  └── 副模型：Qwen 3.7 Max (百炼按量)                            ║
║      → 仅 Phase 1/2/5 和关键决策点（架构/分解/验收/仲裁）       ║
║                                                                  ║
║  💻 Claude Code (主 Coder) ── Kimi K2.6 (包月共用)              ║
║     → 复杂编码、核心功能实现、Bug 修复                            ║
║     → 占总 token 消耗 60-80%                                    ║
║                                                                  ║
║  🔍 CodeWhale (审查员) ── DeepSeek V4 Pro (按量)                ║
║     → 代码审查、安全审计（异构审查，与主 Coder 不同模型）        ║
║                                                                  ║
║  🛠️  Qwen Code (辅助/E2E) ── Qwen3-Coder-Plus (百炼按量)        ║
║     → 简单任务、E2E 测试（含 UI 截图验证）、文档生成             ║
║     → 保持 Qwen 原生适配，工具调用 100% 兼容                    ║
║                                                                  ║
║  💰 月预算：~¥369-429（预估，以 Week 1 实测为准）              ║
║  ├── Kimi 包月：¥199（Hermes 日常 + Claude Code 编码）          ║
║  ├── Qwen 3.7 Max 按量：~¥80-120（仅关键决策点）               ║
║  ├── DS V4 Pro 按量：~¥50（审查）                               ║
║  └── Qwen3-Coder-Plus 按量：~¥40-60（辅助+E2E）               ║
║                                                                  ║
║  ⚠️ **预算声明**：以上为理论估算，实际成本以 Week 1 基线实验 ║
║  测量为准。系统实施三级预算预警：80% 预警 / 100% 软熔断 /      ║
║  150% 硬熔断。用户可随时调整预算上限。                         ║
<!-- v2.2 修改来源：成本审查辩论 P0-9 / 辩论轮次：Round 1 / 证据：推理 / 假设验证：辩论结论-反方胜 / 修改：预算从 P0 降级为 P1，增加"以实测为准"声明和三级预警机制 -->
╚══════════════════════════════════════════════════════════════════╝
```

### 4.5 双模型路由机制（Hermes 专属）

> ✅ **当前状态**：Qwen 3.7 Max 已配置并验证可用。
> 
> **配置信息**：
> - Provider: `qwen-max` (openai compatible mode)
> - Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
> - Model: `qwen3.7-max`
> - API Key: 已配置（DashScope）
> 
> **切换方式**：`hermes config set model.default qwen3.7-max && hermes config set model.provider qwen-max`
> 
> **已验证**：Phase 1 架构设计任务，5358 tokens，输出完整五层约束模型。

**路由规则**：

| 场景 | 模型 | 理由 |
|------|------|------|
| Phase 1: 架构设计 | Qwen 3.7 Max | SWE-bench Pro 工程判断力 |
| Phase 2: 任务分解 | Qwen 3.7 Max | 复杂任务分解能力 |
| Phase 5: 独立验收 | Qwen 3.7 Max | 最终质量判断 |
| 架构决策 | Qwen 3.7 Max | 技术方案评估 |
| 冲突仲裁 | Qwen 3.7 Max | 多 Agent 争议裁决 |
| 回滚决策 | Qwen 3.7 Max | 风险评估 |
| 其他所有任务 | Kimi K2.6 | 包月不限量，成本可控 |

**切换命令**：

```bash
# 切换到副模型（Qwen 3.7 Max）
hermes config set model.default qwen3.7-max
hermes config set model.provider qwen-max

# 执行任务...

# 切换回主模型（Kimi K2.6）
hermes config set model.default kimi-for-coding
hermes config set model.provider kimi-coding
```

**上下文连贯性保障**：
- 切换模型时，Hermes 将积累的完整上下文（用户需求、Phase 0 结论、历史记录）一并传递给副模型
- 决策结果回注 Hermes 上下文，保持全程连贯

**成本监控**：

| 模型 | 价格（入/出 ¥/百万 token） | 适用场景 |
|------|---------------------------|----------|
| Kimi K2.6 | 包月 ¥199 | 日常任务 |
| Qwen 3.7 Max | ¥12 / ¥36 | 关键决策 |

**预算控制**：
- Qwen 3.7 Max 每月预算：¥80-120（仅关键决策点）
- 超出预算时，降级到 Kimi K2.6

**故障处理**：

| 故障 | 处理 |
|------|------|
| Qwen API 不可用 | 降级到 Kimi K2.6，记录日志 |
| Qwen 响应超时 | 重试 3 次，然后降级 |
| Qwen 质量不达标 | 增加审查轮次，或人工介入 |
| API key 无效 | 报错，停止执行，通知用户 |

### 4.6 异构审查设计

编码和审查**必须使用不同模型**，这是本方案的核心差异化设计：

```
编码阶段：Claude Code (Kimi K2.6) → 编码 + 写注释 + commit
    ↓ 输出：git diff + commit message（不含编码 Agent 的思考过程）
审查阶段：CodeWhale (DS V4 Pro) → 只看 diff，独立判断
    ↓ 输出：结构化审查报告（P0/P1/P2 分级）
```

**为什么有效：**
1. 不同模型有不同的"思维盲区"，交叉审查能发现更多问题
2. DeepSeek V4 Pro 的 LiveCodeBench 93.5%（算法推理全球第一）使其极擅长发现逻辑漏洞
3. 审查者不看编码者的思考过程 → 避免确认偏差
4. 审查调用频率远低于编码（每个 feature 一次），按量计费成本可控

### 4.7 Qwen Code 必须保持 Qwen 原生模型

**已否决的方案：** 将 Qwen Code 接入 DeepSeek V4 Flash

**否决原因：**
1. **原生适配丧失**：Qwen Code CLI 的 prompt 工程、工具调用格式、输出解析逻辑都是针对 Qwen 模型深度优化的，换模型后工具调用成功率可能下降 30-40%
2. **多模态能力不足**：E2E 测试中的 UI 截图验证需要强大的视觉理解能力，DeepSeek 开源版视觉权重 Q3 2026 才释放
3. **成本差异不大**：价差对于辅助角色的调用频率来说，月差异仅 ¥20-40

**百炼平台可用模型清单（2026-06-16 验证）：**

| 模型 ID | 用途 | Thinking | 价格(入/出 ¥/百万token) |
|---------|------|----------|----------------------|
| `qwen3-coder-plus` | 日常编码/E2E（**默认**） | 关闭 | ¥2 / ¥8 |
| `qwen3-max-2026-01-23` | 架构决策/工程判断 | 开启 | ¥12 / ¥36 |
| `qwen3.7-max` | 备用（最强） | 开启 | ¥12 / ¥36 |
| `qwen3-vl-plus` | 视觉理解/UI截图分析 | — | 按视觉token计费 |

**Qwen Code 配置（已实施）：**

```json
{
  "modelProviders": {
    "alibaba": [
      {"id": "qwen3-coder-plus", "name": "日常编码/E2E"},
      {"id": "qwen3-max-2026-01-23", "name": "架构决策/工程判断"},
      {"id": "qwen3.7-max", "name": "备用"}
    ]
  },
  "model": {"name": "qwen3-coder-plus"}
}
```

### 4.8 Provider 配置规范（v2 新增）

**Hermes 内置 Provider 别名映射表（源码级事实）：**

| 用户输入 | 实际映射 | Base URL | 环境变量 |
|---------|---------|---------|---------|
| `kimi` / `kimi-coding` | `kimi-coding` | `https://api.kimi.com/coding/v1` | `KIMI_API_KEY` 或 `KIMI_CODING_API_KEY` |
| `kimi-cn` / `moonshot-cn` | `kimi-coding-cn` | `https://api.kimi.cn/coding/v1` | 同上 |
| `deepseek` | `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `glm` / `zai` / `zhipu` | `zai` | `https://api.z.ai/api/paas/v4` | `GLM_API_KEY` 或 `ZAI_API_KEY` |
| `minimax` | `minimax` | `https://api.minimax.io/anthropic/v1` | `MINIMAX_API_KEY` |
| `openai` | `openai` | `https://api.openai.com/v1` ⚠️ 硬编码 | `OPENAI_API_KEY` |
| `custom` | `custom` | 读取 `OPENAI_BASE_URL` | `OPENAI_API_KEY` |

> ⚠️ **致命踩坑记录**：`provider: "openai"` **硬编码路由到 api.openai.com**，不接受自定义 base_url。接入 Kimi K2.6 时必须用 `provider: "kimi-coding"`，不能用 `"openai"`。

**三文件一致性要求（强制规范）：**

```
~/.hermes/.env        → 环境变量（API Key + Base URL）
~/.hermes/config.yaml → model.provider 必须使用内置名称
~/.hermes/auth.json   → active_provider 必须与 config.yaml 一致
                         credential_pool 必须有对应 provider 条目
```

**已修复的配置文件示例：**

```yaml
# config.yaml
model:
  default: "kimi-for-coding"
  name: "kimi-for-coding"
  provider: "kimi-coding"  # ← 必须用内置名称，不能用 "openai"
```

```json
// auth.json
{
  "active_provider": "kimi-coding",
  "credential_pool": {
    "kimi-coding": [{
      "source": "env:KIMI_API_KEY",
      "base_url": "https://api.kimi.com/coding/v1"
    }]
  }
}
```

**规范：** 在 AGENTS.md 或 pipeline 文档中维护一份"模型-Provider-环境变量映射表"，任何模型变更必须同步更新此表和三个配置文件。

### 4.9 模型路由策略

| 任务类型 | 默认 Agent | 降级 Agent | 备注 |
|---------|-----------|-----------|------|
| 复杂编码 | Claude Code | Qwen Code | Kimi 429 时降级 |
| 代码审查 | CodeWhale | — | 必须用 DS 模型审查 |
| Shell/DevOps | CodeWhale | — | — |
| 浏览器 E2E | Qwen Code | — | — |
| 中文文档 | Qwen Code | — | — |
| 简单修复（<20 行） | Qwen Code | — | 成本低 |
| 架构/验收 | Hermes (Qwen 3.7 Max) | Hermes (Kimi K2.6) | 决策点用副模型 |

### 4.10 模型健康度监控（v2 新增）

```python
class ModelHealthMonitor:
    """模型 API 健康度监控"""

    # 滑动窗口统计
    window_size: int = 20           # 最近 20 次调用
    max_response_time_ms: int = 60000
    error_rate_threshold: float = 0.3

    # 健康度指标
    metrics = {
        "response_time_p95": None,     # P95 响应时间
        "error_rate": None,            # 错误率
        "success_rate": None,          # 成功率
        "avg_latency_ms": None,        # 平均延迟
    }

    # 降级策略
    # 健康度下降 → 自动切换降级模型
    # 健康度数据纳入可观测性系统
```

---

## 五、Agent 角色与分工

### 5.1 Agent 能力矩阵

| Agent | 模型 | 核心角色 | 激活条件 | 硬约束 |
|-------|------|---------|---------|--------|
| Hermes | Kimi K2.6 + Qwen 3.7 Max 双路由 | Orchestrator + Judge | 始终在线 | 不写代码、不首审、不首测；所有决策受 Layer 1-3 约束 |
| Claude Code | Kimi K2.6 (包月) | 主 Coder + Tech Lead | 所有编码任务首选 | 按Profile约束运行；一次只做一个 feature；必须产生可测试 diff |
| CodeWhale | DS V4 Pro (按量) | 审查员 + Shell 专家 | Claude 编码后必须激活 | --auto 模式；输出 P0/P1/P2 分级 + 行号 + 修复建议；只看 diff 不看思考过程 |
| Qwen Code | Qwen3-Coder-Plus (按量) | 辅助 Coder + 浏览器测试 | ① Claude 429/失败 ② 浏览器 E2E ③ 中文文档 ④ <20 行简单任务 | -y 模式；简单任务可独立执行 |

### 5.2 默认 2-Agent 模式（80% 场景）

```
Hermes 派发 → Claude Code 编码 → CodeWhale 审查 → 确定性验证 → Hermes 验收
```

### 5.3 全团队 4-Agent 模式（20% 场景）

触发条件（满足任一）：
- 任务涉及 5+ 文件、200+ 行代码
- 需要前后端/多模块协同
- 需要浏览器 E2E 验证
- Hermes 判断任务可并行分解

```
Hermes 派发
  → Claude Code 编码（主）
  → Qwen Code 辅助编码 / 文档（并行）
  → CodeWhale 审查
  → Qwen Code 浏览器 E2E 测试
  → 确定性验证
  → Hermes 验收
```

---

## 六、上下文窗口管理（v2 新增）

> **这是 v1 PRD 最大的盲区。** Claude Code 团队的一手经验表明：上下文压缩导致安全指令丢失是 SEV 级事故，长对话意图漂移是核心挑战。

### 6.1 分层上下文注入策略

```python
class ContextManager:
    """每个 Agent 的上下文窗口管理器"""

    priority_layers = [
        "safety_instructions",     # 永不压缩：安全指令（每次工具调用时重复注入）
        "current_feature_spec",    # 高优先级：当前 feature 的需求和验收标准
        "architecture_contract",   # 中优先级：接口定义、数据模型
        "related_code_files",      # 按需加载：与当前任务相关的代码文件
        "memory_and_pitfalls",     # 按需加载：项目记忆和已知坑点
        "progress_history",        # 可压缩：历史进度和已完成工作
    ]

    max_context_tokens: int = 100000
    safety_reserve_tokens: int = 5000   # 安全指令永远保留
    task_reserve_tokens: int = 20000    # 当前任务上下文保留
    compressible_tokens: int = 75000    # 可压缩部分
```

### 6.2 安全指令强化机制（Reinforcement）

每次工具调用返回结果时，不仅返回数据，还反复提醒 Agent 总体目标和任务状态：

```python
# Reinforcement 模式：
tool_result = execute_test("pytest tests/test_auth.py")
agent_input = f"""
[当前任务] 实现 F001 用户注册接口
[验收标准] 注册成功返回 201，重复邮箱返回 409
[已完成] 代码编写完成，已 git commit
[当前步骤] 运行测试验证
[测试结果] {tool_result}
[提醒] 请根据测试结果决定下一步操作。如果测试失败，修复代码。如果通过，更新 progress.md。
"""
```

### 6.3 上下文压缩策略

不依赖 LLM 总结（会丢失关键信息），而是：
- 将关键决策写入持久化文件（TODO.md, progress.md）
- 每次压缩后重新加载持久化文件
- 使用 Git diff 而非完整文件作为上下文

### 6.4 Agentic Search 替代文件注入

不主动注入完整文件（MEMORY.md、AGENTS.md 等），而是提供搜索工具让 Agent 按需查找：

```
search_memory("已知的认证相关坑点") → 返回 MEMORY.md 中的相关片段
search_code("用户注册") → 在代码库中搜索相关实现
```

上下文只保留当前 feature 的核心需求 + 安全指令。

---

## 七、Prompt Cache 策略（v2 新增）

> Prompt Caching 是降低成本和延迟的核心。Claude Code 团队将缓存命中率视为关键 KPI，命中率过低会被定为 SEV 级严重事故。

### 7.1 Prompt 缓存分层

```python
class PromptCacheStrategy:
    """Prompt 缓存分层策略"""

    # 第一层：静态缓存（跨所有调用共享）
    static_cache = [
        "SOUL.md 内容",          # Agent 角色定义
        "AGENTS.md 内容",        # 协作规则
        "architecture.md 摘要",  # 架构概要
    ]

    # 第二层：项目级缓存（同一项目内共享）
    project_cache = [
        "specs/api-contracts.md",
        "当前 features.json 状态",
        "MEMORY.md 内容",
    ]

    # 第三层：任务级缓存（同一 feature 内共享）
    task_cache = [
        "当前 feature 的详细需求",
        "相关文件内容",
        "最近的 Git diff",
    ]

    # 第四层：不可缓存（每次调用动态生成）
    dynamic = [
        "Agent 的当前对话历史",
        "验证结果反馈",
        "审查意见",
    ]
```

### 7.2 缓存 KPI

- 目标缓存命中率：> 50%
- 缓存命中率低于 30% 触发告警
- 每次调用记录 cache_hit 字段到 traces 表

---

## 八、Agent Adapter 容错架构（v2 重设计）

> v1 假设可以通过统一接口适配所有 Agent，这是理想化假设。不同 CLI 的输出格式、错误码、行为模式差异巨大，需要采用"适配器 + 解析器 + 容错"三层架构。

### 8.1 三层架构

```
1. 适配层（Adapter）：每个 Agent 的启动/停止/通信逻辑
   - 不强求统一输入格式，而是为每个 Agent 生成其原生格式的输入
   - claude.exe → 使用 --print 模式获取结构化输出
   - qwen → 使用 --output-format json（如果支持）

2. 解析层（Parser）：从非结构化输出中提取关键信息
   - 使用正则 + 启发式规则，而非要求 LLM 输出纯 JSON
   - 解析失败时 fallback 到 LLM 提取（用小模型解析大模型的输出）

3. 容错层（Tolerance）：处理各种异常
   - 超时、崩溃、输出截断、编码错误
   - 每种异常有明确的恢复策略
```

### 8.2 统一数据结构

```python
@dataclass
class AgentResult:
    success: bool
    output: str
    structured: Optional[dict]
    tokens_used: int
    cost_usd: float
    latency_ms: int
    exit_code: int
    error_message: Optional[str]
```

### 8.3 每个 Agent 的 Adapter 实现

- `ClaudeCodeAdapter`：使用 `--print` 模式，解析终端输出
- `CodeWhaleAdapter`：使用 `--auto` 模式，解析审查报告
- `QwenCodeAdapter`：使用 `-y` 模式，解析 JSON/Markdown 输出
- `HermesAdapter`：内部决策，不直接执行编码

### 8.4 原子性保障

每个 feature 的编码过程包装为原子事务：
- 开始前 `git stash` 或创建 worktree
- 完成后 `git commit`
- 失败时 `git checkout` 回滚或丢弃 worktree

---

## 九、协作流程：Phase 0-6 详细设计

### 9.0 流程总览

```
Phase 0: Initializer（项目初始化）
Phase 1: Design（架构设计）
Phase 2: Decompose（任务分解）
Phase 3: Develop（增量开发循环）
Phase 4: Test（端到端测试）
Phase 5: Accept（独立验收）
Phase 6: Deploy / Deliver（部署与交付）
```

每个 Phase 的 `advance` 必须通过 `check` 函数。支持显式回退：`pipeline.py rollback-phase <project> --to design`（需人工审批）。

### 9.1 Phase 0: Initializer（项目初始化）

**目标：** 创建项目骨架和所有必要元数据文件。

**触发：** `pipeline.py init <项目名> --description "..." --stack "..."`

**执行内容：**
1. Hermes 读取项目描述和技术栈
2. 创建项目目录：`C:\agent-workspace\<项目名>\`
3. 初始化 git repo
4. 创建元数据文件：
   - `SOUL.md`：每个 Agent 的角色定义（含 schema_version）
   - `AGENTS.md`：团队协作规则（含 schema_version）
   - `MEMORY.md`：项目级共享记忆（初始为空）
   - `progress.md`：当前进度日志
   - `features.json`：验收清单（带 JSON Schema）
   - `specs/`：PRD/API 合约目录
   - `.logs/`：审计日志目录
5. 写入 SQLite 状态数据库
6. 创建 `init.ps1` 环境初始化脚本

**advance 条件（check_init）：**
- 项目目录存在
- git repo 初始化成功
- features.json 符合 schema
- SOUL.md / AGENTS.md / progress.md 存在
- SQLite DB 创建成功

### 9.2 Phase 1: Design（架构设计）

**目标：** 输出可执行的架构方案，经人类审批后锁定。

**模型路由：** 触发 Hermes 副模型（Qwen 3.7 Max）进行架构决策。

**执行内容：**
1. Hermes (Qwen 3.7 Max) 根据 PRD/specs 输出架构方案到 `specs/architecture.md`
2. Claude Code 并行审查技术可行性
3. Qwen Code 并行审查（仅全团队模式）
4. Hermes 综合审查意见，更新 `specs/architecture.md`

**Orchestrator 决策验证层（v2 新增）：**

```
Hermes 输出架构/分解方案
    ↓
[自动校验]
  - 依赖图无环检测
  - feature 粒度检测（不应太大/太小）
  - 接口一致性检测（类型系统级别）
  - 成本预估合理性检测
    ↓
[交叉审查]（仅复杂项目）
  - Claude Code 审查技术可行性
  - 如果 Claude 与 Hermes 意见冲突 → 升级人工裁决
```

5. **人类审批（阻塞式，30分钟超时）**

**advance 条件（check_design）：**
- `specs/architecture.md` 存在
- 人类已审批（状态字段 `design_approved = true`）
- 架构中包含模块划分、接口定义、数据流

### 9.3 Phase 2: Decompose（任务分解）

**目标：** 把 PRD 拆解为可独立交付的 feature 清单。

**模型路由：** 触发 Hermes 副模型（Qwen 3.7 Max）进行任务分解。

**Feature 粒度标准（v2 新增）：**

| 复杂度 | 文件数 | 代码行数 | 预计 tokens |
|--------|--------|---------|------------|
| simple | 1-2 | <50 行 | <5,000 |
| medium | 2-5 | 50-200 行 | 5,000-20,000 |
| complex | 5-10 | 200-500 行 | 20,000-50,000 |
| ❌ 超大 | >10 | >500 行 | **必须拆分** |
| ❌ 过小 | 1 | <10 行 | **应合并** |

**执行内容：**
1. Hermes (Qwen 3.7 Max) 读取 `specs/architecture.md` 和 PRD
2. 生成 `features.json` 初稿
3. 每个 feature 包含：id、title、description、acceptance_criteria、dependencies、estimated_complexity、owner_agent、status、max_token_budget、wave
4. Hermes 验证依赖图无环
5. 按依赖关系分波（wave）规划

**advance 条件（check_decompose）：**
- `features.json` 符合 schema
- 所有 feature 有 acceptance_criteria
- 依赖图无环
- 已分波
- feature 粒度检查通过

### 9.4 Phase 3: Develop（增量开发循环）

**目标：** 逐个 feature 编码、审查、修复、通过。

**循环流程：**
```
1. Hermes 选择下一个 pending 且依赖满足的 feature
2. Hermes 派发任务给 Claude Code（或 Qwen Code）
3. Coding Agent 读取当前 feature spec（Agentic Search 按需加载上下文）
4. Coding Agent 在沙箱中执行编码
5. Coding Agent 完成 → git commit → 更新 progress.md
6. Layer 1 冒烟测试（v2 新增）：应用能否导入/启动
7. Layer 1 验证：diff/commit/lint/type/test
8. Hermes 派发 CodeWhale 审查（对抗性审查：只看 diff，不给编码 Agent 的思考过程）
9. CodeWhale 输出 P0/P1/P2 分级报告
10. 若有 P0 → 返回 Claude 修复 → 重新审查（最多 N 次）
11. 全部通过 → 标记 feature status = "passed"
12. 写入 checkpoint
```

**feature 选取规则：**
- 优先选择无依赖的 pending feature
- 同一 wave 内的 feature 可并行（不同 worktree/分支）
- 文件重叠检测：两个 feature 都要改同一文件时，串行化

**编码 Agent 约束：**
- 一次只做一个 feature
- 按当前 Profile 约束运行（PIPELINE/LOCKDOWN 模式启用多用户隔离）
- 必须产生非空 git diff
- 必须运行 lint/type/test
- 必须更新 `progress.md`
- 不得修改 feature 范围外的代码（`git diff --stat` 范围检查）

**审查报告格式（CodeWhale 输出）：**
```json
{
  "feature_id": "F001",
  "summary": "整体评价",
  "issues": [
    {
      "level": "P0",
      "file": "app.py",
      "line": 42,
      "category": "logic",
      "description": "...",
      "suggestion": "..."
    }
  ],
  "passed": false
}
```

**P0/P1/P2 定义：**
- P0：致命错误（崩溃、安全漏洞、数据丢失、严重逻辑错误）→ 必须修复
- P1：严重问题（性能、可维护性、边界情况）→ 记录并修复，可延期
- P2：建议（命名、注释、风格）→ 记录 pitfall，后续统一处理

### 9.5 Phase 4: Test（端到端测试）

**目标：** 对所有 passing feature 进行集成验证。

**执行内容：**
1. Hermes 触发全量回归测试
2. Qwen Code 执行浏览器 E2E 测试（如适用）
3. 运行集成测试、API 测试、数据库状态验证
4. 发现问题 → 标记对应 feature 为 failed → 返回 Phase 3

**测试质量门禁（v2 新增）：**

```python
class TestQualityGate:
    min_line_coverage: float = 0.70      # 新增代码至少 70% 行覆盖
    min_branch_coverage: float = 0.50    # 新增分支至少 50% 覆盖
    # 反 tautology 检测：检查 assert True 等无效断言
    # 独立测试生成（高级）：让一个 Agent 写代码，另一个独立写测试
```

**advance 条件（check_test）：**
- 所有 passing feature 的回归测试通过
- E2E 测试通过（如适用）
- 测试覆盖率达标
- 没有 failed feature

### 9.6 Phase 5: Accept（独立验收）

**目标：** 最终交付前的人类确认。

**模型路由：** 触发 Hermes 副模型（Qwen 3.7 Max）进行最终验收判断。

**执行内容：**
1. Hermes (Qwen 3.7 Max) 生成验收报告：
   - 所有 feature 列表及状态
   - 总 token 消耗、总成本
   - 测试覆盖率
   - 已知风险/P1/P2 遗留
2. 人类审查并确认（阻塞式审批）
3. 合并到主分支
4. 归档项目状态

**advance 条件（check_accept）：**
- 所有 feature status = "passed"
- 人类已审批
- 主分支合并成功
- 验收报告已生成

### 9.7 Phase 6: Deploy / Deliver（部署与交付）

**目标：** 让非技术用户也能把项目跑起来、用起来。"交付即可使用"是 Phase 6 的唯一标准。

**执行内容：**

1. **Claude Code 生成本地运行脚本**
   - `setup.ps1`：安装依赖
   - `start.ps1`：一键启动应用
   - `stop.ps1`：一键停止应用（可选）
   - `verify-runtime.ps1`：验证应用能正常启动和响应

2. **Claude Code 生成环境配置模板**
   - `.env.example`：列出所有需要配置的环境变量及说明

3. **Qwen Code 生成中文部署文档**
   - `DEPLOY.md`：面向小白的部署与运行指南
   - `README.md` 增加"快速开始"章节

4. **CodeWhale 验证部署脚本**
   - 在沙箱环境中执行 `setup.ps1` 和 `verify-runtime.ps1`
   - 确认应用能正常导入/构建/启动

5. **Hermes 生成部署验收报告**

**交付物清单：**

```
C:\agent-workspace\<project>\
├── README.md              # 项目简介 + 快速开始
├── DEPLOY.md              # 部署与运行指南（面向小白）
├── .env.example           # 环境变量模板
├── setup.ps1              # 依赖安装脚本
├── start.ps1              # 一键启动脚本
├── stop.ps1               # 一键停止脚本（可选）
└── verify-runtime.ps1     # 运行验证脚本
```

**advance 条件（check_deploy）：**
- `README.md` 存在且包含"快速开始"
- `DEPLOY.md` 存在
- `.env.example` 存在
- `setup.ps1` / `start.ps1` / `verify-runtime.ps1` 存在且可执行
- 应用能成功导入/构建
- 健康检查通过（如有 HTTP 服务）

### 9.8 Phase 7 预留：交付后维护（本期不实现）

> 代码开发完成不等于交付完成。用户拿到代码后发现 bug、需要新增功能、依赖库更新等都是真实需求。

**预留设计：**
- 生成代码可读性报告（标注 Agent 生成代码中难以理解的部分）
- 生成维护手册（如何修改、扩展、调试）
- 提供增量开发入口（用户可以用同一套系统继续开发新功能）

---

## 十、硬基础设施详细设计

### 10.1 pipeline.py CLI 设计

```bash
# 项目初始化
python ~/.hermes/scripts/pipeline.py init <project> --description "..." --stack "..."

# 查看状态（rich terminal UI / textual 实时仪表盘）
python ~/.hermes/scripts/pipeline.py status <project>

# 检查当前 phase 是否可推进
python ~/.hermes/scripts/pipeline.py check <project>

# 推进到下一 phase（自动执行 check）
python ~/.hermes/scripts/pipeline.py advance <project>

# 恢复从 checkpoint
python ~/.hermes/scripts/pipeline.py resume <project> [--checkpoint-id N]

# 回滚到指定 checkpoint
python ~/.hermes/scripts/pipeline.py rollback <project> --checkpoint-id N

# Phase 回退（v2 新增，需人工审批）
python ~/.hermes/scripts/pipeline.py rollback-phase <project> --to design|decompose|develop

# 生成 Markdown 报告
python ~/.hermes/scripts/pipeline.py report <project> [--output report.md]

# 部署与交付（Phase 6）
python ~/.hermes/scripts/pipeline.py deploy <project>

# 验证项目可运行
python ~/.hermes/scripts/pipeline.py verify-runtime <project>

# 人工审批
python ~/.hermes/scripts/pipeline.py approve <project> --phase design|accept

# 暂停/继续/中止
python ~/.hermes/scripts/pipeline.py pause <project>
python ~/.hermes/scripts/pipeline.py resume <project>
python ~/.hermes/scripts/pipeline.py abort <project>

# 更新 feature 状态（人工介入）
python ~/.hermes/scripts/pipeline.py set-feature <project> <feature_id> --status passed|failed|pending

# 查看预算消耗
python ~/.hermes/scripts/pipeline.py budget <project>

# 查看模型健康度（v2 新增）
python ~/.hermes/scripts/pipeline.py model-health [model_name]

# Provider 配置校验（v2 新增）
python ~/.hermes/scripts/pipeline.py check-provider

# === 沙箱模式管理（v2 新增） ===

# 查看当前沙箱模式和权限详情
python ~/.hermes/scripts/pipeline.py mode <project>

# 切换沙箱模式
python ~/.hermes/scripts/pipeline.py mode <project> assistant|pipeline|research|free|lockdown

# 启动时指定模式
python ~/.hermes/scripts/pipeline.py init <project> --mode pipeline --description "..." --stack "..."

# 细粒度开关
python ~/.hermes/scripts/pipeline.py toggle <project> --allow-network
python ~/.hermes/scripts/pipeline.py toggle <project> --allow-dir "C:\data"
python ~/.hermes/scripts/pipeline.py toggle <project> --auto-approve-l2

# 临时授权提升
python ~/.hermes/scripts/pipeline.py elevate <project> --profile free --duration 2h
python ~/.hermes/scripts/pipeline.py elevate <project> --allow "git push,pip install" --duration 30m

# 批准 Agent 权限升级请求
python ~/.hermes/scripts/pipeline.py grant <project> --request-id <id> --duration 30m
```

### 10.2 Budget Enforcer（预算治理）

**三级预算（v2 升级：基于实测数据动态调整）：**

```python
class BudgetConfig:
    # 以下数字为初始估值，必须通过 Week 1 基线实验校准
    per_step_max_tokens: int = 8000        # 单次 LLM 调用上限
    per_task_max_tokens: int = 50000       # 单个 feature 任务上限
    per_project_max_usd: float = 50.0      # 单个项目总预算
    per_project_max_usd_daily: float = 10.0 # 单日上限
    max_repair_rounds: int = 3             # P0 返修最大轮数
    max_agent_steps: int = 30              # 单个 Agent 单任务最大步数

    # v2 新增：预算必须是动态可调的，而非硬编码
    # 每个项目可根据 Week 1 基线数据自动校准
```

> **参考数据**：AutoCodeRover 平均每个 Issue 花费 $0.43 USD；Claude Code 平均活跃用户日花费约 $6；SWE-bench 任务的 token 消耗通常在 10,000-50,000 tokens/任务。

**硬熔断规则：**
- 任何一级预算超过 → 立即终止当前任务，记录原因，升级人工
- 同一 feature P0 返修超过 `max_repair_rounds` → 升级人工
- Agent 单任务步数超过 `max_agent_steps` → 强制停止
- 连续 3 次 API 调用失败 → Circuit Breaker 打开

### 10.3 Circuit Breaker（熔断器）

```python
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: int = 300
    half_open_max_calls: int = 2
```

应用场景：
- 某个 Agent 连续失败
- 某个模型 API 连续超时
- 某个 feature 反复无法通过

### 10.4 降级策略（v2 新增）

```python
class DegradationStrategy:
    """系统降级策略"""

    levels = {
        "green":  "全部 Agent 可用，正常流水线",
        "yellow": "某个辅助 Agent 不可用 → 跳过其环节，增加其他 Agent 的验证强度",
        "orange": "审查 Agent 不可用 → 编码 Agent 自审 + 增加自动化测试覆盖率要求",
        "red":    "主 Coder 不可用 → 降级到辅助 Coder，降低任务复杂度预期",
        "black":  "Orchestrator 不可用 → 系统暂停，保存所有状态，等待恢复",
    }
```

### 10.5 Command Gate（命令门控，v2.1 修正：白名单模式）

> ⚠️ **v2.1 修正**：v2 原设计使用正则黑名单，经红队审查验证可被多种方式绕过（base64 编码、分片组合、合法命令替代如 certutil/mshta/rundll32）。成功率 70-85%。
> **改为白名单模式**：仅允许已知安全命令，不在白名单中的命令一律拦截并请求用户确认。

**命令白名单配置（`~/.hermes/config/command-gate.yaml`）：**

```yaml
# 白名单：仅允许以下命令模式，不在白名单中的命令默认拦截
allow:
  # Git 操作（安全子集）
  - pattern: "^git\\s+(status|diff|log|show|branch|stash|checkout|commit|merge|rebase|tag)"
    reason: "Git 版本控制操作"
  # 测试运行
  - pattern: "^(pytest|python\\s+-m\\s+pytest|npm\\s+test|npm\\s+run\\s+test|python\\s+-m\\s+unittest|jest|vitest)"
    reason: "运行测试"
  # 静态分析
  - pattern: "^(flake8|black|ruff|eslint|tsc|mypy|pylint|prettier)"
    reason: "代码静态分析"
  # Python 脚本执行（限定范围）
  - pattern: "^python\\s+(setup\\.py|manage\\.py|app\\.py|main\\.py|run\\.py|pipeline\\.py|verify-runtime\\.py|\\S+test\\.py|\\S+spec\\.py)"
    reason: "执行已知 Python 脚本"
  # 包管理（限定范围）
  - pattern: "^(pip\\s+list|pip\\s+show|npm\\s+list|npm\\s+info)"
    reason: "查询已安装包"
  # 文件操作（安全子集）
  - pattern: "^(ls|dir|cat|type|head|tail|find|grep|wc|stat|file)"
    reason: "只读文件操作"
  - pattern: "^(mkdir|touch|cp|copy|mv|move|rm\\s+\\S+|del\\s+\\S+)"
    reason: "文件操作（需确认目标路径）"
  # 系统信息
  - pattern: "^(echo|printenv|env|set|whoami|hostname|pwd|cd)"
    reason: "系统信息查询"
  # 网络（限定范围）
  - pattern: "^(curl\\s+--head|curl\\s+-I|ping|nslookup|tracert)"
    reason: "网络诊断"

# 需要用户确认的高风险操作（即使在白名单中也需确认）
ask:
  - pattern: "^git\\s+push"
    reason: "推送到远程仓库"
  - pattern: "^git\\s+fetch|git\\s+pull"
    reason: "从远程获取代码"
  - pattern: "^(pip\\s+install|npm\\s+install|npm\\s+ci|yarn\\s+install|pnpm\\s+install)"
    reason: "安装新依赖"
  - pattern: "^(python\\s+.*migrate|alembic|django-admin\\s+migrate|npm\\s+run\\s+migrate)"
    reason: "数据库迁移"
  - pattern: "^(docker\\s+run|docker\\s+build|docker-compose)"
    reason: "Docker 操作"

# 绝对禁止（不可覆盖）
deny:
  - pattern: "^rm\\s+-rf\\s+/|^rmdir\\s+/s\\s+/q"
    reason: "禁止删除根目录"
  - pattern: "format\\s+C:|format\\s+/fs"
    reason: "禁止格式化磁盘"
  - pattern: "reg\\s+delete\\s+HKLM"
    reason: "禁止删除注册表关键项"
  - pattern: "shutdown\\s+/s|shutdown\\s+/r"
    reason: "禁止关机/重启"
  - pattern: "net\\s+user\\s+/delete|net\\s+localgroup"
    reason: "禁止修改系统用户/组"
  - pattern: "certutil\\s+-urlcache|certutil\\s+-decode"
    reason: "禁止 certutil 下载/解码（绕过手段）"
  - pattern: "mshta\\s+javascript:|mshta\\s+vbscript:"
    reason: "禁止 mshta 执行脚本（绕过手段）"
  - pattern: "rundll32\\s+.*,\\s*#"
    reason: "禁止 rundll32 执行任意代码（绕过手段）"
  - pattern: "wmic\\s+process\\s+call\\s+create"
    reason: "禁止 WMI 创建进程（绕过手段）"
  - pattern: "powershell\\s+-enc|powershell\\s+-encodedcommand"
    reason: "禁止 PowerShell 编码执行（绕过手段）"

# 默认策略：不在白名单中的命令 → 拦截并请求用户确认
default_action: ask
```

**绕过检测增强（v2.1 新增）：**

```python
class BypassDetector:
    """检测命令绕过尝试"""
    
    # 编码绕过检测
    encoded_patterns = [
        r"base64\s+-d\s*\|",           # base64 解码后管道执行
        r"echo\s+['\"][A-Za-z0-9+/=]{20,}['\"]\s*\|",  # 长 base64 字符串
        r"certutil\s+-decode",           # certutil 解码
    ]
    
    # 分片绕过检测
    fragmentation_patterns = [
        r"SET\s+\w+\s*=\s*\w+",         # 环境变量拼接
        r"\%\w+\%\s+\%\w+\%",            # 变量组合执行
    ]
    
    # 替代解释器检测
    alt_interpreter_patterns = [
        r"cscript\s+.*\.(vbs|js)",
        r"wscript\s+.*\.(vbs|js)",
        r"mshta\s+.*\.(hta|html)",
    ]
    
    def detect_bypass(self, command: str) -> tuple[bool, str]:
        """返回 (是否检测到绕过, 原因)"""
        for pattern in self.encoded_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return True, f"检测到编码绕过: {pattern}"
        for pattern in self.fragmentation_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return True, f"检测到分片绕过: {pattern}"
        for pattern in self.alt_interpreter_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return True, f"检测到替代解释器: {pattern}"
        return False, ""
```

### 10.6 心跳与超时

- 每个 Agent 进程启动后，每秒向 pipeline 写 heartbeat 文件
- 超过 `heartbeat_timeout`（如 60 秒）无心跳 → 判定卡死
- 卡死后：kill 进程、回滚到最近 checkpoint、升级人工

---

## 十一、人机协作边界（v2 升级）

### 11.1 审批分级

| 级别 | 触发条件 | 系统行为 | 超时策略 |
|------|---------|---------|---------|
| **阻塞式** | Phase 1 架构设计、Phase 5 最终验收 | Agent 暂停等待 | 30分钟超时后暂停保存状态 |
| **异步式** | 依赖安装、git push | Agent 继续做其他任务 | 2小时超时后跳过该操作 |
| **默认放行式** | 低风险操作 | 通知用户但不等待 | 5分钟后自动放行 |

### 11.2 必须人工审批的点

| 触发条件 | 审批级别 | 系统行为 | 用户操作 |
|---------|---------|---------|---------|
| Phase 1 架构设计完成 | 阻塞式 | 输出审批摘要 | 同意 / 要求修改 |
| Phase 5 最终验收 | 阻塞式 | 输出验收报告 | 确认合并 / 拒绝 |
| 超出 token 预算 | 阻塞式 | 暂停并告警 | 增加预算 / 取消任务 |
| P0 返修超过 3 次 | 阻塞式 | 暂停并告警 | 接管 / 放宽标准 |
| 合并冲突 | 阻塞式 | 暂停并告警 | 解决冲突 |
| 安装新依赖 | 异步式 | 拦截并请求 | 同意 / 拒绝 |
| `git push` | 异步式 | 拦截并请求 | 同意 / 拒绝 |
| Agent 卡死/循环 | 自动熔断 | 熔断并告警 | 检查 / 重试 / 终止 |

### 11.3 审批上下文自动摘要

系统自动生成 3-5 行的决策摘要，包含关键数据（成本、风险、替代方案），用户只需回复 "y" / "n" / "修改xxx"。

### 11.4 自动升级条件

```python
class EscalationRules:
    p0_repair_exceeds = 3
    budget_exceeds_ratio = 0.8   # 预算使用 80% 告警，100% 熔断
    same_feature_fails = 2       # 同一 feature 连续失败 2 次升级
    agent_unresponsive_seconds = 60
    test_failure_rate = 0.3      # 回归测试失败率超过 30% 升级
```

### 11.5 人工接管命令

```bash
python ~/.hermes/scripts/pipeline.py pause myproject
# 用户修改代码/状态
python ~/.hermes/scripts/pipeline.py set-feature myproject F002 --status passed
python ~/.hermes/scripts/pipeline.py resume myproject
```

---

## 十二、文件系统协作协议

### 12.1 文件清单与职责

| 文件 | 用途 | Owner | 更新时机 |
|------|------|-------|---------|
| `SOUL.md` | 每个 Agent 的角色定义（含 schema_version） | Hermes | Phase 0 / 角色变更 |
| `AGENTS.md` | 协作规则、通信协议（含 schema_version） | Hermes | Phase 0 / 规则更新 |
| `MEMORY.md` | 项目级共享记忆、pitfall | Hermes | 每次 P1/P2 发现后 |
| `progress.md` | 当前进度日志 | Hermes | 每个 feature 状态变化 |
| `features.json` | 验收清单 | Hermes | Phase 2 / feature 状态变化 |
| `specs/architecture.md` | 架构设计 | Hermes | Phase 1 |
| `specs/prd.md` | 产品需求 | 用户 | Phase 0 |
| `specs/api-contracts.md` | API 合约 | Hermes | Phase 1 |
| `.logs/audit.log` | 命令审计 | Layer 0 | 实时 |
| `.logs/traces.jsonl` | Agent 调用 trace | Layer 3 | 每次调用 |

**协议版本控制（v2 新增）：** 每个协议文件头部增加 `schema_version` 字段，Agent Adapter 根据版本选择解析逻辑。

### 12.2 Schema 约束

**features.json Schema：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "project": { "type": "string" },
    "version": { "type": "string" },
    "schema_version": { "type": "integer" },
    "features": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string", "pattern": "^F[0-9]{3}$" },
          "title": { "type": "string" },
          "description": { "type": "string" },
          "status": { "enum": ["pending", "in_progress", "review", "test", "passed", "failed", "needs_rework"] },
          "owner": { "enum": ["claude", "qwen"] },
          "complexity": { "enum": ["simple", "medium", "complex"] },
          "dependencies": { "type": "array", "items": { "type": "string" } },
          "acceptance_criteria": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "type": { "enum": ["test", "command", "assertion", "manual"] },
                "description": { "type": "string" },
                "payload": { "type": "string" }
              },
              "required": ["type", "description", "payload"]
            }
          },
          "max_token_budget": { "type": "integer" },
          "wave": { "type": "integer" }
        },
        "required": ["id", "title", "description", "status", "owner", "complexity", "acceptance_criteria"]
      }
    }
  },
  "required": ["project", "version", "features"]
}
```

### 12.3 并发控制

1. **文件锁**：写 `features.json` 和 `progress.md` 前获取 `portalocker` 文件锁
2. **原子写入**：先写 `.tmp` 文件，再 `os.replace`
3. **只读快照**：Agent 读取前先复制一份快照，避免读到半写状态
4. **事件驱动**：Agent 不轮询文件，而是等待 pipeline 派发信号

### 12.4 记忆隔离策略

- **项目级记忆**：`MEMORY.md`，只放经过验证的架构决策、pitfall、接口约定
- **Agent 级记忆**：每个 Agent 的私有提示/技能文件，不共享
- **Feature 级记忆**：feature 上下文只注入当前 feature 相关记忆
- **短期记忆**：当前对话窗口内的上下文
- **定期压缩**：MEMORY.md 超过 1000 行时自动摘要

---

## 十三、版本控制与合并策略

### 13.1 分支策略

```
main                 # 保护分支，Agent 不能直推
  ├── agent/f001-claude    # F001 的 worktree/分支
  ├── agent/f002-claude    # F002 的 worktree/分支
  └── agent/f003-qwen      # F003 的 worktree/分支
```

### 13.2 Worktree 管理（v2 优化）

**v2 改进：解决路径长度、磁盘空间、清理遗漏问题**

- Worktree 放在项目目录**外部**：`C:\agent-worktrees\<project>-f001\`（避免 Windows 260 字符路径限制）
- 自动清理机制：feature passed 后自动 `git worktree remove`
- 启用 Windows 长路径支持（`HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1`）
- 考虑分支隔离替代 worktree（更轻量，但不支持并行工作）

### 13.3 文件锁/排他区

- feature 开始前声明要修改的文件集合：`claimed_files`
- 新 feature 开始前检查是否有重叠：
  - 无重叠 → 允许并行
  - 有重叠 → 串行化或升级人工

### 13.4 合并流程

1. feature 完成后，Agent 在 worktree 中创建 commit
2. pipeline 自动 push 到远程 `agent/fxxx-xxx` 分支
3. CI 跑测试（lint/test/build）
4. CodeWhale 审查（已在 Phase 3 完成）
5. 人类最终 review PR 或自动合并（如所有检查通过且用户授权）
6. 合并到 main 后自动删除 worktree

### 13.5 冲突解决

- **文本冲突**：升级人工解决
- **语义冲突**：通过回归测试发现，标记相关 feature 为 failed，返回 Phase 3
- **依赖冲突**：统一依赖入口 + lockfile 校验

---

## 十四、依赖与包管理

### 14.1 依赖安装策略

1. **统一入口**：所有依赖安装由 pipeline 执行，Agent 不直接运行 `pip install`
2. **Agent 提出依赖变更**：在代码注释或 PR 描述中声明需要的新依赖
3. **pipeline 解析依赖变更**：检查兼容性、更新 lockfile、运行测试验证
4. **冲突时停止**：如果两个 feature 引入冲突版本，标记阻塞，升级人工

### 14.2 工具链版本锁定

- 每个项目声明使用的 Python/Node 版本
- 使用 pyenv/nvm 或项目级配置固定版本
- Agent 环境变量中注入正确的工具链路径

---

## 十五、测试与验收

### 15.1 测试金字塔

```
        ▲
       / \
      /E2E\          Puppeteer / Playwright
     /─────\
    /Integration\    API test / DB state check
   /─────────────\
  /   Unit Test    \  pytest / jest
 /───────────────────\
/ Static Analysis + Lint \
/  + Smoke Test (v2新增)  \
```

### 15.2 验收标准类型

```json
{
  "acceptance_criteria": [
    {
      "type": "test",
      "description": "用户注册接口单元测试",
      "payload": "pytest tests/test_auth.py::test_register -v"
    },
    {
      "type": "command",
      "description": "应用能成功启动",
      "payload": "python -c \"from app import create_app; create_app()\""
    },
    {
      "type": "assertion",
      "description": "注册接口返回 201",
      "payload": "response.status_code == 201"
    },
    {
      "type": "manual",
      "description": "UI 文案需人工确认",
      "payload": "检查注册页面中文文案"
    }
  ]
}
```

### 15.3 冒烟测试阶段（v2 新增）

在每个 feature 编码完成后、CodeWhale 审查前，立即运行 <30 秒的冒烟测试：
- 应用能否成功导入/启动
- 新增 API 能否响应
- 核心功能是否正常

冒烟测试通过才进入审查，避免审查一个根本跑不起来的代码。

### 15.4 测试质量门禁（v2 新增）

- 覆盖率要求：新增代码至少 70% 行覆盖、50% 分支覆盖
- 反 tautology 检测：检查 `assert True` 等无效断言
- 独立测试生成（高级）：让一个 Agent 写代码，另一个独立写测试
- Mutation Testing（可选，复杂项目启用）

### 15.5 验收不是 LLM 判断

- Phase 5 的验收由 `pipeline.py` 执行 acceptance_criteria 列表
- 只有所有 criteria 通过，才允许 Hermes 输出"验收通过"
- Hermes 的角色是"解释验收结果"，不是"决定是否通过"

---

## 十六、Prompt 注入防御（v2 新增）

### 16.1 攻击向量

1. 用户在 PRD 或 feature 描述中嵌入 prompt injection
2. Agent 在读取外部文件（如第三方 API 文档）时遭遇注入
3. 审查 Agent 在审查恶意代码时被代码中的注释注入

### 16.2 防御措施

- 所有外部输入经过**输入净化层**（去除 prompt injection 模式）
- Agent 的系统提示使用**分隔符隔离**（将用户输入与系统指令严格分离）
- 定期审计 Agent 输出是否偏离预期行为
- Agent 间通信强制走 pipeline.py，禁止直接进程通信

---

## 十七、可观测性（v2 升级）

### 17.1 可观测性栈

| 层级 | 数据 | 存储 | 展示 |
|------|------|------|------|
| 执行追踪 | Agent 调用输入/输出/工具调用 | SQLite traces 表 | Markdown 报告 / rich status |
| 成本追踪 | tokens、cost、per feature、cache_hit | SQLite traces 表 | `pipeline.py budget` |
| 质量评估 | 审查结果、测试通过率、覆盖率 | SQLite + features.json | Markdown 报告 |
| 审计日志 | 执行的命令、是否允许 | SQLite audit_logs 表 | `pipeline.py audit` |
| 错误追踪 | 失败原因、Agent、feature | SQLite checkpoints 表 | `pipeline.py report` |
| 模型健康度 | 响应时间、错误率、降级事件 | SQLite model_health 表 | `pipeline.py model-health` |
| 实时告警 | 异常检测、阈值突破 | 内存 + 日志 | 终端通知 / 系统通知 |

### 17.2 实时告警系统（v2 新增）

```python
class AlertManager:
    """实时异常检测与告警"""

    # 滑动窗口统计
    token_rate_window: int = 10       # 最近 10 次调用的 token 消耗速度
    error_rate_window: int = 20       # 最近 20 次调用的错误率
    latency_window: int = 20          # 最近 20 次调用的响应时间

    # 告警阈值
    token_spike_multiplier: float = 3.0   # token 消耗速度突增 3 倍
    error_rate_threshold: float = 0.3     # 错误率超过 30%
    latency_threshold_ms: int = 60000     # 响应时间超过 60 秒

    # 告警通道
    channels = [
        "terminal_notification",    # 终端通知
        "system_notification",      # 系统通知（Windows Toast）
        # "webhook",                 # 可选的微信/钉钉 webhook
    ]
```

### 17.3 Rich Terminal UI / 实时仪表盘

`pipeline.py status` 使用 `rich` 或 `textual` 库输出实时仪表盘：

```
┌─────────────────────────────────────────────┐
│ 项目：myproject          Phase：develop     │
├─────────────────────────────────────────────┤
│ Features: 3/8 passed                        │
│ Budget: $12.50 / $50.00                     │
│ Active Agent: Claude (F002, 4min)          │
│ Cache Hit Rate: 62%                         │
│ Model Health: Kimi ✓  DS ✓  Qwen ✓        │
├─────────────────────────────────────────────┤
│ ID  │ Title          │ Owner  │ Status      │
├─────┼────────────────┼────────┼─────────────┤
│ F001│ 用户注册        │ claude │ passed ✓   │
│ F002│ 登录接口        │ claude │ in_progress│
│ F003│ 首页 UI        │ qwen   │ pending    │
└─────┴────────────────┴────────┴─────────────┘
```

---

## 十八、Orchestrator 可靠性（v2 新增）

### 18.1 Hermes 单点故障缓解

**问题：** Hermes 是唯一的 Orchestrator，如果在 Phase 2 的任务分解中产生幻觉，整个后续流程都建立在错误基础上。

**缓解措施：**

1. **决策验证层**（已在 Phase 1/2 中描述）：自动校验 + 交叉审查
2. **状态持久化**：所有 Hermes 决策写入 checkpoint，崩溃后可恢复
3. **降级策略**：Hermes 不可用 → 系统暂停，保存所有状态（black level 降级）

### 18.2 反幻觉级联

> "多智能体系统中，一个智能体的幻觉输出会作为输入传播给下游，错误逐级放大" — MetaGPT 论文

**防御措施：**
- Orchestrator 决策验证层（自动校验 + 交叉审查）
- Layer 1 确定性验证（代码级检查）
- 异构审查（不同模型发现不同问题）
- 反幻觉专项检查（API 幻觉、路径幻觉、范围幻觉等）

---

## 十九、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Agent 假完成 | feature 标记错误 | diff/commit/test/冒烟 四重验证 |
| Token 无限消耗 | 成本失控 | 三级预算 + 硬熔断 + 实时告警 |
| Agent 卡死 | 任务阻塞 | heartbeat + timeout + 熔断 |
| 代码质量下降 | 项目不可维护 | CodeWhale 审查 + lint + test + 覆盖率门禁 |
| 安全漏洞 | secrets 泄露、恶意代码 | 分层Profile沙箱 + 进程白名单 + 命令门控 + 审计 + Prompt注入防御 |
| 沙箱过度限制 | 正常任务被拦截 | 分层信任模型 + 5种Profile + 动态切换 + 用户开关 |
| 权限误升级 | 临时授权未及时回退 | 自动回退机制 + 审计日志 + 告警 |
| 合并冲突 | 代码丢失/语义错误 | worktree + 文件锁 + 人工升级 |
| 依赖冲突 | 构建失败 | 统一依赖入口 + lockfile 校验 |
| 记忆污染 | Agent 决策偏差 | 记忆分层 + 定期压缩 + 隔离 |
| 单点故障（Hermes） | 系统停摆 | checkpoint/resume + 状态持久化 + 降级策略 |
| 模型 API 限流/宕机 | 任务中断 | Circuit Breaker + 降级模型 + 模型健康度监控 |
| 上下文窗口溢出 | 长任务失败 | ContextManager + Reinforcement + Agentic Search |
| Orchestrator 幻觉 | 级联错误 | 决策验证层 + 交叉审查 + 反幻觉检查 |
| Prompt 注入 | Agent 行为偏离 | 输入净化 + 分隔符隔离 + 输出审计 |
| Prompt Cache 失效 | 成本翻倍 | 缓存分层 + 命中率监控 |
| Provider 配置错误 | 连接失败 | 三文件一致性校验 + `check-provider` 命令 |
| 数据泄露 | 通过合法端点 | 本地代理层过滤 + 网络白名单 + 内容审计（WFP驱动级不可用） |
| 沙箱绕过 | NTFS ACL 被符号链接绕过 | 符号链接创建权限限制 + 文件完整性校验 + 定期扫描 |
| 命令门控绕过 | 编码/分片/替代解释器绕过正则 | 白名单模式 + 绕过检测 + 默认拦截未知命令 |

---

## 二十、实施路线图（v2 优化版）

> **v1 路线图问题**：Phase A 4-6 周太长没有 Agent 编码、端到端 Demo 放在最后太晚、没有 Kill Switch。

### Week 1：最小端到端原型（MVP）+ 基线对照

- 单 Agent（Kimi K2.6 包月）+ 基础工具链
- 跑通 1 个真实小任务的 编码→测试→验证 流程
- 同时用 DeepSeek V4 Pro 跑同一任务，对比异构审查效果
- 选 3 个中等复杂度的真实任务，分别用单 Agent 和 2-Agent 执行
- 对比：完成质量、token 消耗、耗时、人工介入次数
- 记录 token 消耗、耗时、质量作为基线
- **Kill Switch**：只有当多 Agent 方案在至少 2/3 的任务上显著优于单 Agent 时，才继续推进多 Agent 架构

### Week 2-3：2-Agent 最小化 + Hermes 双模型路由

- 增加 CodeWhale (DS V4 Pro) 审查环节
- Hermes 接入 Kimi K2.6 (`kimi-coding` provider) + Qwen 3.7 Max (副模型)
- 实现最简版 pipeline.py（init/develop/check/advance）
- 对比 Week 1 基线，验证审查 + 双模型路由是否提升质量

### Week 4-5：硬基础设施

- Layer 0 沙箱（Windows 原生 NTFS ACL + 多用户隔离 + 进程白名单 + 安全增强层）
- Layer 2 SQLite 状态持久化
- Budget Enforcer（基于 Week 1-3 的实测数据设定）
- Provider 配置管理工具（三文件一致性校验）
- ContextManager 上下文管理器

### Week 6-8：完整流程

- Phase 0-6 全流程跑通（含 Hermes 双模型路由决策点）
- 人机审批流程（阻塞式/异步式/默认放行式三级）
- 可观测性仪表盘（实时 token/错误率/响应时间 + 告警）
- Prompt Cache 策略实施

### Week 9-10：4-Agent 扩展

- 增加 Qwen Code (Qwen3-Coder-Plus) 辅助 + E2E 测试
- 并行 worktree 管理（外部目录 + 自动清理）
- 复杂项目验证（≥10 features 的真实项目）

### Week 11-12：打磨与文档

- 性能优化（Prompt Cache + 模型健康度监控）
- 用户文档与部署指南
- 模型-Provider-环境变量映射表文档化
- Kill Switch 评估报告
- Phase 7 预留设计

---

## 二十一、附录 A：features.json 模板

```json
{
  "project": "myproject",
  "version": "1.0.0",
  "schema_version": 1,
  "features": [
    {
      "id": "F001",
      "title": "用户注册接口",
      "description": "实现用户注册 API，包含参数校验、密码加密、数据库写入",
      "status": "pending",
      "owner": "claude",
      "complexity": "medium",
      "dependencies": [],
      "wave": 1,
      "max_token_budget": 30000,
      "acceptance_criteria": [
        {
          "type": "test",
          "description": "注册成功返回 201",
          "payload": "pytest tests/test_auth.py::test_register_success -v"
        },
        {
          "type": "test",
          "description": "重复邮箱返回 409",
          "payload": "pytest tests/test_auth.py::test_register_duplicate_email -v"
        },
        {
          "type": "command",
          "description": "应用能正常导入",
          "payload": "python -c \"from app import create_app; create_app()\""
        }
      ]
    },
    {
      "id": "F002",
      "title": "登录接口",
      "description": "实现用户登录 API，返回 JWT token",
      "status": "pending",
      "owner": "claude",
      "complexity": "medium",
      "dependencies": ["F001"],
      "wave": 2,
      "max_token_budget": 30000,
      "acceptance_criteria": [
        {
          "type": "test",
          "description": "正确密码返回 token",
          "payload": "pytest tests/test_auth.py::test_login_success -v"
        }
      ]
    },
    {
      "id": "F003",
      "title": "部署交付",
      "description": "生成一键启动脚本、部署文档，验证应用可被非技术用户运行",
      "status": "pending",
      "owner": "claude",
      "complexity": "medium",
      "dependencies": ["F001", "F002"],
      "wave": 3,
      "max_token_budget": 20000,
      "acceptance_criteria": [
        {
          "type": "command",
          "description": "setup.ps1 能成功安装依赖",
          "payload": "powershell -ExecutionPolicy Bypass -File setup.ps1"
        },
        {
          "type": "command",
          "description": "应用能正常导入",
          "payload": "python -c \"from app import create_app; create_app()\""
        },
        {
          "type": "command",
          "description": "健康检查通过",
          "payload": "python -c \"import requests; r = requests.get('http://localhost:8000/health'); assert r.status_code == 200\""
        },
        {
          "type": "command",
          "description": "verify-runtime.ps1 执行通过",
          "payload": "powershell -ExecutionPolicy Bypass -File verify-runtime.ps1"
        },
        {
          "type": "manual",
          "description": "README.md 和 DEPLOY.md 面向小白可读",
          "payload": "人工确认文档无技术黑话"
        }
      ]
    }
  ]
}
```

---

## 二十二、附录 B：项目目录结构

```
C:\agent-workspace\<project>\
├── .git/                          # git repo
├── .hermes/                       # 项目级 hermes 配置（可选）
├── .logs/
│   ├── audit.log                  # 命令审计日志
│   ├── traces.jsonl               # Agent 调用 trace
│   └── reports/                   # 自动生成的 Markdown 报告
├── SOUL.md                        # Agent 角色定义（含 schema_version）
├── AGENTS.md                      # 协作规则（含 schema_version）
├── MEMORY.md                      # 项目记忆
├── progress.md                    # 进度日志
├── features.json                  # 验收清单
├── README.md                      # 项目简介 + 快速开始
├── DEPLOY.md                      # 部署与运行指南
├── .env.example                   # 环境变量模板
├── setup.ps1                      # 依赖安装脚本
├── start.ps1                      # 一键启动脚本
├── stop.ps1                       # 一键停止脚本（可选）
├── verify-runtime.ps1             # 运行验证脚本
├── specs/
│   ├── prd.md
│   ├── architecture.md
│   └── api-contracts.md
├── src/                           # 项目源码
├── tests/                         # 测试代码
└── pyproject.toml / package.json  # 项目配置

# Worktree 放在项目外部（v2 优化）
C:\agent-worktrees\<project>-f001\
C:\agent-worktrees\<project>-f002\
```

---

## 二十三、附录 C：关键配置文件

### C.1 `~/.hermes/config/pipeline.yaml`

```yaml
sandbox:
  default_profile: "assistant"        # 系统默认 Profile（非项目场景）
  project_default_profile: "pipeline" # 项目默认 Profile
  workspace_root: "C:\\agent-workspace"
  worktree_root: "C:\\agent-worktrees"
  users:
    hermes: "agent-hermes"
    coder: "agent-coder"
    reviewer: "agent-reviewer"
    helper: "agent-helper"
  hard_limits:
    never_allow:
      - "rm -rf /"
      - "format C:"
      - "reg delete HKLM"
      - "shutdown /s"
      - "curl.*| bash"
      - "net user /delete"
    always_audit: true
    auto_revert_temp_auth: true
    log_mode_changes: true
  profiles:
    lockdown:
      network_policy: "whitelist"
      content_inspection: true
      blocked_paths:
        - "C:\\Users\\*\\.ssh"
        - "C:\\Users\\*\\.aws"
        - "C:\\Users\\*\\.env"
        - "C:\\Windows\\System32"
      allowed_hosts:
        - "api.anthropic.com"
        - "api.kimi.com"
        - "api.deepseek.com"
        - "dashscope.aliyuncs.com"
      process_whitelist: ["python.exe","node.exe","git.exe","claude.exe","codewhale","qwen.exe"]
      agent_communication: "pipeline_only"
      default_tier: "T2"
      acl_isolation: true
      per_agent_user: true
    pipeline:
      network_policy: "whitelist"
      content_inspection: true
      blocked_paths:
        - "C:\\Users\\*\\.ssh"
        - "C:\\Users\\*\\.aws"
        - "C:\\Users\\*\\.env"
      allowed_hosts:
        - "api.anthropic.com"
        - "api.kimi.com"
        - "api.deepseek.com"
        - "dashscope.aliyuncs.com"
        - "registry.npmjs.org"
        - "pypi.org"
      process_whitelist: ["python.exe","node.exe","git.exe","claude.exe","codewhale","qwen.exe"]
      agent_communication: "pipeline_only"
      default_tier: "T2"
      acl_isolation: true
      per_agent_user: true
    assistant:
      network_policy: "open"
      content_inspection: false
      audit_log: true
      blocked_paths:
        - "C:\\Windows\\System32"
        - "C:\\Windows\\SysWOW64"
      process_whitelist: null  # 不限制
      agent_communication: "direct_allowed"
      default_tier: "T0"
      acl_isolation: false
      per_agent_user: false
    research:
      network_policy: "open"
      content_inspection: false
      audit_log: true
      blocked_paths:
        - "C:\\Users\\*\\.ssh"
        - "C:\\Users\\*\\.aws"
        - "C:\\Windows\\System32"
      process_whitelist:
        - "python.exe"
        - "node.exe"
        - "git.exe"
        - "chrome.exe"
        - "msedge.exe"
        - "playwright"
        - "curl.exe"
        - "wget.exe"
      agent_communication: "direct_allowed"
      default_tier: "T1"
      acl_isolation: false
      per_agent_user: false
    free:
      network_policy: "open"
      content_inspection: false
      audit_log: true
      blocked_paths:
        - "C:\\Windows\\System32"
      process_whitelist: null
      agent_communication: "direct_allowed"
      default_tier: "T1"
      acl_isolation: false
      per_agent_user: false
      l4_approval: "notify"  # 通知但不阻塞

budget:
  per_step_max_tokens: 12000       # v2.1 修正：复杂编码任务 8000 偏紧（实测建议 12,000-16,000）
  per_task_max_tokens: 100000     # v2.1 修正：5 文件 feature 50,000 不够（实测建议 100,000-150,000）
  per_project_max_usd: 50.0        # v2.1 修正：小型项目 $50，中型项目建议 $50-100 动态调整
  per_project_max_usd_daily: 10.0
  max_repair_rounds: 3
  max_agent_steps: 30
  dynamic_calibration: true        # v2: 基于实测数据动态调整
  # v2.1 新增：DeepSeek V4 Pro 促销价已于 2026-05-31 结束，当前按原价 $1.74/M 计算
  # 审查模型建议优先使用 DeepSeek V4 Flash（$0.14/M input）降低成本

agents:
  hermes:
    command: "hermes"
    primary_model: "kimi-for-coding"
    secondary_model: "qwen3.7-max"
    provider: "kimi-coding"
    default_max_turns: 30
  claude:
    command: "claude.exe"
    model: "kimi-for-coding"
    provider: "kimi-coding"
    default_max_turns: 30
  codewhale:
    command: "codewhale"
    model: "deepseek-v4-pro"
    provider: "deepseek"
    default_max_turns: 15
    auto: true
    # v2.1 新增：降级路径，当成本超预算或促销结束时切换
    fallback_model: "deepseek-v4-flash"  # $0.14/M input, $0.28/M output
    fallback_trigger: "budget_exceeded_or_promotion_ended"
  qwen:
    command: "qwen"
    model: "qwen3-coder-plus"
    provider: "alibaba"
    default_max_turns: 20

context:
  max_context_tokens: 100000
  safety_reserve_tokens: 5000
  task_reserve_tokens: 20000
  reinforcement_enabled: true
  agentic_search_enabled: true
  # v2.1 新增：双模型路由上下文损耗评估
  # Week 1 基线实验必须测量：同样输入在 Kimi 和 Qwen 上的 token 数差异、输出质量差异
  # 如果损耗 >20%，重新评估双模型路由收益
  dual_model_context_loss_threshold: 0.20

prompt_cache:
  enabled: true
  target_hit_rate: 0.5
  alert_threshold: 0.3
  # v2.1 新增：自建 L2-L4 缓存层设计（因 Qwen 3.7 Max 可能不支持服务端缓存）
  local_cache_backend: "sqlite"  # L2 应用层缓存：缓存重复请求结果
  vector_cache_backend: "none"   # L3 向量检索缓存：相似问题复用历史答案（可选 Redis）
  file_index_backend: "sqlite"   # L4 本地文件索引：避免重复读取文件树
  cache_layers:
    - "L1: 模型服务端缓存（Kimi/DeepSeek 自动提供，Qwen 不明确）"
    - "L2: 应用层缓存（本地 SQLite，缓存相同请求结果）"
    - "L3: 向量检索缓存（可选，相似问题复用历史答案）"
    - "L4: 本地文件索引（SQLite，文件 hash → 内容缓存）"

observability:
  db_path: "~/.hermes/pipelines/{project}.db"
  report_interval_minutes: 5
  rich_status: true
  realtime_dashboard: true
  alert_channels:
    - "terminal_notification"
    - "system_notification"

model_health:
  window_size: 20
  max_response_time_ms: 60000
  error_rate_threshold: 0.3
```

### C.2 Hermes 配置文件（`~/.hermes/config.yaml`）

```yaml
model:
  default: "kimi-for-coding"
  name: "kimi-for-coding"
  provider: "kimi-coding"  # ← 必须用内置名称
```

### C.3 Hermes 认证文件（`~/.hermes/auth.json`）

```json
{
  "active_provider": "kimi-coding",
  "credential_pool": {
    "kimi-coding": [{
      "source": "env:KIMI_API_KEY",
      "base_url": "https://api.kimi.com/coding/v1"
    }],
    "deepseek": [{
      "source": "env:DEEPSEEK_API_KEY",
      "base_url": "https://api.deepseek.com/v1"
    }]
  }
}
```

### C.4 `SOUL.md` 模板

```markdown
# Agent 角色定义
schema_version: 2

## Hermes
- 角色：Orchestrator + Judge
- 模型路由：Kimi K2.6 (日常) + Qwen 3.7 Max (决策)
- 职责：任务派发、phase 推进、最终验收、预算监控
- 禁止：直接写代码、首审代码、首测代码
- 决策原则：所有判断必须基于 Layer 1-3 的硬约束

## Claude Code
- 角色：主 Coder + Tech Lead
- 模型：Kimi K2.6 (包月)
- 职责：实现 feature、编写测试、重构代码
- 约束：一次只做一个 feature；必须产生非空 diff；必须跑 lint/test

## CodeWhale
- 角色：审查员 + Shell 专家
- 模型：DeepSeek V4 Pro (按量，异构审查)
- 职责：代码审查、安全问题、Shell/DevOps 任务
- 输出格式：P0/P1/P2 分级 + 文件 + 行号 + 修复建议
- 约束：只看 git diff，不看编码 Agent 的思考过程

## Qwen Code
- 角色：辅助 Coder + 浏览器测试
- 模型：Qwen3-Coder-Plus (百炼按量，原生适配)
- 职责：Claude 降级备用、E2E 测试、中文文档、简单任务
- 约束：简单任务（<20 行）可独立执行
```

### C.5 `AGENTS.md` 模板

```markdown
# 团队协作规则
schema_version: 2

## 模型-Provider-环境变量映射表
| Agent | 模型 | Provider | 环境变量 |
|-------|------|----------|---------|
| Hermes | kimi-for-coding | kimi-coding | KIMI_API_KEY |
| Hermes (副) | qwen3.7-max | alibaba | DASHSCOPE_API_KEY |
| Claude Code | kimi-for-coding | kimi-coding | KIMI_API_KEY |
| CodeWhale | deepseek-v4-pro | deepseek | DEEPSEEK_API_KEY |
| Qwen Code | qwen3-coder-plus | alibaba | DASHSCOPE_API_KEY |

## 通信协议
1. 所有状态变更必须通过 pipeline.py 写入 SQLite，不直接修改 features.json
2. Agent 间通过文件系统协作，文件写入需加锁
3. 每个 feature 一个 worktree（外部目录），禁止跨 worktree 直接修改
4. Agent 间禁止直接进程通信，所有交互必须通过编排层
5. 所有外部输入经过输入净化层

## 工作流
1. Hermes 派发任务 → Coding Agent 执行 → 冒烟测试 → CodeWhale 审查 → 确定性验证 → Hermes 验收
2. 默认 2-Agent，复杂工程才激活 Qwen
3. 任何 P0 问题必须修复后才能标记 passing
4. Golden Path：修改 <5 文件、<30 行、无依赖/API 变更 → 单 Agent + 自动验证

## 失败处理
1. P0 返修最多 3 轮，超过升级人工
2. Agent 卡死 60 秒后熔断
3. 超出预算立即暂停
4. 降级策略：green → yellow → orange → red → black

## 安全规则
1. Agent 按当前 Profile 约束运行（PIPELINE/LOCKDOWN 模式下使用各自沙箱用户）
2. 禁止读取 secrets、修改系统配置、访问非项目目录
3. git push / 安装依赖 / 数据库迁移需要人工确认
4. 进程白名单限制可执行文件
```

---

## 二十四、附录 D：最小可行验证清单（v2 扩展版）

系统上线前必须验证：

### 基础功能
- [ ] PIPELINE/LOCKDOWN 模式下，Agent 无法读取 `~/.ssh` 和 `.env` ⚠️ 需验证 Junction Point 绕过已修复
- [ ] ASSISTANT 模式下，Agent 可正常访问用户授权目录
- [ ] Profile 切换命令（mode/toggle/elevate）工作正常
- [ ] 临时授权到期后自动回退到原 Profile
- [ ] `pipeline.py init` 能创建完整项目骨架
- [ ] `pipeline.py check` 能正确拦截未满足条件
- [ ] `pipeline.py advance` 不能跳过未通过的 check
- [ ] Agent 编码后无 diff 会被标记为假完成
- [ ] lint/test 失败会 BLOCK feature passing
- [ ] CodeWhale 审查 P0 问题会触发返修
- [ ] P0 返修 3 次失败后升级人工
- [ ] token 超预算会熔断
- [ ] Agent 卡死 60 秒后自动终止并恢复
- [ ] `pipeline.py report` 能生成完整 Markdown 报告
- [ ] `pipeline.py budget` 能显示 per-feature 成本
- [ ] 合并冲突升级人工
- [ ] 崩溃后可 `resume` 继续
- [ ] `pipeline.py deploy` 能生成 `setup.ps1` / `start.ps1` / `DEPLOY.md`
- [ ] `verify-runtime.ps1` 能验证应用可运行
- [ ] 非技术用户按 `README.md` + `DEPLOY.md` 能启动应用

### 安全验证（v2.1 修正）
- [ ] 命令白名单模式工作正常（不在白名单中的命令被拦截）
- [ ] 绕过检测能识别 base64 编码、分片组合、替代解释器
- [ ] certutil/mshta/rundll32 等绕过手段被正确拦截
- [ ] PIPELINE/LOCKDOWN 模式下，Junction Point / 符号链接无法绕过目录限制（v2.1 新增）
- [ ] 文件完整性校验能检测符号链接和连接点攻击（v2.1 新增）
- [ ] 外部输入（PRD、API 文档）经过 prompt injection 净化
- [ ] 进程白名单（基于路径或 SRP）正确限制可执行文件
- [ ] 本地代理层网络过滤能阻止异常出站流量（替代 WFP 驱动级）
- [ ] 密钥由编排层代理，Agent 环境不直接注入 API key

### 信任模型验证（v2.1 修正）
- [ ] ASSISTANT 模式下，Agent 可正常访问网络、用户授权目录
- [ ] RESEARCH 模式下，Agent 可使用搜索引擎和浏览器
- [ ] RESEARCH 模式下，Orchestrator 可调度多 Agent 讨论
- [ ] FREE 模式下，Agent 可执行任何操作（仅审计不拦截）
- [ ] L3/L4 操作在 ASSISTANT 模式下正确触发用户确认
- [ ] L3/L4 操作在 PIPELINE 模式下正确触发审批流
- [ ] Profile 切换（mode/toggle/elevate）命令全部工作正常
- [ ] 临时授权到期后自动回退到原 Profile
- [ ] 硬性限制（rm -rf、format 等）在所有 Profile 下都无法执行
- [ ] 模式切换事件正确记录到审计日志
- [ ] 多用户隔离（如已启用）每个 Agent 用户只能访问自己的项目目录（v2.1 新增，Week 4-5）

### 上下文验证（v2 新增）
- [ ] 上下文压缩后安全指令仍然有效（模拟 100+ 轮对话后检查）
- [ ] MEMORY.md 超过 1000 行时系统仍能正常运行
- [ ] 跨 feature 的上下文不会相互污染
- [ ] Reinforcement 模式在工具调用返回后正确注入任务提醒
- [ ] 双模型路由上下文损耗 <20%（v2.1 新增，Week 1 基线实验）

### 降级验证（v2 新增）
- [ ] 主 Coder 不可用时系统自动降级到辅助 Coder
- [ ] 审查 Agent 不可用时系统以增强测试模式继续运行
- [ ] Orchestrator 崩溃后所有 feature 状态不丢失
- [ ] 模型 API 不可用时降级路径可正常工作
- [ ] DeepSeek V4 Pro 超预算时自动降级到 V4 Flash（v2.1 新增）

### 成本验证（v2 新增）
- [ ] 单 feature 实际 token 消耗在预算范围内（基于 3 个实测任务）
- [ ] Prompt Cache 命中率 > 50%（L1 服务端）
- [ ] 自建 L2-L4 缓存层工作正常（v2.1 新增）
- [ ] 完整项目的总成本不超过预算的 80%（留 20% 余量）
- [ ] DeepSeek 促销结束后审查成本仍在可控范围（v2.1 新增）

### 模型验证（v2 新增）
- [ ] 每个 Agent 使用的模型版本已锁定并记录
- [ ] 模型输出格式良好率 > 95%
- [ ] `pipeline.py check-provider` 校验三文件一致性通过
- [ ] Hermes 双模型路由在决策点正确切换到 Qwen 3.7 Max
- [ ] 审查模型降级路径（V4 Pro → V4 Flash）工作正常（v2.1 新增）

---

## 二十五、结论

本方案在 v1 架构基础上，通过深度调研（学术论文、开源框架实战、国产模型基准、一线工程经验、Hermes 源码分析）和多轮讨论，完成了以下关键升级：

1. **模型选型定稿**：Hermes 双模型路由（Kimi 日常 + Qwen 决策）+ Claude Code（Kimi 包月编码）+ CodeWhale（DS V4 Pro 异构审查）+ Qwen Code（Qwen3-Coder-Plus 原生 E2E）。月预算 ¥369-429
2. **沙箱分层信任模型**：5种Profile（LOCKDOWN/PIPELINE/ASSISTANT/RESEARCH/FREE）× 4级交互等级（T0-T3）× 5级风险等级（L0-L4），动态切换 + 用户开关 + 硬性底线不可关闭。解决了一刀切沙箱导致独立任务和调研协作被拦截的问题
3. **上下文窗口管理**：分层注入 + Reinforcement + Agentic Search + 安全指令永不压缩
4. **Prompt Cache 策略**：四层缓存 + 命中率监控，目标降低成本 50%+
5. **Agent Adapter 容错架构**：放弃统一接口幻想，采用适配器 + 解析器 + 容错三层架构
6. **人机审批分级**：阻塞式/异步式/默认放行式 + 超时机制
7. **降级策略**：green → yellow → orange → red → black 五级降级
8. **实时告警**：滑动窗口统计 + 异常检测 + 多通道告警
9. **反幻觉工程**：API/路径/逻辑/类型/范围五类幻觉的专项检查
10. **Prompt 注入防御**：输入净化 + 分隔符隔离 + 输出审计
11. **Provider 配置规范**：三文件一致性 + `check-provider` 校验命令
12. **先验证再扩展**：Week 1 基线对照实验 + Kill Switch

**v2.1 关键修正（基于对抗性审查）：**

1. **沙箱层诚实化**：承认 Windows 11 Home 不支持 AppLocker、WFP 驱动级审计复杂、NTFS ACL 可被 Junction Point 绕过。改为"最小可行沙箱"：NTFS ACL + 命令白名单 + 本地代理层过滤 + 人工审批。多用户隔离延迟到 Week 4-5 评估。
2. **命令门控白名单化**：正则黑名单 → 白名单 + 绕过检测（base64/分片/替代解释器）。经红队验证，黑名单绕过成功率 70-85%。
3. **预算数字上调**：per_step 8,000→12,000、per_task 50,000→100,000、月预算 ¥369-429→¥500-700（预留 30% 缓冲）。
4. **审查模型降级路径**：DeepSeek V4 Pro 促销价已结束（$0.435→$1.74/M），增加 V4 Flash 降级路径（$0.14/M）。
5. **Prompt Cache 实现层**：增加 L2-L4 自建缓存设计（SQLite 应用缓存 + 文件索引），因 Qwen 3.7 Max 可能不支持服务端缓存。
6. **双模型路由损耗评估**：Week 1 基线实验必须测量 Kimi↔Qwen 上下文传递损耗，>20% 则重新评估收益。
7. **安全验证清单扩展**：增加 Junction Point 绕过检测、命令绕过检测、密钥代理化验证、降级路径验证。

**下一步**：按 Week 1 MVP 计划启动，先用单 Agent 跑通基线，再用 2-Agent 验证多 Agent 的必要性。**Week 1 基线实验必须包含**：(a) 沙箱绕过测试（Junction Point、符号链接）(b) 审查质量评估（人工标注审查发现）(c) 上下文损耗测量（Kimi↔Qwen token 数差异）。实测数据驱动后续所有预算和策略参数的校准。
