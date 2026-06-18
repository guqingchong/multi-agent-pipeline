# setup.ps1 — 一键安装依赖脚本
# 面向小白用户：自动检查环境并安装所有需要的 Python 包

param(
    [switch]$Force,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"

# 颜色定义
$Green = "Green"
$Red = "Red"
$Yellow = "Yellow"
$Cyan = "Cyan"

function Write-Step {
    param([string]$Message)
    Write-Host "`n[STEP] $Message" -ForegroundColor $Cyan
}

function Write-Pass {
    param([string]$Message)
    Write-Host "  [PASS] $Message" -ForegroundColor $Green
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor $Red
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor $Yellow
}

# 获取脚本所在目录（项目根目录）
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "  Multi-Agent Pipeline 依赖安装脚本" -ForegroundColor $Cyan
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "项目目录: $ScriptDir" -ForegroundColor $Cyan

# ───────────────────────────────────────────────
# 1. 检查 Python 版本
# ───────────────────────────────────────────────
Write-Step "检查 Python 版本"

try {
    $PythonVersion = python --version 2>&1
    if ($PythonVersion -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
        $Major = [int]$matches[1]
        $Minor = [int]$matches[2]
        if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {
            Write-Pass "Python 版本: $PythonVersion (符合要求 >= 3.10)"
        } else {
            Write-Fail "Python 版本过低: $PythonVersion (需要 >= 3.10)"
            Write-Host "请访问 https://www.python.org/downloads/ 下载安装新版 Python" -ForegroundColor $Yellow
            exit 1
        }
    } else {
        Write-Fail "无法识别 Python 版本输出: $PythonVersion"
        exit 1
    }
} catch {
    Write-Fail "未检测到 Python，请安装 Python 3.10+ 并添加到 PATH"
    Write-Host "下载地址: https://www.python.org/downloads/" -ForegroundColor $Yellow
    exit 1
}

# ───────────────────────────────────────────────
# 2. 检查 pip 可用性
# ───────────────────────────────────────────────
Write-Step "检查 pip 可用性"

try {
    $PipVersion = pip --version 2>&1
    Write-Pass "pip 可用: $PipVersion"
} catch {
    Write-Fail "pip 不可用"
    exit 1
}

# ───────────────────────────────────────────────
# 3. 升级 pip（可选但推荐）
# ───────────────────────────────────────────────
Write-Step "升级 pip 到最新版本"

try {
    python -m pip install --upgrade pip -q 2>&1 | Out-Null
    Write-Pass "pip 已升级"
} catch {
    Write-Warn "pip 升级失败，继续用现有版本"
}

# ───────────────────────────────────────────────
# 4. 安装核心依赖
# ───────────────────────────────────────────────
Write-Step "安装核心依赖"

# 定义依赖列表
$CorePackages = @(
    "pyyaml>=6.0",
    "pytest>=7.0",
    "pytest-cov",
    "pytest-asyncio",
    "rich>=13.0",
    "playwright>=1.40.0"
)

# 尝试使用国内镜像加速
$MirrorArgs = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple")

$InstallFailed = $false
foreach ($Pkg in $CorePackages) {
    Write-Host "  正在安装 $Pkg ..." -NoNewline
    try {
        pip install $Pkg $MirrorArgs -q 2>&1 | Out-Null
        Write-Host " 完成" -ForegroundColor $Green
    } catch {
        # 镜像失败，尝试默认源
        try {
            pip install $Pkg -q 2>&1 | Out-Null
            Write-Host " 完成" -ForegroundColor $Green
        } catch {
            Write-Host " 失败" -ForegroundColor $Red
            Write-Fail "无法安装 $Pkg"
            $InstallFailed = $true
        }
    }
}

if ($InstallFailed) {
    Write-Fail "部分依赖安装失败"
    Write-Host "请检查网络连接，或尝试手动运行: pip install pyyaml pytest rich" -ForegroundColor $Yellow
    exit 1
} else {
    Write-Pass "所有核心依赖安装完成"
}

# ───────────────────────────────────────────────
# 5. 验证关键模块可导入
# ───────────────────────────────────────────────
Write-Step "验证模块导入"

$ModulesToCheck = @(
    "yaml",
    "pytest",
    "rich",
    "playwright"
)

$ImportFailed = $false
foreach ($Mod in $ModulesToCheck) {
    try {
        python -c "import $Mod" 2>&1 | Out-Null
        Write-Pass "模块 $Mod 可导入"
    } catch {
        Write-Fail "模块 $Mod 导入失败"
        $ImportFailed = $true
    }
}

if ($ImportFailed) {
    Write-Fail "部分模块导入验证失败"
    exit 1
}

# ───────────────────────────────────────────────
# 6. 验证项目模块可导入
# ───────────────────────────────────────────────
Write-Step "验证项目内部模块"

$ProjectModules = @(
    "pipeline",
    "phase_checks",
    "phase_flow",
    "state_store",
    "adapters",
    "sandbox",
    "circuit_breaker",
    "approval",
    "observability",
    "context_manager",
    "prompt_cache",
    "worktree",
    "config_loader",
    "performance_optimizer",
    "fallback_manager",
    "e2e_framework"
)

$SrcPath = Join-Path $ScriptDir "src"
$EnvPath = [System.Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONPATH", "$SrcPath;$EnvPath", "Process")

$ProjectImportFailed = $false
foreach ($Mod in $ProjectModules) {
    try {
        $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import $Mod; print('OK')" 2>&1
        if ($Output -match "OK") {
            Write-Pass "项目模块 $Mod 可导入"
        } else {
            Write-Fail "项目模块 $Mod 导入异常: $Output"
            $ProjectImportFailed = $true
        }
    } catch {
        Write-Fail "项目模块 $Mod 导入失败"
        $ProjectImportFailed = $true
    }
}

if ($ProjectImportFailed) {
    Write-Fail "部分项目模块导入验证失败"
    exit 1
}

# ───────────────────────────────────────────────
# 7. 运行快速测试（可选）
# ───────────────────────────────────────────────
if (-not $SkipTests) {
    Write-Step "运行快速测试验证"
    try {
        $TestOutput = python -m pytest tests/test_pipeline_state_machine.py -q --tb=short 2>&1
        if ($TestOutput -match "passed") {
            Write-Pass "核心测试通过"
        } else {
            Write-Warn "测试输出异常，请检查 tests/ 目录"
            Write-Host $TestOutput -ForegroundColor $Yellow
        }
    } catch {
        Write-Warn "测试运行失败（非阻塞）: $_"
    }
}

# ───────────────────────────────────────────────
# 完成
# ───────────────────────────────────────────────
Write-Host "`n========================================" -ForegroundColor $Green
Write-Host "  依赖安装完成！" -ForegroundColor $Green
Write-Host "========================================" -ForegroundColor $Green
Write-Host "`n下一步：运行验证脚本确认环境" -ForegroundColor $Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File verify-runtime.ps1" -ForegroundColor $Cyan
Write-Host "`n或直接启动应用：" -ForegroundColor $Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File start.ps1" -ForegroundColor $Cyan
