# Multi-Agent Pipeline

> 多智能体协作流水线系统 — 让 AI Agent 像团队一样协作开发软件。

---

## 项目简介

`multi-agent-pipeline` 是一个多智能体协作框架，支持多个 AI Agent（Claude、Qwen、CodeWhale）按阶段协作完成软件开发任务。系统包含沙箱安全、审批流、熔断降级、状态持久化、性能优化等完整能力。

**核心特性：**
- **Phase 0-6 完整流程**：init → design → decompose → develop → test → accept → deploy
- **多 Agent 适配器**：支持 Claude Code、Qwen Code、CodeWhale 三层架构（适配层 + 解析层 + 容错层）
- **沙箱安全**：5 种安全 Profile（LOCKDOWN / PIPELINE / ASSISTANT / RESEARCH / FREE）
- **熔断降级**：Circuit Breaker + 五级降级策略（green → yellow → orange → red → black）
- **状态持久化**：SQLite 自动 checkpoint，支持 resume 恢复
- **性能优化**：Prompt Cache、上下文压缩、可观测性仪表盘
- **人机审批**：阻塞式 / 异步式 / 默认放行式三级审批

---

## 快速开始（5 分钟上手）

### 前提条件

- Windows 10/11
- Python 3.10 或更高版本
- Git（用于 worktree 功能）

### 1. 克隆项目

```powershell
git clone <你的仓库地址>
cd multi-agent-pipeline
```

### 2. 一键安装依赖

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

> 如果系统提示执行策略限制，上面的命令会自动绕过。你也可以手动以管理员身份运行 `Set-ExecutionPolicy RemoteSigned`。

### 3. 验证环境

```powershell
powershell -ExecutionPolicy Bypass -File verify-runtime.ps1
```

看到所有检查项显示 `[PASS]` 即表示环境就绪。

### 4. 启动应用

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1
```

---

## 常用命令

### 直接运行 pipeline

```powershell
cd src
python pipeline.py --help
```

### 运行测试

```powershell
python -m pytest tests/ -q
```

### 查看状态仪表盘

```powershell
cd src
python pipeline.py status
```

---

## 项目结构

```
multi-agent-pipeline/
├── src/                    # 核心源代码
│   ├── pipeline.py         # 主入口：Phase 状态机
│   ├── phase_checks.py     # Phase 0-6 检查函数
│   ├── phase_flow.py       # Phase 流程编排
│   ├── state_store.py      # SQLite 状态持久化
│   ├── adapters.py         # Agent 适配器（Claude/Qwen/CodeWhale）
│   ├── sandbox.py          # 沙箱安全系统
│   ├── circuit_breaker.py  # 熔断器与降级策略
│   ├── approval.py         # 人机审批系统
│   ├── observability.py    # 可观测性仪表盘
│   ├── performance_optimizer.py  # 性能优化与 Prompt Cache
│   ├── context_manager.py  # 上下文窗口管理
│   ├── prompt_cache.py     # Prompt 缓存
│   ├── worktree.py         # Git worktree 并行开发
│   └── config_loader.py    # YAML 配置读取
├── tests/                  # 单元测试（pytest）
├── config/                 # 配置文件目录
├── docs/                   # 项目文档
├── setup.ps1               # 依赖安装脚本
├── start.ps1               # 启动脚本
├── verify-runtime.ps1      # 环境验证脚本
├── README.md               # 本文件
├── DEPLOY.md               # 详细部署指南
├── AGENTS.md               # Agent 协作规则
└── SOUL.md                 # 架构设计文档
```

---

## 下一步

- **非技术用户**：阅读 `DEPLOY.md` 获取详细部署步骤
- **开发者**：阅读 `AGENTS.md` 了解 Agent 协作规则，`SOUL.md` 了解架构设计
- **配置**：在 `config/` 目录下创建 `pipeline.yaml` 自定义系统参数

---

## 常见问题

**Q: PowerShell 执行策略阻止脚本运行？**  
A: 在脚本前加 `powershell -ExecutionPolicy Bypass -File` 即可临时绕过。

**Q: 安装依赖很慢？**  
A: `setup.ps1` 会自动使用清华 PyPI 镜像加速下载。

**Q: 某些测试失败？**  
A: 先运行 `verify-runtime.ps1` 检查环境，确认 Python 版本和依赖完整性。

---

## 许可证

MIT License
