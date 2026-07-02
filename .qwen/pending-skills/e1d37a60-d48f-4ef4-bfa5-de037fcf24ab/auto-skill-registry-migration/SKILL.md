---
name: registry-migration
description: 将配置、检查和消息队列相关的定义统一到REGISTRY模块的迁移方法
source: auto-skill
extracted_at: '2026-07-02T10:35:51.161Z'
---

# Registry Migration Skill

## 概述
这是一个将配置、检查和消息队列相关的定义统一到REGISTRY模块的迁移方法。目标是实现单一真相源（single source of truth）的设计原则。

## 迁移步骤

### 1. 准备工作
- 确保REGISTRY模块已存在并包含phases和task_types数据结构
- 检查所有待迁移的模块（config.py, phase_checks.py, message_queue.py）
- 查找相关测试文件以验证更改

### 2. 迁移config.py
- 在config.py中导入REGISTRY模块
- 修改AVAILABLE_MODES字段，从REGISTRY.phases动态读取phase列表
- 使用default_factory lambda函数来避免循环导入问题

```python
# 示例代码变更
try:
    from registry import REGISTRY
except (ModuleNotFoundError, ImportError):
    from src.registry import REGISTRY

# 修改AVAILABLE_MODES
AVAILABLE_MODES: dict = Field(
    default_factory=lambda: {
        "greenfield": {
            "label": "新建项目",
            "phases": [phase for phase in REGISTRY.list_phases() 
                      if phase in ["init", "design", "decompose", "research", "prd", "journey",
                                   "develop", "integrate", "test", "evaluate", "accept", "deploy"]],
            "trigger": "default",
            "description": "从零开始，先设计再开发",
        },
        "brownfield": {
            "label": "存量优化",
            "phases": [phase for phase in REGISTRY.list_phases() 
                      if phase in ["discover", "benchmark", "analyze", "plan",
                                   "execute", "verify", "deliver"]],
            "trigger": "auto",
            "description": "先摸底再对标再优化",
        },
    },
    description="可用模式及对应Phase链。插件化：新增模式只需在此字典加一条。",
)
```

### 3. 更新phase_checks.py
- 导入REGISTRY模块
- 修改CHECK_REGISTRY，基于REGISTRY中的phases来构建检查函数映射
- 确保只注册在REGISTRY中存在的phases

```python
# 示例代码变更
PHASE_TO_CHECK_FUNC = {
    "init": check_init,
    "design": check_design,
    # ... 其他phase到检查函数的映射
}

# 只注册在REGISTRY中存在的phases
CHECK_REGISTRY: Dict[str, CheckFunc] = {
    phase: PHASE_TO_CHECK_FUNC[phase] 
    for phase in REGISTRY.list_phases() 
    if phase in PHASE_TO_CHECK_FUNC
}
```

### 4. 迁移message_queue.py
- 导入REGISTRY模块
- 修改VALID_TASK_TYPES，从REGISTRY.task_types动态获取任务类型列表

```python
# 示例代码变更
# 从REGISTRY获取有效的任务类型
VALID_TASK_TYPES = tuple(REGISTRY.list_task_types())
```

### 5. 更新REGISTRY（如果需要）
- 如果发现缺失的任务类型或phase，需要在REGISTRY中添加它们
- 例如，在本次迁移中添加了'shutdown'任务类型

### 6. 测试验证
- 运行相关测试以确保所有更改都正确工作
- 特别关注之前因硬编码而可能失败的测试
- 确保所有导入和功能都正常工作

## 注意事项
- 使用try-except导入模式处理包/平铺导入差异
- 在使用default_factory时要小心循环导入问题
- 确保在REGISTRY中注册了所有必需的元素，否则会导致运行时错误
- 测试验证是迁移过程中不可缺少的一部分