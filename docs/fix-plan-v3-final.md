# Multi-Agent Pipeline 修复方案 v3.0（对抗审查后终版）

> 基于：21项诊断 + 8点对抗 + 3轮辩论 + Qwen裁判 
> 核心变化：去除shim过渡、DispatchStrategy降级、Phase 5缩范围、verify状态机硬编码

---

## 一、修复原则（从辩论中提炼）

1. **不分阶段共存**：一次commit只改一个模块+全测试通过。不引入shim/过渡期/双真相源
2. **策略不替换硬映射**：DispatchStrategy记录数据+给出建议，不改变路由决策
3. **安全独立立项**：沙箱安全问题不在当前方案范围内
4. **状态机在代码里**：verify流转逻辑硬编码到phase_checks，不依赖外部协议

---

## 二、Phase 1：统一注册表 + 迁移（解决10项诊断）

### 2.1 新建 `registry.py`

```python
# 纯数据模块。零imports项目模块。单向依赖。
class AgentDef: name/capabilities/cli_path/cli_command/env_vars
class PhaseDef: name/check_func/requires_evidence
class TaskTypeDef: name/default_agent

REGISTRY = Registry()  # 全局只读单例
REGISTRY.mark_ready()  # 所有注册完成后显式标记
```

### 2.2 分步迁移计划

| 步骤 | 改哪个文件 | 测试范围 | 验收 |
|------|-----------|---------|------|
| 1 | 新建 `registry.py` | 自身无依赖 | import通过 |
| 2 | `system_constraint.py` → 从REGISTRY读 | `tests/test_system_constraint.py` | 全部通过 |
| 3 | `adapters.py` → 从REGISTRY读 | `tests/test_adapters.py` | 全部通过 |
| 4 | `pipeline_executor.py` → 端点从REGISTRY读 | `tests/test_pipeline_executor.py` | 全部通过 |
| 5 | `config.py` → Phase从REGISTRY读 | `tests/test_config.py` | 全部通过 |
| 6 | `phase_checks.py` → Phase列表从REGISTRY读 | `tests/test_phase_checks.py` | 全部通过 |
| 7 | `message_queue.py` → task_type从REGISTRY读 | `tests/test_message_queue.py` | 全部通过 |
| 8 | `models.py/workflow_registry.py` → 去掉重复Phase定义 | 全量回归 | 1235 tests |
| 9 | 删除旧常量（ADAPTER_REGISTRY等） | 全量回归 | 1235 tests |
| 10 | 废弃 `config_loader.py` | 全量回归 | prompt_cache迁移到PipelineConfig |

### 2.3 风险控制

- **注册表导入时序**：REGISTRY在`__init__.py`中优先import。各模块在需要时查询，不在import时查询。启动脚本显式调用`REGISTRY.mark_ready()`。
- **旧常量清理**：每步迁移后在代码中标注`# REMOVED: ADAPTER_REGISTRY → REGISTRY.agents`。全部迁移完成后批量删除。

---

## 三、Phase 2：统一入口 + argparse + 健康检查（解决4项诊断）

### 3.1 bridge_cli迁移到argparse

```bash
python bridge_cli.py dispatch claude-code --task-type code \
    --prompt "..." --timeout 900 --stream --feature-id F001
```

### 3.2 pipeline.py命令代理

bridge_cli增加9个子命令：`init/advance/status/resume/rollback/approve/mark-tests/check/reset-circuit`

### 3.3 dispatch前健康检查

```python
def check_endpoint_availability(adapter_name):
    # 1. CLI路径存在
    # 2. --version 可执行
    # 3. curl测试API Key有效性
    # 失败 → 返回人类可读修复指引
```

### 3.4 超时从环境变量读取

`PIPELINE_DISPATCH_TIMEOUT`默认600s。已实现。

---

## 四、Phase 3：流程约束强化（解决3项诊断）

### 4.1 accept强制验证 + verify状态机

`phase_checks.py`增加verify状态流转（硬编码）：

```python
# feature新增verify_state字段: pending/verifying/verified/verify_failed

def check_accept(feature):
    if feature.schema_version >= 2:
        if feature.verify_state != "verified":
            return {"passed": False, "reason": "verify未完成"}
    # schema_version < 2 的旧数据兼容放行
```

### 4.2 verify_record

独立可选顶层字段。新feature(version>=2)必须附带。旧feature不强制。

### 4.3 MQ路径

保留两条执行路径（同步/异步），各自独立。同步路径也写dispatch_history表（标注exec_mode='sync'）。

---

## 五、Phase 4：智能化（解决2项诊断）

### 5.1 DispatchStrategy降级为建议层

```python
class DispatchStrategy:
    def suggest(self, task_type) -> Optional[str]:
        """返回建议的agent名，或None表示无建议"""
        if dispatch_history.total_count < 100:
            return None  # 冷启动：不做建议
        return max(candidates, key=score)
```

不替换TASK_ADAPTER_MAP。Hermes可参考建议但硬映射是最终裁决。dispatch_history纯记录，不影响路由。

### 5.2 suggestion_engine增强

补充brownfield阶段映射。增加verify检查提示。

---

## 六、Phase 5：通用性（解决1项诊断）

### 6.1 严格缩范围

仅做：`ProjectProfile.source_extensions` → 参数化`phase_checks`的文件匹配。

不做：沙箱白名单扩展、编译器放行、多语言test runner。这些推后至独立项目。

### 6.2 实现

```python
class ProjectProfile:
    source_extensions: list[str]  # [".py"] 或 [".cpp",".h"]
    test_patterns: list[str]      # ["test_*.py"] 或 ["*_test.cpp"]

# phase_checks.py
def check_develop(project, profile):
    for ext in profile.source_extensions:
        files = list(src_dir.rglob(f"*{ext}"))
```

---

## 七、Phase 6：科学性（解决1项诊断）

### 7.1 thresholds.yaml

所有硬编码阈值迁移到YAML文件。启动时读取一次。不实现热加载。修改后重启生效。

### 7.2 LLM-as-Judge校准

50个标注样本×Cohen's Kappa。不做"持续任务"——作为可验收里程碑。

---

## 七、Phase 7：辩论协议层（解决 Agent 零上下文协作问题）

### 7.1 问题

每次 `dispatch` = 新进程 = 零记忆。Agent 不知道这是辩论的第几轮、上轮谁说了什么、背景文件在哪。唯一的信息桥梁是 Hermes 手动写的辩护摘要——这在学术界属于 **No-Interaction (NI) baseline**（Marandi, 2026），是最差的 Agent 协作模式。

### 7.2 方案：Shared Context File + 预算停止

核心思路：**把"人脑记上下文→手动拼prompt→dispatch"变成"文件存上下文→自动拼prompt→dispatch"**。

```
辩论 session = 一个共享的 markdown 文件，记录全部轮次

debate-session-001.md:
  ┌─ 第1轮 ─┐
  [Claude 攻击]  8个对抗点...
  [Hermes 辩护]  逐点回应...
  ├─ 第2轮 ─┤
  [Claude 再挑战]  深挖漏洞...
  [Hermes 再辩]  修正方案...
  ├─ 第3轮 ─┐
  [Qwen 裁判]  最终判决...
```

每次 dispatch，Agent 的 prompt = "这是辩论 session-001 的**完整记录**。你当前在第N轮，角色是X。请回应。"

### 7.3 新增模块 `src/debate/`

```
src/debate/
├── session.py        # 管理辩论 session 的创建/读写/状态
│                     # session = 唯一 ID + 背景文件列表 + 角色分配 + 完整对话历史
│
├── context.py        # 构建带完整上下文的 dispatch prompt
│                     # 输入: session对象 + 当前轮次 + Agent角色
│                     # 输出: 完整 prompt（含背景文件内容 + 所有历史轮次）
│
├── protocols.py      # 三种协议
│                     # NI:    No-Interaction（单Agent独立，fallback）
│                     # MORE:  Multi-Advocate One-Round（并行，1轮裁判）
│                     # SAMRE: Single-Advocate Multi-Round（串行，预算停止）
│
└── convergence.py    # 预算停止检测
                      # 当评分差距 < 5% 或连续2轮无变化 → 自动终止
```

### 7.4 bridge_cli debate 子命令

```bash
# 启动一场 3 轮辩论（SAMRE 协议）
python bridge_cli.py debate \
    --session debate-001 \
    --protocol SAMRE \
    --attacker claude-code \
    --defender hermes \
    --judge qwen-code \
    --background fix-plan-v3.md pipeline-diagnosis-final.md \
    --max-rounds 3 \
    --output docs/debate-result.md
```

**执行流程：**
1. `session.py` 创建 `debate-001.md`，写入背景文件内容
2. 第1轮：dispatch Claude（prompt = 背景 + "你是攻击方，第1轮"）
3. Claude 输出追加到 session 文件
4. 第2轮：dispatch Claude（prompt = 背景 + 第1轮完整记录 + "第2轮再挑战"）
5. 重复至 `max-rounds` 或 `convergence.py` 判定停止
6. 最终轮：dispatch Qwen（prompt = 全部记录 + "你是裁判，给出最终判决"）

### 7.5 协议选择指南

| 场景 | 协议 | Agent数 | 轮次 | 停止策略 |
|------|------|--------|------|---------|
| 快速 code review | MORE | k=2 并行 | 1轮 | 裁判即判 |
| 架构方案对抗 | SAMRE | 1串行 | ≤3轮 | 预算停止（gap<5%停止） |
| 简单任务 | NI | 1 | 0轮辩论 | 直接输出 |

---

## 八、P0不再包含（辩论后确认推迟）

| 推迟项 | 原因 | 后续 |
|--------|------|------|
| 沙箱安全 | 辩论确认python/npm仍是高危 | 独立安全审计项目 |
| 跨语言test runner | 辩论确认无runner抽象 | 独立ProjectProfile扩展项目 |
| DispatchStrategy替换路由 | 辩论确认冷启动/数据不充分 | 降级为建议层 |
| shim过渡期 | 辩论确认双真相源自相矛盾 | 改为分步迁移+commit后删除 |

---

## 九、实施计划

| Phase | 周 | Feature数 | 解决诊断项 |
|-------|-----|----------|-----------|
| P1 统一注册表 | 1-2 | 10步 | A1-A5, B1, C2, D1-D3 |
| P2 入口+健壮性 | 1 | 4 | A3, C1, C4, D1 |
| P3 流程约束 | 1 | 3 | B1-B3 |
| P4 智能化 | 1 | 2 | B2, B4 |
| P5 通用性 | 1 | 1 | E1(E2/E3推迟) |
| P6 科学性 | 持续 | 1 | E1 |
| P7 辩论协议 | 1 | 4模块+1子命令 | 新增(#22: Agent无会话上下文协作) |

---

## 十、全局架构终态

```
bridge_cli (argparse, 统一入口)
├── dispatch <agent> --task-type <type> --prompt "..."  # 单任务派发
├── debate   --session <id> --protocol SAMRE ...        # 多轮辩论
├── init / advance / status / resume / ...              # pipeline管理
│
├── src/registry.py          # 统一注册表（唯一真相源）
├── src/debate/              # 辩论协议层（Phase 7新增）
│   ├── session.py
│   ├── context.py
│   ├── protocols.py
│   └── convergence.py
├── src/dispatch_strategy.py # 智能建议层（降级模式）
├── src/pipeline_executor.py # 执行引擎
├── src/system_constraint.py # 硬约束层（最终裁决）
├── src/phase_checks.py      # verify状态机+accept验证
└── src/state_store.py        # dispatch_history + verify_record
```

---

## 十一、执行前提（Qwen裁判要求的4项修正）

1. ✅ 不设shim→分步迁移（P1的10步计划）
2. ✅ DispatchStrategy降级→建议层（Phase 4）
3. ✅ Phase 5缩范围→只做source_extensions（Phase 5）
4. ✅ verify状态机硬编码→phase_checks（Phase 3）
