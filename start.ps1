# start.ps1 — 启动应用脚本
# 检查环境后启动 multi-agent-pipeline 交互式界面

param(
    [switch]$SkipVerify,
    [string]$Project = "",
    [string]$Command = ""
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# 颜色定义
$Green = "Green"
$Red = "Red"
$Yellow = "Yellow"
$Cyan = "Cyan"

# 获取项目根目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "  Multi-Agent Pipeline 启动器" -ForegroundColor $Cyan
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host ""

# ───────────────────────────────────────────────
# 1. 快速环境检查
# ───────────────────────────────────────────────
if (-not $SkipVerify) {
    Write-Host "[1/2] 快速环境检查..." -ForegroundColor $Cyan
    
    try {
        $PythonVersion = python --version 2>&1
        if ($PythonVersion -match "Python\s+(\d+)\.(\d+)") {
            $Major = [int]$matches[1]
            $Minor = [int]$matches[2]
            if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {
                Write-Host "  [PASS] Python: $PythonVersion" -ForegroundColor $Green
            } else {
                Write-Host "  [FAIL] Python 版本过低: $PythonVersion" -ForegroundColor $Red
                Write-Host "请安装 Python 3.10+" -ForegroundColor $Yellow
                exit 1
            }
        } else {
            Write-Host "  [FAIL] 无法识别 Python" -ForegroundColor $Red
            exit 1
        }
    } catch {
        Write-Host "  [FAIL] Python 未安装" -ForegroundColor $Red
        exit 1
    }

    # 检查关键模块
    $KeyModules = @("yaml", "pytest", "rich")
    $AllOK = $true
    foreach ($Mod in $KeyModules) {
        try {
            python -c "import $Mod" 2>&1 | Out-Null
            Write-Host "  [PASS] 模块 $Mod" -ForegroundColor $Green
        } catch {
            Write-Host "  [FAIL] 模块 $Mod 未安装" -ForegroundColor $Red
            $AllOK = $false
        }
    }

    if (-not $AllOK) {
        Write-Host "`n依赖不完整，请先运行:" -ForegroundColor $Yellow
        Write-Host "  powershell -ExecutionPolicy Bypass -File setup.ps1" -ForegroundColor $Yellow
        exit 1
    }

    # 检查项目模块
    $SrcPath = Join-Path $ScriptDir "src"
    try {
        $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import pipeline; print('OK')" 2>&1
        if ($Output -match "OK") {
            Write-Host "  [PASS] 项目模块 pipeline" -ForegroundColor $Green
        } else {
            Write-Host "  [FAIL] 项目模块导入异常" -ForegroundColor $Red
            exit 1
        }
    } catch {
        Write-Host "  [FAIL] 项目模块无法导入" -ForegroundColor $Red
        exit 1
    }
} else {
    Write-Host "[1/2] 跳过环境检查 (--SkipVerify)" -ForegroundColor $Yellow
}

# ───────────────────────────────────────────────
# 2. 启动应用
# ───────────────────────────────────────────────
Write-Host "`n[2/2] 启动应用..." -ForegroundColor $Cyan
Write-Host ""

$SrcPath = Join-Path $ScriptDir "src"

# 如果指定了命令，直接执行
if ($Command) {
    Write-Host "执行命令: python pipeline.py $Command" -ForegroundColor $Cyan
    Set-Location $SrcPath
    python pipeline.py $Command
    exit $LASTEXITCODE
}

# 显示欢迎信息和菜单
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host "  Multi-Agent Pipeline 已启动" -ForegroundColor $Green
Write-Host "========================================" -ForegroundColor $Cyan
Write-Host ""
Write-Host "可用命令:" -ForegroundColor $Cyan
Write-Host "  status              - 查看状态仪表盘" -ForegroundColor $Yellow
Write-Host "  init <项目名>       - 初始化新项目" -ForegroundColor $Yellow
Write-Host "  check               - 检查当前 Phase 条件" -ForegroundColor $Yellow
Write-Host "  advance             - 推进到下一 Phase" -ForegroundColor $Yellow
Write-Host "  rollback <phase>    - 回退到指定 Phase" -ForegroundColor $Yellow
Write-Host "  resume              - 从 checkpoint 恢复" -ForegroundColor $Yellow
Write-Host "  report              - 生成报告" -ForegroundColor $Yellow
Write-Host "  help                - 查看完整帮助" -ForegroundColor $Yellow
Write-Host "  quit / exit         - 退出" -ForegroundColor $Yellow
Write-Host ""

# 交互式循环
Set-Location $SrcPath

while ($true) {
    $Input = Read-Host "pipeline>"
    if (-not $Input) { continue }
    
    $Tokens = $Input.Trim() -split "\s+"
    $Cmd = $Tokens[0].ToLower()
    $Args = $Tokens[1..($Tokens.Length - 1)] -join " "
    
    switch ($Cmd) {
        "quit" { 
            Write-Host "再见！" -ForegroundColor $Green
            exit 0 
        }
        "exit" { 
            Write-Host "再见！" -ForegroundColor $Green
            exit 0 
        }
        "help" {
            Write-Host ""
            Write-Host "完整命令列表:" -ForegroundColor $Cyan
            python pipeline.py --help
            Write-Host ""
        }
        "status" {
            python pipeline.py status
        }
        "init" {
            if ($Args) {
                python pipeline.py init $Args
            } else {
                Write-Host "用法: init <项目名>" -ForegroundColor $Yellow
            }
        }
        "check" {
            python pipeline.py check
        }
        "advance" {
            python pipeline.py advance
        }
        "rollback" {
            if ($Args) {
                python pipeline.py rollback $Args
            } else {
                Write-Host "用法: rollback <phase_name>" -ForegroundColor $Yellow
            }
        }
        "resume" {
            python pipeline.py resume
        }
        "report" {
            python pipeline.py report
        }
        default {
            # 透传任意命令到 pipeline.py
            python pipeline.py $Cmd $Args
        }
    }
    
    Write-Host ""
}
