# Multi-Agent Pipeline 实用性 & 合理性审查报告

> **审查日期**: 2026-07-02  
> **审查范围**: bridge_cli.py、pipeline.py、pipeline_executor.py、mcp_transport.py、system_constraint.py、config.py、config_loader.py、phase_flow.py、phase_checks.py、fallback_manager.py、circuit_breaker.py、workflow_registry.py、condition_engine.py  
> **审查维度**: 入口合理性、dispatch 流程实用性、错误处理充分性、配置管理灵活性

---

## 一、总览

| 维度 | 评分 | 摘要 |
|------|------|------|
| 入口合理性 | ⚠️ 中 | bridge_cli.py 设计意图清晰，但可用性粗糙，命令覆盖不全 |
| Dispatch 流程实用性 | ⚠️ 中 | 架构分层合理，但依赖外部 CLI 安装且无进度反馈 |
| 错误处理充分性 | ⚠️ 中 | 基础设施（熔断/降级）完备，但 CLI 层错误吞没严重 |
| 配置管理灵活性 | ❌ 低 | 存在两套互不集成的配置系统，Phase 列表多处不一致 |

---

## 二、维度一：bridge_cli.py 作为唯一入口是否合理

### 2.1 现状分析

`bridge_cli.py` 定位为"Hermes 到三层架构的桥梁"，提供 7 个子命令：

```
load <project>       → 加载项目状态 + 仪表盘
route <type> [fid]   → 约束路由
suggest <project>    → 下一步建议
full <project>       → load + suggest
check-hermes <type>  → Hermes 权限检查
dispatch <adapter> <type> [prompt] → 真实 CLI 派发
mode [project]       → 模式检测
```

入口层依赖 `entry.py` → `state_store` → `observability`，以及 `system_constraint` → `suggestion_engine`。设计模式是"薄 CLI + JSON 输出"，Hermes（上层 AI）通过 `subprocess` 调用它并解析 stdout JSON。

### 2.2 发现

#### P0 — dispatch 与 pipeline.py 命令分离，入口不统一

- **现状**: `bridge_cli.py` 作为 Hermes 的唯一入口，但 `pipeline.py` 有自己完整的 argparse CLI（`init/develop/check/advance/status/resume/rollback/rollback-phase/approve/mark-tests`，共 10 个子命令），而这些命令在 `bridge_cli.py` 的 `COMMANDS` 字典中完全不存在。
- **影响**: Hermes 若需要执行 `pipeline.py init <project>` 初始化项目，必须绕过 bridge_cli.py 直接调用 pipeline.py。这导致"唯一入口"名不副实，实际的编排逻辑需要通过两条完全不同的 CLI 通道。
- **修复建议**: 在 `bridge_cli.py` 的 `COMMANDS` 中增加 pipeline 代理命令（至少 `init/advance/status/resume`），统一由 bridge_cli.py 转发到 pipeline.py 或直接导入调用。

#### P0 — 缺少 `--help` 和 argparse 标准交互

- **现状**: `bridge_cli.py` 的 `main()` 使用手动位置参数解析（`sys.argv[1]`、`sys.argv[2:]`），没有任何 `--help` 选项。没有任何参数类型校验（`cmd_route` 直接传字符串给 `SystemConstraint.route_task`）。
- **影响**: 人类运维或调试时无法快速了解可用命令和参数格式。`main()` 中虽然打印 `__doc__` 当无参数时，但 `__doc__` 中的示例已经过时（仍标注 `load chengcetong` 而非通用项目名）。
- **修复建议**: 迁移到 `argparse` 或至少为每个子命令提供 `--help` 文本。在 `main()` 中增加 `if '--help' in sys.argv` 分支。

#### P1 — 错误处理只覆盖 JSON 序列化异常

- **现状**: `main()` 中的 try/except 只捕获 `json.JSONDecodeError` 和 `TypeError`（第 286 行）。如果 `cmd_load` 中 `auto_load` 抛出 `FileNotFoundError`、`cmd_route` 中 `SystemConstraint` 抛出未预期异常，会直接导致未捕获的 traceback 打印到 stdout，破坏 JSON 输出协议。
- **影响**: Hermes 解析 JSON 输出时遇到 traceback 文本会解析失败，且无法区分错误类型。
- **修复建议**: 在 `main()` 中增加宽泛的 `except Exception` 兜底，输出 `{"error": "...", "traceback": "..."}` 结构化 JSON，确保协议不破。

#### P2 — dispatch 超时硬编码 300 秒不可配

- **现状**: `cmd_dispatch` 第 243 行 `timeout_sec=300` 硬编码。对于大型项目的代码审查或测试任务，300 秒远远不够。
- **修复建议**: 从环境变量 `PIPELINE_DISPATCH_TIMEOUT` 读取，默认 300。

---

## 三、维度二：Dispatch 流程对真实工程任务是否实用

### 3.1 现状分析

Dispatch 流程涉及 4 层：

```
bridge_cli.py dispatch
  → PipelineExecutor.dispatch_and_wait()
    → SystemConstraint.route_task()        # 约束校验
    → MCPTransport.push()                   # 入队 (SQLite MQ)
    → PipelineExecutor._execute_sync()      # 同步 subprocess 执行
      → subprocess.run([cli_path, ...args]) # 调用真实 CLI agent
```

### 3.2 发现

#### P0 — 依赖外部 CLI 安装，无安装检测和友好降级

- **现状**: `DEFAULT_ENDPOINTS` 中通过 `_resolve_cli_path()` 查找 CLI 路径（npm global 目录），若找不到则 fallback 到裸命令名（依赖 PATH）。没有任何机制在 dispatch 前检测 CLI 是否真实可用。
- **影响**: 用户在新机器上首次运行时，dispatch 会静默失败（`FileNotFoundError` 或 `subprocess` 返回非零退出码），错误信息为原始的 `Exit code 1`，对用户不友好。
- **修复建议**: 在 `register_all_defaults()` 后增加 `check_endpoint_availability()` 方法，对每个 endpoint 执行 `--version` 探测，不可用时输出明确提示（如"未检测到 claude-code CLI，请执行 `npm install -g @anthropic-ai/claude-code`"）。

#### P0 — 同步执行无进度反馈，长任务体验差

- **现状**: `_execute_sync()` 使用 `subprocess.run(capture_output=True)` 阻塞等待，整个执行期间无任何输出。任务完成后才一次性返回全部结果。对于可能运行 5-10 分钟的代码审查任务（如 `codewhale exec --auto "review the entire codebase"`），用户完全不知道进展。
- **影响**: 真实工程任务（如全量代码审查、E2E 测试套件）可能耗时数分钟，纯黑盒等待体验不可接受。
- **修复建议**: 改用 `subprocess.Popen` + 非阻塞读取 stdout 行，通过 `MCPTransport` 的 streaming 通道（`StreamingCollector`）实时推送进度。`bridge_cli.py dispatch` 应增加 `--stream` 模式输出渐进式 JSON Lines。

#### P1 — Agent 工作目录受限于环境变量，无法按任务指定

- **现状**: dispatch 的工作目录由 `PIPELINE_PROJECT_DIR` 环境变量全局控制（`pipeline_executor.py` 第 239 行）。如果要同时处理两个项目的任务（项目 A 的审查 + 项目 B 的编码），必须切换环境变量，无法在单次 dispatch 调用中指定。
- **修复建议**: `cmd_dispatch` 增加可选参数 `--project-dir`，优先级高于环境变量。`PipelineExecutor.dispatch_and_wait()` 增加 `cwd` 参数。

#### P1 — 任务队列（MCPTransport）与同步执行路径割裂

- **现状**: `dispatch_and_wait()` 在无活跃 daemon 时走 `_execute_sync()` 直接 subprocess，绕过了 MCPTransport 的任务生命周期管理。这意味着同步路径下的任务不会被记录到 `_pending` 字典、没有超时检测、不支持 cancel。
- **影响**: 当 daemon 未运行时（大多数情况），dispatch 退化为裸 subprocess 调用，之前设计的 MQ/心跳/取消等基础设施全部失效。
- **修复建议**: 统一路径——即使无 daemon，也通过 MCPTransport 的 push + 内联执行（daemon-in-process）+ collect 完成，保持完整的任务生命周期记录。

#### P2 — CLI 命令模板存在注入风险

- **现状**: `_execute_sync()` 第 386-394 行将 CLI 命令模板按 `{prompt}` 分割后拼接到 `args` 列表。但 `MCPTransport.complete()` 第 252 行使用 `json.dumps` 打包结果时，若 `result.output` 包含特殊字符，已做截断处理。同一文件中 `_execute_sync()` 的 args 构建虽然避免了 `shell=True`（好），但 prompt 内容直接作为独立参数传递，若 prompt 以 `-` 开头可能被误解析为 CLI 选项。
- **修复建议**: 在 prompt 前显式插入 `--` 分隔符确保后续内容不被解析为选项。

---

## 四、维度三：错误处理是否充分

### 4.1 现状分析

项目有完善的多层错误处理基础设施：
- **circuit_breaker.py**: 熔断器（3 次失败 → 300s 恢复，半开状态）
- **fallback_manager.py**: 降级管理器（Claude → Qwen → CodeWhale 多级降级链）
- **pipeline_executor.py**: P1 心跳监控 + 任务取消 + 流式收集

### 4.2 发现

#### P0 — bridge_cli.py main() 异常吞没面过窄

- **现状**: `main()` 第 283-288 行只捕获 `json.JSONDecodeError` 和 `TypeError`。这意味着以下异常会导致裸 traceback 泄露到 stdout：
  - `cmd_load` 中 `auto_load()` 抛出 `FileNotFoundError`
  - `cmd_dispatch` 中 `subprocess.run()` 抛出的未被 `_execute_sync` 捕获的异常
  - `cmd_suggest` 中 `SuggestionEngine` 的任何未预期异常
  - `cmd_mode` 中 `get_config()` 的 pydantic ValidationError
- **影响**: 破坏 JSON 输出协议，Hermes 无法解析错误并进行恢复。
- **修复建议**: 

```python
except Exception as e:
    import traceback
    print(json.dumps({
        "error": str(e),
        "error_type": type(e).__name__,
        "traceback": traceback.format_exc()[-2000:],
        "command": cmd,
    }, ensure_ascii=False))
    sys.exit(1)
```

#### P0 — pipeline_executor._execute_sync() 缺少环境变量缺失检测

- **现状**: `_execute_sync()` 执行时若 `ANTHROPIC_API_KEY` 等关键环境变量缺失，subprocess 会以非零退出码失败，返回模糊的 `Exit code 1`。不做前置检测。
- **影响**: 用户很难定位是 API Key 未配置导致的失败。
- **修复建议**: 执行前检查必要环境变量（根据 adapter 类型），缺失时立即返回明确的错误信息，如 `"claude-code requires ANTHROPIC_API_KEY or KIMI_CODING_API_KEY"`。

#### P1 — MCPTransport.collect() 忙等轮询无退避策略

- **现状**: `collect()` 和 `collect_all()` 使用 `time.sleep(0.5)` 固定间隔轮询。在超时较长（如 600s）时会产生 1200 次轮询调用，且无递增退避。
- **影响**: 在高频轮询场景下 CPU 浪费，且在 daemon 未启动场景下（任务永远不会被标记完成）会空转至超时。
- **修复建议**: 使用指数退避（初始 0.1s，最大 5s），并增加死信检测——若任务状态超过 `max_retries * timeout` 仍未变更，提前返回 DEAD 状态。

#### P1 — fallback_manager 中 `get_active_adapter()` 在 ALL_FAILED 后无恢复机制

- **现状**: 当所有 adapter 都失败后，`_status` 设为 `ALL_FAILED`。后续调用 `get_active_adapter()` 仍会尝试降级链，但如果所有 breaker 都 OPEN，会直接 `raise RuntimeError("All adapters failed")`。没有定时 reset 或手动恢复入口。
- **影响**: 一旦全部熔断，系统需要外部干预（重启进程）才能恢复。
- **修复建议**: 增加 `ALL_FAILED` 状态下的自动恢复定时器（如 600s 后强制 reset 所有 breaker 并重试），或通过 `bridge_cli.py reset-circuit` 命令提供手动恢复入口。

#### P2 — circuit_breaker.py call() 只捕获特定异常类型

- **现状**: `CircuitBreaker.call()` 第 125 行明确列出捕获的异常类：`ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError`。这意味着如果外部 CLI 抛出自定义异常（如 `AdapterError`）或标准库中不在列表中的异常（如 `MemoryError`, `SystemExit`），熔断器不会记录失败，函数会继续运行。
- **影响**: 某些真实故障不会被计入熔断计数，降低了熔断器的保护效果。但也避免了 `BaseException`（如 `KeyboardInterrupt`）被误捕获。
- **修复建议**: 增加 `except Exception` 作为兜底（放在现有 `except` 之后），但对 `KeyboardInterrupt` 和 `SystemExit` 仍保持不捕获。

---

## 五、维度四：配置管理是否灵活

### 5.1 现状分析

项目存在两套配置系统：

| 配置系统 | 文件 | 方式 | 用途 |
|---------|------|------|------|
| PipelineConfig | `src/config.py` | pydantic-settings, `.env` / env vars | 全局 pipeline 配置 |
| ConfigLoader | `src/config_loader.py` | YAML 文件 | prompt_cache 配置 |

### 5.2 发现

#### P0 — 两套配置系统互不集成，ConfigLoader 几乎未被使用

- **现状**: `PipelineConfig` 使用 pydantic-settings，支持 `.env` 和环境变量（前缀 `PIPELINE__`）。`ConfigLoader` 读 YAML 文件，目前仅用于 `prompt_cache` 模块。全局 `get_config()` 只返回 `PipelineConfig` 单例，`ConfigLoader` 没有全局访问入口。架构上两套配置完全割裂——PipelineConfig 不知道 YAML 配置的存在，ConfigLoader 也不知道 `.env` 中的设置。
- **影响**: 运维需要同时维护 `.env` 和 `config.yaml`（或类似文件），配置项散落两处，容易不一致。
- **修复建议**: 统一到 pydantic-settings（推荐），让 `PipelineConfig` 增加 `prompt_cache` 子模型，移除 `ConfigLoader` 或将其降级为只读兼容层。或者让 `get_config()` 返回一个合并视图。

#### P0 — Phase 列表在 4 个位置独立定义，互不一致

| 位置 | Phase 列表 | 数量 |
|------|-----------|------|
| `config.py` `AVAILABLE_MODES["greenfield"]["phases"]` | `init, design, decompose, research, prd, journey, develop, integrate, test, evaluate, accept, deploy` | 12 |
| `models.py` `PHASE_NAMES` | `init, design, decompose, develop, test, accept, deploy` | 7 |
| `workflow_registry.py` `_GREENFIELD_PHASES` | `INIT, PRD, RESEARCH, DESIGN, DESIGN_REVIEW, DECOMPOSE, DEVELOP, CODE_REVIEW, TEST, FIX_LOOP, ACCEPT, DEPLOY` | 12（但名称不同） |
| `phase_checks.py` `CHECK_REGISTRY` 中注册的 check 函数 | `init, design, decompose, develop, test, accept, deploy` | 7（与 models.py 对齐） |

- **影响**: `phase_flow.py` 的 `PHASE_ORDER = get_config().phase_order` 返回 12 个 phase，但 `phase_checks.py` 只有 7 个 check 函数。当 PhaseFlow 推进到 `research/prd/journey/integrate/evaluate` 时，`run_check()` 找不到对应的 check 函数，行为未定义（可能返回默认的 pass）。`workflow_registry.py` 的 phase 名称使用大写+下划线格式（`DESIGN_REVIEW`, `FIX_LOOP`），与其他模块的小写格式不兼容。
- **修复建议**: **选定唯一真相源**（推荐 `models.py` 的 `PHASE_NAMES`），所有其他模块从中派生。`workflow_registry.py` 的 phase 名称应统一为小写格式。为缺失的 phase 补充 check 函数或显式跳过。

#### P1 — PipelineConfig.mode 切换存在静默不一致风险

- **现状**: `PipelineConfig.detect_mode()` 是静态方法，自动检测 brownfield 条件，但 `pipeline_mode` 默认值是 `"greenfield"`。如果在 `bridge_cli.py cmd_mode` 中检测到 brownfield 但未调用 `reload_config()` 更新单例，后续 `phase_order` 仍然返回 greenfield 的 12-phase 链。也就是说模式检测结果并不会自动切换运行模式。
- **影响**: 用户执行 `bridge_cli.py mode my-project` 看到 `detected_mode: brownfield`，但后续 `pipeline.py advance` 仍按 greenfield 的 phase 链推进。
- **修复建议**: `detect_mode()` 应该是实例方法，`cmd_mode` 检测到不匹配时应提供切换指令（如 `bridge_cli.py set-mode brownfield`），或让检测结果自动写入 `.pipeline_mode` 标记文件。

#### P2 — 缺少项目级配置覆盖机制

- **现状**: `.env` 是全局的，`PipelineConfig` 不支持按项目覆盖（如不同项目使用不同的 `db_name`、`pipeline_mode`）。
- **影响**: 在 `base_dir` 下有多个项目时，无法为每个项目独立设置 pipeline 参数。
- **修复建议**: 支持 `<project_dir>/.pipeline.env` 项目级配置，加载时合并覆盖全局 `.env`。

#### P2 — config_loader.py 中的 ConfigLoader 使用了过时的默认值模式

- **现状**: `DEFAULT_CONFIG` 被硬编码在模块级别，`_merge_defaults()` 使用浅拷贝。如果 `loaded` 中有嵌套的 `prompt_cache` 只包含 `enabled: false`，`_merge_defaults` 会合并生成正确的 `{"enabled": false, "target_hit_rate": 0.7, ...}`（因为它是 dict 级别合并），这是正确行为，但代码意图不清晰。
- **修复建议**: 如果统一到 pydantic-settings（推荐方案），此问题自动解决。

---

## 六、汇总与优先级建议

### 按严重性排序的修复路线图

| 优先级 | 数量 | 典型问题 | 建议修复周期 |
|--------|------|---------|-------------|
| P0 | 6 | 入口不统一、Phase 定义多处不一致、dispatch 无 CLI 检测、main() 异常吞没不足、配置系统割裂、同步路径无进度 | 1-2 周 |
| P1 | 5 | 超时硬编码、任务生命周期割裂、忙等轮询、降级无恢复、模式切换不联动 | 2-4 周 |
| P2 | 4 | CLI 注入风险、硬编码超时、异常捕获面、缺少项目级配置 | 持续改进 |

### 架构级建议

1. **统一入口**: 将 pipeline.py 的 10 个子命令代理到 bridge_cli.py，真正实现"一个 CLI 管全部"。
2. **统一 Phase 定义**: 以 `models.py` 的 `PHASE_NAMES` 为唯一真相源，其他模块引用它。
3. **统一配置**: 废弃 ConfigLoader，全部迁移到 pydantic-settings 的 PipelineConfig，增加项目级 `.pipeline.env` 覆盖。
4. **统一 Dispatch 路径**: daemon 存在与否都应走 MCPTransport（内联 daemon），保持任务生命周期完整性。
5. **增加前置检测**: dispatch 前检测 CLI 可用性、API Key 存在性，失败时给出人类可读的修复指引。
