#!/bin/bash
# setup.sh — 一键安装依赖脚本
# 面向小白用户：自动检查环境并安装所有需要的 Python 包
#
# 自动生成于: delivery.py (W5-Q06)

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 函数：打印步骤
print_step() {
    echo -e "\n${CYAN}[STEP]${NC} $1"
}

# 函数：打印通过
print_pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
}

# 函数：打印失败
print_fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
}

# 函数：打印警告
print_warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
}

# 设置错误处理
set -e
trap 'echo -e "${RED}安装过程中发生错误，脚本终止${NC}" >&2; exit 1' ERR

# 获取脚本所在目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  multi-agent-pipeline 依赖安装脚本${NC}"
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}项目目录: $SCRIPT_DIR${NC}"

# ───────────────────────────────────────────────
# 1. 检查 Python 版本
# ───────────────────────────────────────────────
print_step "检查 Python 版本"

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    if [[ $PYTHON_VERSION =~ Python\ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
        MAJOR=${BASH_REMATCH[1]}
        MINOR=${BASH_REMATCH[2]}
        if (( MAJOR > 3 || (MAJOR == 3 && MINOR >= 10) )); then
            print_pass "Python 版本: $PYTHON_VERSION (符合要求 >= 3.10)"
        else
            print_fail "Python 版本过低: $PYTHON_VERSION (需要 >= 3.10)"
            echo -e "${YELLOW}请访问 https://www.python.org/downloads/ 下载安装新版 Python${NC}"
            exit 1
        fi
    else
        print_fail "无法识别 Python 版本输出: $PYTHON_VERSION"
        exit 1
    fi
else
    print_fail "未检测到 Python，请安装 Python 3.10+ 并添加到 PATH"
    echo -e "${YELLOW}下载地址: https://www.python.org/downloads/${NC}"
    exit 1
fi

# ───────────────────────────────────────────────
# 2. 检查 pip 可用性
# ───────────────────────────────────────────────
print_step "检查 pip 可用性"

if python3 -m pip --version &> /dev/null; then
    PIP_VERSION=$(python3 -m pip --version)
    print_pass "pip 可用: $PIP_VERSION"
else
    print_fail "pip 不可用"
    exit 1
fi

# ───────────────────────────────────────────────
# 3. 升级 pip（可选但推荐）
# ───────────────────────────────────────────────
print_step "升级 pip 到最新版本"

if python3 -m pip install --upgrade pip &> /dev/null; then
    print_pass "pip 已升级"
else
    print_warn "pip 升级失败，继续用现有版本"
fi

# ───────────────────────────────────────────────
# 4. 安装核心依赖
# ───────────────────────────────────────────────
print_step "安装核心依赖"

# 定义依赖列表
CORE_PACKAGES=(
    "pyyaml>=6.0"
    "pytest>=7.0"
    "pytest-cov"
    "pytest-asyncio"
    "rich>=13.0"
    "playwright>=1.40.0"
)

# 尝试使用国内镜像加速
MIRROR_ARGS=(-i https://pypi.tuna.tsinghua.edu.cn/simple)

INSTALL_FAILED=false
for pkg in "${CORE_PACKAGES[@]}"; do
    echo -n "  正在安装 $pkg ..."
    if python3 -m pip install "$pkg" "${MIRROR_ARGS[@]}" -q; then
        echo -e " ${GREEN}完成${NC}"
    else
        # 镜像失败，尝试默认源
        if python3 -m pip install "$pkg" -q; then
            echo -e " ${GREEN}完成${NC}"
        else
            echo -e " ${RED}失败${NC}"
            print_fail "无法安装 $pkg"
            INSTALL_FAILED=true
        fi
    fi
done

if [ "$INSTALL_FAILED" = true ]; then
    print_fail "部分依赖安装失败"
    echo -e "${YELLOW}请检查网络连接，或尝试手动运行: pip install pyyaml pytest rich${NC}"
    exit 1
else
    print_pass "所有核心依赖安装完成"
fi

# ───────────────────────────────────────────────
# 5. 验证关键模块可导入
# ───────────────────────────────────────────────
print_step "验证模块导入"

MODULES_TO_CHECK=(
    "yaml"
    "pytest"
    "pytest_cov"
    "pytest_asyncio"
    "rich"
    "playwright"
)

IMPORT_FAILED=false
for mod in "${MODULES_TO_CHECK[@]}"; do
    if python3 -c "import $mod; print('OK')" &> /dev/null; then
        print_pass "模块 $mod 可导入"
    else
        print_fail "模块 $mod 导入失败"
        IMPORT_FAILED=true
    fi
done

if [ "$IMPORT_FAILED" = true ]; then
    print_fail "部分模块导入验证失败"
    exit 1
fi

# ───────────────────────────────────────────────
# 6. 验证项目模块可导入
# ───────────────────────────────────────────────
print_step "验证项目内部模块"

PROJECT_MODULES=(
    "pipeline"
    "phase_checks"
    "phase_flow"
    "state_store"
    "adapters"
    "sandbox"
    "circuit_breaker"
    "approval"
    "observability"
    "context_manager"
    "prompt_cache"
    "prompt_cache_store"
    "worktree"
    "config_loader"
    "performance_optimizer"
    "fallback_manager"
    "e2e_framework"
    "delivery"
)

# 添加 src 目录到 PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

PROJECT_IMPORT_FAILED=false
for mod in "${PROJECT_MODULES[@]}"; do
    if python3 -c "import $mod; print('OK')" &> /dev/null; then
        print_pass "项目模块 $mod 可导入"
    else
        print_fail "项目模块 $mod 导入失败"
        PROJECT_IMPORT_FAILED=true
    fi
done

if [ "$PROJECT_IMPORT_FAILED" = true ]; then
    print_fail "部分项目模块导入验证失败"
    exit 1
fi

# ───────────────────────────────────────────────
# 7. 运行快速测试（可选）
# ───────────────────────────────────────────────
print_step "运行快速测试验证"

if python3 -m pytest tests/test_pipeline_state_machine.py -q --tb=short; then
    print_pass "核心测试通过"
else
    print_warn "测试运行失败（非阻塞）"
fi

# ───────────────────────────────────────────────
# 完成
# ───────────────────────────────────────────────
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  依赖安装完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\n${CYAN}下一步：运行验证脚本确认环境${NC}"
echo -e "${CYAN}  ./verify-runtime.sh${NC}"
echo -e "\n${CYAN}或直接启动应用：${NC}"
echo -e "${CYAN}  ./start.sh${NC}"

exit 0