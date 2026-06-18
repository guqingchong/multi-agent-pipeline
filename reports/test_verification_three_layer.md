# 三层架构设计 — 测试验证报告

> 项目: multi-agent-pipeline
> 日期: 2026-06-19
> 审查范围: specs/three_layer_architecture.md + reports/adversarial_review_three_layer.md + 现有代码
> 现有代码: 18 src 模块, 812 测试, 全部通过

---

## 1. 验收标准可测试性分析

### 1.1 设计文档中的验收标准

| 验收标准 | 位置 | 可测试性 | 问题 |
|----------|------|----------|------|
| 用户说"继续"自动加载并推进 | 9.1 | ⚠️ 部分可测 | 需要外部Agent可用，无法单元测试 |
| 用户说"查看状态"返回200字摘要 | 9.1 | ✅ 可测 | 可Mock SessionLoader + 断言摘要长度 |
| 用户说"让Claude写F014"自动委派 | 9.1 | ⚠️ 部分可测 | 需要Claude Code Adapter可用 |
| 系统提示上下文 < 2000 tokens | 9.1 | ❌ 不可测 | 无token计数器接口，估算因子0.5不准确 |
| Hermes尝试编码→被拦截 | 9.2 | ❌ 不可测 | Hermes是当前进程，无法自我拦截(P0-001) |
| Claude Code尝试架构设计→被拦截 | 9.2 | ✅ 可测 | 可Mock RoleGuard + 断言拦截 |
| 拦截延迟 < 10ms | 9.2 | ✅ 可测 | 纯本地检查，可计时断言 |
| Phase check通过→10秒内自动推进 | 9.3 | ⚠️ 部分可测 | 需要PhaseFlow + check通过条件 |
| Agent超时600s→自动降级 | 9.3 | ❌ 不可测 | 600s超时无法CI测试，Windows无signal.SIGALRM |
| 4个Agent并行→无文件冲突 | 9.3 | ❌ 不可测 | worktree claimed_files未实现重叠检测 |
| 系统崩溃后从checkpoint恢复 | 9.3 | ✅ 可测 | 可模拟崩溃+恢复场景 |

**结论**: 11项验收标准中，4项完全可测，3项部分可测，4项不可测（受P0问题影响）。

---

## 2. 现有代码接口假设验证

### 2.1 设计文档声称"复用"的模块 vs 实际接口

| 设计文档声称 | 现有接口 | 匹配度 | 问题 |
|-------------|---------|--------|------|
| SessionLoader复用 `state_store.StateStore` | StateStore有get_project/get_feature/get_checkpoint等原子操作 | ⚠️ 部分匹配 | 无"加载完整项目状态"接口，需组合多次调用 |
| PhaseEngine复用 `phase_flow.PhaseFlow` | PhaseFlow有current_phase/check/advance | ✅ 匹配 | 但无auto_advance参数，需修改 |
| AgentDispatcher复用 `adapters.py`的Adapter | Adapter有execute()，无can_execute() | ❌ 不匹配 | 需新增can_execute()方法 |
| AgentDispatcher复用 `fallback_manager.FallbackManager` | FallbackManager存在 | ✅ 匹配 | 但无"AgentDispatcher调用"接口 |
| CheckpointSync复用 `state_store.CheckpointRecord` | CheckpointRecord存在 | ✅ 匹配 | 但无文件锁/原子写入功能 |
| ViolationLogger复用 `state_store.AuditLogRecord` | AuditLogRecord只有id/project_id/agent/command/allowed/created_at | ⚠️ 部分匹配 | 缺少被修改文件内容、变更前后快照字段 |
| GoalValidator复用 `phase_checks.py`的check函数 | check函数只检查文件存在性 | ❌ 不匹配 | 无法验证语义（目标对齐、代码实现功能） |
| ContextBuilder复用 `context_manager.ContextManager` | ContextManager有set_layer/get_context，无"注入系统提示"接口 | ❌ 不匹配 | 设计文档假设的接口不存在 |
| SafetyEnforcer复用 `sandbox.py`的Profile | Sandbox有ProfileConfig | ✅ 匹配 | 但无"执行前确认"的自动检查点 |
| RoleGuard复用 `SOUL.md`的ROLE_TASKS | SOUL.md存在但无结构化ROLE_TASKS | ❌ 不匹配 | 需要解析Markdown提取角色定义 |

**结论**: 10个"复用"点中，3个完全匹配，3个部分匹配，4个不匹配。实现需要大量适配代码。

### 2.2 关键接口缺失清单

1. **Adapter.can_execute()** — 设计文档假设存在，实际不存在
2. **ContextManager.inject_system_prompt()** — 设计文档假设存在，实际不存在
3. **StateStore.load_full_project_state()** — 设计文档假设存在，实际不存在
4. **ViolationLogger.record_with_snapshot()** — 设计文档假设存在，实际不存在
5. **ConstraintLayer.validate_task()** — 设计文档假设存在，实际不存在
6. **ConstraintLayer.validate_phase_advance()** — 设计文档假设存在，实际不存在

---

## 3. 测试策略与测试用例

### 3.1 测试策略分层

```
单元测试层（可立即执行）
  ├── 入口层: SessionLoader + IntentParser + ContextBuilder（Mock外部依赖）
  ├── 约束层: RoleGuard + ActionFilter（纯函数，无外部依赖）
  └── 调度层: PhaseEngine.tick()（Mock PhaseFlow + ConstraintLayer）

集成测试层（需要适配代码）
  ├── 入口层→约束层: EntryGate路由 + RoleGuard拦截
  ├── 约束层→调度层: AgentDispatcher + ConstraintLayer验证
  └── 调度层→现有模块: PhaseEngine + PhaseFlow实际交互

E2E测试层（需要外部服务）
  ├── 完整流程: 用户输入→自动推进→Agent委派→结果返回
  └── 故障恢复: 模拟Agent崩溃/超时→自动降级
```

### 3.2 推荐测试用例（按优先级）

#### P0问题检测用例（核心）

| 用例ID | 目标 | 测试内容 | 预期 |
|--------|------|----------|------|
| T-P0-001 | 检测RoleGuard自检悖论 | Hermes进程内调用write_file，RoleGuard是否拦截 | ❌ 无法拦截（验证P0-001） |
| T-P0-002 | 检测自动推进数据损坏 | 创建空features.json，触发PhaseEngine.tick() | Phase不应自动推进（验证P0-002） |
| T-P0-003 | 检测循环依赖 | 同时初始化PhaseEngine和ConstraintLayer | 不应死锁（验证P0-003） |
| T-P0-004 | 检测ViolationLogger事后记录 | Agent调用write_file后RoleGuard拦截 | 文件已被修改，无法回滚（验证P0-004） |
| T-P0-005 | 检测IntentParser延迟 | 模拟LLM兜底解析，计时 | 延迟>200ms，甚至2-5s（验证P0-005） |

#### 单元测试用例（可立即实现）

| 用例ID | 组件 | 测试内容 | 预期 |
|--------|------|----------|------|
| T-U-001 | SessionLoader | 空目录加载 | 返回None/错误，不崩溃 |
| T-U-002 | SessionLoader | 完整项目目录加载 | 正确识别项目标记、加载状态 |
| T-U-003 | IntentParser | 50种典型输入分类 | 规则层匹配准确率>90% |
| T-U-004 | IntentParser | 歧义输入("看看F012") | 返回AMBIGUOUS，不猜测 |
| T-U-005 | RoleGuard | 各Agent执行允许动作 | 全部通过 |
| T-U-006 | RoleGuard | 各Agent执行禁止动作 | 全部拦截，返回明确原因 |
| T-U-007 | ActionFilter | 白名单内动作 | 通过 |
| T-U-008 | ActionFilter | 白名单外动作(delete) | 拦截，需额外审批 |
| T-U-009 | PhaseEngine | check通过+auto_advance=True | 推进到下一Phase |
| T-U-010 | PhaseEngine | check失败+auto_advance=True | 停留当前Phase |
| T-U-011 | PhaseEngine | paused=True | 不推进，返回"已暂停" |
| T-U-012 | AgentDispatcher | 未知任务类型 | 返回错误，不崩溃 |
| T-U-013 | CheckpointSync | 写入→读取→恢复 | 状态一致，不丢失 |

#### 集成测试用例（需要适配层）

| 用例ID | 场景 | 测试内容 | 预期 |
|--------|------|----------|------|
| T-I-001 | EntryGate→RoleGuard | DELEGATE意图路由到约束层 | 正确分发，通过约束检查 |
| T-I-002 | PhaseEngine→PhaseFlow | tick()调用实际PhaseFlow.check() | 使用真实check函数，非Mock |
| T-I-003 | AgentDispatcher→Adapter | dispatch()调用Mock Adapter | 正确传递task参数，收集结果 |
| T-I-004 | TimeoutHandler→Adapter | 模拟超时(缩短到5s) | 触发重试策略 |
| T-I-005 | ViolationLogger→StateStore | 记录违规→查询audit_logs | 记录完整，可查询 |

---

## 4. P0问题测试可发现性验证

### 4.1 P0-001: RoleGuard自检悖论

**测试方法**: 
1. 在Hermes进程内直接调用write_file工具
2. 检查RoleGuard是否被调用
3. 检查文件是否被修改

**预期结果**: 
- RoleGuard不会被调用（因为Hermes不经过AgentDispatcher）
- 文件被修改
- **测试可发现**: ✅ 是，通过单元测试可验证

**测试代码示例**:
```python
def test_roleguard_cannot_constrain_hermes():
    """验证P0-001: RoleGuard无法约束Hermes自身"""
    # Hermes直接调用write_file（模拟）
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("# original")
        path = f.name
    
    # 模拟Hermes直接编码（不经过AgentDispatcher）
    write_file(path, "# modified by Hermes")
    
    # 检查RoleGuard是否拦截
    # 预期: RoleGuard从未被调用，文件已被修改
    assert read_file(path) == "# modified by Hermes"
    # 此测试证明: 约束层无法约束orchestrator自身
```

### 4.2 P0-002: 自动推进数据损坏

**测试方法**:
1. 创建空features.json（满足文件存在性检查）
2. 调用PhaseEngine.tick()
3. 检查是否自动推进

**预期结果**:
- 如果auto_advance=True，PhaseEngine会推进（因为check_init只检查文件存在性）
- **测试可发现**: ✅ 是，通过集成测试可验证

**测试代码示例**:
```python
def test_auto_advance_with_empty_files():
    """验证P0-002: 空文件导致错误推进"""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        # 创建空文件（满足存在性检查）
        (base / "features.json").write_text("{}")
        (base / "SOUL.md").write_text("")
        (base / "AGENTS.md").write_text("")
        (base / "progress.md").write_text("")
        
        flow = PhaseFlow("test", base)
        engine = PhaseEngine("test", base, auto_advance=True)
        result = engine.tick()
        
        # 预期: 空文件不应导致推进
        # 实际: check_init会通过（文件存在），然后推进
        assert not result.can_advance, "空文件不应导致自动推进"
```

### 4.3 P0-003: 循环依赖死锁

**测试方法**:
1. 同时初始化PhaseEngine和ConstraintLayer
2. 检查是否死锁

**预期结果**:
- 如果两者互相引用，可能导致初始化死锁
- **测试可发现**: ✅ 是，通过单元测试可验证（超时检测）

**测试代码示例**:
```python
def test_no_circular_dependency_deadlock():
    """验证P0-003: 初始化不应死锁"""
    import threading
    result = [None]
    
    def init():
        try:
            # 模拟设计文档中的循环依赖
            # PhaseEngine.__init__引用ConstraintLayer
            # ConstraintLayer.GoalValidator需要PhaseFlow
            engine = PhaseEngine("test", Path("/tmp"))
            result[0] = "success"
        except Exception as e:
            result[0] = str(e)
    
    t = threading.Thread(target=init)
    t.start()
    t.join(timeout=5)  # 5秒超时
    
    assert not t.is_alive(), "初始化死锁（P0-003）"
    assert result[0] == "success", f"初始化失败: {result[0]}"
```

### 4.4 P0-004: ViolationLogger事后记录

**测试方法**:
1. 模拟Agent调用write_file（文件已修改）
2. 然后RoleGuard检查并拦截
3. 检查文件是否已损坏

**预期结果**:
- 文件已被修改，无法回滚
- **测试可发现**: ✅ 是，通过集成测试可验证

**测试代码示例**:
```python
def test_violation_logger_post_hoc():
    """验证P0-004: 拦截发生在执行后，无法阻止损害"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("# original content")
        path = f.name
    
    # 模拟: Agent先执行write_file
    write_file(path, "# MALICIOUS CONTENT")
    
    # 然后RoleGuard检查（事后）
    allowed, reason = RoleGuard.check("Claude Code", "code_write")
    # 假设Claude Code无权写此文件
    
    # 文件已被修改，无法恢复
    content = read_file(path)
    assert "MALICIOUS" in content, "P0-004: 损害已发生，拦截无法阻止"
```

### 4.5 P0-005: IntentParser LLM兜底延迟

**测试方法**:
1. 模拟LLM兜底解析（使用sleep模拟延迟）
2. 计时总延迟

**预期结果**:
- 延迟远大于200ms（实际API调用2-5s）
- **测试可发现**: ✅ 是，通过性能测试可验证

**测试代码示例**:
```python
def test_intent_parser_llm_fallback_latency():
    """验证P0-005: LLM兜底延迟不可控"""
    parser = IntentParser()
    
    # 输入无法被规则/模式匹配的语句
    start = time.time()
    intent = parser.parse("Some completely ambiguous statement")
    elapsed = time.time() - start
    
    # 如果使用LLM兜底，延迟会很大
    # 设计文档声称200ms，实际可能2-5s
    assert elapsed < 1.0, f"LLM兜底延迟{elapsed:.2f}s，超出可接受范围"
```

---

## 5. 现有测试覆盖分析

### 5.1 现有812个测试覆盖的模块

| 模块 | 测试文件 | 测试数 | 覆盖功能 |
|------|---------|--------|----------|
| adapters.py | test_adapters.py, test_adapter_tolerance.py, test_qwen_adapter.py | ~150 | Adapter执行、容错、解析 |
| approval.py | test_approval_system.py | ~80 | 三级审批、超时、摘要 |
| circuit_breaker.py | test_circuit_breaker.py | ~60 | 熔断、降级、恢复 |
| context_manager.py | test_context_manager.py | ~90 | 分层压缩、Reinforcement、Search |
| phase_checks.py | test_phase_checks.py | ~70 | 各Phase check函数 |
| phase_flow.py | test_phase_flow.py | ~60 | Phase推进、回退、审批 |
| pipeline.py | test_pipeline_state_machine.py | ~40 | 状态机、命令 |
| state_store.py | test_state_store.py | ~70 | CRUD、checkpoint、恢复 |
| worktree.py | test_worktree.py | ~100 | worktree创建、管理、清理 |
| sandbox.py | test_sandbox.py | ~50 | Profile、命令白名单 |
| observability.py | test_observability.py | ~80 | 监控、告警、日志 |
| prompt_cache | test_prompt_cache*.py | ~120 | 缓存、存储、追踪 |
| performance | test_performance.py | ~80 | 性能优化、压缩 |

### 5.2 现有测试未覆盖的新组件

设计文档新增的15个组件全部**没有测试**:
- entry/session_loader.py, intent_parser.py, context_builder.py, entry_gate.py
- constraint/role_guard.py, action_filter.py, goal_validator.py, safety_enforcer.py, violation_logger.py
- orchestration/phase_engine.py, agent_dispatcher.py, timeout_handler.py, recovery_manager.py, checkpoint_sync.py
- integration.py

**结论**: 需要新增约 **150-200个测试** 覆盖新组件（按现有测试密度估算）。

---

## 6. 测试实施建议

### 6.1 优先级排序

| 优先级 | 测试目标 | 原因 |
|--------|----------|------|
| P0 | 5个P0问题的检测用例 | 致命缺陷，必须验证 |
| P1 | RoleGuard + ActionFilter + PhaseEngine单元测试 | 核心安全+调度功能 |
| P2 | SessionLoader + IntentParser单元测试 | 入口层用户体验 |
| P3 | 集成测试（EntryGate→约束层→调度层） | 端到端流程 |
| P4 | E2E测试（完整用户场景） | 最终验收 |

### 6.2 Mock策略

| 外部依赖 | Mock方法 | 说明 |
|----------|----------|------|
| Claude Code / Qwen Code / CodeWhale | Mock Adapter.execute() | 返回预设AgentResult |
| LLM API | Mock requests.post / sleep | 模拟延迟和返回 |
| StateStore | 使用内存SQLite | 避免文件系统依赖 |
| PhaseFlow | 部分Mock | check()返回预设结果 |
| file system | tempfile | 隔离测试环境 |

### 6.3 CI兼容性

| 问题 | 影响 | 解决方案 |
|------|------|----------|
| 600s超时测试 | CI无法承受 | 缩短超时到5s + 模拟超时 |
| Windows无signal.SIGALRM | TimeoutHandler失效 | 使用threading.Event + 轮询 |
| 外部Agent服务不可用 | 集成测试失败 | 全部使用Mock Adapter |
| 多项目冲突 | SessionLoader测试干扰 | 使用tempdir隔离 |

---

## 7. 核心结论

### 7.1 验收标准可测试性

- **完全可测**: 4/11（拦截延迟、状态恢复、Mock场景下的委派和拦截）
- **部分可测**: 3/11（需要外部服务或复杂Mock）
- **不可测**: 4/11（受P0问题影响：Hermes自我约束、自动推进安全、超时处理、并行冲突）

### 7.2 现有代码支持度

- **接口匹配**: 3/10复用点完全匹配
- **需要适配**: 4/10复用点需要新增接口或修改现有代码
- **无法复用**: 3/10复用点现有代码完全不支持设计文档假设

### 7.3 P0问题可测试性

| P0问题 | 可测试发现 | 推荐测试类型 |
|--------|-----------|-------------|
| P0-001 RoleGuard自检悖论 | ✅ 是 | 单元测试 |
| P0-002 自动推进数据损坏 | ✅ 是 | 集成测试 |
| P0-003 循环依赖死锁 | ✅ 是 | 单元测试（超时检测） |
| P0-004 ViolationLogger事后记录 | ✅ 是 | 集成测试 |
| P0-005 IntentParser延迟 | ✅ 是 | 性能测试 |

**所有5个P0问题都可以通过测试发现**，但需要在实现前编写"失败测试"（TDD方式）来验证设计缺陷。

### 7.4 测试工作量估算

- 新增测试文件: 8-10个
- 新增测试用例: 150-200个
- 预计实施时间: 2-3个Wave（按现有开发速度）
- 风险: 如果设计文档不修改，大量测试会失败（特别是P0问题相关测试）

---

## 8. 建议

1. **先写失败测试，再实现代码**: 对P0问题编写"预期失败"的测试，验证设计缺陷
2. **修改设计文档**: 根据测试结果调整设计，特别是P0问题的缓解方案
3. **分阶段实施**: 先实现入口层（可独立测试），再约束层，最后调度层
4. **增加Mock基础设施**: 创建MockAdapter、MockStateStore等测试工具
5. **CI适配**: 缩短超时、使用tempdir、避免外部依赖

---

*报告完成。基于现有18个src模块、812个通过测试的代码库进行验证。*
