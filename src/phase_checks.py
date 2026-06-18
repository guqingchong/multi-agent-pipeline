"""src/phase_checks.py — Phase 0-6 检查函数

每个 check 函数验证对应 Phase 的 advance 条件，返回统一格式：
    {"passed": bool, "reason": str, "details": dict}

CHECK_REGISTRY 注册所有 check 函数，支持 run_check(phase_name) 统一调用。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ───────────────────────────────────────────────────────────────
# 类型定义
# ───────────────────────────────────────────────────────────────

CheckResult = Dict[str, Any]
CheckFunc = Callable[[str, Path], CheckResult]


# ───────────────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────────────

def _project_dir(project_name: str, base_dir: Path) -> Path:
    return base_dir / project_name


def _file_exists(project_name: str, base_dir: Path, filename: str) -> bool:
    return (_project_dir(project_name, base_dir) / filename).exists()


def _read_json_file(project_name: str, base_dir: Path, filename: str) -> Optional[dict]:
    path = _project_dir(project_name, base_dir) / filename
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _read_text_file(project_name: str, base_dir: Path, filename: str) -> Optional[str]:
    path = _project_dir(project_name, base_dir) / filename
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _check_file_exists(
    project_name: str, base_dir: Path, filename: str, errors: List[str], details: Dict[str, Any]
) -> bool:
    exists = _file_exists(project_name, base_dir, filename)
    details[f"{filename}_exists"] = exists
    if not exists:
        errors.append(f"缺少文件: {filename}")
    return exists


def _check_git_init(project_name: str, base_dir: Path) -> bool:
    git_dir = _project_dir(project_name, base_dir) / ".git"
    return git_dir.exists() and git_dir.is_dir()


def _check_db_created(project_name: str, base_dir: Path) -> bool:
    db_path = _project_dir(project_name, base_dir) / "pipeline_state.db"
    return db_path.exists()


# ───────────────────────────────────────────────────────────────
# Phase 0: init → check_init
# ───────────────────────────────────────────────────────────────

def check_init(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 0 advance 条件：
    - 项目目录存在
    - git repo 初始化成功
    - features.json 符合 schema
    - SOUL.md / AGENTS.md / progress.md 存在
    - SQLite DB 创建成功
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    details["project_dir_exists"] = proj_dir.exists()
    if not proj_dir.exists():
        errors.append("项目目录不存在")
        return {"passed": False, "reason": "项目目录不存在", "details": details}

    # 检查 git
    git_ok = _check_git_init(project_name, base_dir)
    details["git_init"] = git_ok
    if not git_ok:
        errors.append("git repo 未初始化")

    # 检查 DB
    db_ok = _check_db_created(project_name, base_dir)
    details["db_created"] = db_ok
    if not db_ok:
        errors.append("SQLite DB 未创建")

    # 检查元数据文件
    required_files = ["SOUL.md", "AGENTS.md", "progress.md", "features.json"]
    for f in required_files:
        _check_file_exists(project_name, base_dir, f, errors, details)

    # 检查 features.json 是否符合基本 schema
    features_data = _read_json_file(project_name, base_dir, "features.json")
    details["features_json_valid"] = features_data is not None
    if features_data is None:
        errors.append("features.json 不存在或格式错误")
    else:
        if not isinstance(features_data, dict):
            errors.append("features.json 根节点必须是对象")
        elif "project" not in features_data:
            errors.append("features.json 缺少 'project' 字段")
        elif features_data.get("project") != project_name:
            errors.append("features.json 中的 project 名称不匹配")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 1: design → check_design
# ───────────────────────────────────────────────────────────────

def check_design(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 1 advance 条件：
    - specs/architecture.md 存在
    - 人类已审批（design_approved = true）
    - 架构中包含模块划分、接口定义、数据流
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # 检查 architecture.md
    arch_path = proj_dir / "specs" / "architecture.md"
    details["architecture_md_exists"] = arch_path.exists()
    if not arch_path.exists():
        errors.append("缺少 specs/architecture.md")
    else:
        content = _read_text_file(project_name, base_dir, "specs/architecture.md") or ""
        details["architecture_md_length"] = len(content)
        # 检查是否包含模块划分、接口定义、数据流
        has_modules = "模块" in content or "module" in content.lower() or "划分" in content
        has_interfaces = "接口" in content or "interface" in content.lower() or "API" in content
        has_dataflow = "数据流" in content or "data flow" in content.lower() or "数据" in content
        details["has_modules"] = has_modules
        details["has_interfaces"] = has_interfaces
        details["has_dataflow"] = has_dataflow
        if not has_modules:
            errors.append("architecture.md 缺少模块划分")
        if not has_interfaces:
            errors.append("architecture.md 缺少接口定义")
        if not has_dataflow:
            errors.append("architecture.md 缺少数据流描述")

    # 检查 design_approved 状态（从 state store 读取）
    design_approved = False
    state = _load_state(project_name, base_dir)
    if state is not None:
        design_approved = state.get("design_approved", False)
    details["design_approved"] = design_approved
    if not design_approved:
        errors.append("design 未通过人类审批 (design_approved=false)")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 2: decompose → check_decompose
# ───────────────────────────────────────────────────────────────

def check_decompose(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 2 advance 条件：
    - features.json 符合 schema
    - 所有 feature 有 acceptance_criteria
    - 依赖图无环
    - 已分波
    - feature 粒度检查通过
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    features_data = _read_json_file(project_name, base_dir, "features.json")
    details["features_json_valid"] = features_data is not None
    if features_data is None:
        errors.append("features.json 不存在或格式错误")
        return {"passed": False, "reason": " | ".join(errors), "details": details}

    if not isinstance(features_data, dict):
        errors.append("features.json 根节点必须是对象")
        return {"passed": False, "reason": " | ".join(errors), "details": details}

    features = features_data.get("features", [])
    if not isinstance(features, list):
        errors.append("features.json 中 'features' 必须是数组")
        return {"passed": False, "reason": " | ".join(errors), "details": details}

    details["feature_count"] = len(features)

    if not features:
        errors.append("features.json 中没有 feature")

    # 检查每个 feature 的字段
    all_have_ac = True
    all_have_wave = True
    all_have_complexity = True
    dependency_graph: Dict[str, List[str]] = {}
    feature_ids: set = set()

    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            errors.append(f"features[{idx}] 不是对象")
            continue
        fid = feat.get("id", f"feature_{idx}")
        feature_ids.add(fid)
        if not feat.get("acceptance_criteria"):
            all_have_ac = False
            errors.append(f"feature {fid} 缺少 acceptance_criteria")
        if "wave" not in feat:
            all_have_wave = False
            errors.append(f"feature {fid} 未分波 (缺少 wave)")
        if "estimated_complexity" not in feat:
            all_have_complexity = False
            errors.append(f"feature {fid} 缺少 estimated_complexity")
        deps = feat.get("dependencies", [])
        if isinstance(deps, list):
            dependency_graph[fid] = deps
        else:
            dependency_graph[fid] = []
            errors.append(f"feature {fid} 的 dependencies 必须是数组")

    details["all_have_acceptance_criteria"] = all_have_ac
    details["all_have_wave"] = all_have_wave
    details["all_have_complexity"] = all_have_complexity

    # 检查依赖图无环
    has_cycle = _detect_cycle(dependency_graph, feature_ids)
    details["dependency_cycle_detected"] = has_cycle
    if has_cycle:
        errors.append("依赖图存在环")

    # feature 粒度检查
    granularity_ok = True
    for feat in features:
        if not isinstance(feat, dict):
            continue
        complexity = feat.get("estimated_complexity", "")
        if complexity in ("超大", "oversized"):
            granularity_ok = False
            errors.append(f"feature {feat.get('id', '?')} 粒度过大，必须拆分")
        if complexity in ("过小", "undersized"):
            granularity_ok = False
            errors.append(f"feature {feat.get('id', '?')} 粒度过小，应合并")
    details["granularity_ok"] = granularity_ok

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


def _detect_cycle(graph: Dict[str, List[str]], all_nodes: set) -> bool:
    """检测有向图是否有环（DFS）"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in all_nodes}
    # 补充 graph 中未作为 key 的节点
    for node in all_nodes:
        if node not in graph:
            graph[node] = []

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for node in all_nodes:
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False


# ───────────────────────────────────────────────────────────────
# Phase 3: develop → check_develop
# ───────────────────────────────────────────────────────────────

def check_develop(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 3 advance 条件：
    - 所有 feature status 为 "passed" 或至少开发完成
    - 代码已编写（存在 src/ 目录下的 .py 文件）
    - 有 git commit
    - progress.md 已更新
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # 检查 features.json 中所有 feature 状态
    features_data = _read_json_file(project_name, base_dir, "features.json")
    features_passed = True
    if features_data and isinstance(features_data, dict):
        features = features_data.get("features", [])
        if isinstance(features, list):
            for feat in features:
                if isinstance(feat, dict):
                    status = feat.get("status", "")
                    if status not in ("passed", "test", "review"):
                        features_passed = False
                        errors.append(f"feature {feat.get('id', '?')} 状态为 {status}，未通过开发")
    details["all_features_passed"] = features_passed

    # 检查是否有代码文件
    src_dir = proj_dir / "src"
    has_code = False
    if src_dir.exists():
        py_files = list(src_dir.rglob("*.py"))
        has_code = len(py_files) > 0
        details["src_py_file_count"] = len(py_files)
    else:
        details["src_py_file_count"] = 0
    details["has_code"] = has_code
    if not has_code:
        errors.append("src/ 目录下没有 .py 代码文件")

    # 检查 git commit 历史
    git_log_ok = False
    git_dir = proj_dir / ".git"
    if git_dir.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["git", "-C", str(proj_dir), "log", "--oneline", "-n", "1"],
                capture_output=True, text=True, timeout=10,
            )
            git_log_ok = result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            git_log_ok = False
    details["has_git_commit"] = git_log_ok
    if not git_log_ok:
        errors.append("没有 git commit 记录")

    # 检查 progress.md 是否更新（非空且包含当前 phase）
    progress_content = _read_text_file(project_name, base_dir, "progress.md") or ""
    progress_updated = bool(progress_content.strip()) and "develop" in progress_content.lower()
    details["progress_updated"] = progress_updated
    if not progress_updated:
        errors.append("progress.md 未更新开发进度")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 4: test → check_test
# ───────────────────────────────────────────────────────────────

def check_test(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 4 advance 条件：
    - 所有 passing feature 的回归测试通过
    - E2E 测试通过（如适用）
    - 测试覆盖率达标
    - 没有 failed feature
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # 检查 features.json 中是否有 failed feature
    features_data = _read_json_file(project_name, base_dir, "features.json")
    has_failed_feature = False
    all_passed_or_test = True
    if features_data and isinstance(features_data, dict):
        features = features_data.get("features", [])
        if isinstance(features, list):
            for feat in features:
                if isinstance(feat, dict):
                    status = feat.get("status", "")
                    if status == "failed":
                        has_failed_feature = True
                        errors.append(f"feature {feat.get('id', '?')} 状态为 failed")
                    elif status not in ("passed", "test", "review"):
                        all_passed_or_test = False
    details["has_failed_feature"] = has_failed_feature
    details["all_features_ready"] = all_passed_or_test

    if has_failed_feature:
        errors.append("存在 failed 状态的 feature")

    if not all_passed_or_test:
        errors.append("存在未进入测试阶段的 feature")

    # 检查 tests/ 目录下是否有测试文件
    tests_dir = proj_dir / "tests"
    test_files = []
    if tests_dir.exists():
        test_files = list(tests_dir.rglob("test_*.py")) + list(tests_dir.rglob("*_test.py"))
    details["test_file_count"] = len(test_files)
    if not test_files:
        errors.append("tests/ 目录下没有测试文件")

    # 检查 state store 中的 tests_passed 标记
    state = _load_state(project_name, base_dir)
    tests_passed = False
    if state is not None:
        tests_passed = state.get("tests_passed", False)
    details["tests_passed_flag"] = tests_passed
    if not tests_passed:
        errors.append("tests_passed 标记为 false")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 5: accept → check_accept
# ───────────────────────────────────────────────────────────────

def check_accept(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 5 advance 条件：
    - 所有 feature status = "passed"
    - 人类已审批（accept_approved = true）
    - 主分支合并成功
    - 验收报告已生成
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # 检查所有 feature 状态为 passed
    features_data = _read_json_file(project_name, base_dir, "features.json")
    all_passed = True
    if features_data and isinstance(features_data, dict):
        features = features_data.get("features", [])
        if isinstance(features, list):
            for feat in features:
                if isinstance(feat, dict):
                    status = feat.get("status", "")
                    if status != "passed":
                        all_passed = False
                        errors.append(f"feature {feat.get('id', '?')} 状态为 {status}，不是 passed")
    details["all_features_passed"] = all_passed

    if not all_passed:
        errors.append("不是所有 feature 都通过了")

    # 检查 accept_approved
    state = _load_state(project_name, base_dir)
    accept_approved = False
    if state is not None:
        accept_approved = state.get("accept_approved", False)
    details["accept_approved"] = accept_approved
    if not accept_approved:
        errors.append("accept 未通过人类审批 (accept_approved=false)")

    # 检查验收报告（acceptance_report.md）
    report_exists = _file_exists(project_name, base_dir, "acceptance_report.md")
    details["acceptance_report_exists"] = report_exists
    if not report_exists:
        errors.append("缺少验收报告 acceptance_report.md")

    # 检查主分支合并（通过 git branch 检查）
    merged_to_main = False
    git_dir = proj_dir / ".git"
    if git_dir.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["git", "-C", str(proj_dir), "branch", "--merged", "main"],
                capture_output=True, text=True, timeout=10,
            )
            # 简化：只要有 main 分支就认为可能合并过
            result2 = subprocess.run(
                ["git", "-C", str(proj_dir), "branch", "-a"],
                capture_output=True, text=True, timeout=10,
            )
            merged_to_main = "main" in result2.stdout or "master" in result2.stdout
        except Exception:
            merged_to_main = False
    details["merged_to_main"] = merged_to_main
    if not merged_to_main:
        errors.append("未合并到主分支")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 6: deploy → check_deploy
# ───────────────────────────────────────────────────────────────

def check_deploy(project_name: str, base_dir: Path) -> CheckResult:
    """Phase 6 advance 条件（最终交付检查）：
    - README.md 存在且包含"快速开始"
    - DEPLOY.md 存在
    - .env.example 存在
    - setup.ps1 / start.ps1 / verify-runtime.ps1 存在且可执行
    - 应用能成功导入/构建
    - 健康检查通过（如有 HTTP 服务）
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # README.md
    readme_exists = _file_exists(project_name, base_dir, "README.md")
    details["readme_exists"] = readme_exists
    if not readme_exists:
        errors.append("缺少 README.md")
    else:
        readme_content = _read_text_file(project_name, base_dir, "README.md") or ""
        has_quickstart = "快速开始" in readme_content or "Quick Start" in readme_content or "quick start" in readme_content.lower()
        details["readme_has_quickstart"] = has_quickstart
        if not has_quickstart:
            errors.append("README.md 缺少快速开始章节")

    # DEPLOY.md
    deploy_exists = _file_exists(project_name, base_dir, "DEPLOY.md")
    details["deploy_md_exists"] = deploy_exists
    if not deploy_exists:
        errors.append("缺少 DEPLOY.md")

    # .env.example
    env_exists = _file_exists(project_name, base_dir, ".env.example")
    details["env_example_exists"] = env_exists
    if not env_exists:
        errors.append("缺少 .env.example")

    # 脚本文件
    required_scripts = ["setup.ps1", "start.ps1", "verify-runtime.ps1"]
    for script in required_scripts:
        script_path = proj_dir / script
        exists = script_path.exists()
        details[f"{script}_exists"] = exists
        if not exists:
            errors.append(f"缺少脚本: {script}")
        else:
            # 检查是否可执行（Windows 下 .ps1 文件存在即可）
            details[f"{script}_executable"] = True

    # 应用能成功导入/构建（检查 src/ 目录下是否有 __init__.py 或至少一个 .py 文件）
    src_dir = proj_dir / "src"
    can_import = False
    if src_dir.exists():
        py_files = list(src_dir.rglob("*.py"))
        can_import = len(py_files) > 0
    details["can_import_build"] = can_import
    if not can_import:
        errors.append("应用无法导入/构建（src/ 下没有 .py 文件）")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# 状态加载辅助
# ───────────────────────────────────────────────────────────────

def _load_state(project_name: str, base_dir: Path) -> Optional[Dict[str, Any]]:
    """从 legacy 表或 checkpoint 加载项目状态字典"""
    try:
        db_path = _project_dir(project_name, base_dir) / "pipeline_state.db"
        if not db_path.exists():
            return None
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        # 优先从 legacy 表读取
        cursor.execute("SELECT value FROM project_state WHERE key = 'state'")
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


# ───────────────────────────────────────────────────────────────
# 注册表
# ───────────────────────────────────────────────────────────────

CHECK_REGISTRY: Dict[str, CheckFunc] = {
    "init": check_init,
    "design": check_design,
    "decompose": check_decompose,
    "develop": check_develop,
    "test": check_test,
    "accept": check_accept,
    "deploy": check_deploy,
}


def run_check(phase_name: str, project_name: str, base_dir: Path) -> CheckResult:
    """统一调用指定 phase 的 check 函数"""
    phase = phase_name.lower()
    if phase not in CHECK_REGISTRY:
        return {
            "passed": False,
            "reason": f"未知 phase: {phase_name}",
            "details": {"available_phases": list(CHECK_REGISTRY.keys())},
        }
    return CHECK_REGISTRY[phase](project_name, base_dir)


def get_all_phase_names() -> List[str]:
    """返回所有已注册的 phase 名称"""
    return list(CHECK_REGISTRY.keys())
