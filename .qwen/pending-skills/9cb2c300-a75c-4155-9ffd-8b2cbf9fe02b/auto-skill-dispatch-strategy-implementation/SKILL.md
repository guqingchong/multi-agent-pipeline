---
name: dispatch-strategy-implementation
description: 实现调度策略建议层，基于历史性能数据智能推荐最优Agent，冷启动返回None，有数据时返回得分最高Agent名
source: auto-skill
extracted_at: '2026-07-02T11:21:30.663Z'
---

# 调度策略建议层实现方法

## 概述
实现了一个调度策略建议层（DispatchStrategy），基于历史性能数据智能推荐最优 Agent，冷启动返回 None，有数据时返回得分最高的 Agent 名。该策略不替换 TASK_ADAPTER_MAP，仅提供建议，与系统约束层集成。

## 实现步骤

### 1. 创建调度策略类 (src/dispatch_strategy.py)
- 实现 `DispatchStrategy` 类，包含性能数据管理和 Agent 选择逻辑
- 设计 `PerformanceRecord` 数据类存储性能指标（得分、成功率、响应时间等）
- 实现持久化功能，将历史性能数据保存到 JSON 文件

### 2. 实现核心建议方法
- `suggest(task_type)` 方法：冷启动时返回 None，有数据时返回该任务类型得分最高的 Agent
- `record_performance()` 方法：记录 Agent 执行任务的性能数据
- `get_top_agents()` 方法：获取指定任务类型得分最高的前 N 个 Agent

### 3. 集成到现有系统
- 更新 `suggestion_engine.py` 文件，添加 brownfield 阶段的映射
- 保持与 `system_constraint` 的兼容性，建议需经约束层校验
- 确保不替换现有的 `TASK_ADAPTER_MAP`，仅作为建议层存在

### 4. 添加中文注释和支持功能
- 为所有方法和类添加详细的中文注释
- 提供便捷的全局函数接口
- 实现历史数据管理功能（清空、查询支持的任务类型等）

## 关键特性
- 冷启动智能：初始无数据时返回 None，避免错误建议
- 历史感知：基于实际性能数据选择最佳 Agent
- 非侵入性：不修改现有路由机制，仅提供建议
- 持久化：性能数据保存到文件，跨会话可用
- 兼容性：与现有系统架构无缝集成