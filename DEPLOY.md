# DEPLOY.md — Windows 部署指南

> 本文档说明如何在 Windows 本机部署和运行 multi-agent-pipeline 系统。

---

## 环境要求

- Windows 10/11
- Python 3.11+
- PowerShell 7+
- Git

本项目**仅支持 Windows 本地部署**，不依赖 Docker。

---

## 安装步骤

### 1. 确认 Python 已安装

按 `Win + R`，输入 `pwsh`，回车，在 PowerShell 中执行：

```powershell
python --version
```

应看到 `Python 3.11+`。若未安装，请前往 https://www.python.org/downloads 下载安装，并勾选 **Add Python to PATH**。

### 2. 克隆项目

```powershell
git clone <repository-url> C:\path\to\multi-agent-pipeline
Set-Location C:\path\to\multi-agent-pipeline
```

### 3. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

开发或运行质量门禁时，额外安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

### 4. 配置环境变量

```powershell
Copy-Item scripts\env.example.ps1 scripts\env.ps1
```

编辑 `scripts\env.ps1`，确认以下关键项：

```powershell
$env:MULTI_AGENT_PIPELINE_BASE_DIR = "C:\path\to\multi-agent-pipeline"
$env:AGENT_MOCK = "true"
```

`AGENT_MOCK=true` 为默认安全模式，不会调用真实 Agent CLI。

---

## Agent CLI 安装

multi-agent-pipeline 通过本地 CLI Agent 执行真实任务。默认注册的三个 Agent 如下：

| Agent | 能力 | 默认 CLI |
|-------|------|----------|
| `claude-code` | code, adversarial | `claude.cmd` |
| `codewhale` | review | `codewhale-tui.exe` |
| `qwen-code` | test, doc, e2e, inspector | `qwen.cmd` |

安装方式参见 `docs/superpowers/runbooks/agent-setup.md`。

若 Agent 已安装但不在 PATH 中，在 `scripts\env.ps1` 中覆盖路径：

```powershell
$env:AGENT_CLI_PATH_CLAUDE_CODE = "C:\Tools\claude.cmd"
$env:AGENT_CLI_PATH_CODEWHALE = "C:\Tools\codewhale-tui.exe"
$env:AGENT_CLI_PATH_QWEN_CODE = "C:\Tools\qwen.cmd"
```

---

## 启动系统

### 本地启动（推荐）

```powershell
. .\scripts\start-windows.ps1
```

该脚本会：
1. 加载 `scripts\env.ps1` 中的环境变量
2. 创建并激活 `.venv`
3. 安装依赖
4. 检查注册表就绪
5. 打印 `pipeline.py` 与 `bridge_cli.py` 帮助

### 启动 FastAPI 服务（可选）

```powershell
. .\scripts\start-api.ps1
```

服务默认监听 `127.0.0.1:8000`，可通过浏览器或 curl 访问：

- `GET /health`
- `GET /agents`
- `GET /queue/stats`
- `GET /projects/{name}`
- `POST /projects/{name}/advance`

---

## 健康检查

在 `AGENT_MOCK=true` 下验证系统是否就绪：

```powershell
. .\scripts\env.ps1
python src\bridge_cli.py check-hermes --task-type code
```

预期返回 JSON，显示 `hermes_allowed` 与路由信息。

---

## 跑通最小流程

```powershell
. .\scripts\env.ps1
python src\pipeline.py init demo --description "演示项目" --stack python
python src\pipeline.py advance demo
python src\pipeline.py status demo
```

---

## 文件结构

```text
C:\path\to\multi-agent-pipeline\
├── src\                         ← 系统代码
│   ├── pipeline.py               ← CLI 主入口
│   ├── bridge_cli.py             ← Hermes JSON 桥
│   ├── main.py                   ← FastAPI 服务
│   ├── registry.py               ← 统一注册表
│   ├── phase_flow.py             ← 阶段状态机
│   ├── phase_checks.py           ← 阶段检查
│   ├── pipeline_queue.py         ← 统一任务队列
│   ├── adapters.py               ← Agent 适配器
│   ├── state_store.py            ← SQLite 持久化
│   └── ...
├── tests\                        ← 测试代码
├── scripts\                      ← Windows 启动脚本
│   ├── env.example.ps1           ← 环境变量模板
│   ├── start-windows.ps1         ← 本地启动
│   └── start-api.ps1             ← API 服务启动
├── config\                       ← 配置
│   └── thresholds.yaml           ← phase 检查阈值
├── README.md                     ← 使用说明
├── DEPLOY.md                     ← 本文档
├── AGENTS.md                     ← Agent 配置说明
├── requirements.txt              ← Python 依赖
├── requirements-dev.txt          ← 开发/CI 依赖（ruff、mypy、pytest）
```

---

## 故障排查

| 问题 | 现象 | 解决 |
|------|------|------|
| Python 未安装 | `python --version` 报错 | 安装 Python 3.11+ 并勾选 Add to PATH |
| PowerShell 执行策略限制 | 无法运行 `.ps1` | 以管理员运行 `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| 依赖安装失败 | `pip install` 报错 | 升级 pip：`python -m pip install --upgrade pip` |
| 模块导入失败 | `ModuleNotFoundError` | 确认已执行 `. .\scripts\env.ps1` 且位于项目根目录 |
| Agent CLI 找不到 | health check 返回 `ok: false` | 安装 Agent 或在 `env.ps1` 中设置 `AGENT_CLI_PATH_*` |
| 端口被占用 | `start-api.ps1` 启动失败 | 更换端口：`. .\scripts\start-api.ps1 -Port 8080` |

---

## 下一步

- 了解 Agent 安装细节：`docs/superpowers/runbooks/agent-setup.md`
- 查看完整 Windows 部署手册：`docs/superpowers/runbooks/windows-setup.md`
- 查看项目进度：`progress.md`
