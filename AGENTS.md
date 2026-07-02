# AGENTS.md — Agent 配置与分工

multi-agent-pipeline 通过 `src/registry.py` 统一注册 Agent。所有 Agent 均为本地 CLI 程序，默认在 `AGENT_MOCK=true` 下运行，不会触发真实调用。

---

## 已注册 Agent

| Agent | 能力 | 默认 CLI | 命令模板 | 环境变量 |
|-------|------|----------|----------|----------|
| `claude-code` | `code`, `adversarial` | `claude.cmd` | `-p {prompt}` | `CLAUDE_CODE_SIMPLE=1` |
| `codewhale` | `review` | `codewhale-tui.exe` | `exec --auto {prompt}` | `AGENT_MOCK=false` |
| `qwen-code` | `test`, `doc`, `e2e`, `inspector` | `qwen.cmd` | `prompt {prompt}` | `QWEN_CODE_SUPPRESS_YOLO_WARNING=1` |

### Agent 分工

- **claude-code**：负责编码实现与对抗审查中的挑战方。
- **codewhale**：负责代码审查与质量把关。
- **qwen-code**：负责测试、文档、端到端验证与独立审计支持。
- **Hermes**：统筹者，处理 `orchestrate`、`deploy`、`analyze` 等协调类任务，不直接执行代码。

---

## CLI 路径解析顺序

`src/registry.py` 中的 `_resolve_cli_path` 按以下优先级查找 Agent CLI：

1. 环境变量 `AGENT_CLI_PATH_<AGENT_NAME>`（推荐）
2. `config.py` 中的 `agent_cli_paths` 配置
3. registry 中的 `cli_path` fallback
4. 系统 `PATH` 中的同名可执行文件

例如覆盖 claude-code 路径：

```powershell
$env:AGENT_CLI_PATH_CLAUDE_CODE = "C:\Tools\claude.cmd"
```

---

## AGENT_MOCK 模式

默认 `AGENT_MOCK=true`，`src/adapters.py` 会在 `_execute_cli` 中短路真实 subprocess 调用，返回模拟输出。该模式用于：

- 无 Agent 环境下的本地测试
- CI / 快速回归验证
- 安全地体验完整 pipeline 流程

要启用真实 Agent 调用，在 `scripts\env.ps1` 中设置：

```powershell
$env:AGENT_MOCK = "false"
```

并确保至少一个 Agent CLI 已安装且可访问。

---

## 健康检查

检查单个 Agent 是否就绪：

```powershell
python src\bridge_cli.py dispatch --adapter claude-code --task-type code --prompt "hello"
```

在 mock 模式下，上述命令会立即返回成功，无需真实 CLI。

检查 Hermes 是否有权执行某类任务：

```powershell
python src\bridge_cli.py check-hermes --task-type code
python src\bridge_cli.py check-hermes --task-type review
```

---

## 新增 Agent

如需注册新的本地 Agent：

1. 在 `src/registry.py` 中调用 `REGISTRY.register_agent(AgentDef(...))`。
2. 如需覆盖路径，在 `scripts\env.ps1` 中设置 `AGENT_CLI_PATH_<NAME>`。
3. 在 `src/system_constraint.py` 或任务类型中配置路由规则。

注意：不建议在代码中硬编码 Windows 绝对路径。

---

## 相关文档

- `docs/superpowers/runbooks/agent-setup.md` — Agent CLI 安装手册
- `DEPLOY.md` — Windows 部署总览
- `src/registry.py` — Agent/Phase/TaskType 注册表定义
