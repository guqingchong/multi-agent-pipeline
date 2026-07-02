# Agent CLI 安装手册

multi-agent-pipeline 通过调用本地 CLI Agent 完成真实任务。默认注册三个 Agent：`claude-code`、`codewhale`、`qwen-code`。本手册说明如何获取和配置这些 CLI。

---

## 重要说明

- 默认 `AGENT_MOCK=true`，即使不安装任何 Agent，也能完整运行测试和 pipeline 流程。
- 只有在需要真实 AI 调用时，才需要安装本节提到的 CLI。
- 所有路径应通过环境变量覆盖，不要在代码中硬编码 Windows 绝对路径。

---

## claude-code

### 能力

编码实现、对抗审查中的挑战方。

### 安装

claude-code 通常通过 npm 安装：

```powershell
npm install -g @anthropic-ai/claude-code
```

安装完成后，确认：

```powershell
claude.cmd --version
```

### 路径覆盖

如果 `claude.cmd` 不在 PATH 中，在 `scripts\env.ps1` 中设置：

```powershell
$env:AGENT_CLI_PATH_CLAUDE_CODE = "C:\Users\$env:USERNAME\AppData\Roaming\npm\claude.cmd"
```

---

## codewhale

### 能力

代码审查、质量报告。

### 安装

codewhale 以独立可执行文件形式分发。下载后放到项目目录或系统 PATH 中：

```powershell
# 示例：假设 codewhale-tui.exe 已下载到 C:\Tools
$env:AGENT_CLI_PATH_CODEWHALE = "C:\Tools\codewhale-tui.exe"
```

确认可执行：

```powershell
& $env:AGENT_CLI_PATH_CODEWHALE --version
```

### 路径覆盖

```powershell
$env:AGENT_CLI_PATH_CODEWHALE = "C:\Tools\codewhale-tui.exe"
```

---

## qwen-code

### 能力

测试、文档、端到端验证、独立审计支持。

### 安装

qwen-code 通常通过 npm 或专用安装包分发：

```powershell
npm install -g qwen-code
```

安装完成后，确认：

```powershell
qwen.cmd --version
```

### 路径覆盖

```powershell
$env:AGENT_CLI_PATH_QWEN_CODE = "C:\Users\$env:USERNAME\AppData\Roaming\npm\qwen.cmd"
```

---

## 验证所有 Agent

在 mock 模式下验证注册表识别：

```powershell
. .\scripts\env.ps1
python -c "from registry import REGISTRY; print(REGISTRY.list_agents())"
```

预期输出包含 `claude-code`、`codewhale`、`qwen-code`。

检查单个 Agent 健康：

```powershell
python src\bridge_cli.py dispatch --adapter claude-code --task-type code --prompt "hello"
```

在 `AGENT_MOCK=true` 下，该命令会立即返回成功；在 `AGENT_MOCK=false` 下，会真实调用 CLI。

---

## 关闭 Mock 模式

编辑 `scripts\env.ps1`：

```powershell
$env:AGENT_MOCK = "false"
```

重新加载环境变量：

```powershell
. .\scripts\env.ps1
```

此时 `bridge_cli.py dispatch` 会真实调用 Agent CLI，请确保 API key 或本地模型已配置。

---

## 环境变量速查

| 变量 | 说明 |
|------|------|
| `AGENT_CLI_PATH_CLAUDE_CODE` | claude-code CLI 绝对路径 |
| `AGENT_CLI_PATH_CODEWHALE` | codewhale-tui.exe 绝对路径 |
| `AGENT_CLI_PATH_QWEN_CODE` | qwen-code CLI 绝对路径 |
| `AGENT_MOCK` | `true` 短路真实调用，`false` 启用真实调用 |
| `CLAUDE_CODE_SIMPLE` | claude-code 简化模式标志 |
| `QWEN_CODE_SUPPRESS_YOLO_WARNING` | qwen-code 警告抑制标志 |

---

## 相关文档

- `DEPLOY.md` — Windows 部署总览
- `AGENTS.md` — Agent 注册表与分工
- `docs/superpowers/runbooks/windows-setup.md` — Windows 部署手册
