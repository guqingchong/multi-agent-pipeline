# 辩论第1轮：Hermes 辩护

> 针对 Claude Code 的8个对抗点，逐个回应

---

## 对抗点1：大爆炸重构 vs 最小改动

**Claude质疑**：12+文件一次性切换REGISTRY，不是渐进式。

**Hermes辩护**：

承认方案写得激进。修正为**两阶段注册表迁移**：

**阶段1a（shim共存）**：新建`registry.py`，各模块保留旧常量但内部改为从REGISTRY读取。如`adapters.py`：
```python
ADAPTER_REGISTRY = REGISTRY.agents  # 旧名保留，内容来自REGISTRY
```
旧常量作为shim继续导出，所有现有测试不破。

**阶段1b（清理）**：在下一个release周期中逐步移除shim，全部迁移到`REGISTRY`直接访问。

这样保证每次commit测试全绿，不影响现有调用链。

---

## 对抗点2：DispatchStrategy无数据源+与约束层冲突

**Claude质疑**：伪代码有`max(scores).agent` bug，无负载/历史数据源，会被route_task硬拦截。

**Hermes辩护**：

两点全部接受。修正：

1. **BUG修复**：`max(scores, key=scores.get)`而非`max(scores).agent`
2. **数据源**：Phase 4前增加Phase 3.5——在state_store建`dispatch_history`表，每次dispatch记录(agent, task_type, success, latency_ms)。`historical_success`从该表计算。`load_factor`用`message_queue`中该Agent的排队任务数。
3. **约束兼容**：`DispatchStrategy.select_agent()`返回值**不直接用于dispatch**，而是传给`route_task(requested_agent=selected)`做二次校验。如果约束层拒绝，回退到TASK_ADAPTER_MAP兜底。**双重保险**：策略层建议 → 约束层审批。

---

## 对抗点3：dispatch成功≠Agent完成，自动verify会造假

**Claude质疑**：dispatch只入队未等待就写verify_record，制造虚假证据。

**Hermes辩护**：

完全接受。修正为**异步等待完成后再触发verify**：

```python
task_id = dispatch(agent, task_type, payload)
result = transport.collect(task_id, timeout=600)  # 阻塞等待Agent完成
if result.success:
    verify_record = auto_verify(feature_id, result)  # 确认完成后才验证
```

`verify_record`只在`result.success==True`后写入。`_execute_sync`保留作为同步等待路径。废弃的是"裸subprocess绕过MQ"的做法，不是同步等待本身。

---

## 对抗点4：废弃_execute_sync是架构倒退

**Claude质疑**：_execute_sync是可靠fallback，废弃后daemon未运行时dispatch直接失败。

**Hermes辩护**：

接受。修正：**保留_execute_sync，但重构为走MQ路径**：

```python
def _execute_sync(self, ...):
    task_id = self.transport.push(...)  # 先入队记录生命周期
    try:
        return self._run_cli_sync(...)  # 同步执行CLI
    finally:
        self.transport.mark_complete(task_id, result)  # 完成后标记
```

这样同步路径也有完整MQ生命周期，同时保留了fallback可靠性。废弃的是"绕过MQ的裸subprocess"，不是同步执行本身。

---

## 对抗点5：verify_record schema不兼容

**Claude质疑**：state_store用TEXT CHECK枚举，features.json用字符串，新schema会破坏历史数据。

**Hermes辩护**：

接受。修正为**向后兼容的渐进式schema**：

**步骤1**：`verify_record`不作为status的子字段，而是**独立的可选顶层字段**。旧feature无此字段直接跳过验证。
```json
{"id":"F001","status":"passed","verify_record":null}
```

**步骤2**：SQLite加列而非改列：`ALTER TABLE features ADD COLUMN verify_record TEXT`。旧行该列为NULL。

**步骤3**：`phase_checks.accept_check`改为：如果有verify_record则必须校验，如果为NULL则降级为"仅检查status==passed"——兼容历史项目。

新feature没有verify_record不能标passed。旧feature已passed的不受影响。

---

## 对抗点6：一行注册是过度承诺

**Claude质疑**：真实链路里有adapter工厂、CLI endpoint、capabilities反向映射多处硬编码，单点注册不够。

**Hermes辩护**：

部分接受。修正注册表的覆盖范围：

REGISTRY不仅存Agent定义，还需一次注册同步更新：
```python
REGISTRY.register_agent(AgentDef(
    name="security-scanner",
    cli_path="...",
    capabilities=["review","test"],
    cli_command="exec --auto {prompt}",  # ← CLI命令模板也从REGISTRY读取
    env_vars=["SECURITY_API_KEY"],        # ← 环境变量也从REGISTRY读取
))
```

**register_agent()内部自动做三件事**：
1. 注入`TASK_ADAPTER_MAP`（从capabilities推导）
2. 注入`ADAPTER_CAPABILITIES`（反向映射）
3. 注入`DEFAULT_ENDPOINTS`（从cli_path+cli_command+env_vars生成）

保证一次注册，所有硬编码点自动同步。

---

## 对抗点7：沙箱白名单扩大攻击面

**Claude质疑**：放行g++/cmake后Agent可编译执行任意二进制。

**Hermes辩护**：

接受。修正为**命令级沙箱**，不只是白名单：

```python
class SandboxRule:
    command: str      # "g++"
    allowed_args: list[str]  # ["-c", "-o", "-I", "-std="]
    disallow_patterns: list[str]  # ["-o /tmp/*", "-o /etc/*", ";", "|", "`"]
    require_flag: str  # "-fsanitize=address" （强制安全编译参数）
```

白名单放行命令但不放行参数组合：`g++ -c src/*.cpp`✅ 但 `g++ evil.cpp -o /tmp/payload && /tmp/payload`❌（包含`&&`）。

此外：Sandbox规则独立于ProjectProfile。跨语言支持通过`source_extensions`参数化phase_checks，不通过放行命令实现。

---

## 对抗点8：科学性持续任务=空中楼阁

**Claude质疑**：无落地点，会无限期拖延。

**Hermes辩护**：

接受。修正为**可验收里程碑**：

| 里程碑 | 产出 | 验收标准 |
|--------|------|---------|
| M1（Phase 3完成时） | `thresholds.yaml` + 阈值注册模块 | 所有30+阈值从此文件读取；改一个值立即生效 |
| M2（Phase 4完成时） | judge评分表 + 10个标注样本 | 至少跑一次Cohen's Kappa计算 |
| M3（Phase 5完成时） | Python+Cpp+Go各1个项目验证集 | phase_checks对三种语言均运行通过 |

每个里程碑有具体产出和验收标准，不是"持续"模糊任务。

---

## 总结：方案修正要点

| 对抗点 | 状态 | 修正 |
|--------|------|------|
| 1.大爆炸 | ✅修正 | 两阶段shim迁移 |
| 2.无数据源 | ✅修正 | 先建dispatch_history表+约束审批 |
| 3.虚假verify | ✅修正 | collect等待完成后再写 |
| 4.废弃fallback | ✅修正 | 保留_execute_sync，加MQ生命周期 |
| 5.schema不兼容 | ✅修正 | ALTER TABLE加列，NULL降级 |
| 6.一行注册 | ✅修正 | register_agent内部同步所有硬编码点 |
| 7.沙箱攻击 | ✅修正 | 命令级SandboxRule，参数白名单 |
| 8.空中楼阁 | ✅修正 | 3个可验收里程碑 |
