# 对抗审查：fix-plan-v2.md 致命风险点

> 审查目标：`C:/tmp/multi-agent-pipeline/docs/fix-plan-v2.md`
>
> 审查方法：逐条核对方案主张与现有代码实现、schema、数据状态的一致性，找出“看起来合理、落地会爆炸”的地方。

---

## 对抗点 1：P1“统一注册表”是大爆炸式重构，与“最小改动”原则直接冲突

【对抗点】方案号称“最小改动、渐进式重构”，但 Phase 1 要求一次性把入口、约束、执行、检查、建议等 12+ 个核心文件全部改为依赖一个新的全局 `REGISTRY` 单例，本质上是一次高风险全量替换，不是渐进式修复。

【论据】
- 方案原文：
  > “修改范围：adapters.py / system_constraint.py / pipeline_executor.py / config.py / config_loader.py / models.py / workflow_registry.py / phase_checks.py / message_queue.py / agent_daemon.py / condition_engine.py / suggestion_engine.py”
- 代码证据：这些文件当前各自维护不兼容的注册表。例如 `adapters.py:1661` 的 `ADAPTER_REGISTRY` 使用键 `"claude" / "codewhale" / "qwen"`，而 `system_constraint.py:88-90` 的常量与 `TASK_ADAPTER_MAP` 使用 `"claude-code" / "codewhale" / "qwen-code"`；`models.py:62` 的 `PHASE_NAMES` 只有 7 个阶段，而 `workflow_registry.py:59` 的 `_GREENFIELD_PHASES` 有 12 个；`message_queue.py:36` 的 `VALID_TASK_TYPES` 缺少 `analyze` / `deploy`。
- 方案对“兼容性”只字不提：没有 import 顺序治理、没有旧枚举到新注册表的 shim、没有 schema 版本控制，所有模块将在同一次提交里同时切换真相源。

【风险】
- 任一模块的循环导入、类型注解、运行时动态加载会在切换瞬间触发 `ImportError` 或 `KeyError`，导致整个入口层无法启动。
- 旧 wave 的验收测试（如 `tests/test_adapters.py`、`tests/test_phase_checks.py`）大量依赖旧常量，一次性全改会造成测试大面积失效，反而阻塞修复进度。
- 全局单例 `REGISTRY` 成为新的单点故障：某个模块误修改它，所有模块的行为都被污染。

---

## 对抗点 2：`DispatchStrategy` 没有数据源，且与现有约束层硬冲突

【对抗点】Phase 4 要用“能力评分 + 负载感知 + 历史表现 + 成本”智能选 Agent，但方案既没有说明这些数据存在哪、怎么更新，也没意识到它选出的 Agent 会被 `system_constraint.route_task` 的硬映射直接拒绝。

【论据】
- 方案原文：
  > ```python
  > scores[agent] = (
  >     capability_match(agent, task_type) * 0.4 +
  >     load_factor(agent) * 0.2 +
  >     historical_success(agent, task_type) * 0.3 +
  >     cost_factor(agent) * 0.1
  > )
  > return max(scores).agent
  > ```
- 代码证据：
  - `max(scores)` 返回的是字典的键（字符串），字符串没有 `.agent` 属性，这段代码本身就会抛 `AttributeError`。
  - `pipeline_executor.py:322` 与 `:352` 在 dispatch 时仍调用 `self.constraint.route_task(task_type, ...)`；而 `system_constraint.py:259-268` 会校验 `requested_agent`，如果不等于 `TASK_ADAPTER_MAP` 的硬编码目标，就抛 `ConstraintViolation`。
  - `system_constraint.py:113-117` 的 `ADAPTER_CAPABILITIES` 只声明 `claude-code: [CODE]`、`codewhale: [REVIEW]`、`qwen-code: [TEST, DOC, E2E]`，没有负载/历史/成本表，也没有任何持久化表存储这些数据。

【风险】
- 如果策略与约束一致，它就成了“花里胡哨的硬映射包装”；如果不一致，每次 dispatch 都会触发 `ConstraintViolation`，流水线直接中断。
- 在缺少 ground-truth 评分数据的情况下上线，等于用随机权重决定把审查任务发给谁，可能把审核任务派给 coding Agent，导致审计失效。

---

## 对抗点 3：Phase 3 “dispatch 成功后自动触发 verify” 混淆了“任务入队”与“任务完成”

【对抗点】方案要求 `bridge_cli dispatch` 一返回成功就自动调度 inspector/test 并写入 `verify_record`，但当前 `dispatch()` 只是把任务推进 MCP transport 队列并返回 `task_id`，Agent 此时根本还没执行，自动 verify 会制造出“未经验收却被记录为已验收”的虚假证据。

【论据】
- 方案原文：
  > “bridge_cli dispatch成功后自动调度verify流程：1. route → inspector审查(qwen-code) 2. route → test测试(qwen-code) 3. 写入verify_record”
- 代码证据：
  - `pipeline_executor.py:304-337` 的 `dispatch()` 仅做三件事：路由校验 → 检查 endpoint 存在 → `transport.push(...)` 返回 `task_id`，没有任何等待或结果校验。
  - `pipeline_executor.py:339-362` 的 `dispatch_and_wait()` 也只是在 daemon 未运行时走 `_execute_sync` 同步执行；方案要废弃 `_execute_sync` 并改为 daemon-in-process，进一步削弱了“等待完成”的语义。
  - `phase_checks.py:505-584` 的 `check_accept` 目前只检查 `feat.get("status") == "passed"` 与 `accept_approved`，没有任何 `verify_record` 校验。

【风险】
- `verify_record` 会在代码实际还没被审查/测试时就写入，形成“形式上合规、实质上造假”的验收链。
- 后续 `check_accept` 一旦强制校验 `verify_record`，就会依赖这些假记录把未经验收的 feature 推进到 deploy；质量缺陷和风险直接逃逸到生产。

---

## 对抗点 4：废弃 `_execute_sync`、改为 daemon-in-process 是架构倒退

【对抗点】方案用“统一 MQ 路径”为理由废弃同步执行 fallback，但当前 daemon 是否运行并不确定；把 `bridge_cli` 绑到 daemon-in-process 会引入单进程阻塞、daemon 生命周期和崩溃恢复的新风险，反而降低可靠性。

【论据】
- 方案原文：
  > “无论daemon是否运行，dispatch都走MCPTransport.push→collect，保持任务生命周期完整性。`_execute_sync`废弃，改为daemon-in-process模式。”
- 代码证据：
  - `pipeline_executor.py:364-443` 的 `_execute_sync` 是 daemon 未运行时的安全 fallback：使用 `subprocess.run` 直接执行 CLI，有明确的超时、异常处理和结果回写。
  - `pipeline_executor.py:247` 的 `_daemon_procs` 只在 `enable_stdio=True` 时通过 `StdioTransport` 启动；没有外部 daemon 时，`dispatch_and_wait()` 完全依赖 `_execute_sync`。
  - 当前 `bridge_cli.py:94-117` 的 `cmd_route` 只返回目标 Agent，不启动 daemon；方案未给出 daemon 由谁启动、如何保活、崩溃后如何重启的设计。

【风险】
- 如果 daemon 没有预先运行，`dispatch` 会失败，导致 CLI 在常规使用场景下直接不可用。
- daemon-in-process 让长耗时任务与 CLI/MCP 主进程同生共死；daemon 崩溃会同时带走当前命令，丢失任务状态。
- 调试和故障排查变复杂：失败点从“一个子进程”扩散到“MCP transport + daemon + CLI + 注册表”整条链路。

---

## 对抗点 5：`verify_record` 嵌套 schema 与现有 `features.json` / SQLite 状态不兼容，且无迁移方案

【对抗点】Phase 3 把 `feature.status` 从简单字符串扩展为带 `verify_record` 子结构的对象，但现有 `state_store` 的 `features.status` 列是枚举字符串，`features.json` 里也是字符串，整个历史项目和测试数据都没有这个字段，方案却没有给出迁移路径。

【论据】
- 方案原文：
  > ```json
  > {
  >   "id": "F001",
  >   "status": "passed",
  >   "verify_record": {
  >     "reviewer": "codewhale",
  >     "review_report": "docs/review-F001.md",
  >     ...
  >   }
  > }
  > ```
- 代码证据：
  - `state_store.py:42-57` 的 `features` 表定义：
    ```sql
    status TEXT CHECK(status IN ('pending','in_progress','review','test','passed','failed','needs_rework'))
    ```
    没有 `verify_record` 列或 JSON 字段。
  - `features.json` 当前所有 feature 的 `status` 都是字符串，例如 `"status": "passed"`（见 `features.json:15`）。
  - `phase_checks.py:527-529` 直接比较 `status != "passed"`，`state_store.py` 也没有读取或写入 `verify_record` 的逻辑。

【风险】
- 直接应用新 schema 会让已有 SQLite 数据库和 `features.json` 瞬间失效；`check_accept` 读到旧数据会误判所有 feature 未通过。
- 没有迁移脚本意味着团队必须手动重建状态或一次性重写历史数据，违背“保持现有接口兼容”的承诺。
- 一旦数据写入失败或部分成功，会出现 SQLite 与 `features.json` 状态不一致，后续所有 checkpoint/resume/rollback 都不可靠。

---

## 对抗点 6：“新增 Agent 只需在 REGISTRY 加一行”是过度承诺

【对抗点】方案声称注册新 Agent 只需改 `registry.py`，但真实执行链路里还有 adapter 工厂、CLI endpoint、能力反向映射、prompt 解析、fallback 收件箱等多处硬编码，单点注册并不能让新 Agent 真正可用。

【论据】
- 方案原文：
  > “新增1个Agent：只需在REGISTRY加一行，无需改任何其他文件。”
- 代码证据：
  - `adapters.py:1661-1676` 的 `ADAPTER_REGISTRY` 与 `create_adapter()` 只认 `"claude" / "codewhale" / "qwen"`，键名甚至与方案里的 `"claude-code"` 不一致。
  - `pipeline_executor.py:197-223` 的 `DEFAULT_ENDPOINTS` 为每个 Agent 单独配置 `cli_path`、`cli_command` 和环境变量；这些不是从任何注册表读取的。
  - `system_constraint.py:113-117` 的 `ADAPTER_CAPABILITIES` 反向映射是硬编码的，新增 Agent 不更新这里会导致 `can_adapter_execute()` 误判。
  - `message_queue.py:36` 的 `VALID_TASK_TYPES` 与 `agent_daemon.py:48` 的 `VALID_TASK_TYPES` 也是独立常量，新增任务类型必须同步修改。

【风险】
- 运维或开发者按方案只在 REGISTRY 加一行后，CLI 找不到启动命令、system_constraint 拒绝路由、MessageQueue 拒绝入队，出现“注册成功但无法运行”的迷惑状态。
- 这种“注册表透明”的幻觉会让后续扩展产生大量隐藏遗漏点，反而比原先显式修改更危险。

---

## 对抗点 7：ProjectProfile 与 Sandbox 白名单扩展会扩大攻击面并误判多语言项目

【对抗点】Phase 5 用简单的 `source_extensions` + `build_commands` 抽象多语言项目，并计划把 `cmake / make / g++ / clang / idf.py / pio` 加入沙箱白名单；这既没有解决参数注入和路径穿越，又会把原本受控的高危编译/构建命令直接放行。

【论据】
- 方案原文：
  > “Sandbox白名单扩展：增加 cmake/make/g++/clang/idf.py/pio 等嵌入式工具链命令。”
  > “`check_develop` 不再写死 `rglob("*.py")`，改为 `rglob(pattern)` 从 `ProjectProfile.source_extensions` 读取。”
- 代码证据：
  - `phase_checks.py:398` 当前硬编码 `py_files = list(src_dir.rglob("*.py"))`，`check_test` 也使用 `test_*.py` / `*_test.py` 模式；这些检查函数在多处重复写死 Python 模式（如 `:482`、`:654`、`:1053`），不是简单改一个 `ProjectProfile` 就能覆盖。
  - `sandbox.py:145-157` 的 `_ALLOW_PATTERNS` 目前只允许 git、pytest、python、npm 等命令，且采用正则前缀匹配，没有任何参数级校验；例如 `python -c "..."` 已经能被匹配放行，进一步加入 `g++`、`cmake` 等任意文件编译命令后，Agent 可以用 `g++ /path/to/malicious.cpp -o /tmp/evil && /tmp/evil` 直接执行任意代码。
  - `sandbox.py:204-232` 的绕过检测仅覆盖 base64、分片、替代解释器等简单模式，对 `cmake -P script.cmake`、`make -f Makefile` 里的命令注入没有针对性检测。

【风险】
- 沙箱白名单一旦放行编译器，Agent 就能通过构造源文件编译并执行任意二进制，沙箱形同虚设。
- `source_extensions` 无法表达多语言混合仓库的真实情况；一个项目同时有 `.py`、`.cpp`、`.h` 时，`rglob(pattern)` 要么漏检、要么需要多次调用，导致 phase check 误报或漏报。

---

## 对抗点 8：Phase 6 “科学性”工作是持续性的空中楼阁，缺少工程落点

【对抗点】方案把阈值外部化、LLM-as-Judge 校准、多语言验证数据集标为“持续”任务，但没有给出与现有 pipeline 集成的接口、数据格式和责任人；这很容易变成无限期拖延，最终所有阈值仍硬编码或以未经验证的默认值运行。

【论据】
- 方案原文：
  > “P6 科学性 | 持续 | 3 | E1-E3 | 风险：需要人工标注数据集”
  > “8.1 所有30+个硬编码阈值迁移到 thresholds.yaml，标注来源和校准方法。”
  > “8.2 用人评分作为 ground truth，计算不同权重下 Cohen's Kappa，迭代优化。”
  > “8.3 至少建立 Python+C+++Go 各3个项目的基准验证集。”
- 代码证据：
  - 当前代码里没有 `thresholds.yaml`、没有阈值注册/读取模块、没有 judge 评分持久化表、也没有人工标注接口。
  - `evaluate.py`、`inspector.py`、`gate.py` 等文件里大量阈值（如 P0/P1/P2 判定线、覆盖率阈值、相似度阈值）仍是硬编码常量，方案没有列出具体阈值清单，也未说明如何在不中断运行的情况下热切换。

【风险】
- “科学性”被无限期推迟后，P1-P5 的修复将建立在未经校准的阈值上，方案声称的“质量提升”无法被证明。
- 即使部分完成，没有统一接口会导致各模块继续各自维护阈值，`thresholds.yaml` 沦为文档而非真相源。
- 人工标注数据集需要持续投入，但方案没有把它排进可验收的里程碑，最终变成“做了但永远不够好”的沉没成本。

---

## 总结：最该优先质疑的三条

1. **不要一次性大爆炸重构注册表**——先通过 shim/适配器让新 `registry.py` 与旧常量共存，至少跑完一轮回归测试再逐步迁移。
2. **不要在缺少数据源和兼容设计的情况下替换路由策略**——要么先补齐 load/success/cost 持久化与约束层契约，要么保留硬映射作为安全兜底。
3. **不要把 `verify_record` 做成形式合规的幌子**——先明确“dispatch 成功”是否等于“Agent 完成”以及由谁负责等待结果，再谈自动验收。
