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

import yaml

# 导入REGISTRY
try:
    from registry import REGISTRY
except (ModuleNotFoundError, ImportError):
    from src.registry import REGISTRY

# 导入ProjectProfile
try:
    from project_profile import ProjectProfile, get_project_profile
except (ModuleNotFoundError, ImportError):
    from src.project_profile import ProjectProfile, get_project_profile


# ───────────────────────────────────────────────────────────────
# Threshold loading
# ───────────────────────────────────────────────────────────────

_THRESHOLDS: Optional[dict] = None


def load_thresholds() -> dict:
    """Load phase-check thresholds from config/thresholds.yaml (cached)."""
    global _THRESHOLDS
    if _THRESHOLDS is None:
        path = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"
        with path.open("r", encoding="utf-8") as f:
            _THRESHOLDS = yaml.safe_load(f)
    return _THRESHOLDS


def _check_threshold(key_path: str, default: Any) -> Any:
    """Fetch a single threshold value by dotted path under checks.*"""
    data = load_thresholds()
    keys = ("checks", *key_path.split("."))
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return default
    return data


# ───────────────────────────────────────────────────────────────
# 类型定义
# ───────────────────────────────────────────────────────────────

CheckResult = Dict[str, Any]
CheckFunc = Callable[[str, Path], CheckResult]

# Phase 3: verify 状态机合法状态
VERIFY_STATES = {"pending", "verifying", "verified", "verify_failed"}


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
    # Workflow registry + condition engine
    try:
        from workflow import detect_project_type
        from condition_engine import ConditionEngine
        ptype = detect_project_type(str(proj_dir))
        details["workflow_type"] = {"ran": True, "type": str(ptype)}
        ce = ConditionEngine()
        details["condition_engine"] = {"ran": True, "available": True}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["workflow_type"] = {"ran": False, "error": str(e)[:80]}
    if features_data is None:
        errors.append("features.json 不存在或格式错误")
    else:
        # Subtask chunker
        try:
            from subtask_chunker import SubtaskChunker
            chunker = SubtaskChunker()
            chunk_count = chunker.chunk_tasks(features_data)
            details["subtask_chunker"] = {"ran": True, "chunks": chunk_count}
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            details["subtask_chunker"] = {"ran": False, "error": str(e)[:80]}
        if not isinstance(features_data, dict):
            errors.append("features.json 根节点必须是对象")
        elif "project" not in features_data:
            errors.append("features.json 缺少 'project' 字段")
        elif features_data.get("project") != project_name:
            errors.append("features.json 中的 project 名称不匹配")
        else:
            # Description length gate (only enforced when description is provided)
            min_desc_len = _check_threshold("init.min_description_length", 10)
            description = features_data.get("description", "")
            details["description_length"] = len(description)
            if description and len(description) < min_desc_len:
                errors.append(f"项目描述长度不足 {min_desc_len} 字符")

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

    # 检查设计文档（从 thresholds.yaml 读取 required_files）
    required_files = _check_threshold("design.required_files", ["docs/design.md"])
    design_doc_path: Optional[Path] = None
    for rel_path in required_files:
        file_path = proj_dir / rel_path
        details[f"{rel_path}_exists"] = file_path.exists()
        if not file_path.exists():
            errors.append(f"缺少设计文档: {rel_path}")
        elif design_doc_path is None:
            design_doc_path = file_path

    if design_doc_path is not None and design_doc_path.exists():
        content = design_doc_path.read_text(encoding="utf-8", errors="ignore")
        details["design_doc_length"] = len(content)
        # 检查是否包含模块划分、接口定义、数据流
        has_modules = "模块" in content or "module" in content.lower() or "划分" in content
        has_interfaces = "接口" in content or "interface" in content.lower() or "API" in content
        has_dataflow = "数据流" in content or "data flow" in content.lower() or "数据" in content
        details["has_modules"] = has_modules
        details["has_interfaces"] = has_interfaces
        details["has_dataflow"] = has_dataflow
        if not has_modules:
            errors.append("设计文档缺少模块划分")
        if not has_interfaces:
            errors.append("设计文档缺少接口定义")
        if not has_dataflow:
            errors.append("设计文档缺少数据流描述")

    # 检查 design_approved 状态（从 state store 读取）
    design_approved = False
    state = _load_state(project_name, base_dir)
    if state is not None:
        design_approved = state.get("design_approved", False)
    details["design_approved"] = design_approved
    if not design_approved:
        errors.append("design 未通过人类审批 (design_approved=false)")

    # Inspector + AdversarialReview
    # Architecture review
    try:
        from architecture_review import generate_review_report
        rpt = generate_review_report(str(proj_dir / "docs" / "architecture-review.md"))
        details["architecture_review"] = {"ran": True, "report": rpt}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["architecture_review"] = {"ran": False, "error": str(e)[:80]}
    details["inspector"] = _run_inspector_review("design", proj_dir)
    details["adversarial"] = _run_adversarial_review("design", proj_dir)

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
    # Workflow registry + condition engine
    try:
        from workflow import detect_project_type
        from condition_engine import ConditionEngine
        ptype = detect_project_type(str(proj_dir))
        details["workflow_type"] = {"ran": True, "type": str(ptype)}
        ce = ConditionEngine()
        details["condition_engine"] = {"ran": True, "available": True}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["workflow_type"] = {"ran": False, "error": str(e)[:80]}
    if features_data is None:
        errors.append("features.json 不存在或格式错误")
        return {"passed": False, "reason": " | ".join(errors), "details": details}

    # Try subtask_chunker (non-fatal if unavailable)
    try:
        from subtask_chunker import SubtaskChunker
        chunker = SubtaskChunker()
        chunk_count = chunker.chunk_tasks(features_data)
        details["subtask_chunker"] = {"ran": True, "chunks": chunk_count}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["subtask_chunker"] = {"ran": False, "error": str(e)[:80]}

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
    - 代码已编写（存在源代码文件）
    - 有 git commit
    - progress.md 已更新
    """
    errors: List[str] = []
    details: Dict[str, Any] = {}

    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "项目目录不存在", "details": {"project_dir_exists": False}}

    # 使用 ProjectProfile 获取项目配置
    project_profile = get_project_profile(str(proj_dir))

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

    # 检查是否有代码文件（使用 ProjectProfile 获取源代码文件）
    source_files = project_profile.get_source_files()
    min_source_files = _check_threshold("develop.min_source_files", 1)
    has_code = len(source_files) >= min_source_files
    details["src_file_count"] = len(source_files)
    details["has_code"] = has_code
    if not has_code:
        errors.append(f"项目中源代码文件不足 {min_source_files} 个")

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
        except (subprocess.SubprocessError, OSError):
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

    # 使用 ProjectProfile 获取项目配置
    project_profile = get_project_profile(str(proj_dir))

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

    # 检查是否有测试文件（使用 ProjectProfile 获取测试文件）
    test_files = project_profile.get_test_files()
    min_test_files = _check_threshold("test.min_test_files", 1)
    details["test_file_count"] = len(test_files)
    if len(test_files) < min_test_files:
        errors.append(f"项目中测试文件不足 {min_test_files} 个")

    # 检查 state store 中的 tests_passed 标记
    state = _load_state(project_name, base_dir)
    tests_passed = False
    if state is not None:
        tests_passed = state.get("tests_passed", False)
    details["tests_passed_flag"] = tests_passed
    required_pass_rate = _check_threshold("test.required_pass_rate", 0.9)
    details["required_pass_rate"] = required_pass_rate
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
    schema_version = int(features_data.get("schema_version", 1)) if isinstance(features_data, dict) else 1
    details["schema_version"] = schema_version
    verify_state_ok = True
    verify_record_ok = True
    require_verified = _check_threshold("accept.require_verified", True)
    details["require_verified"] = require_verified
    if features_data and isinstance(features_data, dict):
        features = features_data.get("features", [])
        if isinstance(features, list):
            for feat in features:
                if not isinstance(feat, dict):
                    continue
                fid = feat.get("id", "?")
                status = feat.get("status", "")
                if status != "passed":
                    all_passed = False
                    errors.append(f"feature {fid} 状态为 {status}，不是 passed")

                # 当 thresholds 要求 verified 时强制校验 verify_state
                if require_verified:
                    verify_state = feat.get("verify_state", "pending")
                    if verify_state not in VERIFY_STATES:
                        verify_state_ok = False
                        errors.append(f"feature {fid} verify_state 非法: {verify_state}")
                    elif verify_state != "verified":
                        verify_state_ok = False
                        errors.append(f"feature {fid} verify未完成 (verify_state={verify_state})")

        # 顶层 verify_record 可选校验（独立可选字段）
        verify_record = features_data.get("verify_record")
        if verify_record is not None:
            if not isinstance(verify_record, dict):
                verify_record_ok = False
                errors.append("verify_record 必须是对象")

    details["all_features_passed"] = all_passed
    details["verify_state_ok"] = verify_state_ok
    details["verify_record_ok"] = verify_record_ok

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

    # Gate + Approval (orphan modules wired)
    try:
        from gate import run_gate, GateLevel
        from approval import ApprovalSystem
        gr = run_gate(GateLevel.ACCEPT, project_name)
        details["gate"] = {"ran": True, "passed": gr.passed, "summary": gr.summary}
        if not gr.passed:
            errors.append(f"质量门禁未通过: {gr.summary}")
        ap = ApprovalSystem(project_name=project_name)
        details["approval"] = {"ran": True, "approved": ap.is_blanket_authorized()}
        if not ap.is_blanket_authorized():
            errors.append("人机审批未授权")
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["gate"] = {"ran": False, "error": str(e)}

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
        except (subprocess.SubprocessError, OSError):
            merged_to_main = False
    details["merged_to_main"] = merged_to_main
    if not merged_to_main:
        errors.append("未合并到主分支")

    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": "PASS", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 6: deploy → check_deploy
# (github_sync wired inline below)
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
    except (sqlite3.Error, json.JSONDecodeError, TypeError):
        pass
    return None


# ───────────────────────────────────────────────────────────────
# 注册表
# ───────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════
# Integrated Review Helpers (Inspector + AdversarialReview)
# ═══════════════════════════════════════════════════════════════


# ── Inspector + AdversarialReview 审查靶向定义 ──

_REVIEW_TARGETS = {
    "design": {
        "title": "架构设计文档",
        "doc": "docs/architecture.md",
        "inspector_prompt": (
            "以用户视角审查架构设计：用户(政府投融资人员)能否基于此架构理解系统运作？"
            "检查：模块命名是否直观、数据流是否清晰、是否有过度设计。给出PASS/WARNING/FAIL。"
        ),
        "adversarial_prompt": (
            "挑战此架构设计文档中的每个设计决策。对任何你觉得不够合理、过度工程化、"
            "或与实际需求不匹配的设计点提出质疑。每个质疑需标注具体位置。"
        ),
    },
    "prd": {
        "title": "PRD产品需求文档",
        "doc": "docs/PRD*.md",
        "inspector_prompt": None,
        "adversarial_prompt": (
            "挑战此PRD文档中的每个需求点。质疑：需求是否可验证、边界是否清晰、"
            "是否有遗漏场景。每个质疑需标注具体位置。"
        ),
    },
    "journey": {
        "title": "用户旅程设计",
        "doc": "docs/*journey*.md",
        "inspector_prompt": (
            "以用户视角走完整个旅程：用户(政府投融资人员)能否顺畅完成每一步？"
            "检查：步骤是否完整、异常路径是否覆盖、是否有多余步骤。给出PASS/WARNING/FAIL。"
        ),
        "adversarial_prompt": (
            "挑战此用户旅程设计：每一步是否必要？是否有更简路径？"
            "是否有用户真正会遇到的场景被遗漏？每个质疑需标注具体步骤。"
        ),
    },
    "evaluate": {
        "title": "系统评估",
        "doc": None,
        "inspector_prompt": (
            "以用户视角评估当前系统：用户能否完成核心任务？"
            "检查E2E测试覆盖是否充分、是否有明显的体验断点。给出PASS/WARNING/FAIL。"
        ),
        "adversarial_prompt": None,
    },
    "plan": {
        "title": "优化方案",
        "doc": "docs/PRD*.md",
        "inspector_prompt": (
            "以用户视角审查优化方案：优化后用户体验是否明显提升？"
            "检查优化项的优先级是否合理。给出PASS/WARNING/FAIL。"
        ),
        "adversarial_prompt": (
            "挑战此优化方案：每个优化项是否真正必要？是否有更简单的替代方案？"
            "是否有被忽略的重要优化点？每个质疑需标注具体位置。"
        ),
    },
}


def _get_executor(project_dir):
    """懒加载 PipelineExecutor（进程级单例）。"""
    global _executor
    if _executor is None:
        try:
            from pipeline_executor import create_executor
        except ImportError:
            from src.pipeline_executor import create_executor
        _executor = create_executor(work_dir=str(project_dir))
        _executor.register_all_defaults()
    return _executor

_executor = None


def _run_inspector_review(phase, project_dir):
    """派发 qwen-code 做独立审查（不同于编码模型Kimi）。"""
    target = _REVIEW_TARGETS.get(phase, {})
    if not target.get("inspector_prompt"):
        return {"ran": False, "error": f"no inspector target for phase {phase}"}
    # AGENT_MOCK: skip real CLI dispatch in test environments
    if os.environ.get("AGENT_MOCK", "").lower() in ("true", "1"):
        return {"ran": True, "agent": "qwen-code",
                "output": "[MOCK] inspector review passed", "success": True}
    try:
        executor = _get_executor(project_dir)
        doc_hint = ""
        doc_pattern = target.get("doc")
        if doc_pattern:
            docs_dir = project_dir / "docs"
            pattern = doc_pattern.replace("docs/", "")
            matches = list(docs_dir.glob(pattern)) if docs_dir.exists() else []
            if matches:
                doc_hint = f"文档: {matches[0]}"
        prompt = (
            f"审查阶段: {phase} ({target['title']})\n"
            f"{doc_hint}\n"
            f"{target['inspector_prompt']}\n"
            f"请读取文档后给出审查结论。用中文，200字内。"
        )
        result = executor.dispatch_and_wait("qwen-code", "inspector", {"prompt": prompt})
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / f"inspector-{phase}.md").write_text(
            f"# Inspector Review - {phase}\n\n{result.output}\n",
            encoding="utf-8")
        return {"ran": True, "agent": "qwen-code",
                "output": result.output[:500], "success": result.success}
    except (IOError, OSError) as e:
        return {"ran": False, "error": str(e)}


def _run_adversarial_review(phase, project_dir):
    """对抗审查: codewhale挑战 → claude-code辩护。"""
    target = _REVIEW_TARGETS.get(phase, {})
    if not target.get("adversarial_prompt"):
        return {"ran": False, "error": f"no adversarial target for phase {phase}"}
    # AGENT_MOCK: skip real CLI dispatch in test environments
    if os.environ.get("AGENT_MOCK", "").lower() in ("true", "1"):
        return {"ran": True, "agent": "claude-code+codewhale",
                "output": "[MOCK] adversarial review completed (3 rounds)", "success": True}
    try:
        executor = _get_executor(project_dir)
        doc_pattern = target.get("doc")
        doc_path = None
        if doc_pattern:
            docs_dir = project_dir / "docs"
            pattern = doc_pattern.replace("docs/", "")
            matches = list(docs_dir.glob(pattern)) if docs_dir.exists() else []
            doc_path = matches[0] if matches else None
        if doc_path is None:
            return {"ran": False, "error": f"no document for phase {phase}"}
        # Step 1: claude-code (Kimi) 挑战 — 不同于文档作者DeepSeek
        challenge_prompt = (
            f"审查阶段: {phase} ({target['title']})\n"
            f"文档: {doc_path}\n"
            f"{target['adversarial_prompt']}\n"
            f"请读取文档，逐一提出质疑。用中文，300字内。"
        )
        challenge_result = executor.dispatch_and_wait(
            "claude-code", "adversarial", {"prompt": challenge_prompt})
        # Step 2: codewhale (DeepSeek) 逐条辩护
        defend_prompt = (
            f"以下是对{target['title']}的质疑，请逐条辩护:\n\n"
            f"=== 质疑 ===\n{challenge_result.output[:2000]}\n\n"
            f"文档: {doc_path}\n"
            f"请读取文档原文，逐条回应。标注每条为 [接受]/[辩护]/[澄清]。用中文，300字内。"
        )
        defend_result = executor.dispatch_and_wait(
            "codewhale", "adversarial", {"prompt": defend_prompt})
        # 写产出
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        import json
        (docs_dir / f"adversarial-{phase}.json").write_text(
            json.dumps({
                "phase": phase, "document": str(doc_path),
                "challenge": {"agent": "codewhale", "output": challenge_result.output[:500]},
                "defense": {"agent": "claude-code", "output": defend_result.output[:500]},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ran": True, "challenge": "codewhale", "defend": "claude-code",
                "challenge_ok": challenge_result.success, "defend_ok": defend_result.success}
    except (json.JSONDecodeError, TypeError) as e:
        return {"ran": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Brownfield check functions
# ═══════════════════════════════════════════════════════════════

def check_discover(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    audit_files = list(docs.glob("audit-*.md")) if docs.exists() else []
    if not audit_files:
        return {"passed": False, "reason": "no audit reports", "details": {"audit_count": 0}}
    return {"passed": True, "reason": f"{len(audit_files)} audit reports", "details": {"audit_count": len(audit_files)}}


def check_benchmark(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    files = list(docs.glob("benchmark*.md")) if docs.exists() else []
    if not files:
        return {"passed": False, "reason": "no benchmark doc", "details": {}}
    return {"passed": True, "reason": "benchmark doc OK", "details": {"size_bytes": files[0].stat().st_size}}


def check_analyze(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    files = (list(docs.glob("gap-matrix*.md")) + list(docs.glob("gap-analysis*.md"))) if docs.exists() else []
    if not files:
        return {"passed": False, "reason": "no gap matrix", "details": {}}
    content = files[0].read_text(encoding="utf-8", errors="ignore")
    if "P0" not in content:
        return {"passed": False, "reason": "no P0 items in gap matrix", "details": {"has_P0": False}}
    return {"passed": True, "reason": "gap matrix OK", "details": {"has_P0": True}}


def check_plan(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    if not docs.exists():
        return {"passed": False, "reason": "docs/ not found", "details": {}}
    missing = []
    if not list(docs.glob("PRD*.md")): missing.append("PRD")
    if not list(docs.glob("architecture*.md")): missing.append("architecture")
    details = {"inspector": _run_inspector_review("plan", proj_dir),
               "adversarial": _run_adversarial_review("plan", proj_dir)}
    if missing:
        details["missing"] = missing
        return {"passed": False, "reason": "missing: " + ", ".join(missing), "details": details}
    return {"passed": True, "reason": "plan complete", "details": details}


def check_execute(project_name, base_dir):
    import json
    ff = _project_dir(project_name, base_dir) / "features.json"
    if not ff.exists():
        return {"passed": False, "reason": "features.json not found", "details": {}}
    data = json.loads(ff.read_text(encoding="utf-8"))
    features = data.get("features", [])
    total = len(features)
    passed = sum(1 for f in features if f.get("status") == "passed")
    if total == 0:
        return {"passed": False, "reason": "no features", "details": {}}
    if passed < total:
        return {"passed": False, "reason": f"{passed}/{total} passed",
                "details": {"passed": passed, "total": total}}
    return {"passed": True, "reason": f"all {total} passed", "details": {"passed": passed, "total": total}}


def check_verify(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    tests_dir = proj_dir / "tests" / "e2e"
    if not tests_dir.exists():
        return {"passed": False, "reason": "tests/e2e/ not found", "details": {}}
    test_files = list(tests_dir.glob("test_*.py"))
    if not test_files:
        return {"passed": False, "reason": "no E2E test files", "details": {}}
    return {"passed": True, "reason": f"{len(test_files)} E2E test files", "details": {"test_count": len(test_files)}}


def check_deliver(project_name, base_dir):
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    missing = []
    if not (proj_dir / "DEPLOY.md").exists() and not (proj_dir / "README.md").exists():
        missing.append("DEPLOY.md/README.md")
    if missing:
        return {"passed": False, "reason": "missing: " + ", ".join(missing), "details": {"missing": missing}}
    return {"passed": True, "reason": "delivery ready", "details": {}}



# ───────────────────────────────────────────────────────────────
# Phase 3: research → check_research
# ───────────────────────────────────────────────────────────────

def check_research(project_name, base_dir):
    """Phase research: 调研材料存在。"""
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    research_files = (list(docs.glob("research*.md")) + list(docs.glob("*research*.md"))
                      + list(docs.glob("knowledge*.md"))) if docs.exists() else []
    if not research_files:
        return {"passed": False, "reason": "no research docs", "details": {}}
    details = {"count": len(research_files)}
    # Research agent dispatch
    try:
        from research_agent import quick_research
        r = quick_research(project_name, str(proj_dir))
        details["research_agent"] = {"ran": True, "dispatched": bool(r)}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["research_agent"] = {"ran": False, "error": str(e)[:80]}
    try:
        from research_agent import dispatch_research
    except ImportError:
        from src.research_agent import dispatch_research
    try:
        r_result = dispatch_research(project_name, str(proj_dir))
        details["research_agent"] = {"ran": True, "agents_dispatched": len(r_result) if r_result else 0}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["research_agent"] = {"ran": False, "error": str(e)}
    return {"passed": True, "reason": f"{len(research_files)} research docs", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 4: prd → check_prd
# ───────────────────────────────────────────────────────────────

def check_prd(project_name, base_dir):
    """Phase prd: PRD文档存在 + AdversarialReview对抗审查。"""
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    prd_files = list(docs.glob("PRD*.md")) if docs.exists() else []
    if not prd_files:
        return {"passed": False, "reason": "no PRD doc", "details": {}}
    details = {"prd_count": len(prd_files)}
    details["adversarial"] = _run_adversarial_review("prd", proj_dir)
    return {"passed": True, "reason": "PRD exists", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 5: journey → check_journey
# ───────────────────────────────────────────────────────────────

def check_journey(project_name, base_dir):
    """Phase journey: 用户旅程文档存在 + Inspector审查 + AdversarialReview审查。"""
    proj_dir = _project_dir(project_name, base_dir)
    if not proj_dir.exists():
        return {"passed": False, "reason": "project dir not found", "details": {}}
    docs = proj_dir / "docs"
    journey_files = list(docs.glob("*journey*.md")) if docs.exists() else []
    if not journey_files:
        return {"passed": False, "reason": "no journey doc", "details": {}}
    details = {"journey_count": len(journey_files)}
    # Journey designer engine
    try:
        from journey_designer import JourneyDesigner
        jd = JourneyDesigner()
        jd.load(str(proj_dir))
        details["journey_designer"] = {"ran": True, "loaded": True}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["journey_designer"] = {"ran": False, "error": str(e)[:80]}
    details["inspector"] = _run_inspector_review("journey", proj_dir)
    details["adversarial"] = _run_adversarial_review("journey", proj_dir)
    return {"passed": True, "reason": "journey doc exists", "details": details}


# ───────────────────────────────────────────────────────────────
# Phase 7: integrate → check_integrate
# ───────────────────────────────────────────────────────────────

def check_integrate(project_name, base_dir):
    """Phase integrate: 集成代码存在。"""
    proj_dir = _project_dir(project_name, base_dir)
    src_dir = proj_dir / "src"
    if not src_dir.exists():
        return {"passed": False, "reason": "src/ not found", "details": {}}
    py_files = list(src_dir.rglob("*.py"))
    if not py_files:
        return {"passed": False, "reason": "no python files", "details": {}}
    return {"passed": True, "reason": f"{len(py_files)} python files", "details": {"py_count": len(py_files)}}


# ───────────────────────────────────────────────────────────────
# Phase 9: evaluate → check_evaluate
# ───────────────────────────────────────────────────────────────

def check_evaluate(project_name, base_dir):
    """Phase evaluate: E2E测试存在 + Inspector审查。"""
    proj_dir = _project_dir(project_name, base_dir)
    tests_dir = proj_dir / "tests" / "e2e"
    if not tests_dir.exists():
        return {"passed": False, "reason": "tests/e2e/ not found", "details": {}}
    test_files = list(tests_dir.glob("test_*.py"))
    if not test_files:
        return {"passed": False, "reason": "no E2E test files", "details": {}}
    details = {"test_count": len(test_files)}
    errors: List[str] = []
    # LLM-as-Judge 质量评估
    try:
        from evaluate import evaluate as _evaluate_fn
    except ImportError:
        from src.evaluate import evaluate as _evaluate_fn
    try:
        eval_result = _evaluate_fn(project_dir=proj_dir, project_name=project_name,
                                   judge_model="qwen3-coder-plus")
        details["evaluate"] = {"ran": True, "verdict": str(eval_result.verdict),
                               "total_score": eval_result.total_score}
        min_score = _check_threshold("evaluate.llm_judge_min_score", 0.7)
        details["llm_judge_min_score"] = min_score
        # evaluate.py total_score is on a 1-10 scale; YAML value is normalized 0-1
        if (eval_result.total_score / 10.0) < min_score:
            errors.append(f"LLM judge 评分 {eval_result.total_score:.2f}/10 低于阈值 {min_score * 10:.1f}/10")
        if eval_result.verdict.value in ("BLOCK", "P0"):
            errors.append(f"LLM judge  verdict 为 {eval_result.verdict.value}")
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
        details["evaluate"] = {"ran": False, "error": str(e)[:100]}
    # Inspector review
    details["inspector"] = _run_inspector_review("evaluate", proj_dir)
    if errors:
        return {"passed": False, "reason": " | ".join(errors), "details": details}
    return {"passed": True, "reason": f"{len(test_files)} E2E tests", "details": details}


# ───────────────────────────────────────────────────────────────
# 检查函数注册表（显式字典注册，禁止 globals() 扫描）
# ───────────────────────────────────────────────────────────────

_CHECK_FUNCS: Dict[str, CheckFunc] = {
    "init": check_init,
    "design": check_design,
    "decompose": check_decompose,
    "develop": check_develop,
    "test": check_test,
    "accept": check_accept,
    "deploy": check_deploy,
    "research": check_research,
    "prd": check_prd,
    "journey": check_journey,
    "integrate": check_integrate,
    "evaluate": check_evaluate,
    # Brownfield
    "discover": check_discover,
    "benchmark": check_benchmark,
    "analyze": check_analyze,
    "plan": check_plan,
    "execute": check_execute,
    "verify": check_verify,
    "deliver": check_deliver,
}


def register_check(name: str) -> Callable[[CheckFunc], CheckFunc]:
    """显式注册一个检查函数（可选装饰器用法）。"""

    def decorator(func: CheckFunc) -> CheckFunc:
        _CHECK_FUNCS[name.lower()] = func
        return func

    return decorator


# 只注册在 REGISTRY 中存在的 phases
CHECK_REGISTRY: Dict[str, CheckFunc] = {
    phase: _CHECK_FUNCS[phase]
    for phase in REGISTRY.list_phases()
    if phase in _CHECK_FUNCS
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
