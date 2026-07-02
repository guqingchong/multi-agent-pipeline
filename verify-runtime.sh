#!/bin/bash
# verify-runtime.sh — 运行时环境验证脚本
# 验证 multi-agent-pipeline 所需的所有依赖是否正确安装
#
# 自动生成于: delivery.py (W5-Q06)

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 函数：打印信息
print_info() {
    echo -e "${CYAN}$1${NC}"
}

print_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

print_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_step() {
    echo -e "\n${CYAN}[STEP]${NC} $1"
}

# 获取项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  multi-agent-pipeline 环境验证${NC}"
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
        PATCH=${BASH_REMATCH[3]}
        
        if (( MAJOR > 3 || (MAJOR == 3 && MINOR >= 10) )); then
            print_success "Python 版本: $PYTHON_VERSION (符合要求 >= 3.10)"
        else
            print_error "Python 版本过低: $PYTHON_VERSION (需要 >= 3.10)"
            exit 1
        fi
    else
        print_error "无法识别 Python 版本输出: $PYTHON_VERSION"
        exit 1
    fi
else
    print_error "未检测到 Python"
    exit 1
fi

# ───────────────────────────────────────────────
# 2. 检查 pip 可用性
# ───────────────────────────────────────────────
print_step "检查 pip 可用性"

if command -v python3 &> /dev/null && python3 -m pip --version &> /dev/null; then
    PIP_VERSION=$(python3 -m pip --version)
    print_success "pip 可用: $PIP_VERSION"
else
    print_error "pip 不可用"
    exit 1
fi

# ───────────────────────────────────────────────
# 3. 检查必需的 Python 包
# ───────────────────────────────────────────────
print_step "检查必需的 Python 包"

REQUIRED_PACKAGES=(
    "yaml"
    "pytest"
    "pytest_cov"
    "pytest_asyncio"
    "rich"
    "playwright"
)

MISSING_PACKAGES=()
for pkg in "${REQUIRED_PACKAGES[@]}"; do
    if python3 -c "import $pkg" &> /dev/null; then
        print_success "包 $pkg 可导入"
    else
        print_error "包 $pkg 无法导入"
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -ne 0 ]; then
    print_error "缺少以下包: ${MISSING_PACKAGES[*]}"
    echo -e "${YELLOW}请运行 ./setup.sh 重新安装依赖${NC}"
    exit 1
fi

# ───────────────────────────────────────────────
# 4. 检查项目文件结构
# ───────────────────────────────────────────────
print_step "检查项目文件结构"

REQUIRED_FILES=(
    "src/pipeline.py"
    "src/phase_checks.py"
    "src/phase_flow.py"
    "src/state_store.py"
    "src/adapters.py"
    "DEPLOY.md"
    "setup.sh"
    "start.sh"
    "docker-compose.yml"
)

MISSING_FILES=()
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        print_success "文件存在: $file"
    else
        print_error "文件缺失: $file"
        MISSING_FILES+=("$file")
    fi
done

if [ ${#MISSING_FILES[@]} -ne 0 ]; then
    print_error "缺少以下文件: ${MISSING_FILES[*]}"
    exit 1
fi

# ───────────────────────────────────────────────
# 5. 检查项目模块可导入
# ───────────────────────────────────────────────
print_step "检查项目模块可导入性"

export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

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

MISSING_MODULES=()
for mod in "${PROJECT_MODULES[@]}"; do
    if python3 -c "import $mod" &> /dev/null; then
        print_success "模块 $mod 可导入"
    else
        print_error "模块 $mod 无法导入"
        MISSING_MODULES+=("$mod")
    fi
done

if [ ${#MISSING_MODULES[@]} -ne 0 ]; then
    print_error "以下项目模块无法导入: ${MISSING_MODULES[*]}"
    exit 1
fi

# ───────────────────────────────────────────────
# 6. 检查可执行权限
# ───────────────────────────────────────────────
print_step "检查脚本执行权限"

EXECUTABLE_SCRIPTS=(
    "setup.sh"
    "start.sh"
    "verify-runtime.sh"
)

PERMISSION_ISSUES=()
for script in "${EXECUTABLE_SCRIPTS[@]}"; do
    if [ -x "$script" ]; then
        print_success "脚本有执行权限: $script"
    else
        print_error "脚本无执行权限: $script"
        PERMISSION_ISSUES+=("$script")
    fi
done

if [ ${#PERMISSION_ISSUES[@]} -ne 0 ]; then
    print_warning "发现权限问题，尝试修复..."
    for script in "${PERMISSION_ISSUES[@]}"; do
        chmod +x "$script"
        if [ $? -eq 0 ]; then
            print_success "已修复权限: $script"
        else
            print_error "无法修复权限: $script"
        fi
    done
fi

# ───────────────────────────────────────────────
# 7. 运行基本功能测试
# ───────────────────────────────────────────────
print_step "运行基本功能测试"

TEST_OUTPUT=$(python3 -c "
import sys
sys.path.insert(0, './src')
import pipeline
print('Pipeline module loaded successfully')
" 2>&1)

if echo "$TEST_OUTPUT" | grep -q "Pipeline module loaded successfully"; then
    print_success "基本功能测试通过"
else
    print_error "基本功能测试失败: $TEST_OUTPUT"
    exit 1
fi

# ───────────────────────────────────────────────
# 总结
# ───────────────────────────────────────────────
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  环境验证完成！${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n${CYAN}所有检查项均已通过，multi-agent-pipeline 环境已准备就绪！${NC}"
echo -e "\n${CYAN}接下来您可以：${NC}"
echo -e "${CYAN}  1. 运行 ./start.sh 启动应用${NC}"
echo -e "${CYAN}  2. 运行 docker-compose up 启动容器环境${NC}"
echo -e "${CYAN}  3. 开始您的多智能体协作编码任务${NC}"

exit 0