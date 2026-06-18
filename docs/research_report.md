# Phase 1 调研报告 — 多 Agent 协作构建方案

> 生成者: Hermes-Research (Qwen 3.7 Max)
> 日期: 2026-06-18
> 项目: multi-agent-pipeline
> 状态: 调研完成，等待 Phase 1 架构设计

---

## 1. 调研范围与目标

根据 PRD 第 9.2 节 Phase 1 (Design) 定义，本阶段目标为：
- 理解 PRD 需求（v2.3 完整版，2824 行）
- 技术选型分析（沙箱方案、模型选择、工具链）
- 风险识别（Windows 11 Home 限制、模型 API 稳定性）
- 为 Phase 1 架构设计提供决策依据

**调研约束**：本阶段不编写代码，仅输出调研报告。

---

## 2. PRD 需求理解

### 2.1 核心目标
构建以 Hermes 为 Orchestrator、Claude Code 为主 Coder、CodeWhale 为审查员、Qwen Code 为辅助/测试的多 Agent 协作系统。通过**硬基础设施 + 确定性流程约束 + LLM 智能决策**三层结合，解决单 Agent 在复杂工程级项目中的能力瓶颈。

### 2.2 关键设计原则（8 条）
1. **简单优先**：从单 Agent 开始，80% 任务用 2-Agent，20% 用 4-Agent
2. **硬约束优先于 Prompt 约束**：关键规则必须写成代码
3. **确定性验证优先于 LLM 判断**：完成的唯一标准是可执行检查通过
4. **可观测性是第一天工程**：trace、cost、quality 数据必须实时采集
5. **失败可恢复**：每个状态持久化，每个 action 可重试
6. **人机边界清晰**：审批分级（阻塞式/异步式/默认放行式）+ 超时机制
7. **异构审查**：编码和审查必须使用不同模型
8. **先验证再扩展**：必须通过单 Agent 基线对照实验验证多 Agent 必要性

### 2.3 五层约束模型

| 层级 | 名称 | 核心职责 |
|------|------|---------|
| Layer 4 | Agent 智能决策层 | Hermes / Claude / CodeWhale / Qwen |
| Layer 3 | 编排与流程约束层 | pipeline.py 状态机 / Budget / Circuit Breaker |
| Layer 2 | 状态持久化与检查点层 | SQLite State DB / Checkpoint / Resume |
| Layer 1 | 代码级确定性验证层 | Lint / Test / Build / Diff / Feature Assertion |
| Layer 0 | 沙箱与执行隔离层 | 5种Profile / 4级交互 / 5级风险 / 动态切换 |

### 2.4 协作流程 Phase 0-6

```
Phase 0: Initializer（项目初始化）
Phase 1: Design（架构设计）← 当前阶段
Phase 2: Decompose（任务分解）
Phase 3: Develop（增量开发循环）
Phase 4: Test（端到端测试）
Phase 5: Accept（独立验收）
Phase 6: Deploy / Deliver（部署与交付）
```

---

## 3. 技术选型分析

### 3.1 沙箱方案选型

PRD 第 3.1 节详细记录了沙箱方案的实测踩坑过程：

| 方案 | 状态 | 原因 |
|------|------|------|
| WSL2 | ❌ 废弃 | 长提示静默问题 |
| Docker Desktop | ❌ 废弃 | 占用 8-16GB 内存，性能疲劳 |
| Hyper-V 容器 | ❌ 废弃 | Windows 11 Home 不支持 |
| Windows Sandbox | ❌ 废弃 | 每次重启清空，不支持持久化 |
| AppLocker | ❌ 废弃 | Windows 11 Home 不支持 |
| WFP 驱动开发 | ⚠️ 延迟 | 用户层 API 可用，驱动级需额外开发 |
| NTFS ACL | ⚠️ 有绕过风险 | 可被 Junction Point / 符号链接绕过（已实测） |
| **最小可行沙箱** | ✅ **选定** | NTFS ACL + 命令白名单 + 本地代理层网络过滤 + 人工审批 |

**选型结论**：
- Week 1-3 实施阶段采用"最小可行沙箱"
- 核心组件：NTFS ACL（有绕过风险）+ 命令白名单 + 代理层网络过滤 + 人工审批
- 多用户隔离（每个 Agent 独立 Windows 用户）延迟到 Week 4-5 评估
- 安全声明从"防恶意"降级为"防误操作"

### 3.2 模型选型定稿

PRD 第 4 节基于 2026-06 基准数据完成模型选型：

**编程能力基准**：

| 模型 | SWE-bench Pro | SWE-bench Verified | LiveCodeBench | 特点 |
|------|-------------|-------------------|-------------|------|
| Qwen 3.7 Max | **60.6%** 🏆 | 80.4% | 91.6% | 工程实战全球第一 |
| Kimi K2.6 | 58.6% | 80.2% | 89.6% | 综合均衡，有包月 |
| DeepSeek V4 Pro | 55.4% | **80.6%** | **93.5%** 🏆 | 算法推理全球第一 |

**定价对比**：

| 模型 | 计费模式 | 输入价格 | 输出价格 | 包月选项 |
|------|---------|---------|---------|---------|
| Kimi K2.6 | 包月为主 | ¥6.50/M | ¥27.00/M | **¥49-699/月** |
| DeepSeek V4 Pro | 按量 | ¥3.00/M | ¥6.00/M | ❌ 无包月 |
| Qwen 3.7 Max | 按量 | ¥12.00/M | ¥36.00/M | ❌ 无包月 |
| Qwen3-Coder-Plus | 按量 | ¥2.00/M | ¥8.00/M | 百炼 ¥40/月起 |

**最终适配方案**：

| Agent | 模型 | 角色 | 成本策略 |
|-------|------|------|---------|
| Hermes (Orchestrator) | Kimi K2.6 (主) + Qwen 3.7 Max (副，决策点) | 统筹协调 | 包月 + 按量决策点 |
| Claude Code (主 Coder) | Kimi K2.6 | 编码实现 | 包月共用 |
| CodeWhale (审查员) | DeepSeek V4 Pro | 代码审查 | 按量，异构审查 |
| Qwen Code (辅助) | Qwen3-Coder-Plus | E2E测试/辅助 | 按量 |

**月预算估算**：¥369-429（理论值，以 Week 1 实测为准）
- Kimi 包月：¥199
- Qwen 3.7 Max 按量：~¥80-120
- DS V4 Pro 按量：~¥50
- Qwen3-Coder-Plus 按量：~¥40-60

### 3.3 工具链选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 状态数据库 | SQLite | 轻量、零配置、Python 原生支持 |
| 可观测性 | SQLite + Markdown 报告 | 终端友好，无需外部服务 |
| 实时仪表盘 | rich / textual | Python 生态，终端内实时展示 |
| 文件锁 | portalocker | Windows 兼容，跨平台 |
| 版本控制 | Git + worktree | 并行 feature 开发，原子性保障 |
| 测试框架 | pytest | Python 标准，与 lint/type 工具链集成 |
| 静态分析 | ruff / mypy | 快速、现代 Python 工具链 |
| 沙箱网络过滤 | 本地 HTTP 代理层 | Windows 11 Home 可行方案 |

---

## 4. 风险识别与评估

### 4.1 Windows 11 Home 限制（高风险）

| 限制项 | 影响 | 缓解措施 | 状态 |
|--------|------|---------|------|
| 不支持 Hyper-V / Docker Desktop | 无法使用容器级隔离 | 改用 Windows 原生沙箱 | 已接受 |
| 不支持 AppLocker | 无法做进程白名单 | 改用软件限制策略 (SRP) 或基于路径的进程限制 | 已规划 |
| NTFS ACL 可被 Junction Point 绕过 | 目录隔离有漏洞 | 配合符号链接创建权限限制 + 文件完整性校验 | 需验证 |
| WFP 驱动级审计复杂 | 无法做系统级网络内容审计 | 改用本地代理层过滤 | 已接受 |
| 全局快捷键不可行 (pynput) | 无法注册系统级热键切换 Profile | 改用 CLI 命令 + 终端内快捷键 | 已接受 |

**诚实声明**：Week 1-3 实际安全边界 = NTFS ACL（有绕过风险）+ 命令白名单 + 代理层网络过滤 + 人工审批。这不是企业级安全，是"防误操作"级别的安全。

### 4.2 模型 API 稳定性（中高风险）

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Qwen 3.7 Max API 不稳定 | 关键决策点（Phase 1/2/5）无法执行 | 降级到 Kimi K2.6；记录日志；人工介入 |
| DeepSeek V4 Pro 促销结束 | 审查成本上升（$0.435→$1.74/M） | 增加 V4 Flash 降级路径（$0.14/M） |
| Kimi K2.6 包月 429 限流 | 高频编码任务中断 | 降级到 Qwen Code；增加重试逻辑 |
| 模型输出格式不稳定 | Agent Adapter 解析失败 | 适配器 + 解析器 + 容错三层架构 |
| Provider 配置错误 | 连接失败 | 三文件一致性校验 + `check-provider` 命令 |

**已验证配置**：
- Qwen 3.7 Max：已配置并验证可用（DashScope，5358 tokens 输出完整五层约束模型）
- Kimi K2.6：当前会话正在使用（kimi-coding provider）
- 三文件一致性：`.env` + `config.yaml` + `auth.json` 已规范

### 4.3 架构级风险（中风险）

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 多 Agent 协调成本 > 收益 | 系统复杂度过高，不如单 Agent | Week 1 Kill Switch：2/3 任务显著优于单 Agent 才继续 |
| 上下文窗口溢出 | 长任务失败，安全指令丢失 | ContextManager + Reinforcement + Agentic Search |
| Orchestrator 幻觉级联 | 错误逐级放大 | 决策验证层 + 交叉审查 + 反幻觉检查 |
| Prompt Cache 命中率低 | 成本翻倍 | 四层缓存 + 命中率监控 + 自建 L2-L4 缓存 |
| Agent 假完成 | feature 标记错误但代码不可用 | diff/commit/test/冒烟 四重验证 |
| 合并冲突 / 语义冲突 | 代码丢失或错误 | worktree + 文件锁 + 回归测试 |

### 4.4 成本风险（中风险）

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Token 消耗超出预算 | 项目成本失控 | 三级预算预警（80% 告警 / 100% 软熔断 / 150% 硬熔断） |
| 双模型路由上下文损耗 | Kimi↔Qwen 切换导致信息丢失 | Week 1 基线实验测量损耗，>20% 则重新评估 |
| DeepSeek 审查成本超支 | 促销结束后按原价计费 | 降级到 V4 Flash；控制审查频率 |

---

## 5. 关键决策建议

### 5.1 立即决策（Phase 1 前必须确定）

1. **沙箱 Profile 默认策略**：系统默认 ASSISTANT，pipeline 启动时切换 PIPELINE
2. **双模型路由阈值**：上下文损耗 >20% 时放弃 Qwen 副模型，全程用 Kimi
3. **预算硬上限**：per_project_max_usd = $50（小型项目），per_task_max_tokens = 100,000
4. **审查降级触发**：DeepSeek V4 Pro 成本超 ¥80/月 或促销结束后自动降级到 V4 Flash

### 5.2 延迟决策（Week 1-3 基线实验后确定）

1. **多用户隔离**：Week 4-5 评估 CLI 工具链切换复杂度 vs 安全收益
2. **Prompt Cache 自建层**：L2-L4 缓存是否必要，取决于 Qwen 3.7 Max 服务端缓存支持情况
3. **4-Agent 全团队模式**：仅在 Week 1 Kill Switch 通过且 2-Agent 跑通后启用
4. **WFP 驱动级审计**：仅在高敏感项目（金融/医疗）时评估开发成本

### 5.3 需要人工审批的决策

1. **Phase 1 架构设计输出**：`specs/architecture.md` 需人类审批（阻塞式，30分钟超时）
2. **Week 1 Kill Switch 结果**：多 Agent 是否显著优于单 Agent，决定后续投入
3. **预算调整**：超出初始预算时是否增加上限或缩减范围

---

## 6. 调研结论

### 6.1 可行性评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 技术可行性 | ✅ 可行 | 最小可行沙箱 + 命令白名单 + 代理层过滤在 Windows 11 Home 上可行 |
| 模型可用性 | ✅ 已验证 | Kimi K2.6（当前在用）、Qwen 3.7 Max（已验证）、DeepSeek（API 可用） |
| 成本可控性 | ⚠️ 需实测 | 理论月预算 ¥369-429，实际以 Week 1 基线为准 |
| 安全性 | ⚠️ 有限 | 防误操作级别，非企业级安全。NTFS ACL 有绕过风险 |
| 复杂度 | ⚠️ 高 | 五层约束模型 + 4-Agent 协作 + 双模型路由，协调成本高 |

### 6.2 关键成功因素

1. **Week 1 Kill Switch 必须通过**：多 Agent 在 2/3 任务上显著优于单 Agent
2. **预算三级预警必须生效**：80% 告警 / 100% 软熔断 / 150% 硬熔断
3. **沙箱绕过测试必须通过**：Junction Point、符号链接、base64 编码等绕过手段被拦截
4. **上下文损耗必须 <20%**：Kimi↔Qwen 双模型路由的上下文传递损耗

### 6.3 下一步行动

1. **Phase 1 架构设计**：基于本调研报告，Hermes-Research 输出 `specs/architecture.md`
2. **Week 1 MVP 启动**：单 Agent（Kimi）跑通编码→测试→验证流程
3. **基线对照实验**：3 个中等复杂度任务，单 Agent vs 2-Agent 对比
4. **安全验证**：命令白名单、绕过检测、Junction Point 限制

---

## 7. 参考文档

- PRD: `specs/prd.md` (v2.3, 2824 行, 121KB)
- Agent 角色定义: `SOUL.md` (schema_version: 1)
- 协作规则: `AGENTS.md` (schema_version: 1)
- 实施路线图: PRD 第 20 节 (Week 1-12)
- 最小可行验证清单: PRD 附录 D

---

*报告完成。本调研严格遵循 PRD Phase 1 流程，未编写任何代码，仅输出调研分析。*
