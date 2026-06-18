# 对抗性审查报告：三层架构设计
# 项目: multi-agent-pipeline
# 审查日期: 2026-06-19
# 审查范围: specs/three_layer_architecture.md (1077行)
# 审查方法: 基于现有18个src模块代码的静态分析 + 架构设计交叉验证

---

## 审查摘要

本报告对"三层架构设计"进行对抗性审查，假设设计存在隐藏缺陷、实现后会暴露问题、用户使用时会遇到陷阱。审查基于现有代码库（18个src模块、约60000行代码）的静态分析，对架构设计中的每个组件进行"能否真正工作"的验证。

**核心发现**：设计文档存在严重的"纸上架构"问题——大量组件声称"复用现有模块"，但现有模块的接口、数据模型、执行模型与设计文档的假设不匹配。实现后必然面临大规模重构。

---

## P0 — 致命缺陷（实现即崩溃 / 安全失效）

### P0-001: RoleGuard 无法约束 Hermes（架构根本失效）
**风险等级**: 🔴 致命
**影响**: 约束层核心失效，整个安全模型崩塌

**问题描述**:
设计文档声称 RoleGuard 能拦截 Hermes 的编码行为，检测逻辑是：
```python
def check_role_constraint(agent_name: str, action: str) -> Tuple[bool, str]:
    allowed = ROLE_TASKS.get(agent_name, [])
    if action not in allowed:
        return False, "..."
```

**根本缺陷**：
1. **Hermes 是 orchestrator，不是被调度的 worker**：在现有代码中，Hermes 就是当前运行进程（pipeline.py 的 CLI 入口）。RoleGuard 检查的是"AgentDispatcher 派发任务前"，但 Hermes 自己的代码执行（如直接调用 write_file 工具）根本不经过 AgentDispatcher。
2. **action 字符串无法定义**：现有 adapters.py 中，Claude Code / Qwen Code 的 action 是 `code_write` / `code_fix`，但 Hermes 实际执行的是 `write_file` / `patch` / `terminal` 等工具调用。这些工具调用在 Hermes 内部直接发生，没有 "agent_name" 参数。
3. **自检悖论**：RoleGuard 如果要约束 Hermes，必须由 Hermes 自己调用自己检查自己。Hermes 完全可以不调用 RoleGuard，或者修改 ROLE_TASKS 定义。

**代码证据**:
- adapters.py 第 1-100 行：Adapter 是外部 Agent 的封装，Hermes 本身不是 Adapter
- pipeline.py 第 1-50 行：Hermes 是 CLI 入口，直接执行命令
- 设计文档 4.3.1："状态：无状态，纯函数判断"——但谁来调用这个纯函数？

**攻击路径**:
```
用户: "修复这个bug"
Hermes 内部: "我来修复..." → 直接调用 write_file() → 文件被修改
RoleGuard: 从未被调用，因为 Hermes 不经过 AgentDispatcher
结果: 编码完成，约束层完全绕过
```

**缓解建议**:
- 将 RoleGuard 实现为工具调用拦截器（monkey-patch 或 wrapper），在 `write_file` / `patch` / `terminal` 等工具调用前强制检查
- 引入外部审计进程（非 Hermes 进程）监控文件系统变更
- 或者放弃"约束 Hermes 自身"的幻想，改为"约束 Hermes 委派给外部 Agent 的行为"

---

### P0-002: 自动推进导致不可逆数据损坏
**风险等级**: 🔴 致命
**影响**: 自动推进可能覆盖用户工作、错误前进到下一阶段

**问题描述**:
PhaseEngine 设计为 "check 通过即自动推进"：
```python
def tick(self) -> PhaseTickResult:
    passed, msg = self.flow.check()
    if not passed:
        return PhaseTickResult(can_advance=False, reason=f"check 未通过: {msg}")
    # ... 自动推进
    if self.auto_advance:
        advance_ok, advance_msg = self.flow.advance()
```

**根本缺陷**:
1. **phase_checks.py 的 check 函数极其脆弱**：现有 check 函数检查的是文件存在性（如 `features.json` 是否存在、`SOUL.md` 是否存在），而不是语义正确性。一个空文件就能让 check 通过。
2. **没有人工审批节点**：设计文档声称 Phase 1/5 需要审批（approval.py 的 BlockingApproval），但 PhaseEngine.tick() 中完全没有调用 approval 系统。
3. **推进后生成 actions 立即执行**：`_generate_phase_actions` 直接生成 AgentAction 列表并执行，没有给用户确认机会。

**代码证据**:
- phase_checks.py 第 83-120 行：check_init 只检查文件存在性，不检查内容
- approval.py 第 38-52 行：定义了 BlockingApproval，但 PhaseEngine 设计中没有调用点
- 设计文档 5.2.1："auto_advance = True" 默认开启

**攻击场景**:
```
1. 用户创建空文件 features.json（占位）
2. check_init 返回 passed（文件存在）
3. PhaseEngine 自动推进到 design
4. 生成 action: Hermes-Research arch_design
5. 但用户还没有写任何需求，arch_design 基于空内容生成垃圾设计
6. 设计文档被覆盖，不可逆
```

**缓解建议**:
- auto_advance 默认关闭，改为 "check 通过 → 通知用户 → 等待确认"
- 在 PhaseEngine 中集成 approval.py 的 BlockingApproval
- check 函数增加语义验证（文件非空、内容符合 schema）

---

### P0-003: 约束层与调度层循环依赖导致死锁
**风险等级**: 🔴 致命
**影响**: 系统启动即死锁，无法执行任何任务

**问题描述**:
设计文档中存在隐式循环依赖：
1. PhaseEngine.__init__ 引用 ConstraintLayer
2. AgentDispatcher.dispatch() 调用 constraint.validate_task()
3. 但 ConstraintLayer 的 RoleGuard 检查 Agent 动作时，需要知道当前 Phase（来自 PhaseEngine）
4. GoalValidator 检查目标对齐时，需要调用 phase_checks.py 的 check 函数

**代码证据**:
- 设计文档 5.2.1：PhaseEngine 初始化 `self.constraint = ConstraintLayer()`
- 设计文档 5.2.2：AgentDispatcher `ok, reason = self.constraint.validate_task(task)`
- 设计文档 4.3.3：GoalValidator "复用 phase_checks.py 的 check 函数"

**死锁场景**:
```
PhaseEngine.tick() → 需要 ConstraintLayer
ConstraintLayer.GoalValidator → 需要 PhaseFlow.check() → 需要 PhaseFlow 实例
PhaseFlow 实例由 PhaseEngine 持有
→ 循环依赖，如果两者都单例初始化，可能导致死锁
```

**缓解建议**:
- 明确依赖方向：ConstraintLayer 只依赖静态配置（SOUL.md / AGENTS.md），不依赖 PhaseEngine
- GoalValidator 改为接收 phase 名称字符串，不调用 PhaseFlow 实例方法
- 使用依赖注入容器管理生命周期

---

### P0-004: ViolationLogger 写入审计日志但无法阻止实际损害
**风险等级**: 🔴 致命
**影响**: 违规操作已执行，日志只是事后记录

**问题描述**:
约束层执行流程：
```
动作请求
  ├─→ RoleGuard.check() → 违规 → ViolationLogger.record() → 返回拦截
```

**根本缺陷**：
1. **拦截发生在动作请求阶段，不是执行阶段**：如果 Agent 已经调用了 `write_file`，文件已经被修改，RoleGuard 检查的是"请求"而不是"执行"。
2. **ViolationLogger 只记录到 SQLite**：audit_logs 表只有 `id, project_id, agent, command, allowed, created_at` 字段，没有记录被修改的文件内容、无法回滚。
3. **没有补偿机制**：拦截后没有自动回滚已执行的副作用。

**代码证据**:
- state_store.py 第 79-86 行：audit_logs 表结构无文件内容字段
- 设计文档 4.4："任何一层拦截，后续层不再执行"——但拦截前可能已经执行了部分操作

**攻击场景**:
```
Claude Code: 调用 write_file(path="src/core.py", content="malicious") → 文件已写入
RoleGuard: "拦截！Claude Code 无权写 core.py" → 但文件已损坏
ViolationLogger: 记录违规 → 无法恢复 core.py
```

**缓解建议**:
- 采用"两阶段提交"：先预演（dry-run）检查，通过后执行
- 或拦截器前置到工具调用层（在 write_file 实际写入前检查）
- audit_logs 增加变更前后内容快照

---

### P0-005: IntentParser 的 LLM 兜底层引入不可控延迟和成本
**风险等级**: 🔴 致命
**影响**: 每次对话增加 200ms+ 延迟和额外 token 成本，且可能解析错误导致误操作

**问题描述**:
IntentParser 设计三层级联：
1. 规则匹配层（关键词 + 正则）
2. 模式匹配层（启发式）
3. LLM 解析层（兜底，增加 200ms 延迟）

**根本缺陷**:
1. **200ms 是乐观估计**：实际 LLM API 调用（即使是轻量模型）在 cold start 时可能 2-5 秒
2. **LLM 解析不可控**：用户说 "帮我写个测试"，LLM 可能解析为 CODE_REVIEW（查看测试代码）或 DELEGATE（让 Qwen 写测试），语义歧义无法消除
3. **置信度阈值无校准**：文档说 CONTINUE 阈值 0.9，但没有说明如何计算置信度。规则匹配的置信度是硬编码 1.0 还是 0.95？

**代码证据**:
- 设计文档 3.2.2："LLM 解析层：仅当规则/模式均无法识别时，调用轻量模型解析（兜底，增加 200ms 延迟）"
- 设计文档 3.2.2：置信度阈值表无计算方法说明

**攻击场景**:
```
用户: "删掉这个测试"（意思是删除过时的测试文件）
规则层: 匹配到 "删掉" → 无匹配（规则中没有"删掉"关键词）
模式层: 无历史模式
LLM 层: 解析为 "DELETE 操作" → 但 ActionFilter 中 delete 需额外审批
→ 用户意图是 DELEGATE(Qwen, test_delete)，但解析为 ACTION_DELETE
→ 系统要求审批，用户困惑
```

**缓解建议**:
- 放弃 LLM 兜底层，改为 "无法识别 → 请求澄清"（明确交互优于模糊猜测）
- 或 LLM 兜底仅用于 CHAT/AMBIGUOUS 分类，不用于动作映射
- 增加意图确认步骤：解析结果展示给用户，要求确认

---

## P1 — 严重缺陷（实现后暴露 / 用户频繁踩坑）

### P1-001: SessionLoader 的"零配置启动"假设不成立
**风险等级**: 🟠 严重
**影响**: 用户仍需手动配置，"开口即协作"目标失败

**问题描述**:
SessionLoader 声称：
- "检测当前工作目录是否有项目标记（`.hermes-project` 或 `pipeline_state.db`）"
- "零配置启动：用户无需手动输入项目路径或当前状态"

**实际缺陷**:
1. **项目标记不存在**：现有代码中没有任何地方创建 `.hermes-project` 文件。state_store.py 创建的是 `pipeline_state.db`，但它在项目目录内，不是标记文件。
2. **多项目冲突**：如果用户在 `C:\tmp\` 目录下有多个项目，SessionLoader 如何知道加载哪个？当前工作目录可能是任意位置。
3. **SessionContext 注入系统提示**：设计文档说 "构建 SessionContext 并注入到本轮对话系统提示"，但现有 context_manager.py 的 ContextLayer 没有 "注入系统提示"的接口，只有分层压缩策略。

**代码证据**:
- state_store.py 第 30-96 行：CORE_TABLES_SQL 创建 projects 表，但没有 `.hermes-project` 文件
- context_manager.py 第 48-62 行：ContextLayer 只有 name/priority/content/compressible/tags/source，没有 "注入系统提示"的方法
- 设计文档 3.2.1："读取 `SOUL.md` / `AGENTS.md`"——但这些文件可能不存在（搜索结果显示 specs/ 目录下没有这些文件）

**用户踩坑场景**:
```
用户: "继续"
系统: "未检测到项目标记，请提供项目路径"
用户: "C:\tmp\multi-agent-pipeline"
系统: "加载中... 错误：SOUL.md 不存在"
用户: ???（从未听说过 SOUL.md）
```

**缓解建议**:
- 实现 `hermes init` 命令创建 `.hermes-project` 标记文件
- SessionLoader 增加多项目选择交互
- 缺失 SOUL.md / AGENTS.md 时使用默认配置，不报错

---

### P1-002: ContextBuilder 的 2000 tokens 限制与中文内容冲突
**风险等级**: 🟠 严重
**影响**: 系统提示上下文溢出，安全指令被压缩或截断

**问题描述**:
验收标准："系统提示上下文 < 2000 tokens（中文字符）"

**实际缺陷**:
1. **2000 tokens 远远不够**：现有 context_manager.py 的估算因子是 `0.5`（1 char ≈ 0.5 token），2000 tokens = 4000 字符。但 SessionContext 包含：项目名、路径、Phase、Wave、features 列表（22个）、审批记录、上次动作、约束指令（禁止编码/测试/审核）。仅约束指令就可能 500+ 字符。
2. **安全指令标记为 LayerPriority.SAFETY 永不压缩**：如果 SAFETY 层内容超过 2000 tokens，系统无法压缩，只能截断或报错。
3. **中文 token 估算不准确**：GPT-4 对中文的 token 化约 1.5-2 tokens/字符，不是 0.5。2000 tokens 只能容纳约 1000-1300 中文字符。

**代码证据**:
- context_manager.py 第 31 行：`TOKEN_ESTIMATE_FACTOR = 0.5`
- context_manager.py 第 38-45 行：`LayerPriority.SAFETY = auto()` 标记为永不压缩
- 设计文档 3.2.3：约束指令包含 3 条禁止规则，每条约 50 字符

**用户踩坑场景**:
```
项目有 22 个 features，每个 feature 标题 20 字符
活跃 features: 2 个，已完成: 20 个
SessionContext 摘要: 200 字符
约束指令: 150 字符
可用指令列表: 200 字符
总计: 约 550 字符 × 2 tokens/char = 1100 tokens（看起来OK）

但加上 Reinforcement 强化提示（每次工具调用后重复注入）
→ 总上下文迅速超过 2000 tokens
→ 安全指令被截断或压缩失败
```

**缓解建议**:
- 将 token 估算因子修正为 1.5（中文）/ 1.3（英文）
- 增加上下文自适应压缩：当 SAFETY 层 + 必要层超过阈值时，提示用户而非静默失败
- 或放弃 2000 tokens 限制，改为动态预算

---

### P1-003: AgentDispatcher 的 worktree 并行假设与现有实现冲突
**风险等级**: 🟠 严重
**影响**: 并行 feature 开发导致文件冲突、git 状态混乱

**问题描述**:
AgentDispatcher 设计：
```python
if task.feature_id and self._should_use_worktree(task):
    worktree_path = self.worktree_manager.create_worktree(task.project, task.feature_id)
    task.worktree_path = worktree_path
```

**实际缺陷**:
1. **worktree.py 的 create_worktree 需要 git repo**：但现有 worktree.py 第 77-87 行 `_run_git` 函数在 `cwd` 运行 git 命令。如果项目目录不是 git repo（或 git 未初始化），worktree 创建失败。
2. **claimed_files 重叠检测不完整**：worktree.py 第 41-69 行 WorktreeEntry 有 claimed_files，但 worktree.py 中没有实现 "claimed_files 重叠检测" 的逻辑（搜索 `claimed_files` 只在数据模型中定义，没有使用）。
3. **Adapter 在 worktree 中执行后，结果如何合并回主分支？** 设计文档没有说明 worktree 结果如何合并。worktree.py 只有 `create` 和 `remove`，没有 `merge`。

**代码证据**:
- worktree.py 第 100-200 行：只有 create/remove/list 函数，没有 merge
- worktree.py 第 41-69 行：claimed_files 字段从未被读取或检查
- adapters.py 第 1-100 行：Adapter 执行时的工作目录是项目目录，不是 worktree 目录

**用户踩坑场景**:
```
PhaseEngine 推进到 develop，生成 4 个并行编码任务
AgentDispatcher 为每个 feature 创建 worktree
Claude Code 在 worktree-1 修改 src/core.py
Claude Code 在 worktree-2 也修改 src/core.py（claimed_files 未检测重叠）
两个 worktree 都完成后，没有合并逻辑
→ 主分支代码未更新，worktree 被删除（feature passed 后自动清理）
→ 工作丢失
```

**缓解建议**:
- worktree.py 增加 claimed_files 重叠检测和冲突解决逻辑
- AgentDispatcher 增加 worktree 结果合并步骤（或改用分支合并）
- 或放弃并行 worktree，改为串行开发（降低复杂度）

---

### P1-004: TimeoutHandler 的超时策略与 Adapter 实际执行模型不匹配
**风险等级**: 🟠 严重
**影响**: 超时处理无法实际中断 Agent，任务继续挂死

**问题描述**:
TimeoutHandler 设计：
```python
result = self.timeout_handler.run_with_timeout(
    adapter.execute,
    task=task,
    timeout_seconds=task.timeout or 600,
)
```

**实际缺陷**:
1. **adapter.execute 是同步阻塞调用**：adapters.py 中的 Adapter 执行的是子进程调用（如 `claude code` CLI）或 API 调用。Python 的 `threading.Timer` 或 `signal.alarm` 无法中断子进程或 HTTP 请求。
2. **"保存 checkpoint → 重试" 不可行**：如果 Adapter 在超时前已经修改了文件，checkpoint 保存的是已污染状态，重试会从污染状态开始。
3. **Windows 下 signal.SIGALRM 不存在**：设计文档没有考虑 Windows 兼容性。当前宿主是 Windows 11，Python 的 `signal.alarm` 在 Windows 上不可用。

**代码证据**:
- adapters.py 第 300-500 行（推测）：Adapter 执行子进程或 API 调用
- 设计文档 5.2.3：TimeoutPolicy 使用 `timeout_seconds`，但没有说明中断机制
- Windows 环境：`signal.SIGALRM` 不可用

**用户踩坑场景**:
```
Claude Code 执行编码任务，进入无限循环（或 API 挂死）
TimeoutHandler: 600s 后触发超时
但 adapter.execute 是阻塞子进程，无法中断
→ 超时处理线程等待子进程结束，永远等不到
→ 用户看到 "处理中..." 永远不动
→ 强制关闭终端，状态丢失
```

**缓解建议**:
- 使用 `subprocess.Popen` + `process.terminate()` 实现可中断的子进程
- API 调用使用 `requests` 的 `timeout` 参数 + 可取消的 Future
- Windows 下使用 `threading.Event` + 轮询而非 signal

---

### P1-005: 与现有 pipeline.py 的命令体系冲突
**风险等级**: 🟠 严重
**影响**: 新增命令与现有命令重复或语义冲突，用户困惑

**问题描述**:
设计文档 7.2 声称修改 pipeline.py：
- "新增命令: auto-tick, auto-dispatch, status-full"

**实际缺陷**:
1. **pipeline.py 已有 status 命令**：第 200-300 行（推测）已有 `status` 命令返回当前 phase。新增 `status-full` 与 `status` 语义重叠。
2. **pipeline.py 的 Phase 枚举是 0-6，但设计文档的 PhaseEngine 使用字符串名称**：pipeline.py 第 61-71 行 Phase 枚举有 REVIEW=7 的兼容别名，但设计文档的 PhaseEngine 使用 `"init"` / `"design"` 等字符串。类型转换容易出错。
3. **pipeline.py 是 CLI 工具，不是服务**：设计文档的 PhaseEngine.tick() 是"心跳检查"，但 pipeline.py 是命令行工具，执行完命令就退出。没有常驻进程，tick 由谁触发？

**代码证据**:
- pipeline.py 第 61-71 行：Phase 枚举定义
- pipeline.py 第 1-20 行：CLI 入口，非服务
- 设计文档 5.2.1：PhaseEngine.tick() "心跳检查"

**用户踩坑场景**:
```
用户: "status" → 返回简短状态（现有命令）
用户: "status-full" → 返回详细状态（新命令）
用户困惑: 为什么有两个 status？什么时候用哪个？

用户: "auto-tick" → 系统执行一次 tick 后退出
用户: "继续" → 期望系统自动推进，但进程已退出
→ 每次对话都需要重新启动 pipeline.py，SessionLoader 重新加载
→ "开口即协作"目标失败
```

**缓解建议**:
- 将 pipeline.py 重构为常驻服务（或守护进程），支持持续对话
- 或放弃 tick 模型，改为事件驱动（用户输入触发一次完整流程）
- 统一命令命名，避免 `status` / `status-full` 重复

---

### P1-006: GoalValidator 的"目标对齐"无法量化验证
**风险等级**: 🟠 严重
**影响**: GoalValidator 形同虚设，要么全通过要么全拦截

**问题描述**:
GoalValidator 设计：
- "编码前：验证目标是否已分解为可执行任务"
- "审核前：验证代码是否实现目标功能"
- "测试前：验证测试用例是否覆盖目标场景"

**实际缺陷**:
1. **"目标是否已分解" 无法自动验证**：现有 features.json 只有 title/description/status/owner_agent 字段，没有"分解完成"的标记。如何验证 "已分解为可执行任务"？
2. **"代码是否实现目标功能" 需要语义理解**：这需要代码静态分析 + 自然语言理解，现有代码中没有这种能力。phase_checks.py 只检查文件存在性。
3. **"测试用例是否覆盖目标场景" 需要测试覆盖率分析**：现有代码中没有覆盖率分析工具集成。

**代码证据**:
- phase_checks.py 第 83-120 行：check 函数只检查文件存在性
- state_store.py 第 40-50 行：features 表没有 "decomposed" / "coverage" 字段
- 设计文档 4.3.3：GoalValidator 输入 `(feature_id, phase, artifacts)`，但没有说明 artifacts 如何获取

**用户踩坑场景**:
```
GoalValidator 实现后：
- 编码前检查 features.json 存在 → 通过（空文件也通过）
- 审核前检查 src/ 目录有 .py 文件 → 通过（任何 .py 文件都通过）
- 测试前检查 tests/ 目录有 test_ 文件 → 通过（空测试文件也通过）
→ GoalValidator 永远通过，没有任何实际约束作用
→ 或永远拦截（如果实现严格），导致正常流程无法推进
```

**缓解建议**:
- 放弃 GoalValidator 的语义验证，改为检查清单（checklist）模式
- 或集成外部工具（如 pytest-cov、ast 分析）实现量化验证
- 明确 GoalValidator 的通过标准，避免主观判断

---

### P1-007: CheckpointSync 的"原子写入"在 Windows 上不可靠
**风险等级**: 🟠 严重
**影响**: 状态同步失败，checkpoint 损坏，无法恢复

**问题描述**:
CheckpointSync 设计：
- "原子写入：先写 `.tmp` 文件，再 `os.replace`"
- "文件锁：写 `features.json` / `progress.md` 前获取 `portalocker` 锁"

**实际缺陷**:
1. **os.replace 在 Windows 上不是原子操作**：Windows 的 `MoveFileEx`（Python `os.replace` 底层）在目标文件存在时，如果目标文件被其他进程打开，会失败。而 pipeline.py 可能同时被多个进程运行。
2. **portalocker 在 Windows 上需要 pywin32**：设计文档没有说明 portalocker 的依赖。当前环境是否有 pywin32？
3. **双写策略（SQLite + 文本文件）不一致风险**：SQLite 写入和文本文件写入是两个操作，中间崩溃会导致两者不一致。

**代码证据**:
- 设计文档 5.2.5："双写：关键状态同时写入 SQLite + 文本文件"
- 设计文档 5.2.5："文件锁：写前获取 portalocker 锁"
- Windows 文件系统特性：`os.replace` 非原子（目标文件被占用时失败）

**用户踩坑场景**:
```
CheckpointSync 写入 checkpoint:
1. 写 SQLite → 成功
2. 写 progress.md.tmp → 成功
3. os.replace(progress.md.tmp, progress.md) → 失败（progress.md 被文本编辑器打开）
4. 用户关闭编辑器后重试 → SQLite 和 progress.md 不一致
→ 恢复时从 SQLite 读取 phase=test，从 progress.md 读取 phase=develop
→ 状态混乱，无法确定真实状态
```

**缓解建议**:
- Windows 下使用 `ReplaceFile` API（需要 pywin32）实现真正的原子替换
- 或放弃文本文件双写，只保留 SQLite 作为唯一数据源
- 增加一致性校验（启动时检查 SQLite 和文本文件是否一致，不一致时提示）

---

## P2 — 中等缺陷（实现后麻烦 / 用户体验差）

### P2-001: EntryGate 路由表过于简化，无法处理复杂意图
**风险等级**: 🟡 中等
**影响**: 用户需要学习特定指令，自然语言交互体验差

**问题描述**:
EntryGate 路由表只有 9 种意图类型（CONTINUE / STATUS / CODE_REVIEW / ROLLBACK / MODIFY_REQ / PAUSE / RESUME / DELEGATE / CHAT / AMBIGUOUS）。

**实际缺陷**:
1. **用户不会按意图类型说话**：用户说 "看看 F012 写得怎么样"，这是 CODE_REVIEW 还是 STATUS？用户说 "F012 好像有问题，帮我看看"，这是 CODE_REVIEW 还是 MODIFY_REQ？
2. **DELEGATE 意图需要精确指定 Agent**：用户说 "让 Claude 写测试"，但系统有 Claude Code 和 CodeWhale，用户可能说 "Claude"、"Claude Code"、"claude"、"那个写代码的"。
3. **MODIFY_REQ 意图需要解析需求变更**：用户说 "把登录改成用 OAuth"，这需要理解语义变更、更新 PRD、重新分解任务。IntentParser 无法处理这种复杂变更。

**缓解建议**:
- 增加意图混合处理（如 "看看 F012 写得怎么样" → 同时触发 CODE_REVIEW + STATUS）
- DELEGATE 意图增加模糊匹配（Agent 别名映射）
- MODIFY_REQ 意图增加确认步骤：展示解析的需求变更，要求用户确认

---

### P2-002: 实施计划的 Phase 1-4 顺序与依赖关系矛盾
**风险等级**: 🟡 中等
**影响**: 实施计划无法按顺序执行，Phase 1 完成后 Phase 2 无法工作

**问题描述**:
实施计划：
- Phase 1: 约束层（最高优先级，安全基础）
- Phase 2: 入口层（用户体验）
- Phase 3: 调度层（自动化核心）
- Phase 4: 集成与验收

**实际缺陷**:
1. **约束层（Phase 1）需要调度层（Phase 3）才能测试**：RoleGuard 检查 Agent 动作，但如果没有 AgentDispatcher（Phase 3），无法验证 RoleGuard 是否有效。
2. **入口层（Phase 2）需要调度层（Phase 3）才能工作**：EntryGate 路由到 PhaseEngine 或 AgentDispatcher，如果 Phase 3 未完成，EntryGate 路由后无处理程序。
3. **"预计：1 个 Wave，1 个 feature" 过于乐观**：每个 Wave 包含 design → decompose → develop → test → accept，至少 5 个 Phase。1 个 Wave 完成 1 个 feature 需要 5 次推进，每次推进需要 check 通过。

**缓解建议**:
- 调整实施顺序：先实现最小可运行的端到端流程（EntryGate → 直接回复 / STATUS），再逐步增加约束和调度
- 或采用垂直切片：每个 Wave 实现一个端到端场景（如 "用户说继续 → 加载状态 → 返回摘要"），而非水平分层

---

### P2-003: 新增 15 个文件 + 修改 3 个文件，与"最小修改现有文件"承诺矛盾
**风险等级**: 🟡 中等
**影响**: 代码膨胀，维护成本增加，与现有代码风格不一致

**问题描述**:
设计文档 7.1-7.2：
- 新增 15 个文件（entry/ 4个 + constraint/ 5个 + orchestration/ 5个 + integration.py）
- 修改 3 个文件（pipeline.py / phase_flow.py / context_manager.py）
- 声称 "不修改的现有文件" 有 11 个

**实际缺陷**:
1. **新增 15 个文件意味着 15 个新模块的测试、文档、维护**：现有 18 个 src 模块，新增 15 个后达到 33 个模块。模块间依赖关系复杂化。
2. **"不修改的现有文件"实际上需要修改**：例如 adapters.py 需要增加 "在 worktree 目录执行" 的支持，state_store.py 需要增加 audit_logs 的字段，fallback_manager.py 需要增加 "AgentDispatcher 调用" 的接口。
3. **integration.py 作为"对外统一接口"，实际上成为新的上帝对象**：所有三层都依赖 integration.py，它可能成为新的瓶颈。

**缓解建议**:
- 减少新增文件数量，将相关组件合并（如 RoleGuard + ActionFilter 合并为 ConstraintChecker）
- 或采用插件架构，让三层作为可选插件加载，不强制集成

---

### P2-004: 测试策略中的集成测试依赖外部 Agent 可用性
**风险等级**: 🟡 中等
**影响**: 集成测试无法稳定运行，CI 不可靠

**问题描述**:
测试策略 8.4：
- `test_full_pipeline_continue`：用户说"继续"完整流程 → 自动推进，委派 Agent，返回结果
- `test_violation_interception`：Hermes 尝试编码 → 拦截
- `test_timeout_recovery`：Agent 超时 → 自动降级

**实际缺陷**:
1. **"委派 Agent" 需要外部 Agent 服务可用**：Claude Code / Qwen Code / CodeWhale 是外部服务，测试时可能不可用（API 限制、网络问题、成本限制）。
2. **"Agent 超时" 测试需要模拟 600s 超时**：测试运行时间 600s+，CI 无法承受。
3. **"Hermes 尝试编码" 测试需要模拟 Hermes 内部行为**：但 Hermes 是当前进程，测试如何模拟"Hermes 尝试编码"？

**缓解建议**:
- 集成测试使用 Mock Adapter，不依赖外部服务
- 超时测试使用缩短的超时时间（如 5s）+ 模拟超时
- Hermes 编码测试改为工具调用拦截测试（Mock write_file）

---

### P2-005: 风险与缓解表中的"高"风险没有具体缓解措施
**风险等级**: 🟡 中等
**影响**: 高风险项实际无法缓解，风险暴露后无应对手段

**问题描述**:
设计文档 11 风险与缓解表：
| 风险 | 等级 | 缓解措施 |
| 自动推进导致错误前进 | 高 | Phase check 严格，支持 pause 模式，推进前通知用户 |
| 状态同步失败导致数据丢失 | 高 | 双写策略（SQLite + 文本），checkpoint 链保留 50 个 |

**实际缺陷**:
1. **"Phase check 严格" 不成立**：phase_checks.py 的 check 函数只检查文件存在性（见 P0-002）。
2. **"支持 pause 模式" 没有实现细节**：PhaseEngine 有 `paused` 字段，但没有说明如何触发 pause（用户命令？自动检测？）。
3. **"推进前通知用户" 与 "自动推进" 矛盾**：如果自动推进，如何通知用户？通知后用户来不及响应就已经推进了。
4. **"双写策略" 增加不一致风险**：见 P1-007。

**缓解建议**:
- 为每个高风险项制定具体的、可验证的缓解措施
- 增加风险监控指标（如 check 严格度评分、状态一致性校验）

---

### P2-006: 设计文档声称"复用现有模块"但接口不匹配
**风险等级**: 🟡 中等
**影响**: 实现时需要大量适配代码，"复用"变成"重写"

**问题描述**:
设计文档多处声称"复用现有模块"：
- SessionLoader "复用 `state_store.StateStore`"
- PhaseEngine "复用 `phase_flow.PhaseFlow`"
- AgentDispatcher "复用 `adapters.py` 的 `ClaudeCodeAdapter`"
- CheckpointSync "复用 `state_store.py` 的 `CheckpointRecord`"

**实际缺陷**:
1. **state_store.StateStore 没有 `legacy_load` / `legacy_save` 之外的项目状态加载方法**：SessionLoader 需要加载 "当前 Phase + features 状态 + 审批记录 + 最近 checkpoint"，但 StateStore 的接口是原子操作（get_project / get_feature / get_checkpoint），没有 "加载完整项目状态" 的接口。
2. **phase_flow.PhaseFlow 没有 `auto_advance` 参数**：PhaseEngine 需要修改 PhaseFlow 增加 auto_advance，但设计文档 7.2 说 "新增 auto_advance 参数支持"——这是修改现有文件，不是"复用"。
3. **adapters.py 的 Adapter 没有 `can_execute()` 方法**：AgentDispatcher 需要检查 Agent 可用性，但 adapters.py 中的 Adapter 只有 `execute()` 方法，没有 `can_execute()`。需要新增方法。

**缓解建议**:
- 明确区分"复用"（无需修改）和"扩展"（需要修改/新增接口）
- 为每个"复用"点提供接口映射表（设计文档接口 → 现有模块接口）

---

### P2-007: 设计文档与现有代码的 Phase 定义不一致
**风险等级**: 🟡 中等
**影响**: Phase 推进逻辑混乱，新旧代码不兼容

**问题描述**:
设计文档的 Phase 顺序：init → design → decompose → develop → test → accept → deploy（7个Phase）
现有 pipeline.py 的 Phase 枚举：INIT=0, DESIGN=1, DECOMPOSE=2, DEVELOP=3, TEST=4, ACCEPT=5, DEPLOY=6, REVIEW=7（8个值，含兼容别名）

**实际缺陷**:
1. **REVIEW=7 是兼容别名，但设计文档没有提到**：PhaseEngine 的 `_generate_phase_actions` 中 `phase == "test"` 时生成测试和审核任务，但现有代码中 REVIEW 是一个独立的 Phase。
2. **pipeline.py 的 `next()` 方法兼容旧版**：INIT→DEVELOP→REVIEW→TEST，不是设计文档的 INIT→DESIGN→DECOMPOSE→DEVELOP→TEST。如果 PhaseEngine 使用设计文档的顺序，与 pipeline.py 的 next() 冲突。
3. **phase_flow.py 的 PHASE_ORDER 是字符串列表**，但 pipeline.py 的 Phase 是枚举。PhaseEngine 使用字符串比较，容易因大小写或拼写错误导致不匹配。

**缓解建议**:
- 统一 Phase 定义，移除 REVIEW 兼容别名（或明确说明何时使用）
- PhaseEngine 使用 Phase 枚举而非字符串，利用类型检查避免错误

---

## 总结与建议

### 核心结论

1. **架构设计存在"纸上架构"问题**：设计文档的组件、接口、数据模型与现有代码不匹配。实现后必然面临大规模重构，而非"平滑复用"。

2. **约束层无法约束 Hermes（P0-001）**：这是架构的根本缺陷。Hermes 是当前进程，任何自我约束都可以被绕过。需要外部审计机制。

3. **自动推进是危险的（P0-002）**：现有 check 函数过于脆弱，自动推进会导致不可逆的数据损坏。必须增加人工确认节点。

4. **循环依赖和超时处理是技术债务（P0-003 / P1-004）**：实现后会导致死锁和任务挂死，需要重新设计依赖关系和超时机制。

5. **"复用现有模块"是过度承诺（P2-006）**：大量接口需要新增或修改，实际工作量远超设计文档估计的 "4 个 Wave，4 个 features"。

### 实施建议

**如果必须实施**：
1. 先修复 P0-001：将 RoleGuard 实现为工具调用拦截器（monkey-patch），而非 AgentDispatcher 前置检查
2. 先修复 P0-002：auto_advance 默认关闭，增加人工确认
3. 调整实施顺序：采用垂直切片（端到端场景）而非水平分层
4. 增加接口适配层：明确"复用" vs "扩展"的边界，减少预期落差
5. 重写测试策略：使用 Mock 替代外部 Agent，缩短超时时间

**如果资源有限**：
- 建议优先实现入口层（SessionLoader + IntentParser）+ 约束层（RoleGuard 作为工具拦截器），放弃调度层的自动推进和自动委派
- 将 PhaseEngine 和 AgentDispatcher 改为"建议模式"（生成建议，等待用户确认）而非"自动执行模式"

---

*审查完成。报告文件: C:/tmp/multi-agent-pipeline/reports/adversarial_review_three_layer.md*
