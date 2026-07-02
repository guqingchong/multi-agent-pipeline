# 对抗审查 1/3：Agent 从 3 个扩展到 10 个——扩展性最坏假设审查

> **审查日期**：2026-07-02  
> **审查范围**：`src/system_constraint.py`、`src/pipeline_executor.py`、`src/message_queue.py`、`src/mcp_transport.py`、`src/bridge_cli.py`、`src/agent_daemon.py`  
> **最坏假设**：在不修改源码、不新增配置的前提下，直接把 Agent 数量从 3 个（`claude-code` / `codewhale` / `qwen-code`）扩展到 10 个（新增安全审计、UI/UX、DevOps、文档、数据、需求澄清、回归测试等 Agent）  
> **结论先行**：**不会瞬间崩溃，但会在硬编码路由表、端点注册、任务类型白名单、调度语义四个层面被拦住，等于事实上的不可扩展。**

---

## 1. `TASK_ADAPTER_MAP` 路由表会怎样？

### 当前实现

`src/system_constraint.py:98-110` 把 8 个任务类型固定映射到 3 个 adapter；`ADAPTER_CAPABILITIES` 反向表同样只认识这 3 个（`src/system_constraint.py:113-117`）。

### 最坏假设下的表现

| 维度 | 结果 |
|------|------|
| **容量** | `Dict` 本身装 10 个键值对毫无压力，Python hash map 查找仍是 O(1)。 |
| **功能** | 新增的 7 个 Agent 在 `TASK_ADAPTER_MAP` 里没有入口，`SystemConstraint.route_task()` 里 `TASK_ADAPTER_MAP.get(tt)` 会返回旧 adapter 或 `None`，导致任务被路由到错误 Agent 或抛出 `ConstraintViolation`（`src/system_constraint.py:249-256`）。 |
| **任务类型** | `TaskType` 是封闭 `Enum`（`src/system_constraint.py:69-81`），新 Agent 带来的新任务类型必须改源码才能加入。 |
| **显式指定** | 如果调用方显式 `requested_agent=new-agent`，`route_task()` 会校验它必须等于 `TASK_ADAPTER_MAP` 里的固定值，否则报冲突（`src/system_constraint.py:259-268`）。新 Agent 连“被点名执行”都不被允许。 |
| **负载均衡** | 当前是 **1 个任务类型 → 1 个 adapter** 的硬映射。10 个 Agent 里即使有 3 个都能做 review，也没有任何路由选择、负载均衡或能力评分机制。 |

### 结论

`TASK_ADAPTER_MAP` **字典不会“爆”，但它是一个写死的 1:1 路由表**。新增 Agent 必须同步修改：
1. `TASK_ADAPTER_MAP`
2. `ADAPTER_CAPABILITIES`
3. `TaskType` Enum（如果有新任务类型）
4. 所有单元测试里的断言

否则新 Agent 只是“注册了但不被承认”。

---

## 2. `pipeline_executor._cli_endpoints` 字典能支撑吗？

### 当前实现

- `_cli_endpoints: Dict[str, CLIEndpoint]`（`src/pipeline_executor.py:248`）按 `adapter_name` 索引。
- 默认只注册 3 个 endpoint：`DEFAULT_ENDPOINTS`（`src/pipeline_executor.py:197-223`），`register_all_defaults()` 遍历该列表注册（`src/pipeline_executor.py:284-300`）。
- `dispatch()` 校验 `if adapter_name not in self._cli_endpoints` 则抛 `ValueError`（`src/pipeline_executor.py:326-330`）。

### 最坏假设下的表现

| 维度 | 结果 |
|------|------|
| **容量** | Python dict 装 10 个 endpoint 完全没问题；key 冲突时后者直接覆盖，不会告警。 |
| **自动发现** | 不会从 `TASK_ADAPTER_MAP` 或 `ADAPTER_REGISTRY` 自动推导出新 endpoint。新增 Agent 必须手动 `register_cli_endpoint()` 或修改 `DEFAULT_ENDPOINTS`。 |
| **调度语义** | `_cli_endpoints` 只保存 `cli_path` / `cli_command` / `env`，没有 `max_workers`、`cost`、`fallback`、`health`、`capability` 等元数据。10 个 Agent 无法做负载均衡或智能选择。 |
| **daemon 启动** | `start_all_daemons()` 会遍历 `_cli_endpoints`（`src/pipeline_executor.py:492-495`），所以能启动 10 个进程；但每个 daemon 脚本固定 `max_concurrent=1`（`src/pipeline_executor.py:614`），热门 Agent 无法横向扩容。 |

### 结论

`_cli_endpoints` **字典本身能装 10 个 key，但“装得下 ≠ 支撑得了”**。它缺少自动注册、元数据和并发控制，目前只是一个静态容器，而不是可扩展的端点注册表。

---

## 3. `message_queue` 的轮询机制会崩溃吗？

### 当前实现

- `MessageQueue` 基于 SQLite，WAL 模式，每次操作新建连接（`src/message_queue.py:129-139`），使用 `threading.RLock` 保护懒加载。
- `pull()` 按 `target_agent` + `status='queued'` 原子抢占一条任务（`src/message_queue.py:231-276`），有复合索引。
- `pipeline_executor.py` 生成的 daemon 脚本采用固定 `time.sleep(1)` 空转拉取（`src/pipeline_executor.py:619-623`）；`AgentDaemon` 用 0.5s（`src/agent_daemon.py:178`）。
- `mcp_transport.collect()` 在内存 `_pending` 上忙等，0.5s 扫描一次（`src/mcp_transport.py:289-307`）。
- `VALID_TASK_TYPES = ('code', 'review', 'test', 'shutdown', 'inspector', 'adversarial', 'doc', 'e2e')`（`src/message_queue.py:36`），push 时拒绝未注册类型（`src/message_queue.py:193-196`）。
- 未设置 `PRAGMA busy_timeout`，且连接每操作后关闭。

### 最坏假设下的表现

| 维度 | 结果 |
|------|------|
| **数据库压力** | 10 个 Agent 各 1Hz 轮询 ≈ 10 QPS，SQLite WAL 完全能扛；不会单纯因为查询量崩溃。 |
| **锁与 daemon 稳定性** | 多个进程共写同一 DB，且未设 `busy_timeout`；高并发或长事务下可能出现 `database is locked`。`pull()` 里 `sqlite3.Error` 会 rollback 后 re-raise（`src/message_queue.py:272-274`），但 `pipeline_executor.py` 生成的 daemon 脚本在 `while True` 里未捕获该异常，会直接导致 daemon 进程退出。 |
| **空转与延迟** | 固定 1s / 0.5s 睡眠造成任务派发延迟；突发任务时不能快速消费。无 backoff、无 long-polling、无事件通知。 |
| **任务类型白名单** | 新 Agent 的新 `task_type` 会被 `MessageQueue.push()` 直接拒绝，连队列都进不了。 |
| **并发度** | 每个 daemon `max_concurrent=1`，同类 Agent 只有单实例消费；如果 10 个 Agent 中某类任务突发，吞吐量被单进程卡住。 |
| **队列深度** | `agent_queue_depth()` 只统计内存 `_pending` 中 `DISPATCHED` 状态（`src/mcp_transport.py:333-338`），不读 DB 中 `queued` 数量，调度器无法根据真实 backlog 做扩缩容。 |

### 结论

轮询机制**不会因为 10 个 Agent 而立即崩溃**，但存在 3 个潜在崩溃/失稳点：SQLite 锁导致 daemon 退出、任务类型白名单阻断新 Agent、单并发 + 固定睡眠导致吞吐不足。它是“能用但不稳”的设计。

---

## 4. `bridge_cli` 的 `dispatch` 参数够用吗？

### 当前实现

`src/bridge_cli.py:218-256` 的 `cmd_dispatch()` 只接受：

```text
bridge_cli.py dispatch <adapter_name> <task_type> [prompt]
```

内部生成 `payload = {'prompt': prompt_text}`，硬编码 `timeout_sec=300`，然后调用 `executor.dispatch_and_wait(adapter, task_type, payload)`。

### 最坏假设下的表现

| 维度 | 结果 |
|------|------|
| **路由能力** | 必须显式指定 `adapter_name`，没有按 `task_type` 自动路由；10 个 Agent 时用户/脚本要记住每个 adapter 名字和能力。 |
| **payload 能力** | 只能传一个字符串 prompt，无法传 `feature_id`、`diff`、`context`、`priority`、`max_retries` 等结构化字段。 |
| **优先级 / 超时** | 无 `--priority`，无 `--timeout`，无 `--work-dir`；所有任务都走默认 300s、`priority=0`。 |
| **批量 / 协调** | 只能单任务派发，无法一次派发多个 Agent、无法指定工作流链、无法做 map-reduce。 |
| **扩展性** | 每新增一个 Agent，CLI 调用方就要硬编码新 adapter 名；`bridge_cli` 本身不感知任何 AgentRegistry 变化。 |

### 结论

**远远不够。** 当前 `dispatch` 只是一个“手动点名执行”的最小接口，无法支撑 10 个 Agent 的自动路由、优先级调度、批量协作和动态发现。

---

## 汇总

| 组件 | 3→10 Agent 下会不会崩溃/失效 | 核心瓶颈 | 风险等级 |
|------|------------------------------|----------|----------|
| `TASK_ADAPTER_MAP` | 不会爆，但新 Agent 无法被路由 | 写死 1:1 映射、封闭 Enum、无能力匹配 | 🔴 高 |
| `pipeline_executor._cli_endpoints` | 字典能装，但注册和调度链路未就绪 | 无自动发现、无元数据、单并发 daemon | 🟠 中高 |
| `message_queue` 轮询 | 不会瞬间崩溃，但可能因锁/白名单/单并发失稳 | busy-wait、无 `busy_timeout`、任务类型白名单、队列深度统计失真 | 🟠 中高 |
| `bridge_cli dispatch` | 明显不够 | 参数过少、无路由/优先级/批量/结构化 payload | 🔴 高 |

---

## 必须做的改动（反方案）

1. **统一 `AgentRegistry`**  
   用配置/代码声明每个 Agent 的 `name`、`capabilities`、`cli_path`、`cost`、`max_workers`、`fallback_chain`，让 `TASK_ADAPTER_MAP`、`DEFAULT_ENDPOINTS`、`VALID_TASK_TYPES` 全部从注册表派生。

2. **任务类型动态化**  
   把 `TaskType` Enum、`VALID_TASK_TYPES` tuple 改为注册表驱动的 schema；新增 Agent 只需注册新 capability，不用改三处白名单。

3. **路由表升级为能力匹配器**  
   从“任务 → 唯一 adapter”改为“任务 → 候选 adapter 集合”，结合负载、成本、历史成功率做选择。

4. **轮询机制事件化**  
   - 设置 `PRAGMA busy_timeout` 并复用连接/连接池；  
   - 用 long-polling 或轻量 pub/sub 替代固定 sleep；  
   - 支持 per-adapter 多实例并发消费；  
   - `agent_queue_depth()` 改为真实读取 DB 中 `queued` 数量。

5. **`bridge_cli dispatch` 增强**  
   支持 `bridge_cli dispatch --task-type <t> --feature-id <id> --priority <p> --timeout <s> --work-dir <dir> --payload '<json>'`，并新增 `route` 自动选择 adapter、`batch` 批量派发。

6. **端点元数据与自动注册**  
   `PipelineExecutor` 启动时根据 `AgentRegistry` 自动注册全部 endpoint，并校验 `TASK_ADAPTER_MAP` 中的 adapter 是否都有对应 CLI endpoint。

---

> 下一篇（2/3）建议聚焦：Phase / Workflow 在 Brownfield ↔ Greenfield 切换下的复用性。
