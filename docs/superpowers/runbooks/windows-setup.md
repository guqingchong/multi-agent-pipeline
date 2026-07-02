# Windows 部署手册

本手册面向需要在全新 Windows 机器上部署 multi-agent-pipeline 的用户。按本手册操作，可在 30 分钟内跑通 `init → advance → status`。

---

## 前置条件

| 项目 | 最低要求 | 验证命令 |
|------|----------|----------|
| 操作系统 | Windows 10/11 | `winver` |
| Python | 3.11+ | `python --version` |
| PowerShell | 7+ | `$PSVersionTable.PSVersion` |
| Git | 任意版本 | `git --version` |

---

## 步骤 1：安装 Python

1. 访问 https://www.python.org/downloads/windows/
2. 下载 Python 3.11+ 安装包。
3. 运行安装程序，勾选 **Add Python to PATH** 和 **Use admin privileges when installing py.exe**。
4. 点击 **Install Now**。
5. 验证：

```powershell
python --version
```

---

## 步骤 2：安装 PowerShell 7

1. 访问 https://github.com/PowerShell/PowerShell/releases
2. 下载 `PowerShell-7.x.x-win-x64.msi`。
3. 运行安装程序，按默认选项完成安装。
4. 验证：

```powershell
pwsh -Command "$PSVersionTable.PSVersion"
```

后续所有命令均在 **PowerShell 7** 中执行。

---

## 步骤 3：安装 Git

1. 访问 https://git-scm.com/download/win
2. 下载并运行安装程序，按默认选项完成。
3. 验证：

```powershell
git --version
```

---

## 步骤 4：克隆项目

```powershell
$ProjectDir = "C:\path\to\multi-agent-pipeline"
git clone <repository-url> $ProjectDir
Set-Location $ProjectDir
```

---

## 步骤 5：创建虚拟环境并安装依赖

```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

---

## 步骤 6：配置环境变量

```powershell
Copy-Item scripts\env.example.ps1 scripts\env.ps1
notepad scripts\env.ps1
```

至少设置以下两项：

```powershell
$env:MULTI_AGENT_PIPELINE_BASE_DIR = "C:\path\to\multi-agent-pipeline"
$env:AGENT_MOCK = "true"
```

保存并关闭记事本。

---

## 步骤 7：启动系统

```powershell
. .\scripts\start-windows.ps1
```

看到 `Registry ready: phases: 19 agents: 3` 类似的输出，即表示启动成功。

---

## 步骤 8：跑通第一个项目

保持 PowerShell 在项目根目录，执行：

```powershell
. .\scripts\env.ps1
python src\pipeline.py init hello-world --description "第一个演示项目" --stack python
python src\pipeline.py check hello-world
python src\pipeline.py advance hello-world
python src\pipeline.py status hello-world
```

预期 `advance` 会从 `init` 推进到 `prd`（greenfield 模式）。

---

## 步骤 9：运行测试

```powershell
. .\scripts\env.ps1
pytest tests/ -q
```

---

## 步骤 10：启动 API（可选）

在另一个 PowerShell 7 窗口中执行：

```powershell
Set-Location C:\path\to\multi-agent-pipeline
. .\scripts\start-api.ps1
```

访问 http://127.0.0.1:8000/health 验证服务运行。

---

## 故障排查

| 问题 | 可能原因 | 解决 |
|------|----------|------|
| `python` 命令不存在 | 未添加到 PATH | 重新安装 Python 并勾选 Add to PATH |
| 无法运行 `.ps1` | 执行策略限制 | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `pip install` 失败 | pip 过旧或网络问题 | `python -m pip install --upgrade pip` |
| `Registry readiness check failed` | PYTHONPATH 或依赖缺失 | 确认已激活 venv 并安装 requirements |
| `advance` 被阻塞 | phase check 未通过 | 按提示补充文档或文件，或检查 `config/thresholds.yaml` |

---

## 下一步

- 安装真实 Agent CLI：`docs/superpowers/runbooks/agent-setup.md`
- 了解核心命令：`README.md`
- 查看 Windows 部署总览：`DEPLOY.md`
