# "交付即能用"架构设计可行性审查报告

> 审查时间: 2026-06-19
> 审查对象: multi-agent-pipeline 5层架构设计
> 现有模块: 17个src模块 + 测试 + 文档

---

## 一、可行性评估

**综合评级: MEDIUM**

| 维度 | 评级 | 说明 |
|------|------|------|
| 技术可行性 | 中 | 核心模块已存在，但关键集成点缺失 |
| 实现复杂度 | 高 | 17个模块需重构整合，引入新组件 |
| 风险可控性 | 中 | 降级策略已部分实现，但边界情况未覆盖 |
| 时间成本 | 高 | 预计需要2-3个迭代周期完成整合 |

---

## 二、关键技术难点

1. 硬约束'Hermes不能编码/测试/审核'需要运行时强制拦截，但当前sandbox.py仅做命令白名单，未针对特定Agent角色做行为限制。
2. 约束绕过风险：Agent可通过多轮对话逐步诱导突破限制，需要语义级检测而非仅命令级。
3. delegate_task的超时限制是模型层限制，无法通过代码架构消除。自动委派需要异步任务队列+回调机制，当前代码中不存在。
4. Claude/Qwen/CodeWhale是外部模型，无法保证实时可用。fallback_manager.py已实现降级链，但缺少异步任务持久化。
5. 三级审批(approval.py)已实现，但阻塞式审批需要UI/CLI实时交互，当前start.ps1是批处理菜单，无法真正'暂停等待'。
6. 异步审批的2小时超时在实际运行中可能导致状态不一致（Agent已继续执行，用户稍后拒绝）。
7. observability.py提供仪表盘数据聚合，但缺少实时WebSocket推送，'一屏统览'需要前端实现。
8. 当前Dashboard.render_text()输出纯文本，无法满足'一屏统览'的交互需求。
9. 现有模块间存在循环依赖风险：adapters.py导入circuit_breaker.py，circuit_breaker.py又引用adapters.py（通过try/except回退）。
10. pipeline.py同时承担CLI入口、状态机、Phase流转三个职责，违反单一职责原则。
11. context_manager.py的Agentic Search是简单关键词匹配，无法支撑大规模代码库检索。

---

## 三、建议修改或补充

1. 建议将硬约束从'命令白名单'升级为'角色能力矩阵'：在adapters.py中为每个Agent定义allowed_actions={read, design, code, test, review}，调度层根据角色过滤任务。
2. 增加语义级约束检测：在ContextManager中注入'system_capability_guard'层，检测Agent输出是否包含被禁止的行为（如Hermes输出代码块）。
3. 引入异步任务队列(TaskQueue)：基于SQLite持久化，支持submit_task(task_id, agent, callback_phase)和poll_task(task_id)。
4. delegate_task的超时限制无法绕过，但可通过'任务分片+增量交付'策略：将大任务拆分为5分钟可完成的子任务，避免单次超时。
5. 将阻塞式审批与Hermes的conversation loop集成：在关键phase_advance前调用approval.request()，通过Hermes的交互界面等待用户输入。
6. 增加审批状态机持久化：当前approval.py的_records仅存内存，进程重启后丢失，应写入state_store的checkpoints表。
7. 驾驶舱建议分阶段实现：Phase 1用rich库终端仪表盘（已完成），Phase 2用轻量级Web UI（如Gradio/Streamlit），Phase 3用WebSocket实时推送。
8. observability.py的AlertManager应增加持久化通道：将告警写入SQLite，Dashboard启动时加载历史告警。
9. 将pipeline.py拆分为：pipeline_cli.py（CLI入口）、state_machine.py（核心状态机）、phase_orchestrator.py（Phase流转）。
10. 消除循环依赖：将共享类型（AgentResult, CircuitState等）提取到src/types.py或src/models.py。
11. context_manager.py的Agentic Search应接入向量数据库（如chromadb）或至少使用BM25文本检索，替代当前关键词匹配。
12. 增加'交付层'独立模块：当前17个模块中无专门的delivery/deployment模块，DEPLOY.md只是文档。建议创建src/delivery.py处理打包、验证、交付。
13. 增加'配置中心'：当前config_loader.py只读prompt_cache配置，应扩展为全系统配置管理（Agent路由规则、审批阈值、熔断参数）。
14. 增加'健康检查端点'：ResilienceManager应暴露HTTP/CLI接口，供外部监控工具查询系统状态。

---

## 四、实现优先级

- P0-紧急（架构阻塞）：消除模块循环依赖 + 拆分pipeline.py
- P0-紧急：实现异步任务队列（替代delegate_task超时限制）
- P1-高优：角色能力矩阵（硬约束不可绕过）
- P1-高优：审批状态持久化（防止进程重启丢失）
- P2-中优：驾驶舱Web UI（一屏统览）
- P2-中优：Agentic Search向量检索升级
- P3-低优：语义级约束检测（AI辅助）
- P3-低优：配置中心统一化

---

## 五、风险与遗漏

### 风险

1. 循环依赖导致import失败：在特定Python环境或打包时可能触发ImportError，影响系统启动。
2. 审批状态内存丢失：进程崩溃或重启后所有待审批请求丢失，用户可能重复提交。
3. 自动委派超时累积：即使任务分片，多轮委派的总时间仍可能超过用户预期，导致体验下降。
4. 驾驶舱数据延迟：基于轮询的Dashboard无法实时反映Agent状态，在快速迭代场景下信息滞后。
5. 硬约束被提示工程绕过：恶意或意外的用户提示可能诱导Agent突破角色限制，语义检测准确率难以保证100%。
6. 降级链雪崩：Claude→Qwen→CodeWhale全部不可用时，系统进入BLACK状态，但缺少人工接管流程。

### 遗漏

1. 缺少'交付层'代码模块：5层架构中的交付层只有DEPLOY.md文档，无对应src/delivery.py。
2. 缺少'调度层'核心实现：自动委派逻辑散落在fallback_manager.py和adapters.py，无统一调度器。
3. 缺少配置中心：系统参数（超时、阈值、Agent路由）硬编码在各模块中，无法动态调整。
4. 缺少端到端集成测试：tests/目录下只有单元测试，无跨模块集成测试验证5层架构流转。
5. 缺少Agent间通信协议：Claude/Qwen/CodeWhale通过文件系统（features.json, progress.md）间接通信，无直接消息通道。
6. 缺少用户交互协议：阻塞式审批需要定义标准输入格式（如JSON/YAML），当前仅支持自由文本。

---

## 六、结论

该架构设计理念先进（5层分离、硬约束、自动委派、用户门禁、驾驶舱），与现有17个模块的方向一致。

**主要障碍**:
- 模块循环依赖和职责混乱（pipeline.py过重）
- 异步任务队列缺失（无法替代delegate_task超时）
- 硬约束仅实现命令级，未实现角色级
- 审批状态未持久化

**建议路径**:
1. 先做模块重构（P0）：拆分pipeline.py、消除循环依赖、提取共享类型
2. 再做核心机制（P1）：异步队列、角色矩阵、审批持久化
3. 最后做体验优化（P2/P3）：驾驶舱UI、语义检测、配置中心

按此路径，预计可在2-3个迭代周期内达到'交付即能用'状态。
