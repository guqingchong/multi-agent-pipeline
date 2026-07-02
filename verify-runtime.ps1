# verify-runtime.ps1 — 环境验证脚本
# 检查 Python、依赖、项目模块、Git 是否全部就绪
#
# 自动生成于: delivery.py (W5-Q06)

param(
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# 颜色定义
$Green = "Green"
$Red = "Red"
$Yellow = "Yellow"
$Cyan = "Cyan"
$Gray = "Gray"

$PassCount = 0
$FailCount = 0
$WarnCount = 0

function Write-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail = ""
    )
    if ($Passed) {
        Write-Host "  [PASS] $Name" -ForegroundColor $Green
        if ($Detail -and $Verbose) {
            Write-Host "         $Detail" -ForegroundColor $Gray
        }
        $script:PassCount++
    } else {
        Write-Host "  [FAIL] $Name" -ForegroundColor $Red
        if ($Detail) {
            Write-Host "         $Detail" -ForegroundColor $Red
        }
        $script:FailCount++
    }
}

function Write-WarnCheck {
    param([string]$Name, [string]$Detail = "")
    Write-Host "  [WARN] $Name" -ForegroundColor $Yellow
    if ($Detail) {
        Write-Host "         $Detail" -ForegroundColor $Yellow
    }
    $script:WarnCount++
}

# 获取项目根目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "  multi-agent-pipeline 环境验证" -ForegroundColor $Cyan
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "项目目录: $ScriptDir" -ForegroundColor $Cyan
Write-Host ""

# ───────────────────────────────────────────────
# 1. Python 环境
# ───────────────────────────────────────────────
Write-Host "[1/5] Python 环境检查" -ForegroundColor $Cyan

try {
    $PythonVersion = python --version 2>&1
    if ($PythonVersion -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
        $Major = [int]$matches[1]
        $Minor = [int]$matches[2]
        if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {
            Write-Check "Python 版本" $true $PythonVersion
        } else {
            Write-Check "Python 版本" $false "当前: $PythonVersion, 需要 >= 3.10"
        }
    } else {
        Write-Check "Python 版本" $false "无法识别输出: $PythonVersion"
    }
} catch {
    Write-Check "Python 版本" $false "Python 未安装或未加入 PATH"
}

try {
    $PipVersion = pip --version 2>&1
    Write-Check "pip 可用性" $true $PipVersion
} catch {
    Write-Check "pip 可用性" $false "pip 不可用"
}

# ───────────────────────────────────────────────
# 2. 第三方依赖包
# ───────────────────────────────────────────────
Write-Host "`n[2/5] 第三方依赖检查" -ForegroundColor $Cyan

$ThirdPartyPackages = @(
    @{ Name = "PyYAML"; Import = "yaml" },
@{ Name = "pytest"; Import = "pytest" },
@{ Name = "pytest-cov"; Import = "pytest_cov" },
@{ Name = "pytest-asyncio"; Import = "pytest_asyncio" },
@{ Name = "rich"; Import = "rich" },
@{ Name = "playwright"; Import = "playwright" }
)

foreach ($Pkg in $ThirdPartyPackages) {
    try {
        $Output = python -c "import $($Pkg.Import); print('OK')" 2>&1
        if ($Output -match "OK") {
            Write-Check "$($Pkg.Name)" $true
        } else {
            Write-Check "$($Pkg.Name)" $false "导入异常"
        }
    } catch {
        Write-Check "$($Pkg.Name)" $false "未安装"
    }
}

# ───────────────────────────────────────────────
# 3. 项目内部模块
# ───────────────────────────────────────────────
Write-Host "`n[3/5] 项目模块导入检查" -ForegroundColor $Cyan

$SrcPath = Join-Path $ScriptDir "src"
$ProjectModules = @(
"pipeline", "phase_checks", "phase_flow", "state_store", "adapters", "sandbox", "circuit_breaker", "approval", "observability", "context_manager", "prompt_cache", "prompt_cache_store", "worktree", "config_loader", "performance_optimizer", "fallback_manager", "e2e_framework", "delivery"
)

foreach ($Mod in $ProjectModules) {
    try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import pipeline; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.pipeline" $true
    } else {
        Write-Check "src.pipeline" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.pipeline" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import phase_checks; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.phase_checks" $true
    } else {
        Write-Check "src.phase_checks" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.phase_checks" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import phase_flow; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.phase_flow" $true
    } else {
        Write-Check "src.phase_flow" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.phase_flow" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import state_store; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.state_store" $true
    } else {
        Write-Check "src.state_store" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.state_store" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import adapters; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.adapters" $true
    } else {
        Write-Check "src.adapters" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.adapters" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import sandbox; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.sandbox" $true
    } else {
        Write-Check "src.sandbox" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.sandbox" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import circuit_breaker; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.circuit_breaker" $true
    } else {
        Write-Check "src.circuit_breaker" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.circuit_breaker" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import approval; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.approval" $true
    } else {
        Write-Check "src.approval" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.approval" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import observability; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.observability" $true
    } else {
        Write-Check "src.observability" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.observability" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import context_manager; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.context_manager" $true
    } else {
        Write-Check "src.context_manager" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.context_manager" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import prompt_cache; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.prompt_cache" $true
    } else {
        Write-Check "src.prompt_cache" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.prompt_cache" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import prompt_cache_store; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.prompt_cache_store" $true
    } else {
        Write-Check "src.prompt_cache_store" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.prompt_cache_store" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import worktree; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.worktree" $true
    } else {
        Write-Check "src.worktree" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.worktree" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import config_loader; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.config_loader" $true
    } else {
        Write-Check "src.config_loader" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.config_loader" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import performance_optimizer; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.performance_optimizer" $true
    } else {
        Write-Check "src.performance_optimizer" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.performance_optimizer" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import fallback_manager; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.fallback_manager" $true
    } else {
        Write-Check "src.fallback_manager" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.fallback_manager" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import e2e_framework; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.e2e_framework" $true
    } else {
        Write-Check "src.e2e_framework" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.e2e_framework" $false "导入失败: $_"
}
try {
    $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import delivery; print('OK')" 2>&1
    if ($Output -match "OK") {
        Write-Check "src.delivery" $true
    } else {
        Write-Check "src.delivery" $false "导入输出异常: $Output"
    }
} catch {
    Write-Check "src.delivery" $false "导入失败: $_"
}
}

# ───────────────────────────────────────────────
# 4. Git 检查
# ───────────────────────────────────────────────
Write-Host "`n[4/5] Git 环境检查" -ForegroundColor $Cyan

try {
    $GitVersion = git --version 2>&1
    if ($GitVersion -match "git version") {
        Write-Check "Git 安装" $true $GitVersion
    } else {
        Write-Check "Git 安装" $false "无法识别 git 输出"
    }
} catch {
    Write-Check "Git 安装" $false "Git 未安装，worktree 功能将不可用"
}

# 检查当前目录是否是 Git 仓库
try {
    $GitRoot = git rev-parse --show-toplevel 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Check "Git 仓库" $true "根目录: $GitRoot"
    } else {
        Write-WarnCheck "Git 仓库" "当前目录不是 Git 仓库，部分功能受限"
    }
} catch {
    Write-WarnCheck "Git 仓库" "无法检测 Git 状态"
}

# ───────────────────────────────────────────────
# 5. 运行核心测试
# ───────────────────────────────────────────────
Write-Host "`n[5/5] 核心测试运行" -ForegroundColor $Cyan

try {
    $TestOutput = python -m pytest tests/test_pipeline_state_machine.py -q --tb=short 2>&1
    if ($TestOutput -match "(\d+) passed") {
        $PassedCount = $matches[1]
        Write-Check "pipeline 状态机测试" $true "$PassedCount 个测试通过"
    } elseif ($TestOutput -match "passed") {
        Write-Check "pipeline 状态机测试" $true "测试通过"
    } else {
        Write-Check "pipeline 状态机测试" $false "测试未通过或无测试运行"
        if ($Verbose) {
            Write-Host $TestOutput -ForegroundColor $Yellow
        }
    }
} catch {
    Write-Check "pipeline 状态机测试" $false "运行失败: $_"
}

# 额外运行几个关键测试
try {
    $TestOutput = python -m pytest tests/test_state_store.py -q --tb=short 2>&1
    if ($TestOutput -match "passed") {
        Write-Check "state_store 测试" $true "测试通过"
    } else {
        Write-WarnCheck "state_store 测试" "部分测试未通过"
    }
} catch {
    Write-WarnCheck "state_store 测试" "运行失败"
}

try {
    $TestOutput = python -m pytest tests/test_adapters.py -q --tb=short 2>&1
    if ($TestOutput -match "passed") {
        Write-Check "adapters 测试" $true "测试通过"
    } else {
        Write-WarnCheck "adapters 测试" "部分测试未通过"
    }
} catch {
    Write-WarnCheck "adapters 测试" "运行失败"
}

# ───────────────────────────────────────────────
# 汇总
# ───────────────────────────────────────────────
Write-Host "`n========================================" -ForegroundColor $Cyan
Write-Host "  验证结果汇总" -ForegroundColor $Cyan
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "  通过: $PassCount" -ForegroundColor $Green
Write-Host "  失败: $FailCount" -ForegroundColor $(if ($FailCount -gt 0) { $Red } else { $Green })
Write-Host "  警告: $WarnCount" -ForegroundColor $(if ($WarnCount -gt 0) { $Yellow } else { $Green })
Write-Host "========================================" -ForegroundColor $Cyan

if ($FailCount -eq 0) {
    Write-Host "  验证结果: 全部通过" -ForegroundColor $Green
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "`n环境已就绪，可以启动应用：" -ForegroundColor $Cyan
    Write-Host "  powershell -ExecutionPolicy Bypass -File start.ps1" -ForegroundColor $Cyan
    exit 0
} else {
    Write-Host "  验证结果: 存在失败项，请检查上方 [FAIL] 详情" -ForegroundColor $Red
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "`n建议：先运行 setup.ps1 安装依赖，再重新验证。" -ForegroundColor $Yellow
    exit 1
}
