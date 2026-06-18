# AGENTS.md — 协作规则与通信协议

> schema_version: 1
> 日期: 2026-06-18
> 项目: multi-agent-pipeline

## 核心原则

1. **目标对齐**: 所有代码、审核、测试、验收必须与PRD目标、架构设计目标、分阶段任务目标严格对齐
2. **角色分离**: Agent必须严格执行角色分工，禁止越界
3. **流程推进**: Phase推进必须通过前置检查点

## 协作流程

```
Hermes(统筹派发) → Hermes-Research(架构设计) → Claude Code(编码实现)
                                                          ↓
                                              CodeWhale(代码审核)
                                                          ↓
                                              Qwen Code(测试验证)
                                                          ↓
                                              Hermes(验收归拢)
```

## 通信协议

1. **Agent间通信**: 强制走pipeline.py，禁止直接进程通信
2. **文件锁**: 写features.json和progress.md前获取portalocker文件锁
3. **原子写入**: 先写.tmp文件，再os.replace
4. **只读快照**: Agent读取前先复制快照，避免读到半写状态
5. **事件驱动**: Agent不轮询文件，等待pipeline派发信号

## 文件清单

| 文件 | 用途 | Owner | 更新时机 |
|------|------|-------|---------|
| SOUL.md | Agent角色定义 | Hermes | Phase 0 / 角色变更 |
| AGENTS.md | 协作规则 | Hermes | Phase 0 / 规则更新 |
| MEMORY.md | 项目级共享记忆 | Hermes | 每次P1/P2发现后 |
| progress.md | 当前进度日志 | Hermes | 每个feature状态变化 |
| features.json | 验收清单 | Hermes | Phase 2 / feature状态变化 |
| specs/architecture.md | 架构设计 | Hermes-Research | Phase 1 |
| specs/prd.md | 产品需求 | 用户 | Phase 0 |

## 审批分级

| 级别 | 触发条件 | 系统行为 | 超时策略 |
|------|---------|---------|---------|
| 阻塞式 | Phase 1架构设计、Phase 5最终验收 | Agent暂停等待 | 30分钟超时后暂停保存状态 |
| 异步式 | 依赖安装、git push | Agent继续做其他任务 | 2小时超时后跳过 |
| 默认放行式 | 低风险操作 | 通知用户但不等待 | 5分钟后自动放行 |

## 自动升级条件

- P0返修超过3次 → 升级人工
- 预算使用80%告警，100%熔断
- 同一feature连续失败2次 → 升级
- Agent无响应60秒 → 卡死判定
- 回归测试失败率超过30% → 升级
