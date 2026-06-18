"""tests/test_phase_checks.py — phase_checks.py 单元测试

至少 14 个测试，覆盖所有 7 个 check 函数的通过/失败场景。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# 将 src 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from phase_checks import (
    CHECK_REGISTRY,
    check_init,
    check_design,
    check_decompose,
    check_develop,
    check_test,
    check_accept,
    check_deploy,
    run_check,
    get_all_phase_names,
)


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """在临时目录中运行测试"""
    tmpdir = tempfile.mkdtemp(prefix="phase_checks_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def minimal_project(tmp_cwd: Path) -> tuple[str, Path]:
    """创建一个最小项目目录结构（仅满足目录存在）"""
    project_name = "test_project"
    proj_dir = tmp_cwd / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    return project_name, tmp_cwd


@pytest.fixture
def init_project(tmp_cwd: Path) -> tuple[str, Path]:
    """创建一个满足 check_init 通过条件的项目"""
    project_name = "test_project"
    proj_dir = tmp_cwd / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    # 创建元数据文件
    (proj_dir / "SOUL.md").write_text("# SOUL\n", encoding="utf-8")
    (proj_dir / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (proj_dir / "progress.md").write_text("# progress\n", encoding="utf-8")
    (proj_dir / "features.json").write_text(
        json.dumps({"project": project_name, "features": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 初始化 git
    os.system(f"cd {proj_dir} && git init -q")

    # 创建 SQLite DB（空文件即可）
    (proj_dir / "pipeline_state.db").touch()

    return project_name, tmp_cwd


@pytest.fixture
def design_project(init_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_design 通过条件的项目"""
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name

    # 创建 specs/architecture.md
    (proj_dir / "specs").mkdir(exist_ok=True)
    (proj_dir / "specs" / "architecture.md").write_text(
        "# 架构\n\n## 模块划分\n模块A、模块B\n\n## 接口定义\nAPI /v1/users\n\n## 数据流\n数据从A流向B\n",
        encoding="utf-8",
    )

    # 创建 state store 并设置 design_approved
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    state = {"design_approved": True, "name": project_name, "phase": "design"}
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    return project_name, base_dir


@pytest.fixture
def decompose_project(design_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_decompose 通过条件的项目"""
    project_name, base_dir = design_project
    proj_dir = base_dir / project_name

    features = {
        "project": project_name,
        "features": [
            {
                "id": "F001",
                "title": "Feature 1",
                "description": "desc",
                "acceptance_criteria": ["ac1"],
                "dependencies": [],
                "estimated_complexity": "simple",
                "owner_agent": "agent1",
                "status": "pending",
                "wave": 1,
            },
            {
                "id": "F002",
                "title": "Feature 2",
                "description": "desc",
                "acceptance_criteria": ["ac2"],
                "dependencies": ["F001"],
                "estimated_complexity": "medium",
                "owner_agent": "agent2",
                "status": "pending",
                "wave": 2,
            },
        ],
    }
    (proj_dir / "features.json").write_text(
        json.dumps(features, ensure_ascii=False), encoding="utf-8"
    )

    return project_name, base_dir


@pytest.fixture
def develop_project(decompose_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_develop 通过条件的项目"""
    project_name, base_dir = decompose_project
    proj_dir = base_dir / project_name

    # 创建 src/ 目录和代码文件
    (proj_dir / "src").mkdir(exist_ok=True)
    (proj_dir / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    # 更新 features.json 状态为 passed
    features = {
        "project": project_name,
        "features": [
            {
                "id": "F001",
                "title": "Feature 1",
                "acceptance_criteria": ["ac1"],
                "dependencies": [],
                "estimated_complexity": "simple",
                "owner_agent": "agent1",
                "status": "passed",
                "wave": 1,
            },
        ],
    }
    (proj_dir / "features.json").write_text(
        json.dumps(features, ensure_ascii=False), encoding="utf-8"
    )

    # 更新 progress.md
    (proj_dir / "progress.md").write_text(
        "# progress\n\n当前 Phase: develop\n", encoding="utf-8"
    )

    # 配置 git 并创建 commit
    os.system(f'cd {proj_dir} && git config user.email "test@test.com" && git config user.name "Test" && git add -A && git commit -m "init" -q')

    return project_name, base_dir


@pytest.fixture
def test_project(develop_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_test 通过条件的项目"""
    project_name, base_dir = develop_project
    proj_dir = base_dir / project_name

    # 创建测试文件
    (proj_dir / "tests").mkdir(exist_ok=True)
    (proj_dir / "tests" / "test_app.py").write_text(
        "def test_app(): assert True\n", encoding="utf-8"
    )

    # 更新 state store 设置 tests_passed
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    state = {
        "tests_passed": True,
        "name": project_name,
        "phase": "test",
    }
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    return project_name, base_dir


@pytest.fixture
def accept_project(test_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_accept 通过条件的项目"""
    project_name, base_dir = test_project
    proj_dir = base_dir / project_name

    # 更新 features.json 所有 feature 为 passed
    features = {
        "project": project_name,
        "features": [
            {
                "id": "F001",
                "title": "Feature 1",
                "acceptance_criteria": ["ac1"],
                "dependencies": [],
                "estimated_complexity": "simple",
                "owner_agent": "agent1",
                "status": "passed",
                "wave": 1,
            },
        ],
    }
    (proj_dir / "features.json").write_text(
        json.dumps(features, ensure_ascii=False), encoding="utf-8"
    )

    # 创建验收报告
    (proj_dir / "acceptance_report.md").write_text("# 验收报告\n", encoding="utf-8")

    # 更新 state store 设置 accept_approved
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    state = {
        "accept_approved": True,
        "tests_passed": True,
        "name": project_name,
        "phase": "accept",
    }
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    return project_name, base_dir


@pytest.fixture
def deploy_project(accept_project: tuple[str, Path]) -> tuple[str, Path]:
    """创建一个满足 check_deploy 通过条件的项目"""
    project_name, base_dir = accept_project
    proj_dir = base_dir / project_name

    # 创建部署相关文件
    (proj_dir / "README.md").write_text(
        "# Project\n\n## 快速开始\n\n1. 安装依赖\n2. 运行\n",
        encoding="utf-8",
    )
    (proj_dir / "DEPLOY.md").write_text("# 部署指南\n", encoding="utf-8")
    (proj_dir / ".env.example").write_text("# 环境变量\n", encoding="utf-8")
    (proj_dir / "setup.ps1").write_text("Write-Host 'setup'\n", encoding="utf-8")
    (proj_dir / "start.ps1").write_text("Write-Host 'start'\n", encoding="utf-8")
    (proj_dir / "verify-runtime.ps1").write_text("Write-Host 'verify'\n", encoding="utf-8")

    return project_name, base_dir


# ───────────────────────────────────────────────────────────────
# 1. CHECK_REGISTRY 与工具函数测试
# ───────────────────────────────────────────────────────────────

def test_check_registry_has_all_phases() -> None:
    expected = ["init", "design", "decompose", "develop", "test", "accept", "deploy"]
    assert list(CHECK_REGISTRY.keys()) == expected


def test_get_all_phase_names() -> None:
    names = get_all_phase_names()
    assert len(names) == 7
    assert "init" in names
    assert "deploy" in names


def test_run_check_unknown_phase(tmp_cwd: Path) -> None:
    result = run_check("unknown", "proj", tmp_cwd)
    assert result["passed"] is False
    assert "未知 phase" in result["reason"]


def test_run_check_init_pass(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    result = run_check("init", project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"


# ───────────────────────────────────────────────────────────────
# 2. check_init 测试
# ───────────────────────────────────────────────────────────────

def test_check_init_pass(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    result = check_init(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["project_dir_exists"] is True
    assert result["details"]["git_init"] is True
    assert result["details"]["db_created"] is True


def test_check_init_fail_missing_dir(tmp_cwd: Path) -> None:
    result = check_init("nonexistent", tmp_cwd)
    assert result["passed"] is False
    assert "项目目录不存在" in result["reason"]


def test_check_init_fail_missing_files(minimal_project: tuple[str, Path]) -> None:
    project_name, base_dir = minimal_project
    result = check_init(project_name, base_dir)
    assert result["passed"] is False
    assert "缺少文件" in result["reason"]


def test_check_init_fail_bad_features_json(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name
    # 写入错误的 features.json（project 名称不匹配）
    (proj_dir / "features.json").write_text(
        json.dumps({"project": "wrong_name", "features": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = check_init(project_name, base_dir)
    assert result["passed"] is False
    assert "project 名称不匹配" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 3. check_design 测试
# ───────────────────────────────────────────────────────────────

def test_check_design_pass(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    result = check_design(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["design_approved"] is True
    assert result["details"]["has_modules"] is True
    assert result["details"]["has_interfaces"] is True
    assert result["details"]["has_dataflow"] is True


def test_check_design_fail_missing_architecture(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    result = check_design(project_name, base_dir)
    assert result["passed"] is False
    assert "architecture.md" in result["reason"]


def test_check_design_fail_not_approved(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name
    (proj_dir / "specs").mkdir(exist_ok=True)
    (proj_dir / "specs" / "architecture.md").write_text(
        "# 架构\n\n## 模块划分\nA\n\n## 接口定义\nAPI\n\n## 数据流\nflow\n",
        encoding="utf-8",
    )
    result = check_design(project_name, base_dir)
    assert result["passed"] is False
    assert "design_approved" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 4. check_decompose 测试
# ───────────────────────────────────────────────────────────────

def test_check_decompose_pass(decompose_project: tuple[str, Path]) -> None:
    project_name, base_dir = decompose_project
    result = check_decompose(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["all_have_acceptance_criteria"] is True
    assert result["details"]["all_have_wave"] is True
    assert result["details"]["dependency_cycle_detected"] is False


def test_check_decompose_fail_missing_features_json(minimal_project: tuple[str, Path]) -> None:
    project_name, base_dir = minimal_project
    result = check_decompose(project_name, base_dir)
    assert result["passed"] is False
    assert "features.json" in result["reason"]


def test_check_decompose_fail_cycle(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name
    features = {
        "project": project_name,
        "features": [
            {
                "id": "F001",
                "title": "Feature 1",
                "acceptance_criteria": ["ac1"],
                "dependencies": ["F002"],
                "estimated_complexity": "simple",
                "wave": 1,
            },
            {
                "id": "F002",
                "title": "Feature 2",
                "acceptance_criteria": ["ac2"],
                "dependencies": ["F001"],
                "estimated_complexity": "simple",
                "wave": 1,
            },
        ],
    }
    (proj_dir / "features.json").write_text(
        json.dumps(features, ensure_ascii=False), encoding="utf-8"
    )
    result = check_decompose(project_name, base_dir)
    assert result["passed"] is False
    assert "环" in result["reason"]


def test_check_decompose_fail_oversized_feature(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name
    features = {
        "project": project_name,
        "features": [
            {
                "id": "F001",
                "title": "Feature 1",
                "acceptance_criteria": ["ac1"],
                "dependencies": [],
                "estimated_complexity": "超大",
                "wave": 1,
            },
        ],
    }
    (proj_dir / "features.json").write_text(
        json.dumps(features, ensure_ascii=False), encoding="utf-8"
    )
    result = check_decompose(project_name, base_dir)
    assert result["passed"] is False
    assert "粒度过大" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 5. check_develop 测试
# ───────────────────────────────────────────────────────────────

def test_check_develop_pass(develop_project: tuple[str, Path]) -> None:
    project_name, base_dir = develop_project
    result = check_develop(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["has_code"] is True
    assert result["details"]["has_git_commit"] is True


def test_check_develop_fail_no_code(decompose_project: tuple[str, Path]) -> None:
    project_name, base_dir = decompose_project
    result = check_develop(project_name, base_dir)
    assert result["passed"] is False
    assert "没有 .py 代码文件" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 6. check_test 测试
# ───────────────────────────────────────────────────────────────

def test_check_test_pass(test_project: tuple[str, Path]) -> None:
    project_name, base_dir = test_project
    result = check_test(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["tests_passed_flag"] is True


def test_check_test_fail_no_tests(develop_project: tuple[str, Path]) -> None:
    project_name, base_dir = develop_project
    result = check_test(project_name, base_dir)
    assert result["passed"] is False
    assert "没有测试文件" in result["reason"] or "tests_passed" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 7. check_accept 测试
# ───────────────────────────────────────────────────────────────

def test_check_accept_pass(accept_project: tuple[str, Path]) -> None:
    project_name, base_dir = accept_project
    result = check_accept(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["accept_approved"] is True
    assert result["details"]["acceptance_report_exists"] is True


def test_check_accept_fail_not_approved(test_project: tuple[str, Path]) -> None:
    project_name, base_dir = test_project
    result = check_accept(project_name, base_dir)
    assert result["passed"] is False
    assert "accept_approved" in result["reason"]


# ───────────────────────────────────────────────────────────────
# 8. check_deploy 测试
# ───────────────────────────────────────────────────────────────

def test_check_deploy_pass(deploy_project: tuple[str, Path]) -> None:
    project_name, base_dir = deploy_project
    result = check_deploy(project_name, base_dir)
    assert result["passed"] is True
    assert result["reason"] == "PASS"
    assert result["details"]["readme_has_quickstart"] is True
    assert result["details"]["deploy_md_exists"] is True


def test_check_deploy_fail_missing_files(accept_project: tuple[str, Path]) -> None:
    project_name, base_dir = accept_project
    result = check_deploy(project_name, base_dir)
    assert result["passed"] is False
    assert "README.md" in result["reason"] or "DEPLOY.md" in result["reason"]


def test_check_deploy_fail_readme_no_quickstart(accept_project: tuple[str, Path]) -> None:
    project_name, base_dir = accept_project
    proj_dir = base_dir / project_name
    (proj_dir / "README.md").write_text("# Project\n\nNo quickstart section here.\n", encoding="utf-8")
    (proj_dir / "DEPLOY.md").write_text("# Deploy\n", encoding="utf-8")
    (proj_dir / ".env.example").write_text("# ENV\n", encoding="utf-8")
    for script in ["setup.ps1", "start.ps1", "verify-runtime.ps1"]:
        (proj_dir / script).write_text("# script\n", encoding="utf-8")
    result = check_deploy(project_name, base_dir)
    assert result["passed"] is False
    assert "快速开始" in result["reason"] or "Quick Start" in result["reason"]
