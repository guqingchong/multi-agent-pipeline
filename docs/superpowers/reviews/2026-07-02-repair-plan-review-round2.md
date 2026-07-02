# Multi-Agent Pipeline 修复方案 — 第二轮审查意见

> 审查对象：`docs/superpowers/plans/2026-07-02-multi-agent-pipeline-repair-plan.md`（修订版，1337 行）
> 审查日期：2026-07-02
> 基于第一轮审查的 8 个问题逐条核验

---

## 一、逐条核验结果

| # | 问题（第一轮） | 优先 | 状态 | 修正位置 |
|---|-------------|------|------|---------|
| P0-1 | 22+ 孤儿模块遗漏 | P0 | ✅ 撤回——7/1已完成 | 不涉及方案 |
| P0-2 | observability.py 覆盖现有文件 | P0 | ✅ 已修正 | Task 8 Step 1 明确"增强现有，不删除"；验收标准加保护条款 |
| P1-1 | Phase enum 删除影响 15+ 模块 | P1 | ✅ 已修正 | Task 1 Step 6 列出全局替换清单 + 原则声明；Step 7 扩展测试范围 |
| P1-2 | brownfield Phase 命名不一致 | P1 | ✅ 已修正 | Self-Review 明确"统一为现有 7-phase"；验收标准同步 |
| P1-3 | Queue DDL SQL 注入风险 | P1 | ✅ 已修正 | Task 1 Step 4 新增 `_TASK_TYPE_NAME_RE` 正则校验 |
| P2-1 | debate 子系统缺位 | P2 | ✅ 已修正 | 架构图新增 `debate/` 行；声明"本次不涉及，保留现状" |
| P2-2 | main.py 城策通残留 | P2 | ✅ 已修正 | 验收标准明确"`/finance/*`、`/knowledge/*`、`/documents/*` 路由已删除" |
| P2-3 | AGENT_MOCK 削弱测试 | P2 | ⚠️ 需微调 | Self-Review 已修正语义，但代码实现仍需对齐 |

**8 项中 7 项已完全修正，1 项需微调。**

---

## 二、唯一剩余问题：AGENT_MOCK 实现

### 问题

方案 Self-Review（line 1322）已承诺正确语义：

> `AGENT_MOCK` 仅短路真实 subprocess，解析层/容错层保持可测。

但 Task 6 Step 1 的代码实现仍然是**入口短路**模式：

```python
def run(self, task_type, payload, work_dir, timeout=600) -> AgentResult:
    if os.environ.get("AGENT_MOCK", "false").lower() == "true":
        return self._mock_run(task_type, payload)  # ← 跳过整个 pipeline
    # real CLI run...
```

问题：`_mock_run()` 直接返回 `AgentResult`，跳过了 adapters.py 的核心三层逻辑——**解析层**（~500 行，正则 + 启发式规则）和**容错层**（~400 行，Timeout/崩溃/截断恢复），导致这两层在 `AGENT_MOCK=true` 下完全不被测试覆盖。

### 修正

将 `run()` 拆为双层结构，mock 仅作用于 subprocess 调用：

```python
def run(self, task_type, payload, work_dir, timeout=600) -> AgentResult:
    """Agent 执行入口：CLI 调用 → 解析 → 容错。mock 仅短路 subprocess。"""
    raw_stdout, raw_stderr, exit_code = self._execute_cli(
        task_type, payload, work_dir, timeout
    )
    # 解析层和容错层始终运行
    return self._parse_and_validate(raw_stdout, raw_stderr, exit_code)


def _execute_cli(self, task_type, payload, work_dir, timeout):
    """执行 CLI 进程。mock 模式下返回模拟原始输出。"""
    if os.environ.get("AGENT_MOCK", "false").lower() == "true":
        return self._mock_raw_output(task_type, payload), "", 0
    # 真实 subprocess.run(...)
    ...


def _mock_raw_output(self, task_type, payload) -> str:
    """返回模拟的原始 CLI 输出字符串（不含任何解析）。"""
    return (
        f"[MOCK] Agent simulated output for task_type={task_type}\n"
        f"Task completed successfully.\n"
        f"Generated code/files as requested."
    )


def _parse_and_validate(self, raw_stdout, raw_stderr, exit_code) -> AgentResult:
    """解析层 + 容错层：从原始输出提取结构化结果。"""
    # 1. 解析（正则 + 启发式规则）——始终运行
    parsed = self._parser.extract(raw_stdout, raw_stderr)
    # 2. 容错（Timeout/崩溃/截断检测）——始终运行
    if exit_code != 0:
        return self._tolerance.handle_failure(raw_stderr, exit_code)
    return AgentResult(success=True, output=parsed, ...)
```

### 影响

- Task 6 Step 1 代码块需更新
- `_mock_run()` 重命名为 `_mock_raw_output()`，只返回字符串
- conftest.py 的 `set_mock_mode` fixture 无需改动

---

## 三、综合评价

修订后的方案质量从 B+/A- 提升到 **A-**。第一轮发现的问题已系统性修正：

- **架构完整性**：debate 子系统在架构图中得到标注和边界声明
- **安全性**：Queue DDL 和 Phase 模型都加了输入校验
- **可维护性**：Phase 全局替换有了明确的执行清单和原则约束
- **诚实性**：progress.md 重写为真实状态，不再声称虚假完成度
- **一致性**：brownfield 统一为单一 7-phase，消除命名冲突
- **可观测性**：observability.py 从"新建覆盖"改为"增强现有"，保护了 Dashboard/AlertManager 等已实现功能

仅 AGENT_MOCK 实现细节需要在执行 Task 6 时对齐——不影响方案整体架构设计，可在执行时自然修正。

**结论：方案已具备执行条件，建议开始实施。**
