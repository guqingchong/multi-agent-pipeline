# DEPLOY.md — 面向小白的部署指南

> 本文档面向非技术用户，手把手教你把 multi-agent-pipeline 跑起来。不需要懂代码，跟着步骤做就行。

---

## 你需要准备什么

### 1. 一台 Windows 电脑

- Windows 10 或 Windows 11
- 能正常上网

### 2. Python（运行环境）

**检查是否已安装：**

1. 按 `Win + R`，输入 `cmd`，回车打开命令窗口
2. 输入以下命令，回车：

```
python --version
```

- 如果显示 `Python 3.10.x` 或更高版本（如 `3.11`、`3.12`、`3.13`），恭喜，Python 已就绪！
- 如果显示 "找不到命令" 或报错，请先去 https://www.python.org/downloads/ 下载安装 Python 3.10+，安装时勾选 **"Add Python to PATH"**。

### 3. Git（代码管理工具）

**检查是否已安装：**

在刚才的命令窗口里输入：

```
git --version
```

- 如果显示版本号（如 `git version 2.x.x`），Git 已就绪
- 如果报错，请去 https://git-scm.com/download/win 下载安装

---

## 部署步骤（共 3 步）

### 第 1 步：下载项目代码

**方式 A：用 Git 克隆（推荐，方便后续更新）**

1. 打开命令窗口（`Win + R` → `cmd`）
2. 输入以下命令，回车：

```
cd %USERPROFILE%
git clone https://github.com/your-org/multi-agent-pipeline.git
cd multi-agent-pipeline
```

> 注意：如果仓库地址不同，请替换为实际的 Git 地址。

**方式 B：直接下载 ZIP**

1. 在浏览器打开项目页面
2. 点击绿色按钮 `<> Code` → `Download ZIP`
3. 下载后解压到桌面或任意文件夹
4. 记住解压后的文件夹路径（如 `C:\Users\你的用户名\multi-agent-pipeline`）

---

### 第 2 步：安装依赖

1. 打开 PowerShell（在文件夹空白处按住 `Shift + 右键` → `在此处打开 PowerShell 窗口`）
2. 输入以下命令，回车：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

**你会看到什么：**

- 脚本开始检查 Python 版本
- 自动安装需要的 Python 包（如 PyYAML、pytest、rich 等）
- 安装完成后显示 "[PASS] 依赖安装完成"
- 如果某一步失败，会显示 "[FAIL]" 和具体原因

**整个过程大约需要 1-3 分钟**，取决于你的网速。

---

### 第 3 步：验证环境

安装完成后，运行验证脚本确认一切正常：

```powershell
powershell -ExecutionPolicy Bypass -File verify-runtime.ps1
```

**期望结果：** 所有检查项都显示 `[PASS]`，最后一行显示：

```
========================================
验证结果: 全部通过
========================================
```

如果有 `[FAIL]` 项，脚本会告诉你具体缺什么，按提示解决后重新运行即可。

---

## 启动应用

环境验证通过后，就可以启动系统了：

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1
```

启动后，脚本会：
1. 检查环境是否就绪
2. 显示当前项目状态
3. 进入交互式菜单，你可以输入命令操作

**常用菜单选项：**
- `status` — 查看当前状态仪表盘
- `init <项目名>` — 初始化一个新项目
- `check` — 检查当前 Phase 是否满足推进条件
- `advance` — 推进到下一个 Phase
- `help` — 查看所有命令
- `quit` — 退出

---

## 如果遇到问题

### 问题 1：PowerShell 提示 "无法加载脚本，因为在此系统上禁止运行脚本"

**解决：** 在命令前加上 `powershell -ExecutionPolicy Bypass -File`，例如：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

或者永久解决（以管理员身份运行 PowerShell，输入）：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

### 问题 2：setup.ps1 安装依赖时卡住或报错

**可能原因：**
- 网络问题导致下载失败
- pip 版本过旧

**解决步骤：**

1. 先更新 pip：
```powershell
python -m pip install --upgrade pip
```

2. 重新运行 setup.ps1

3. 如果还是失败，尝试手动安装核心依赖：
```powershell
pip install pyyaml pytest rich
```

---

### 问题 3：verify-runtime.ps1 显示某些检查失败

**常见原因和对策：**

| 失败项 | 可能原因 | 解决办法 |
|--------|---------|---------|
| Python 版本 | 版本低于 3.10 | 去 python.org 下载安装新版 |
| 依赖包缺失 | 安装中断 | 重新运行 setup.ps1 |
| Git 未安装 | 系统没有 Git | 安装 Git for Windows |
| 模块导入失败 | 路径问题 | 确认在 `multi-agent-pipeline` 根目录运行 |
| 测试失败 | 环境不完整 | 检查前面的依赖是否全部安装 |

---

### 问题 4：不知道项目文件夹在哪

如果你用 Git 克隆，默认在：

```
C:\Users\你的用户名\multi-agent-pipeline
```

如果你下载 ZIP 解压，就在你解压的位置。

**快速打开：** 在文件资源管理器地址栏输入 `%USERPROFILE%\multi-agent-pipeline` 回车即可。

---

## 日常维护

### 更新代码

如果用 Git 克隆的，可以定期更新：

```powershell
cd %USERPROFILE%\multi-agent-pipeline
git pull
```

### 重新安装依赖

如果依赖出问题，可以删除后重装：

```powershell
cd %USERPROFILE%\multi-agent-pipeline
pip uninstall -y pyyaml pytest rich playwright pytest-asyncio pytest-cov
powershell -ExecutionPolicy Bypass -File setup.ps1
```

### 查看日志

系统运行日志保存在 `.logs/` 目录下，可以用记事本打开查看。

---

## 联系支持

如果按本文档步骤仍无法解决，请提供以下信息寻求帮助：

1. `verify-runtime.ps1` 的完整输出（复制粘贴）
2. 你的 Windows 版本（`Win + R` → `winver`）
3. Python 版本（`python --version` 的输出）

---

**祝你部署顺利！** 🚀
