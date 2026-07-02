#!/bin/bash
# start.sh — 启动应用脚本
# 检查环境后启动 multi-agent-pipeline 交互式界面
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
    echo -e "${GREEN}$1${NC}"
}

print_error() {
    echo -e "${RED}$1${NC}"
}

print_warning() {
    echo -e "${YELLOW}$1${NC}"
}

# 获取项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  multi-agent-pipeline 启动器${NC}"
echo -e "${CYAN}========================================${NC}"
echo

# ───────────────────────────────────────────────
# 1. 快速环境检查
# ───────────────────────────────────────────────
print_info "[1/2] 快速环境检查..."

if ! command -v python3 &> /dev/null; then
    print_error "Python 未安装"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
if [[ $PYTHON_VERSION =~ Python\ ([0-9]+)\.([0-9]+) ]]; then
    MAJOR=${BASH_REMATCH[1]}
    MINOR=${BASH_REMATCH[2]}
    if (( MAJOR > 3 || (MAJOR == 3 && MINOR >= 10) )); then
        print_success "  [PASS] Python: $PYTHON_VERSION"
    else
        print_error "  [FAIL] Python 版本过低: $PYTHON_VERSION (需要 >= 3.10)"
        exit 1
    fi
else
    print_error "  [FAIL] 无法识别 Python 版本"
    exit 1
fi

# 检查关键模块
KEY_MODULES=("yaml" "pytest" "rich")
ALL_OK=true

for mod in "${KEY_MODULES[@]}"; do
    if python3 -c "import $mod" &> /dev/null; then
        print_success "  [PASS] 模块 $mod"
    else
        print_error "  [FAIL] 模块 $mod 未安装"
        ALL_OK=false
    fi
done

if [ "$ALL_OK" = false ]; then
    print_error ""
    print_warning "依赖不完整，请先运行:"
    print_warning "  ./setup.sh"
    exit 1
fi

# 检查项目模块
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"
if python3 -c "import pipeline" &> /dev/null; then
    print_success "  [PASS] 项目模块 pipeline"
else
    print_error "  [FAIL] 项目模块无法导入"
    exit 1
fi

# ───────────────────────────────────────────────
# 2. 启动应用
# ───────────────────────────────────────────────
print_info ""
print_info "[2/2] 启动应用..."
echo

# 添加 src 目录到 PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

# 显示欢迎信息和菜单
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}  multi-agent-pipeline 已启动${NC}"
echo -e "${CYAN}========================================${NC}"
echo
echo -e "${CYAN}可用命令:${NC}"
echo -e "${YELLOW}  status                 - 查看状态仪表盘${NC}"
echo -e "${YELLOW}  init <项目名>             - 初始化新项目${NC}"
echo -e "${YELLOW}  check                  - 检查当前 Phase 条件${NC}"
echo -e "${YELLOW}  advance                - 推进到下一 Phase${NC}"
echo -e "${YELLOW}  rollback <phase>       - 回退到指定 Phase${NC}"
echo -e "${YELLOW}  resume                 - 从 checkpoint 恢复${NC}"
echo -e "${YELLOW}  report                 - 生成报告${NC}"
echo -e "${YELLOW}  deploy                 - 生成部署脚本${NC}"
echo -e "${YELLOW}  help                   - 查看完整帮助${NC}"
echo -e "${YELLOW}  quit / exit            - 退出${NC}"
echo

# 进入交互式循环
cd "$SCRIPT_DIR/src"

while true; do
    read -p "pipeline> " input
    
    if [ -z "$input" ]; then
        continue
    fi
    
    # 解析命令和参数
    cmd=$(echo "$input" | awk '{print $1}')
    args=$(echo "$input" | cut -d' ' -f2-)
    
    case "$cmd" in
        quit|exit)
            print_success "再见！"
            exit 0
            ;;
        help)
            echo
            echo -e "${CYAN}完整命令列表:${NC}"
            python pipeline.py --help
            echo
            ;;
        status)
            python pipeline.py status $args
            ;;
        init)
            if [ -n "$args" ]; then
                python pipeline.py init $args
            else
                print_warning "用法: init <项目名>"
            fi
            ;;
        check)
            python pipeline.py check $args
            ;;
        advance)
            python pipeline.py advance $args
            ;;
        rollback)
            if [ -n "$args" ]; then
                python pipeline.py rollback $args
            else
                print_warning "用法: rollback <phase_name>"
            fi
            ;;
        resume)
            python pipeline.py resume $args
            ;;
        report)
            python pipeline.py report $args
            ;;
        deploy)
            python pipeline.py deploy $args
            ;;
        *)
            # 透传任意命令到 pipeline.py
            python pipeline.py $cmd $args
            ;;
    esac
    
    echo
done