# 辩论第2轮：Hermes 最终辩护

> 针对 Claude Code 第2轮深挖的剩余漏洞，逐点回应

---

## 对抗点1：shim双真相源

**Claude第2轮攻击**：导入循环、测试读旧名掩盖bug、shim无限期拖延。

**Hermes最终辩护**：

接受shim有维护成本。但**不设shim直接切换的风险更大**。双层防护：

1. **导入隔离**：`registry.py`不import任何项目模块。它只定义数据结构（AgentDef/PhaseDef/TaskTypeDef），由各模块import它。`from registry import REGISTRY`是单向的，无循环风险。

2. **shim自动检测**：在CI中增加lint规则：任何文件直接引用旧常量名（`ADAPTER_REGISTRY`、`TASK_ADAPTER_MAP`等）→ CI RED。只有`from registry import REGISTRY`是合法的。shim到期有强制手段。

3. **分步提交（不是一次12文件）**：
   - Commit 1: 新建`registry.py`（纯数据，零依赖）
   - Commit 2-4: 逐模块迁移（每commit只改一个模块+运行该模块的全部测试）
   不是12文件一次性改——这是Claude对我之前描述的误解。我接受分步提交而不是大爆炸。

---

## 对抗点2：DispatchStrategy冷启动

**Claude第2轮攻击**：冷启动无样本、load读队列≠真实负载、约束fallback让策略名存实亡。

**Hermes最终辩护**：

承认冷启动阶段的策略不可信。**引入置信度阈值**：

```python
def select_agent(task_type, feature, context):
    if dispatch_history.count < MIN_SAMPLES:  # 默认每个(task,agent)至少30条记录
        return TASK_ADAPTER_MAP[task_type]     # 冷启动直接走硬映射
    return weighted_score(...)                  # 有足够数据后才启用策略
```

冷启动时不做"随机选择"——直接fallback到硬映射，待数据积累到统计显著后再启用。`load_factor`不在冷启动阶段使用（改用简单队列深度近似即可）。

---

## 对抗点3：collect()阻塞+内容不校验

**Claude第2轮攻击**：10分钟阻塞串行化、Agent success标志可伪造、失败无审计。

**Hermes最终辩护**：

这三点全部接受并修正：

1. **并发而非串行**：`dispatch`返回`task_id`立即返回，不阻塞。另一条goroutine/线程异步`collect`。verify不阻塞主流程。

2. **内容校验**：`auto_verify`不是简单看`success`标志。对于review任务，检查输出是否包含代码行号引用（`grep ":" output`）；对于test任务，解析pytest输出（`re.search(r"(\d+) passed", output)`）。空输出或格式不符=失败。这是启发式但比纯看布尔值强。

3. **失败审计**：增加`verify_attempts`表，记录每次尝试（成功/失败/超时）。`verify_record`只在成功时写入，但`verify_attempts`记录所有尝试，支持重试和回溯。

---

## 对抗点4：_execute_sync MQ路径状态冲突

**Claude第2轮攻击**：push可能需daemon、同步异步状态模型冲突、重复执行。

**Hermes最终辩护**：

接受复杂度代价。**简化方案**：不追求完美统一，接受两种执行路径：

- **同步路径（daemon未运行）**：直接`subprocess.run` → 写结果 → 写verify_record。不经过MQ。
- **异步路径（daemon运行中）**：MQ push → daemon执行 → collect → 写verify_record。

两条路径的行为差异如下：

| 特性 | 同步路径 | 异步路径 |
|------|---------|---------|
| MQ生命周期 | 无 | 有 |
| 超时控制 | subprocess timeout | daemon lease |
| 进度反馈 | 无 | 有(streaming) |

这不是最优但**不会制造虚假状态**。两个路径各自干净，不试图伪装对方的能力。不强行合并。

---

## 对抗点5：旧数据绕过新标准

**Claude第2轮攻击**：grandfathered旧数据无证通过、无强制约束、schema无版本号。

**Hermes最终辩护**：

接受"旧数据绕过"是真实的妥协。但**务实的选择**：

1. **版本标记而非强制**：`features.json`增加`schema_version: 2`。`check_accept`读取版本号——v1数据降级处理，v2数据强制校验verify_record。

2. **强制约束**：SQLite加CHECK约束：`CHECK ((schema_version=2 AND verify_record IS NOT NULL AND status='passed') OR schema_version=1 OR status!='passed')`。v2数据status=passed必须有verify_record。数据库层面不可绕过。

3. **迁移策略**：旧数据保持v1标记，不强制升级。新feature自动标记v2。渐进式覆盖。

---

## 对抗点6：register_agent上帝对象

**Claude第2轮攻击**：跨层上帝对象、能力推导不清、测试污染、覆盖用户配置。

**Hermes最终辩护**：

接受"上帝对象"批评。修正：**注册表是只读发布，不是主动注入**。

```python
# 注册时只存数据，不主动修改任何模块
REGISTRY.register(AgentDef(...))

# 各模块在需要时从REGISTRY查询——不预先注入
# system_constraint.py
def route_task(task_type):
    candidates = [a for a in REGISTRY.agents if task_type in a.capabilities]
    if len(candidates) == 1:
        return candidates[0].name
    return dispatch_strategy.select(candidates)  # 多个匹配时走策略
```

不再有`register_agent()`内部自动同步所有硬编码点。各模块各自查询REGISTRY。REGISTRY回归纯数据角色。

测试隔离用`REGISTRY.fork()`（返回副本），避免污染。

---

## 对抗点7：沙箱规则形同虚设

**Claude第2轮攻击**：-include/-D绕过、make/cmake执行框架、tokenizer复杂。

**Hermes最终辩护**：

接受沙箱规则的局限性。**缩减Phase 5范围**：不把多语言沙箱放在P0。

- Phase 5只做`ProjectProfile.source_extensions` → 参数化phase_checks的文件匹配。
- 多语言沙箱安全推后到独立研究议题。当前仅允许Python生态（pytest/python/npm）。
- 白名单不增加g++/cmake/make等编译命令。

这样Phase 5不引入新的安全风险，纯粹解决"跨语言phase_checks"问题。

---

## 对抗点8：里程碑形式化

**Claude第2轮攻击**：热加载未实现、10样本Kappa无意义、M3模糊。

**Hermes最终辩护**：

接受。三处修正：

1. **thresholds.yaml热加载**：不实现热加载——接受启动时读取一次。改值需重启。简单可靠。验收标准改为："所有阈值从thresholds.yaml读取，修改后重启生效"。

2. **Kappa样本数**：从10→50个标注样本。Cohen's Kappa在50样本下95%置信区间约±0.2（接受范围）。

3. **M3验收标准具体化**：
   - `check_develop`对每个项目返回`passed=True`（能发现源码文件）
   - `check_test`对每个项目返回`passed=True`（能发现测试文件）
   - 每种语言提供1个真实开源项目（非玩具示例）

---

## 总结：从21项诊断到可执行方案的变化

| 原方案 | 第1轮修正 | 第2轮修正 |
|--------|----------|----------|
| 大爆炸切换 | shim共存 | 分步提交+CI lint强关 |
| 无数据策略 | dispatch_history表 | 冷启动走硬映射，30样本后启用 |
| dispatch后假验收 | collect等待 | 异步collect+内容启发式+失败审计 |
| 废弃同步fallback | 保留+MQ包裹 | 两条路径并行，不伪装无法力 |
| 强制新schema | NULL降级 | v2标记+SQLite CHECK约束 |
| 一行注册 | 自动同步 | 只读REGISTRY+各模块查询 |
| 沙箱白名单 | 命令级SandboxRule | 仅参数化phase_checks，不扩展沙箱 |
| 空中楼阁 | 3里程碑 | 50样本Kappa+具体M3标准+接受无热加载 |
