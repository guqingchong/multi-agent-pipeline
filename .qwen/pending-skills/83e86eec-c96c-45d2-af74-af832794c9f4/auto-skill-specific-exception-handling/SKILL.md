---
name: specific-exception-handling
description: 将代码中的通用异常处理（except Exception:）替换为更具体的异常类型
source: auto-skill
extracted_at: '2026-07-02T04:06:19.435Z'
---

# 将通用异常处理替换为具体异常类型

## 概述
此技能涵盖了将代码中的通用异常处理 `except Exception:` 替换为更具体的异常类型的系统方法。这提高了代码的健壮性和调试能力。

## 步骤

### 1. 查找目标文件
使用适当的工具查找包含 `except Exception:` 的文件：
```bash
find "path/to/project" -name "*.py" -not -path "*/test*" -not -path "*/tests*" -exec grep -l "except Exception:" {} \;
```

### 2. 分析异常类型需求
根据文件的功能和上下文，确定应该使用的具体异常类型：
- 数据库操作：`except SQLAlchemyError:`
- 向量数据库：`except chromadb.errors.ChromaError:`
- WebSocket连接：`except WebSocketDisconnect:`
- 一般应用逻辑：`except (ValueError, KeyError):`

### 3. 更新导入语句
如果需要特定的异常类，在文件顶部添加相应的导入语句：
```python
from sqlalchemy.exc import SQLAlchemyError
```

### 4. 替换异常处理代码
将通用异常处理替换为具体的异常类型：
```python
# 之前
except Exception:
    pass

# 之后
except SpecificExceptionType:
    pass
```

### 5. 验证修改
确保所有 `except Exception:` 都已被替换，并且代码能够正常工作：
```bash
grep -r "except Exception:" path/to/project/
```

## 注意事项
- 避免修改测试文件
- 确保导入所需的异常类
- 根据上下文选择合适的异常类型
- 测试修改后的代码以确保功能正常