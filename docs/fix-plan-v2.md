# Multi-Agent Pipeline 修复方案 v2.0

> 基于21项诊断问题，从全局视角设计修复方案

---

## 一、修复原则

1. **抽象优先**：先建平台层，再修具体bug。修复一个根因解决一类问题
2. **最小改动**：保持现有接口兼容，通过渐进式重构实现
3. **实证验证**：每个修复附带测试用例，不接受"看起来好了"
4. **模式透明**：新增Agent/语言/任务类型不应要求改核心代码

---

## 二、修复架构总览

新建三层抽象，解决三个根因：

```
┌──────────────────────────────────────┐
│  Layer 3: 决策层 (新增)               │
│  dispatch_strategy.py                │
│  能力评分 + 负载感知 + 历史表现       │
│  替代 static TASK_ADAPTER_MAP        │
├──────────────────────────────────────┤
│  Layer 2: 平台层 (新增)               │
│  registry.py — 统一注册表             │
│  Agent定义/Phase定义/任务类型/配置     │
│  全部从此注册表读取,单一真相源         │
├──────────────────────────────────────┤
│  Layer 1: 现有代码 (修正)             │
│  bridge_cli / pipeline_executor      │
│  phase_checks / system_constraint    │
│  不再硬编码,改为从Layer2读取          │
└──────────────────────────────────────┘
```

---

## 三、Phase 1：统一注册表（P0 — 解决10项问题）

### 目标
消除所有硬编码注册表，建立单一真相源。解决：命名不统一、Phase不一致、任务类型三版本、配置割裂、Agent写死。

### 3.1 新建 `registry.py`

```python
# 统一注册表 — 所有模块从此读取,严禁各自定义

class AgentDef:
    name: str          # 统一用 "claude-code" 格式
    cli_path: str
    capabilities: list[str]  # ["code","review","test",...]
    model: str         # "Kimi" / "qwen3-coder" / "deepseek-v4"

class PhaseDef:
    name: str
    check_func: str    # check函数名
    requires_evidence: bool  # 是否需要验证证据

class TaskTypeDef:
    name: str
    default_agent: str
    requires_review: bool

# 全局单例
REGISTRY = Registry(
    agents={
        "claude-code": AgentDef(capabilities=["code","adversarial"]),
        "qwen-code": AgentDef(capabilities=["code","test","doc","inspector","e2e"]),
        "codewhale": AgentDef(capabilities=["review","code"]),
    },
    phases=BROWNFIELD_PHASES + GREENFIELD_PHASES,  # 唯一Phase定义处
    task_types=["code","review","test","doc","e2e","inspector","adversarial"],
)
```

### 3.2 修改范围

| 文件 | 修改 | 删除 |
|------|------|------|
| `adapters.py` | ADAPTER_REGISTRY→REGISTRY.agents | ADAPTER_REGISTRY硬编码 |
| `system_constraint.py` | TASK_ADAPTER_MAP→REGISTRY路由 | TaskType枚举+TASK_ADAPTER_MAP |
| `pipeline_executor.py` | DEFAULT_ENDPOINTS→REGISTRY.agents | DEFAULT_ENDPOINTS硬编码 |
| `config.py` | AVAILABLE_MODES→REGISTRY.phases | 重复Phase定义 |
| `config_loader.py` | 废弃 | 整文件 |
| `models.py` | PHASE_NAMES→REGISTRY.phases | 重复定义 |
| `workflow_registry.py` | _GREENFIELD_PHASES→REGISTRY.phases | 重复定义 |
| `phase_checks.py` | CHECK_REGISTRY→REGISTRY.phases | 自行维护的Phase列表 |
| `message_queue.py` | VALID_TASK_TYPES→REGISTRY.task_types | 硬编码白名单 |
| `agent_daemon.py` | VALID_TASK_TYPES→REGISTRY.task_types | 硬编码白名单 |
| `condition_engine.py` | phase_order→REGISTRY.phases | 硬编码greenfield顺序 |
| `suggestion_engine.py` | phase_task_map→REGISTRY.task_types | 只认识greenfield |

新增1个Agent：只需在REGISTRY加一行，无需改任何其他文件。

---

## 四、Phase 2：统一入口 + 健壮性（P0 — 解决4项问题）

### 4.1 bridge_cli 迁移到 argparse

```python
# 支持命名参数, --help, 参数校验
python bridge_cli.py dispatch claude-code --task-type code \
    --feature-id F001 --prompt "..." --timeout 900 --stream
```

### 4.2 pipeline.py命令代理到bridge_cli

bridge_cli增加9个子命令：`init/advance/status/resume/rollback/approve/mark-tests/check/reset-circuit`

### 4.3 dispatch健康检查

`register_all_defaults()`后增加`check_endpoint_availability()`：
- 检查CLI路径存在
- 执行`claude --version`验证可用
- curl验证API Key有效性
- 失败时输出人类可读修复指引

### 4.4 dispatch进度流式输出

`_execute_sync`改用`Popen`+非阻塞stdout行读取，边执行边推送进度（通过MCPTransport streaming通道）。

### 4.5 异常处理兜底

main()增加`except Exception`兜底，输出结构化`{"error":"...","error_type":"...","traceback":"..."}` JSON。

---

## 五、Phase 3：流程约束强化（P0 — 解决3项问题）

### 5.1 accept强制验证

每个feature的status字段增加子结构：
```json
{
  "id": "F001",
  "status": "passed",
  "verify_record": {
    "reviewer": "codewhale",
    "review_report": "docs/review-F001.md",
    "test_results": "tests/report-F001.xml",
    "inspector_agent": "qwen-code",
    "adversarial_agent": "claude-code"
  }
}
```

`check_accept`不仅检查status=='passed'，还要求verify_record存在且包含审查报告路径。

### 5.2 verify阶段自动触发

bridge_cli dispatch成功后自动调度verify流程：
1. route → inspector审查(qwen-code)
2. route → test测试(qwen-code)
3. 写入verify_record

### 5.3 统一MQ路径

无论daemon是否运行，dispatch都走MCPTransport.push→collect，保持任务生命周期完整性。
`_execute_sync`废弃，改为daemon-in-process模式。

---

## 六、Phase 4：决策层智能化（P1 — 解决4项问题）

### 6.1 新建 `dispatch_strategy.py`

```python
class DispatchStrategy:
    def select_agent(task_type, feature, context) -> AgentDef:
        scores = {}
        for agent in REGISTRY.agents:
            scores[agent] = (
                capability_match(agent, task_type) * 0.4 +
                load_factor(agent) * 0.2 +
                historical_success(agent, task_type) * 0.3 +
                cost_factor(agent) * 0.1
            )
        return max(scores).agent
```

替代`TASK_ADAPTER_MAP`死映射。Agent选择基于：
- 能力匹配（多Agent可做同一任务，根据confidence选最优）
- 当前负载（避免向正在忙的Agent派任务）
- 历史成功率（自动避开表现差的Agent）
- 成本（同等条件下选便宜的）

### 6.2 suggestion_engine增强

从"布尔转发"升级为上下文感知：
- 不仅告诉你"next_phase是什么"，还告诉你"为什么"和"需要准备什么"
- 增加brownfield阶段映射
- verify检查提示（"F019还没经过审查，需要先运行verify"）

---

## 七、Phase 5：通用性（P2 — 解决3项问题）

### 7.1 项目类型抽象

```python
class ProjectProfile:      # 语言+工具链
    source_extensions: list[str]  # [".py"] 或 [".cpp",".h"]
    test_patterns: list[str]     # ["test_*.py"] 或 ["*_test.cpp"]
    build_commands: list[str]    # ["pip install -r requirements.txt"] 或 ["cmake ..","make"]
```

### 7.2 Phase检查参数化

`check_develop`不再写死`rglob("*.py")`，改为`rglob(pattern)`从`ProjectProfile.source_extensions`读取。

### 7.3 Sandbox白名单扩展

增加`cmake/make/g++/clang/idf.py/pio`等嵌入式工具链命令。

---

## 八、Phase 6：科学性（P2 — 解决3项问题）

### 8.1 阈值外部化+文档化

所有30+个硬编码阈值迁移到`thresholds.yaml`，标注来源和校准方法。

### 8.2 LLM-as-Judge一致性校验

用人评分作为ground truth，计算不同权重下Cohen's Kappa，迭代优化。

### 8.3 多语言验证数据集

至少建立Python+C+++Go各3个项目的基准验证集。

---

## 九、实施计划

| Phase | 周数 | Feature数 | 解决诊断项 | 风险 |
|-------|------|----------|-----------|------|
| P1 统一注册表 | 1-2 | 12 | A1-A5, B1, C1-C3, D1-D3 | 改动面大,需全部测试通过 |
| P2 入口+健壮性 | 1 | 5 | A3, C1, C4, D1, D4 | argparse迁移可能影响现有调用 |
| P3 流程约束 | 1 | 4 | B1-B3, C2 | features.json格式变更需迁移 |
| P4 决策智能化 | 1-2 | 3 | B2, B4, C3 | 新策略需与旧路由兼容 |
| P5 通用性 | 2-3 | 3 | E1-E3 | 需要非Python项目验证 |
| P6 科学性 | 持续 | 3 | E1-E3 | 需要人工标注数据集 |

---

*等待Claude Code对抗审查。*
