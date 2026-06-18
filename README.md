# Multi-Agent Pipeline — AI 团队工作台

> 让多个 AI 像人类团队一样协作完成你的任务。

---

## 一句话说明

**你有一个任务 → 告诉 Hermes（主AI）→ AI 团队自动分工 → 产出结果**

你现在就在使用这个系统。你看到的这个聊天窗口，就是交互界面。

---

## 我能做什么（3个典型场景）

### 场景1：软件开发
你说"帮我写一个爬虫" → Hermes 分配任务 → Claude 设计方案 → Qwen 写代码 → CodeWhale 测试 → 你验收 → 交付代码

### 场景2：文档整理
你说"整理这些文件" → Hermes 分配任务 → AI 分类/摘要/归档 → 你确认 → 完成

### 场景3：数据分析
你说"分析这份数据" → Hermes 分配任务 → AI 清洗/分析/可视化 → 你审阅 → 出报告

---

## 如何使用（真实流程）

### 第一步：发起任务

直接在这个聊天窗口告诉 Hermes 你想做什么：

```
我想开发一个 XXX 系统，需求是...
```

### 第二步：Hermes 响应

Hermes 会：
1. 理解你的需求
2. 制定开发计划（Phase 0-6）
3. 分配任务给其他 AI
4. 告诉你当前进展

### 第三步：AI 团队工作

你不需要操作任何东西。AI 团队会自动：
- Claude 设计架构和 PRD
- Qwen 编写代码
- CodeWhale 审查代码
- Qwen 测试验证

### 第四步：你参与关键节点

在需要人工确认的地方，Hermes 会问你：
- "这个设计方案可以吗？"
- "这个代码逻辑对吗？"
- "测试通过了，可以推进吗？"

### 第五步：交付结果

项目完成后，Hermes 会交付：
- 完整代码
- 测试报告
- 文档

---

## 示例：走一个完整流程

### 你发起任务

```
我想开发一个多Agent协作系统，要求：
1. 支持4个Agent（Hermes统筹、Claude架构、Qwen编码、CodeWhale审核）
2. 按Phase 0-6流程执行
3. 有人机审批节点
4. 有完整的测试和文档
```

### Hermes 响应

```
收到。这是一个 complex 级别项目，预计需要 8 个 Wave。

Phase 0: 初始化项目结构
Phase 1: 设计架构
Phase 2: 分解任务
Phase 3: 编码开发
Phase 4: 测试验证
Phase 5: 验收
Phase 6: 部署交付

现在开始 Wave 1...
```

### 你等待或参与

Hermes 会自动推进，在关键节点问你：

```
Wave 1 完成，产出：
- src/pipeline.py（状态机）
- tests/test_pipeline_state_machine.py（测试）

是否继续 Wave 2？
```

你回复：
```
继续
```

### 最终交付

```
项目完成！

交付物：
- 代码：C:\tmp\multi-agent-pipeline\src\（16个模块）
- 测试：C:\tmp\multi-agent-pipeline\tests\（773个测试）
- 文档：README.md、DEPLOY.md
- 报告：reports/e2e_report.md

项目路径：C:\tmp\multi-agent-pipeline
```

---

## 常用指令

| 指令 | 作用 | 什么时候用 |
|------|------|-----------|
| `继续` | 推进到下一步 | Hermes 问"是否继续"时 |
| `暂停` | 暂停当前任务 | 需要思考或处理其他事情 |
| `回退` | 回退到上一步 | 发现前面有问题 |
| `查看状态` | 看当前进度 | 忘了做到哪了 |
| `查看代码` | 看具体代码 | 想确认代码质量 |
| `修改需求` | 调整需求 | 发现需求有问题 |

---

## 项目文件在哪

**当前项目路径：** `C:\tmp\multi-agent-pipeline`

**你可以直接查看：**
- 代码：`C:\tmp\multi-agent-pipeline\src\`
- 测试：`C:\tmp\multi-agent-pipeline\tests\`
- 文档：`C:\tmp\multi-agent-pipeline\README.md`
- 进度：`C:\tmp\multi-agent-pipeline\progress.md`

**快速打开：**
文件资源管理器地址栏输入 `C:\tmp\multi-agent-pipeline` 回车。

---

## 遇到问题

| 问题 | 解决 |
|------|------|
| 不知道当前进度 | 说"查看状态" |
| 想改需求 | 说"修改需求：..." |
| 觉得代码有问题 | 说"查看代码"或"回退到 develop 阶段" |
| 想重新开始 | 说"重新初始化项目" |
| 系统卡住了 | 说"检查状态"或"暂停" |

---

## 技术说明（给想了解的人）

**系统架构：**
- 你（用户）←→ Hermes（统筹AI）←→ 其他AI（Claude/Qwen/CodeWhale）

**当前交互方式：**
- 你正在用的这个聊天窗口，就是交互界面
- 没有单独的"启动应用"步骤
- 没有 Web UI（当前版本）

**AI 团队分工：**
- Hermes：理解需求、制定计划、分配任务、监督进度、最终交付
- Claude：架构设计、PRD编写、代码审查
- Qwen：编码实现、测试编写、bug修复
- CodeWhale：代码审查、测试验证、质量报告

**开发流程（Phase 0-6）：**
```
init → design → decompose → develop → test → accept → deploy
```

---

**现在开始：** 直接告诉 Hermes 你想做什么任务。
