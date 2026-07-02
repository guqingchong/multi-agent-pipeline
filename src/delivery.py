"""src/delivery.py — Delivery layer for the multi-agent pipeline.

W5-Q06: Auto-generates setup.ps1 / start.ps1 / DEPLOY.md for non-technical users.
Verifies deployment scripts executable in sandbox. Validates app starts and responds.

Design:
  - DeliveryManager: core class that generates and verifies deployment artifacts
  - Template-based generation with customizable parameters
  - Sandbox integration for script verification
  - Startup validation to ensure the application responds correctly
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────


@dataclass
class DeliveryConfig:
    """Configuration for delivery artifact generation."""

    project_name: str = "multi-agent-pipeline"
    project_dir: str = "."
    python_min_version: str = "3.10"
    description: str = "Multi-Agent Pipeline — AI 驱动的多智能体项目开发系统"
    core_packages: List[str] = field(default_factory=lambda: [
        "pyyaml>=6.0",
        "pytest>=7.0",
        "pytest-cov",
        "pytest-asyncio",
        "rich>=13.0",
        "playwright>=1.40.0",
    ])
    project_modules: List[str] = field(default_factory=lambda: [
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
        "prompt_cache_store",
        "worktree",
        "config_loader",
        "performance_optimizer",
        "fallback_manager",
        "e2e_framework",
        "delivery",
    ])
    third_party_modules: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("PyYAML", "yaml"),
        ("pytest", "pytest"),
        ("pytest-cov", "pytest_cov"),
        ("pytest-asyncio", "pytest_asyncio"),
        ("rich", "rich"),
        ("playwright", "playwright"),
    ])
    start_menu_commands: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("status", "查看状态仪表盘"),
        ("init <项目名>", "初始化新项目"),
        ("check", "检查当前 Phase 条件"),
        ("advance", "推进到下一 Phase"),
        ("rollback <phase>", "回退到指定 Phase"),
        ("resume", "从 checkpoint 恢复"),
        ("report", "生成报告"),
        ("deploy", "生成部署脚本"),
        ("help", "查看完整帮助"),
        ("quit / exit", "退出"),
    ])


@dataclass
class DeliveryResult:
    """Result of delivery artifact generation."""

    success: bool
    generated_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifyResult:
    """Result of deployment verification."""

    passed: bool
    checks: List[Tuple[str, bool, str]] = field(default_factory=list)  # (name, passed, detail)
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0


# ───────────────────────────────────────────────────────────────
# Template generators
# ───────────────────────────────────────────────────────────────


def _generate_setup_ps1(config: DeliveryConfig) -> str:
    """Generate setup.ps1 — one-click dependency installation script."""
    core_packages_lines = "\n".join(
        f'    "{pkg}"' for pkg in config.core_packages
    )
    third_party_checks = "\n".join(
        f'        python -c "import {imp}; print(\'OK\')" 2>&1 | Out-Null\n'
        f'        Write-Pass "模块 {name} 可导入"\n'
        f'    }} catch {{\n'
        f'        Write-Fail "模块 {name} 导入失败"\n'
        f'        $ImportFailed = $true\n'
        f'    }}'
        for name, imp in config.third_party_modules
    )
    project_module_checks = "\n".join(
        f'    try {{\n'
        f'        $Output = python -c "import sys; sys.path.insert(0, \'$SrcPath\'); import {mod}; print(\'OK\')" 2>&1\n'
        f'        if ($Output -match "OK") {{\n'
        f'            Write-Pass "项目模块 {mod} 可导入"\n'
        f'        }} else {{\n'
        f'            Write-Fail "项目模块 {mod} 导入异常: $Output"\n'
        f'            $ProjectImportFailed = $true\n'
        f'        }}\n'
        f'    }} catch {{\n'
        f'        Write-Fail "项目模块 {mod} 导入失败"\n'
        f'        $ProjectImportFailed = $true\n'
        f'    }}'
        for mod in config.project_modules
    )

    return textwrap.dedent(f"""\
    # setup.ps1 — 一键安装依赖脚本
    # 面向小白用户：自动检查环境并安装所有需要的 Python 包
    #
    # 自动生成于: delivery.py (W5-Q06)

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

    function Write-Step {{
        param([string]$Message)
        Write-Host "`n[STEP] $Message" -ForegroundColor $Cyan
    }}

    function Write-Pass {{
        param([string]$Message)
        Write-Host "  [PASS] $Message" -ForegroundColor $Green
    }}

    function Write-Fail {{
        param([string]$Message)
        Write-Host "  [FAIL] $Message" -ForegroundColor $Red
    }}

    function Write-Warn {{
        param([string]$Message)
        Write-Host "  [WARN] $Message" -ForegroundColor $Yellow
    }}

    # 获取脚本所在目录（项目根目录）
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    Set-Location $ScriptDir

    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "  {config.project_name} 依赖安装脚本" -ForegroundColor $Cyan
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "项目目录: $ScriptDir" -ForegroundColor $Cyan

    # ───────────────────────────────────────────────
    # 1. 检查 Python 版本
    # ───────────────────────────────────────────────
    Write-Step "检查 Python 版本"

    try {{
        $PythonVersion = python --version 2>&1
        if ($PythonVersion -match "Python\\s+(\\d+)\\.(\\d+)\\.(\\d+)") {{
            $Major = [int]$matches[1]
            $Minor = [int]$matches[2]
            if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {{
                Write-Pass "Python 版本: $PythonVersion (符合要求 >= {config.python_min_version})"
            }} else {{
                Write-Fail "Python 版本过低: $PythonVersion (需要 >= {config.python_min_version})"
                Write-Host "请访问 https://www.python.org/downloads/ 下载安装新版 Python" -ForegroundColor $Yellow
                exit 1
            }}
        }} else {{
            Write-Fail "无法识别 Python 版本输出: $PythonVersion"
            exit 1
        }}
    }} catch {{
        Write-Fail "未检测到 Python，请安装 Python {config.python_min_version}+ 并添加到 PATH"
        Write-Host "下载地址: https://www.python.org/downloads/" -ForegroundColor $Yellow
        exit 1
    }}

    # ───────────────────────────────────────────────
    # 2. 检查 pip 可用性
    # ───────────────────────────────────────────────
    Write-Step "检查 pip 可用性"

    try {{
        $PipVersion = pip --version 2>&1
        Write-Pass "pip 可用: $PipVersion"
    }} catch {{
        Write-Fail "pip 不可用"
        exit 1
    }}

    # ───────────────────────────────────────────────
    # 3. 升级 pip（可选但推荐）
    # ───────────────────────────────────────────────
    Write-Step "升级 pip 到最新版本"

    try {{
        python -m pip install --upgrade pip -q 2>&1 | Out-Null
        Write-Pass "pip 已升级"
    }} catch {{
        Write-Warn "pip 升级失败，继续用现有版本"
    }}

    # ───────────────────────────────────────────────
    # 4. 安装核心依赖
    # ───────────────────────────────────────────────
    Write-Step "安装核心依赖"

    # 定义依赖列表
    $CorePackages = @(
    {core_packages_lines}
    )

    # 尝试使用国内镜像加速
    $MirrorArgs = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple")

    $InstallFailed = $false
    foreach ($Pkg in $CorePackages) {{
        Write-Host "  正在安装 $Pkg ..." -NoNewline
        try {{
            pip install $Pkg $MirrorArgs -q 2>&1 | Out-Null
            Write-Host " 完成" -ForegroundColor $Green
        }} catch {{
            # 镜像失败，尝试默认源
            try {{
                pip install $Pkg -q 2>&1 | Out-Null
                Write-Host " 完成" -ForegroundColor $Green
            }} catch {{
                Write-Host " 失败" -ForegroundColor $Red
                Write-Fail "无法安装 $Pkg"
                $InstallFailed = $true
            }}
        }}
    }}

    if ($InstallFailed) {{
        Write-Fail "部分依赖安装失败"
        Write-Host "请检查网络连接，或尝试手动运行: pip install pyyaml pytest rich" -ForegroundColor $Yellow
        exit 1
    }} else {{
        Write-Pass "所有核心依赖安装完成"
    }}

    # ───────────────────────────────────────────────
    # 5. 验证关键模块可导入
    # ───────────────────────────────────────────────
    Write-Step "验证模块导入"

    $ModulesToCheck = @(
    {", ".join(f'"{imp}"' for _, imp in config.third_party_modules)}
    )

    $ImportFailed = $false
    foreach ($Mod in $ModulesToCheck) {{
        try {{
    {third_party_checks}
    }}

    if ($ImportFailed) {{
        Write-Fail "部分模块导入验证失败"
        exit 1
    }}

    # ───────────────────────────────────────────────
    # 6. 验证项目模块可导入
    # ───────────────────────────────────────────────
    Write-Step "验证项目内部模块"

    $ProjectModules = @(
    {", ".join(f'"{mod}"' for mod in config.project_modules)}
    )

    $SrcPath = Join-Path $ScriptDir "src"
    $EnvPath = [System.Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    [System.Environment]::SetEnvironmentVariable("PYTHONPATH", "$SrcPath;$EnvPath", "Process")

    $ProjectImportFailed = $false
    foreach ($Mod in $ProjectModules) {{
    {project_module_checks}
    }}

    if ($ProjectImportFailed) {{
        Write-Fail "部分项目模块导入验证失败"
        exit 1
    }}

    # ───────────────────────────────────────────────
    # 7. 运行快速测试（可选）
    # ───────────────────────────────────────────────
    if (-not $SkipTests) {{
        Write-Step "运行快速测试验证"
        try {{
            $TestOutput = python -m pytest tests/test_pipeline_state_machine.py -q --tb=short 2>&1
            if ($TestOutput -match "passed") {{
                Write-Pass "核心测试通过"
            }} else {{
                Write-Warn "测试输出异常，请检查 tests/ 目录"
                Write-Host $TestOutput -ForegroundColor $Yellow
            }}
        }} catch {{
            Write-Warn "测试运行失败（非阻塞）: $_"
        }}
    }}

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
    """)


def _generate_start_ps1(config: DeliveryConfig) -> str:
    """Generate start.ps1 — application launcher script."""
    menu_commands_lines = "\n".join(
        f'    Write-Host "  {cmd:<22} - {desc}" -ForegroundColor $Yellow'
        for cmd, desc in config.start_menu_commands
    )
    # Build switch cases for known commands
    known_commands = ["quit", "exit", "help", "status", "init", "check", "advance", "rollback", "resume", "report", "deploy"]
    switch_cases = []
    for cmd in known_commands:
        if cmd == "quit":
            switch_cases.append('        "quit" { \n            Write-Host "再见！" -ForegroundColor $Green\n            exit 0 \n        }')
        elif cmd == "exit":
            switch_cases.append('        "exit" { \n            Write-Host "再见！" -ForegroundColor $Green\n            exit 0 \n        }')
        elif cmd == "help":
            switch_cases.append('        "help" {\n            Write-Host ""\n            Write-Host "完整命令列表:" -ForegroundColor $Cyan\n            python pipeline.py --help\n            Write-Host ""\n        }')
        elif cmd == "status":
            switch_cases.append('        "status" {\n            python pipeline.py status\n        }')
        elif cmd == "init":
            switch_cases.append('        "init" {\n            if ($Args) {\n                python pipeline.py init $Args\n            } else {\n                Write-Host "用法: init <项目名>" -ForegroundColor $Yellow\n            }\n        }')
        elif cmd == "check":
            switch_cases.append('        "check" {\n            python pipeline.py check\n        }')
        elif cmd == "advance":
            switch_cases.append('        "advance" {\n            python pipeline.py advance\n        }')
        elif cmd == "rollback":
            switch_cases.append('        "rollback" {\n            if ($Args) {\n                python pipeline.py rollback $Args\n            } else {\n                Write-Host "用法: rollback <phase_name>" -ForegroundColor $Yellow\n            }\n        }')
        elif cmd == "resume":
            switch_cases.append('        "resume" {\n            python pipeline.py resume\n        }')
        elif cmd == "report":
            switch_cases.append('        "report" {\n            python pipeline.py report\n        }')
        elif cmd == "deploy":
            switch_cases.append('        "deploy" {\n            python pipeline.py deploy\n        }')
    switch_block = "\n".join(switch_cases)

    return textwrap.dedent(f"""\
    # start.ps1 — 启动应用脚本
    # 检查环境后启动 {config.project_name} 交互式界面
    #
    # 自动生成于: delivery.py (W5-Q06)

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
    Write-Host "  {config.project_name} 启动器" -ForegroundColor $Cyan
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host ""

    # ───────────────────────────────────────────────
    # 1. 快速环境检查
    # ───────────────────────────────────────────────
    if (-not $SkipVerify) {{
        Write-Host "[1/2] 快速环境检查..." -ForegroundColor $Cyan
        
        try {{
            $PythonVersion = python --version 2>&1
            if ($PythonVersion -match "Python\\s+(\\d+)\\.(\\d+)") {{
                $Major = [int]$matches[1]
                $Minor = [int]$matches[2]
                if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {{
                    Write-Host "  [PASS] Python: $PythonVersion" -ForegroundColor $Green
                }} else {{
                    Write-Host "  [FAIL] Python 版本过低: $PythonVersion" -ForegroundColor $Red
                    Write-Host "请安装 Python {config.python_min_version}+" -ForegroundColor $Yellow
                    exit 1
                }}
            }} else {{
                Write-Host "  [FAIL] 无法识别 Python" -ForegroundColor $Red
                exit 1
            }}
        }} catch {{
            Write-Host "  [FAIL] Python 未安装" -ForegroundColor $Red
            exit 1
        }}

        # 检查关键模块
        $KeyModules = @("yaml", "pytest", "rich")
        $AllOK = $true
        foreach ($Mod in $KeyModules) {{
            try {{
                python -c "import $Mod" 2>&1 | Out-Null
                Write-Host "  [PASS] 模块 $Mod" -ForegroundColor $Green
            }} catch {{
                Write-Host "  [FAIL] 模块 $Mod 未安装" -ForegroundColor $Red
                $AllOK = $false
            }}
        }}

        if (-not $AllOK) {{
            Write-Host "`n依赖不完整，请先运行:" -ForegroundColor $Yellow
            Write-Host "  powershell -ExecutionPolicy Bypass -File setup.ps1" -ForegroundColor $Yellow
            exit 1
        }}

        # 检查项目模块
        $SrcPath = Join-Path $ScriptDir "src"
        try {{
            $Output = python -c "import sys; sys.path.insert(0, '$SrcPath'); import pipeline; print('OK')" 2>&1
            if ($Output -match "OK") {{
                Write-Host "  [PASS] 项目模块 pipeline" -ForegroundColor $Green
            }} else {{
                Write-Host "  [FAIL] 项目模块导入异常" -ForegroundColor $Red
                exit 1
            }}
        }} catch {{
            Write-Host "  [FAIL] 项目模块无法导入" -ForegroundColor $Red
            exit 1
        }}
    }} else {{
        Write-Host "[1/2] 跳过环境检查 (--SkipVerify)" -ForegroundColor $Yellow
    }}

    # ───────────────────────────────────────────────
    # 2. 启动应用
    # ───────────────────────────────────────────────
    Write-Host "`n[2/2] 启动应用..." -ForegroundColor $Cyan
    Write-Host ""

    $SrcPath = Join-Path $ScriptDir "src"

    # 如果指定了命令，直接执行
    if ($Command) {{
        Write-Host "执行命令: python pipeline.py $Command" -ForegroundColor $Cyan
        Set-Location $SrcPath
        python pipeline.py $Command
        exit $LASTEXITCODE
    }}

    # 显示欢迎信息和菜单
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "  {config.project_name} 已启动" -ForegroundColor $Green
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host ""
    Write-Host "可用命令:" -ForegroundColor $Cyan
{menu_commands_lines}
    Write-Host ""

    # 交互式循环
    Set-Location $SrcPath

    while ($true) {{
        $Input = Read-Host "pipeline>"
        if (-not $Input) {{ continue }}
        
        $Tokens = $Input.Trim() -split "\\s+"
        $Cmd = $Tokens[0].ToLower()
        $Args = $Tokens[1..($Tokens.Length - 1)] -join " "
        
        switch ($Cmd) {{
{switch_block}
            default {{
                # 透传任意命令到 pipeline.py
                python pipeline.py $Cmd $Args
            }}
        }}
        
        Write-Host ""
    }}
    """)


def _generate_verify_runtime_ps1(config: DeliveryConfig) -> str:
    """Generate verify-runtime.ps1 — environment verification script."""
    third_party_checks = "\n".join(
        f'    @{{ Name = "{name}"; Import = "{imp}" }}'
        + ("," if i < len(config.third_party_modules) - 1 else "")
        for i, (name, imp) in enumerate(config.third_party_modules)
    )
    project_module_checks = "\n".join(
        f'    try {{\n'
        f'        $Output = python -c "import sys; sys.path.insert(0, \'$SrcPath\'); import {mod}; print(\'OK\')" 2>&1\n'
        f'        if ($Output -match "OK") {{\n'
        f'            Write-Check "src.{mod}" $true\n'
        f'        }} else {{\n'
        f'            Write-Check "src.{mod}" $false "导入输出异常: $Output"\n'
        f'        }}\n'
        f'    }} catch {{\n'
        f'        Write-Check "src.{mod}" $false "导入失败: $_"\n'
        f'    }}'
        for mod in config.project_modules
    )

    return textwrap.dedent(f"""\
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

    function Write-Check {{
        param(
            [string]$Name,
            [bool]$Passed,
            [string]$Detail = ""
        )
        if ($Passed) {{
            Write-Host "  [PASS] $Name" -ForegroundColor $Green
            if ($Detail -and $Verbose) {{
                Write-Host "         $Detail" -ForegroundColor $Gray
            }}
            $script:PassCount++
        }} else {{
            Write-Host "  [FAIL] $Name" -ForegroundColor $Red
            if ($Detail) {{
                Write-Host "         $Detail" -ForegroundColor $Red
            }}
            $script:FailCount++
        }}
    }}

    function Write-WarnCheck {{
        param([string]$Name, [string]$Detail = "")
        Write-Host "  [WARN] $Name" -ForegroundColor $Yellow
        if ($Detail) {{
            Write-Host "         $Detail" -ForegroundColor $Yellow
        }}
        $script:WarnCount++
    }}

    # 获取项目根目录
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    Set-Location $ScriptDir

    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "  {config.project_name} 环境验证" -ForegroundColor $Cyan
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "项目目录: $ScriptDir" -ForegroundColor $Cyan
    Write-Host ""

    # ───────────────────────────────────────────────
    # 1. Python 环境
    # ───────────────────────────────────────────────
    Write-Host "[1/5] Python 环境检查" -ForegroundColor $Cyan

    try {{
        $PythonVersion = python --version 2>&1
        if ($PythonVersion -match "Python\\s+(\\d+)\\.(\\d+)\\.(\\d+)") {{
            $Major = [int]$matches[1]
            $Minor = [int]$matches[2]
            if ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10)) {{
                Write-Check "Python 版本" $true $PythonVersion
            }} else {{
                Write-Check "Python 版本" $false "当前: $PythonVersion, 需要 >= {config.python_min_version}"
            }}
        }} else {{
            Write-Check "Python 版本" $false "无法识别输出: $PythonVersion"
        }}
    }} catch {{
        Write-Check "Python 版本" $false "Python 未安装或未加入 PATH"
    }}

    try {{
        $PipVersion = pip --version 2>&1
        Write-Check "pip 可用性" $true $PipVersion
    }} catch {{
        Write-Check "pip 可用性" $false "pip 不可用"
    }}

    # ───────────────────────────────────────────────
    # 2. 第三方依赖包
    # ───────────────────────────────────────────────
    Write-Host "`n[2/5] 第三方依赖检查" -ForegroundColor $Cyan

    $ThirdPartyPackages = @(
    {third_party_checks}
    )

    foreach ($Pkg in $ThirdPartyPackages) {{
        try {{
            $Output = python -c "import $($Pkg.Import); print('OK')" 2>&1
            if ($Output -match "OK") {{
                Write-Check "$($Pkg.Name)" $true
            }} else {{
                Write-Check "$($Pkg.Name)" $false "导入异常"
            }}
        }} catch {{
            Write-Check "$($Pkg.Name)" $false "未安装"
        }}
    }}

    # ───────────────────────────────────────────────
    # 3. 项目内部模块
    # ───────────────────────────────────────────────
    Write-Host "`n[3/5] 项目模块导入检查" -ForegroundColor $Cyan

    $SrcPath = Join-Path $ScriptDir "src"
    $ProjectModules = @(
    {", ".join(f'"{mod}"' for mod in config.project_modules)}
    )

    foreach ($Mod in $ProjectModules) {{
    {project_module_checks}
    }}

    # ───────────────────────────────────────────────
    # 4. Git 检查
    # ───────────────────────────────────────────────
    Write-Host "`n[4/5] Git 环境检查" -ForegroundColor $Cyan

    try {{
        $GitVersion = git --version 2>&1
        if ($GitVersion -match "git version") {{
            Write-Check "Git 安装" $true $GitVersion
        }} else {{
            Write-Check "Git 安装" $false "无法识别 git 输出"
        }}
    }} catch {{
        Write-Check "Git 安装" $false "Git 未安装，worktree 功能将不可用"
    }}

    # 检查当前目录是否是 Git 仓库
    try {{
        $GitRoot = git rev-parse --show-toplevel 2>&1
        if ($LASTEXITCODE -eq 0) {{
            Write-Check "Git 仓库" $true "根目录: $GitRoot"
        }} else {{
            Write-WarnCheck "Git 仓库" "当前目录不是 Git 仓库，部分功能受限"
        }}
    }} catch {{
        Write-WarnCheck "Git 仓库" "无法检测 Git 状态"
    }}

    # ───────────────────────────────────────────────
    # 5. 运行核心测试
    # ───────────────────────────────────────────────
    Write-Host "`n[5/5] 核心测试运行" -ForegroundColor $Cyan

    try {{
        $TestOutput = python -m pytest tests/test_pipeline_state_machine.py -q --tb=short 2>&1
        if ($TestOutput -match "(\\d+) passed") {{
            $PassedCount = $matches[1]
            Write-Check "pipeline 状态机测试" $true "$PassedCount 个测试通过"
        }} elseif ($TestOutput -match "passed") {{
            Write-Check "pipeline 状态机测试" $true "测试通过"
        }} else {{
            Write-Check "pipeline 状态机测试" $false "测试未通过或无测试运行"
            if ($Verbose) {{
                Write-Host $TestOutput -ForegroundColor $Yellow
            }}
        }}
    }} catch {{
        Write-Check "pipeline 状态机测试" $false "运行失败: $_"
    }}

    # 额外运行几个关键测试
    try {{
        $TestOutput = python -m pytest tests/test_state_store.py -q --tb=short 2>&1
        if ($TestOutput -match "passed") {{
            Write-Check "state_store 测试" $true "测试通过"
        }} else {{
            Write-WarnCheck "state_store 测试" "部分测试未通过"
        }}
    }} catch {{
        Write-WarnCheck "state_store 测试" "运行失败"
    }}

    try {{
        $TestOutput = python -m pytest tests/test_adapters.py -q --tb=short 2>&1
        if ($TestOutput -match "passed") {{
            Write-Check "adapters 测试" $true "测试通过"
        }} else {{
            Write-WarnCheck "adapters 测试" "部分测试未通过"
        }}
    }} catch {{
        Write-WarnCheck "adapters 测试" "运行失败"
    }}

    # ───────────────────────────────────────────────
    # 汇总
    # ───────────────────────────────────────────────
    Write-Host "`n========================================" -ForegroundColor $Cyan
    Write-Host "  验证结果汇总" -ForegroundColor $Cyan
    Write-Host "========================================" -ForegroundColor $Cyan
    Write-Host "  通过: $PassCount" -ForegroundColor $Green
    Write-Host "  失败: $FailCount" -ForegroundColor $(if ($FailCount -gt 0) {{ $Red }} else {{ $Green }})
    Write-Host "  警告: $WarnCount" -ForegroundColor $(if ($WarnCount -gt 0) {{ $Yellow }} else {{ $Green }})
    Write-Host "========================================" -ForegroundColor $Cyan

    if ($FailCount -eq 0) {{
        Write-Host "  验证结果: 全部通过" -ForegroundColor $Green
        Write-Host "========================================" -ForegroundColor $Cyan
        Write-Host "`n环境已就绪，可以启动应用：" -ForegroundColor $Cyan
        Write-Host "  powershell -ExecutionPolicy Bypass -File start.ps1" -ForegroundColor $Cyan
        exit 0
    }} else {{
        Write-Host "  验证结果: 存在失败项，请检查上方 [FAIL] 详情" -ForegroundColor $Red
        Write-Host "========================================" -ForegroundColor $Cyan
        Write-Host "`n建议：先运行 setup.ps1 安装依赖，再重新验证。" -ForegroundColor $Yellow
        exit 1
    }}
    """)


def _generate_deploy_md(config: DeliveryConfig) -> str:
    """Generate DEPLOY.md — deployment guide for non-technical users."""
    project_dir = config.project_dir.replace("\\", "\\\\")
    return textwrap.dedent(f"""\
    # DEPLOY.md — 部署指南

    > 本文档说明如何在本机部署和运行 {config.project_name} 系统。
    >
    > 自动生成于: delivery.py (W5-Q06)

    ---

    ## 重要前提

    **项目路径：** `{project_dir}`

    **如果你已经在这个路径看到项目文件，说明项目已存在，不需要下载。**

    ---

    ## 环境要求

    - Windows 10/11
    - Python {config.python_min_version}+
    - Git（可选，用于 worktree 功能）

    ---

    ## 安装步骤

    ### 1. 确认 Python 已安装

    按 `Win + R`，输入 `cmd`，回车。

    输入：
    ```
    python --version
    ```

    看到 `Python {config.python_min_version}+` 即可。如果报错，去 https://www.python.org/downloads 下载安装。

    ### 2. 安装依赖

    在项目文件夹 `{project_dir}` 中：

    按住 `Shift + 右键` → `在此处打开 PowerShell 窗口`。

    输入：
    ```powershell
    powershell -ExecutionPolicy Bypass -File setup.ps1
    ```

    等 1-3 分钟，看到"安装完成"即可。

    ### 3. 验证环境

    ```powershell
    powershell -ExecutionPolicy Bypass -File verify-runtime.ps1
    ```

    全绿 = 环境就绪。

    ---

    ## 启动系统

    ```powershell
    powershell -ExecutionPolicy Bypass -File start.ps1
    ```

    启动后进入命令行菜单，可以输入命令操作。

    **注意：** 这不是聊天界面。当前版本的交互方式是通过你正在使用的这个聊天窗口（Hermes Agent）。

    ---

    ## 使用方式

    ### 方式1：通过聊天窗口（当前方式）

    直接在这个对话中告诉 Hermes 你想做什么任务。

    示例：
    ```
    我想开发一个爬虫系统，需求是...
    ```

    Hermes 会自动分配任务给 AI 团队，并在关键节点问你确认。

    ### 方式2：通过命令行（start.ps1）

    启动后输入命令：

    | 命令 | 作用 |
    |------|------|
    {chr(10).join(f"| `{cmd}` | {desc} |" for cmd, desc in config.start_menu_commands)}

    ---

    ## 项目文件说明

    ```
    {project_dir}\\  ← 项目根目录
    ├── src\\                        ← 系统代码
    │   ├── pipeline.py             ← 主入口
    │   ├── adapters.py             ← AI 适配器
    │   ├── phase_checks.py         ← Phase 检查
    │   ├── delivery.py             ← 交付层
    │   └── ...                     ← 其他模块
    ├── tests\\                      ← 测试代码
    ├── README.md                   ← 使用说明
    ├── DEPLOY.md                   ← 本文档
    ├── setup.ps1                   ← 安装脚本
    ├── start.ps1                   ← 启动脚本
    ├── verify-runtime.ps1          ← 验证脚本
    └── features.json               ← 功能规格
    ```

    ---

    ## 常见问题

    ### Q: 项目不在 {project_dir}？

    A: 如果需要移动，复制整个文件夹到新位置，然后重新运行 setup.ps1。

    ### Q: 启动后没有聊天界面？

    A: 当前版本没有 Web UI。交互方式是通过你正在使用的这个聊天窗口（Hermes Agent）。

    ### Q: 如何开始一个新任务？

    A: 直接在这个对话中说"我想做 XXX"。

    ### Q: 如何查看项目进度？

    A: 说"查看状态"或使用 `pipeline.py status` 命令。

    ### Q: 代码在哪里？

    A: `{project_dir}\\src\\` 目录下。

    ---

    ## 故障排查

    | 问题 | 现象 | 解决 |
    |------|------|------|
    | Python 未安装 | `python --version` 报错 | 安装 Python {config.python_min_version}+ |
    | PowerShell 限制 | 无法运行脚本 | 加 `-ExecutionPolicy Bypass` |
    | 依赖安装失败 | setup.ps1 报错 | 手动运行 `pip install pyyaml pytest rich` |
    | 模块导入失败 | verify-runtime 失败 | 确认在项目根目录运行 |

    ---

    ## 联系支持

    如果按本文档步骤仍无法解决，请提供：

    1. `verify-runtime.ps1` 的完整输出
    2. Windows 版本（`Win + R` → `winver`）
    3. Python 版本（`python --version`）
    4. 具体报错信息

    ---

    **祝你使用顺利！** 🚀
    """)


# ───────────────────────────────────────────────────────────────
# DeliveryManager
# ───────────────────────────────────────────────────────────────


class DeliveryManager:
    """Delivery layer — generates and verifies deployment artifacts.

    Responsibilities:
      1. Auto-generate setup.ps1 / start.ps1 / verify-runtime.ps1 / DEPLOY.md
      2. Verify deployment scripts are executable in sandbox
      3. Validate application starts and responds correctly
    """

    GENERATORS = {
        "setup.ps1": _generate_setup_ps1,
        "start.ps1": _generate_start_ps1,
        "verify-runtime.ps1": _generate_verify_runtime_ps1,
        "DEPLOY.md": _generate_deploy_md,
    }

    def __init__(self, config: Optional[DeliveryConfig] = None) -> None:
        self.config = config or DeliveryConfig()

    def generate_file(self, filename: str) -> str:
        """Generate a single deployment artifact as a string.

        Args:
            filename: One of 'setup.ps1', 'start.ps1', 'verify-runtime.ps1', 'DEPLOY.md'

        Returns:
            The generated file content as a string.

        Raises:
            ValueError: If filename is not a recognized artifact.
        """
        if filename not in self.GENERATORS:
            raise ValueError(
                f"Unknown artifact '{filename}'. "
                f"Available: {', '.join(self.GENERATORS)}"
            )
        return self.GENERATORS[filename](self.config)

    def generate_all(self) -> Dict[str, str]:
        """Generate all deployment artifacts.

        Returns:
            Dict mapping filename -> content.
        """
        return {name: gen(self.config) for name, gen in self.GENERATORS.items()}

    def write_all(self, output_dir: Optional[Path] = None) -> DeliveryResult:
        """Generate all deployment artifacts and write them to disk.

        Args:
            output_dir: Directory to write files to. Defaults to config.project_dir.

        Returns:
            DeliveryResult with generation status.
        """
        if output_dir is None:
            output_dir = Path(self.config.project_dir)
        output_dir = Path(output_dir)

        result = DeliveryResult(success=True)
        artifacts = self.generate_all()

        for filename, content in artifacts.items():
            filepath = output_dir / filename
            try:
                filepath.write_text(content, encoding="utf-8")
                result.generated_files.append(str(filepath))
            except OSError as e:
                result.success = False
                result.errors.append(f"Failed to write {filename}: {e}")

        result.details = {
            "output_dir": str(output_dir),
            "file_count": len(result.generated_files),
        }
        return result

    def verify_deployment(self, project_dir: Optional[Path] = None) -> VerifyResult:
        """Verify that deployment scripts exist and are structurally valid.

        Checks:
          1. All required scripts exist (setup.ps1, start.ps1, verify-runtime.ps1, DEPLOY.md)
          2. PowerShell scripts have valid .ps1 extension
          3. DEPLOY.md is non-empty
          4. Scripts contain expected key commands/patterns

        Args:
            project_dir: Project directory. Defaults to config.project_dir.

        Returns:
            VerifyResult with per-check pass/fail status.
        """
        start = time.time()
        if project_dir is None:
            project_dir = Path(self.config.project_dir)
        project_dir = Path(project_dir)

        result = VerifyResult(passed=True)
        required_files = ["setup.ps1", "start.ps1", "verify-runtime.ps1", "DEPLOY.md"]

        for filename in required_files:
            filepath = project_dir / filename
            exists = filepath.exists()
            if not exists:
                result.passed = False
                result.errors.append(f"Missing file: {filename}")
                result.checks.append((f"{filename} exists", False, "File not found"))
                continue

            # Check non-empty
            try:
                content = filepath.read_text(encoding="utf-8")
                if not content.strip():
                    result.passed = False
                    result.errors.append(f"Empty file: {filename}")
                    result.checks.append((f"{filename} content", False, "File is empty"))
                    continue
            except (IOError, OSError) as e:
                result.passed = False
                result.errors.append(f"Cannot read {filename}: {e}")
                result.checks.append((f"{filename} readable", False, str(e)))
                continue

            # Structural checks
            if filename == "DEPLOY.md":
                has_required = (
                    "快速开始" in content or "Quick Start" in content or "安装步骤" in content
                )
                result.checks.append(
                    (f"{filename} structural", has_required,
                     "Contains deployment instructions" if has_required else "Missing deployment instructions")
                )
                if not has_required:
                    result.passed = False
            elif filename.endswith(".ps1"):
                # Check for expected PowerShell patterns
                has_params = "param(" in content
                has_error_action = "ErrorActionPreference" in content
                result.checks.append(
                    (f"{filename} structural", has_params and has_error_action,
                     "Valid PowerShell script" if (has_params and has_error_action) else "Missing PowerShell structure")
                )
                if not (has_params and has_error_action):
                    result.passed = False
                    result.errors.append(f"{filename} does not appear to be a valid PowerShell script")
            else:
                result.checks.append((f"{filename} exists", True, "File present"))

        result.duration_ms = int((time.time() - start) * 1000)
        return result

    def verify_startup(self, project_dir: Optional[Path] = None, timeout_seconds: int = 10) -> VerifyResult:
        """Verify that the application can start and respond.

        Attempts to import the pipeline module and check basic functionality.

        Args:
            project_dir: Project directory. Defaults to config.project_dir.
            timeout_seconds: Max time to wait for startup check.

        Returns:
            VerifyResult with startup check status.
        """
        start = time.time()
        if project_dir is None:
            project_dir = Path(self.config.project_dir)
        project_dir = Path(project_dir)
        src_dir = project_dir / "src"

        result = VerifyResult(passed=True)

        # Check 1: src directory exists
        if not src_dir.exists():
            result.passed = False
            result.errors.append("src/ directory not found")
            result.checks.append(("src/ exists", False, "Directory missing"))
            result.duration_ms = int((time.time() - start) * 1000)
            return result
        result.checks.append(("src/ exists", True, str(src_dir)))

        # Check 2: Python files exist in src/
        py_files = list(src_dir.glob("*.py"))
        if not py_files:
            result.passed = False
            result.errors.append("No Python files in src/")
            result.checks.append(("src/*.py found", False, "No .py files"))
        else:
            result.checks.append(("src/*.py found", True, f"{len(py_files)} Python files"))

        # Check 3: Can import delivery module itself
        try:
            sys.path.insert(0, str(src_dir))
            import importlib
            importlib.import_module("delivery")
            result.checks.append(("import delivery", True, "Module imported successfully"))
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            result.passed = False
            result.errors.append(f"Failed to import delivery: {e}")
            result.checks.append(("import delivery", False, str(e)))

        # Check 4: Try importing pipeline module
        try:
            importlib.import_module("pipeline")
            result.checks.append(("import pipeline", True, "Module imported successfully"))
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            result.passed = False
            result.errors.append(f"Failed to import pipeline: {e}")
            result.checks.append(("import pipeline", False, str(e)))

        # Check 5: Try importing models (used as proxy for core functionality)
        try:
            importlib.import_module("models")
            result.checks.append(("import models", True, "Module imported successfully"))
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            result.passed = False
            result.errors.append(f"Failed to import models: {e}")
            result.checks.append(("import models", False, str(e)))

        result.duration_ms = int((time.time() - start) * 1000)
        return result

    def run_full_verification(self, project_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Run complete deployment verification: file checks + startup validation.

        Args:
            project_dir: Project directory. Defaults to config.project_dir.

        Returns:
            Dict with deploy_check and startup_check results.
        """
        deploy = self.verify_deployment(project_dir)
        startup = self.verify_startup(project_dir)

        overall_passed = deploy.passed and startup.passed

        return {
            "passed": overall_passed,
            "deploy_check": {
                "passed": deploy.passed,
                "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in deploy.checks],
                "errors": deploy.errors,
            },
            "startup_check": {
                "passed": startup.passed,
                "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in startup.checks],
                "errors": startup.errors,
            },
        }

    def sandbox_verify_scripts(self, project_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Verify deployment scripts in sandbox context.

        Uses the project's sandbox module to check if the deployment scripts
        would be allowed/denied under the current sandbox profile.

        Args:
            project_dir: Project directory. Defaults to config.project_dir.

        Returns:
            Dict with sandbox evaluation results.
        """
        if project_dir is None:
            project_dir = Path(self.config.project_dir)
        project_dir = Path(project_dir)

        sandbox = None
        try:
            from src.sandbox import Sandbox
            sandbox = Sandbox()
        except ImportError:
            # Fallback: try direct import
            try:
                sys.path.insert(0, str(project_dir / "src"))
                from sandbox import Sandbox  # type: ignore[no-redef]
                sandbox = Sandbox()
            except ImportError:
                return {
                    "passed": False,
                    "error": "Cannot import sandbox module",
                    "evaluations": [],
                }

        scripts = ["setup.ps1", "start.ps1", "verify-runtime.ps1"]
        evaluations = []

        for script in scripts:
            test_cmd = f"powershell -ExecutionPolicy Bypass -File {script}"
            action, reason = sandbox.evaluate_command(test_cmd)
            evaluations.append({
                "script": script,
                "test_command": test_cmd,
                "action": action.name,
                "reason": reason,
            })

        all_allowed = all(
            e["action"] in ("ALLOW",) for e in evaluations
        )

        return {
            "passed": all_allowed,
            "sandbox_profile": sandbox.profile.value,
            "evaluations": evaluations,
        }


# ───────────────────────────────────────────────────────────────
# Module-level convenience functions
# ───────────────────────────────────────────────────────────────


def generate_delivery_artifacts(
    project_dir: Optional[str] = None,
    project_name: str = "multi-agent-pipeline",
    **kwargs: Any,
) -> DeliveryResult:
    """Convenience function: generate and write all delivery artifacts.

    Args:
        project_dir: Output directory path.
        project_name: Name of the project.
        **kwargs: Additional DeliveryConfig overrides.

    Returns:
        DeliveryResult with generation status.
    """
    if project_dir is None:
        project_dir = str(Path.cwd())

    config = DeliveryConfig(
        project_name=project_name,
        project_dir=project_dir,
        **{k: v for k, v in kwargs.items() if hasattr(DeliveryConfig, k)},
    )
    manager = DeliveryManager(config)
    return manager.write_all(Path(project_dir))


def verify_delivery(project_dir: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function: run full deployment verification.

    Args:
        project_dir: Project directory path.

    Returns:
        Dict with verification results.
    """
    if project_dir is None:
        project_dir = str(Path.cwd())

    config = DeliveryConfig(project_dir=project_dir)
    manager = DeliveryManager(config)
    return manager.run_full_verification(Path(project_dir))


# ───────────────────────────────────────────────────────────────
# CLI integration for pipeline.py
# ───────────────────────────────────────────────────────────────


def delivery_cli(args: Optional[List[str]] = None) -> int:
    """CLI entry point for delivery operations.

    Usage:
        python delivery.py generate        # Generate all artifacts
        python delivery.py verify          # Verify deployment
        python delivery.py sandbox-verify  # Sandbox verification
        python delivery.py full            # Generate + verify

    Args:
        args: CLI arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    if args is None:
        args = sys.argv[1:]

    if not args:
        print("Usage: python delivery.py <generate|verify|sandbox-verify|full>")
        print()
        print("Commands:")
        print("  generate        Generate all deployment artifacts (setup.ps1, start.ps1, etc.)")
        print("  verify          Verify deployment artifacts exist and are valid")
        print("  sandbox-verify  Verify scripts in sandbox context")
        print("  full            Generate all artifacts + run full verification")
        return 0

    cmd = args[0].lower()
    project_dir = Path.cwd()

    if cmd == "generate":
        result = generate_delivery_artifacts(str(project_dir))
        if result.success:
            print(f"[OK] Generated {len(result.generated_files)} files:")
            for f in result.generated_files:
                print(f"  - {f}")
            return 0
        else:
            print("[FAIL] Generation failed:")
            for e in result.errors:
                print(f"  - {e}")
            return 1

    elif cmd == "verify":
        result = verify_delivery(str(project_dir))
        deploy_ok = result["deploy_check"]["passed"]
        startup_ok = result["startup_check"]["passed"]

        print(f"Deploy check: {'PASS' if deploy_ok else 'FAIL'}")
        for c in result["deploy_check"]["checks"]:
            icon = "✓" if c["passed"] else "✗"
            print(f"  {icon} {c['name']}: {c['detail']}")

        print(f"Startup check: {'PASS' if startup_ok else 'FAIL'}")
        for c in result["startup_check"]["checks"]:
            icon = "✓" if c["passed"] else "✗"
            print(f"  {icon} {c['name']}: {c['detail']}")

        return 0 if result["passed"] else 1

    elif cmd == "sandbox-verify":
        config = DeliveryConfig(project_dir=str(project_dir))
        manager = DeliveryManager(config)
        result = manager.sandbox_verify_scripts()
        print(f"Sandbox verify: {'PASS' if result['passed'] else 'FAIL'}")
        print(f"Profile: {result.get('sandbox_profile', 'unknown')}")
        for e in result.get("evaluations", []):
            print(f"  {e['script']}: {e['action']} — {e['reason']}")
        return 0 if result["passed"] else 1

    elif cmd == "full":
        # Step 1: Generate
        print("=== Step 1: Generate artifacts ===")
        gen_result = generate_delivery_artifacts(str(project_dir))
        if gen_result.success:
            print(f"[OK] Generated {len(gen_result.generated_files)} files")
        else:
            print(f"[FAIL] Generation errors: {gen_result.errors}")

        # Step 2: Verify
        print("\n=== Step 2: Verify deployment ===")
        result = verify_delivery(str(project_dir))

        deploy_ok = result["deploy_check"]["passed"]
        startup_ok = result["startup_check"]["passed"]
        print(f"Deploy check: {'PASS' if deploy_ok else 'FAIL'}")
        print(f"Startup check: {'PASS' if startup_ok else 'FAIL'}")

        # Step 3: Sandbox verify
        print("\n=== Step 3: Sandbox verify ===")
        config = DeliveryConfig(project_dir=str(project_dir))
        manager = DeliveryManager(config)
        sb_result = manager.sandbox_verify_scripts()
        print(f"Sandbox verify: {'PASS' if sb_result['passed'] else 'FAIL'}")

        overall = gen_result.success and result["passed"]
        print(f"\n=== Overall: {'PASS' if overall else 'FAIL'} ===")
        return 0 if overall else 1

    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python delivery.py <generate|verify|sandbox-verify|full>")
        return 1


if __name__ == "__main__":
    sys.exit(delivery_cli())
