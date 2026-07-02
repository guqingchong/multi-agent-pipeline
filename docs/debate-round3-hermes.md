# 辩论第3轮：Hermes 最终辩护

> 针对 Claude Code 第3轮致命追问的最终回应

---

## 对抗点1: shim与CI lint矛盾 → Claude赢了

**Hermes接受**。CI lint不能同时禁止旧常量又允许shim导出它。

**修正**：不用CI lint作时限保障。改为**shim代码注释标注过期版本**：
```python
# @deprecated v2.1 — remove after 2026-08-01
# 迁移到 from registry import REGISTRY
ADAPTER_REGISTRY = REGISTRY.agents
```
每月release checklist人工检查过期shim。这不是自动化的，但不会自相矛盾。承认：shim有维护成本，没有银弹。

---

## 对抗点2: 600样本才能启用 → Claude赢了

**Hermes接受**。按(task,agent)组合计样本数确实太慢。

**修正**：冷启动阈值改为**全局样本数**而非组合数：
```python
if dispatch_history.total_count < 100:  # 整个系统累计100次dispatch后启用
    return TASK_ADAPTER_MAP[task_type]
```
100次dispatch在活跃项目中约1-2周可达。简化后牺牲了组合粒度，但避免了策略永不开机。

---

## 对抗点3: 异步collect与check_accept状态机 → 无结论

**Hermes提供状态机设计**：

```
状态流转:
pending → [dispatch完成, collect未开始] → verifying
       → [collect成功] → verified
       → [collect失败/超时] → verify_failed (可重试)

check_accept逻辑:
- status==verified → 通过
- status==verifying → 等待(非阻塞轮询，由suggest引擎提示)
- status==verify_failed → 阻止，建议重试
- 无verify状态 → 拒绝(新feature)
```

这不是异步同步的选择题，而是**状态驱动的验收门**。check_accept不阻塞，只看当前状态是什么。

---

## 对抗点4: sync路径数据黑洞 → Claude赢了

**Hermes接受**。同步路径不写dispatch_history确实会让策略层挨饿。

**修正**：同步路径也写dispatch_history表，但标注为`exec_mode='sync'`：
```python
# 同步路径中:
state_store.record_dispatch(agent, task, success, latency, exec_mode='sync')
```
与异步路径共享同一张表，策略计算时两种模式等同对待。简单直接，不引入路径不统一问题。

---

## 对抗点5: schema_version与旧数据兼容 → Claude赢了

**Hermes接受**。SQL ALTER TABLE加列后旧行NULL确实无法同时兼容CHECK约束。

**修正**：不用SQL层面约束，改为**Python层验证**：
```python
def check_accept(feature):
    schema_ver = feature.get('schema_version', 1)
    if schema_ver >= 2:
        assert feature.get('verify_record') is not None
    elif feature.get('status') == 'passed':
        pass  # v1已passed的旧feature兼容放行
```

SQL只存数据，不存约束。验收逻辑在Python里实现。避免ALTER TABLE和CHECK约束间的SQL级矛盾。

---

## 对抗点6: 注册表启动时序 → 无结论

**Hermes接受**。启动时序确实是隐患。

**修正**：REGISTRY增加`is_ready()`标记：
```python
REGISTRY = Registry()
# ... 所有Agent注册完成后显式标记
REGISTRY.mark_ready()

# 各模块查询前检查
def route_task(task_type):
    if not REGISTRY.is_ready():
        raise RuntimeError("Registry not initialized")
```

启动脚本有明确的注册→标记→可查询顺序。漏注册在启动时就报错，不是运行时静默fallback。

---

## 对抗点7: python/npm仍是高危解释器 → Claude赢了

**Hermes接受**。缩范围不等于解决安全问题，python -c和npm run确实可执行任意代码。

**最终立场**：Phase 5不解决沙箱安全问题——这不是Phase 5的目标。沙箱安全是独立的安全审计议题，当前在整个诊断报告里列为P2长期项，不应在P0-P1阶段解决。Phase 5只做一件事：让phase_checks不写死*.py。

**Claude说得对但这不是Phase 5该承担的责任。**

---

## 对抗点8: 跨语言check_test无runner抽象 → Claude赢了

**Hermes接受**。M3验收标准确实假设了check_test能适配C++/Go的测试框架，但方案没有定义如何适配。

**修正**：M3验收标准改为：
```
M3: ProjectProfile支持3种语言的source_extensions参数，
    使check_develop能发现源码文件。
    check_test跨语言适配推迟到后续Phase，
    M3不要求check_test通过C++/Go项目。
```

自降标准。承认跨语言测试runner是未解决的大问题，不在当前方案范围内。

---

## 最终结论

| 对抗点 | 结果 | Hermes最终立场 |
|--------|------|---------------|
| 1. shim | Claude赢了，Hermes接受 | 改为deprecated注释+release checklist清理 |
| 2. 冷启动 | Claude赢了，Hermes接受 | 改为全局100样本阈值 |
| 3. 状态机 | 维持无结论 | 给出明确状态机设计 |
| 4. 数据黑洞 | Claude赢了，Hermes接受 | sync路径也写dispatch_history |
| 5. 旧数据 | Claude赢了，Hermes接受 | 放弃SQL CHECK，用Python验证 |
| 6. 注册表 | 维持无结论 | 增加is_ready()标记+启动检查 |
| 7. 沙箱 | Claude赢了，Hermes不反驳但指出范围外 | Phase 5不解决沙箱安全问题 |
| 8. 里程碑 | Claude赢了，Hermes接受 | M3降标，跨语言test runner推后 |

**6/8对抗点中Hermes接受了Claude的批评并修正了方案。2/8争议实质已澄清为范围边界问题。** 提交Qwen最终裁判。
