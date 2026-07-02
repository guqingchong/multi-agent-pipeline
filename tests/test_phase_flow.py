"""tests/test_phase_flow.py — phase_flow.py 单元测试

覆盖 PhaseFlow 类及高层便捷函数的通过/失败场景。
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

from phase_flow import (
    PhaseFlow,
    phase_check,
    phase_advance,
    phase_rollback,
    phase_approve_design,
    phase_approve_accept,
    phase_mark_tests,
    PHASE_ORDER,
)


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """在临时目录中运行测试"""
    tmpdir = tempfile.mkdtemp(prefix="phase_flow_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


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

    # 创建 SQLite DB 并初始化 state_store 表
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    state = {"name": project_name, "phase": "init"}
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

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

    # 更新 state store 设置 design_approved
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
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

    # 更新 phase 为 decompose
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    state = {"design_approved": True, "name": project_name, "phase": "decompose"}
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

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

    # 更新 phase 为 develop
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    state = {"design_approved": True, "name": project_name, "phase": "develop"}
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

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


    # 确保 git 主分支存在（需要至少一个 commit）
    import subprocess
    git_dir = proj_dir / ".git"
    if git_dir.exists():
        subprocess.run(
            ["git", "-C", str(proj_dir), "add", "-A"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(proj_dir), "commit", "-m", "init commit"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(proj_dir), "branch", "-M", "main"],
            capture_output=True, timeout=10,
        )

    # 创建 E2E 测试脚本（输出 JSON 评分，grade 不为 D/F）
    e2e_dir = proj_dir / "tests" / "e2e"
    e2e_dir.mkdir(parents=True, exist_ok=True)
    (e2e_dir / "test_e2e_accept.py").write_text(
        'import json\nprint(json.dumps({"grade": "B", "total": 8}))\n',
        encoding="utf-8",
    )

    # 创建 E2E 基准库文件
    benchmark_dir = Path.home() / ".hermes"
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    benchmark_path = benchmark_dir / "e2e-benchmark.json"
    benchmark_path.write_text(
        json.dumps({"project_averages": {project_name: {"avg_total": 7.0}}}, ensure_ascii=False),
        encoding="utf-8",
    )


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

    # 更新 phase 为 deploy
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    state = {
        "accept_approved": True,
        "tests_passed": True,
        "name": project_name,
        "phase": "deploy",
    }
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    return project_name, base_dir


# ───────────────────────────────────────────────────────────────
# 1. PhaseFlow 类基础测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_init(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    assert flow.project_name == project_name
    assert flow.base_dir == base_dir
    assert flow.proj_dir == base_dir / project_name


def test_phase_flow_current_phase(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    assert flow.current_phase() == "init"


def test_phase_flow_check_pass(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.check()
    assert passed is True
    assert msg == "PASS"


def test_phase_flow_check_fail(tmp_cwd: Path) -> None:
    flow = PhaseFlow("nonexistent", tmp_cwd)
    passed, msg = flow.check()
    assert passed is False
    assert "项目目录不存在" in msg or "git repo 未初始化" in msg


# ───────────────────────────────────────────────────────────────
# 2. advance 测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_advance_init_to_prd(init_project: tuple[str, Path]) -> None:
    """v3.0: init → prd（12 Phase 顺序）"""
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name

    # PRD phase requires a PRD document
    (proj_dir / "docs").mkdir(exist_ok=True)
    (proj_dir / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")

    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "init" in msg
    assert "prd" in msg
    assert flow.current_phase() == "prd"


def test_phase_flow_advance_blocked_at_prd(init_project: tuple[str, Path]) -> None:
    """prd phase 没有 PRD 文档，应该被 BLOCK"""
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    # 先推进到 prd
    flow.advance()
    # 再次 advance 应该被 block
    passed, msg = flow.advance()
    assert passed is False
    assert "check 未通过" in msg


def test_phase_flow_advance_design_to_decompose(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "design" in msg
    assert "decompose" in msg
    assert flow.current_phase() == "decompose"


def test_phase_flow_advance_decompose_to_journey(decompose_project: tuple[str, Path]) -> None:
    """v3.0: decompose → journey（12 Phase 顺序）"""
    project_name, base_dir = decompose_project
    proj_dir = base_dir / project_name

    # journey phase requires a journey document
    (proj_dir / "docs").mkdir(exist_ok=True)
    (proj_dir / "docs" / "journey.md").write_text("# Journey\n", encoding="utf-8")

    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "decompose" in msg
    assert "journey" in msg
    assert flow.current_phase() == "journey"


def test_phase_flow_advance_develop_to_integrate(develop_project: tuple[str, Path]) -> None:
    """v3.0: develop → integrate"""
    project_name, base_dir = develop_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "develop" in msg
    assert "integrate" in msg
    assert flow.current_phase() == "integrate"


def test_phase_flow_advance_test_to_evaluate(test_project: tuple[str, Path]) -> None:
    """v3.0: test → evaluate"""
    project_name, base_dir = test_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "test" in msg
    assert "evaluate" in msg
    assert flow.current_phase() == "evaluate"


def test_phase_flow_advance_accept_to_deploy(accept_project: tuple[str, Path]) -> None:
    project_name, base_dir = accept_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "accept" in msg
    assert "deploy" in msg
    assert flow.current_phase() == "deploy"


def test_phase_flow_advance_at_deploy(deploy_project: tuple[str, Path]) -> None:
    """deploy 是最终阶段，无需推进"""
    project_name, base_dir = deploy_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()
    assert passed is True
    assert "deploy" in msg
    assert "无需推进" in msg


# ───────────────────────────────────────────────────────────────
# 3. rollback 测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_rollback_without_approval(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.rollback("init", approved=False)
    assert passed is False
    assert "审批" in msg


def test_phase_flow_rollback_with_approval(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.rollback("init", approved=True)
    assert passed is True
    assert "design" in msg
    assert "init" in msg
    assert flow.current_phase() == "init"


def test_phase_flow_rollback_same_phase(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.rollback("init", approved=True)
    assert passed is True
    assert "无需回退" in msg


def test_phase_flow_rollback_unknown_phase(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.rollback("unknown", approved=True)
    assert passed is False
    assert "未知 phase" in msg


# ───────────────────────────────────────────────────────────────
# 4. approve 测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_approve_design(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.approve_design()
    assert passed is True
    assert "design 已审批通过" in msg


def test_phase_flow_approve_accept(test_project: tuple[str, Path]) -> None:
    project_name, base_dir = test_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.approve_accept()
    assert passed is True
    assert "accept 已审批通过" in msg


# ───────────────────────────────────────────────────────────────
# 5. mark_tests 测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_mark_tests_passed(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.mark_tests(True)
    assert passed is True
    assert "tests_passed 标记为 True" in msg


def test_phase_flow_mark_tests_failed(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.mark_tests(False)
    assert passed is True
    assert "tests_passed 标记为 False" in msg


# ───────────────────────────────────────────────────────────────
# 6. 高层便捷函数测试
# ───────────────────────────────────────────────────────────────

def test_phase_check_function(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    passed, msg = phase_check(project_name, base_dir)
    assert passed is True
    assert msg == "PASS"


def test_phase_advance_function(init_project: tuple[str, Path]) -> None:
    """v3.0: init → prd（12 Phase 顺序）"""
    project_name, base_dir = init_project
    proj_dir = base_dir / project_name

    # PRD phase requires a PRD document
    (proj_dir / "docs").mkdir(exist_ok=True)
    (proj_dir / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")

    passed, msg = phase_advance(project_name, base_dir)
    assert passed is True
    assert "init" in msg
    assert "prd" in msg


def test_phase_rollback_function(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    passed, msg = phase_rollback(project_name, base_dir, "init", approved=True)
    assert passed is True
    assert "design" in msg
    assert "init" in msg


def test_phase_approve_design_function(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    passed, msg = phase_approve_design(project_name, base_dir)
    assert passed is True
    assert "design 已审批通过" in msg


def test_phase_approve_accept_function(test_project: tuple[str, Path]) -> None:
    project_name, base_dir = test_project
    passed, msg = phase_approve_accept(project_name, base_dir)
    assert passed is True
    assert "accept 已审批通过" in msg


def test_phase_mark_tests_function(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    passed, msg = phase_mark_tests(project_name, base_dir, True)
    assert passed is True
    assert "tests_passed 标记为 True" in msg


# ───────────────────────────────────────────────────────────────
# 7. checkpoint 测试
# ───────────────────────────────────────────────────────────────

def test_phase_flow_advance_writes_checkpoint(init_project: tuple[str, Path]) -> None:
    project_name, base_dir = init_project
    flow = PhaseFlow(project_name, base_dir)
    flow.advance()
    # 检查 checkpoint 是否写入
    checkpoints = flow.store.list_checkpoints(project_name)
    assert len(checkpoints) >= 1
    # 至少有一个 checkpoint 包含 advance 动作
    assert any("advance" in cp.action for cp in checkpoints)


def test_phase_flow_rollback_writes_checkpoint(design_project: tuple[str, Path]) -> None:
    project_name, base_dir = design_project
    flow = PhaseFlow(project_name, base_dir)
    flow.rollback("init", approved=True)
    checkpoints = flow.store.list_checkpoints(project_name)
    assert any("rollback" in cp.action for cp in checkpoints)
