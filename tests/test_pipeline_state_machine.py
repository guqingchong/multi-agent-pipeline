"""tests/test_pipeline_state_machine.py — pipeline.py 状态机单元测试"""

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

from pipeline import (
    Phase,
    ProjectState,
    StateStore,
    CHECK_REGISTRY,
    check_init,
    check_develop,
    check_review,
    check_test,
    cmd_init,
    cmd_check,
    cmd_advance,
    cmd_develop,
    cmd_status,
)


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """在临时目录中运行测试"""
    tmpdir = tempfile.mkdtemp(prefix="pipeline_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def init_project(tmp_cwd: Path) -> str:
    """初始化一个测试项目并返回项目名"""
    project_name = "test_project"
    cmd_init(
        type(
            "Args",
            (),
            {
                "project": project_name,
                "description": "test",
                "stack": "python",
                "force": False,
            },
        )()
    )
    return project_name


# ───────────────────────────────────────────────────────────────
# Phase 枚举测试
# ───────────────────────────────────────────────────────────────

def test_phase_names() -> None:
    assert str(Phase("init")) == "init"
    assert str(Phase("develop")) == "develop"
    assert str(Phase("review")) == "review"
    assert str(Phase("test")) == "test"


def test_phase_from_name() -> None:
    assert Phase.from_name("init") == Phase("init")
    assert Phase.from_name("develop") == Phase("develop")
    assert Phase.from_name("review") == Phase("review")
    assert Phase.from_name("test") == Phase("test")


def test_phase_next() -> None:
    # Greenfield order is the default for the registry-driven Phase class.
    assert Phase("init").next() == Phase("prd")
    assert Phase("prd").next() == Phase("research")
    assert Phase("research").next() == Phase("design")
    assert Phase("deploy").next() is None


# ───────────────────────────────────────────────────────────────
# ProjectState 序列化测试
# ───────────────────────────────────────────────────────────────

def test_project_state_roundtrip() -> None:
    state = ProjectState(
        name="demo",
        phase=Phase("develop"),
        description="desc",
        stack="python",
        created=True,
        git_init=True,
        metadata_files=["SOUL.md"],
        db_created=True,
        check_results={"check_init": True},
    )
    data = state.to_dict()
    restored = ProjectState.from_dict(data)
    assert restored.name == "demo"
    assert restored.phase == Phase("develop")
    assert restored.check_results["check_init"] is True


# ───────────────────────────────────────────────────────────────
# StateStore 测试
# ───────────────────────────────────────────────────────────────

def test_state_store_save_load(tmp_cwd: Path) -> None:
    store = StateStore(tmp_cwd)
    state = ProjectState(name="x", phase=Phase("init"))
    store.save(state)

    loaded = store.load("x")
    assert loaded is not None
    assert loaded.name == "x"
    assert loaded.phase == Phase("init")


def test_state_store_db_created(tmp_cwd: Path) -> None:
    store = StateStore(tmp_cwd)
    assert (tmp_cwd / "pipeline_state.db").exists()


# ───────────────────────────────────────────────────────────────
# Check 函数测试
# ───────────────────────────────────────────────────────────────

def test_check_init_pass() -> None:
    state = ProjectState(
        name="x",
        phase=Phase("init"),
        created=True,
        git_init=True,
        db_created=True,
        metadata_files=["SOUL.md", "AGENTS.md", "progress.md", "features.json"],
    )
    passed, msg = check_init(state)
    assert passed is True
    assert msg == "PASS"


def test_check_init_fail_missing_files() -> None:
    state = ProjectState(
        name="x",
        phase=Phase("init"),
        created=True,
        git_init=True,
        db_created=True,
        metadata_files=["SOUL.md"],  # 缺少其他文件
    )
    passed, msg = check_init(state)
    assert passed is False
    assert "Missing metadata files" in msg


def test_check_init_fail_no_git() -> None:
    state = ProjectState(
        name="x",
        phase=Phase("init"),
        created=True,
        git_init=False,
        db_created=True,
        metadata_files=["SOUL.md", "AGENTS.md", "progress.md", "features.json"],
    )
    passed, msg = check_init(state)
    assert passed is False
    assert "Git repo not initialized" in msg


def test_check_develop_pass() -> None:
    state = ProjectState(
        name="x", phase=Phase("develop"),
        check_results={"develop_started": True, "code_written": True}
    )
    passed, msg = check_develop(state)
    assert passed is True


def test_check_review_fail() -> None:
    state = ProjectState(name="x", phase=Phase("review"))
    passed, msg = check_review(state)
    assert passed is False
    assert "code_written" in msg


def test_check_review_pass() -> None:
    state = ProjectState(
        name="x",
        phase=Phase("review"),
        check_results={"code_written": True, "tests_passed": True},
    )
    passed, msg = check_review(state)
    assert passed is True


def test_check_test_fail() -> None:
    state = ProjectState(name="x", phase=Phase("test"))
    passed, msg = check_test(state)
    assert passed is False
    assert "tests_passed" in msg


def test_check_test_pass() -> None:
    state = ProjectState(
        name="x",
        phase=Phase("test"),
        check_results={"tests_passed": True},
    )
    passed, msg = check_test(state)
    assert passed is True


# ───────────────────────────────────────────────────────────────
# 命令集成测试
# ───────────────────────────────────────────────────────────────

def test_cmd_init_creates_project(tmp_cwd: Path) -> None:
    ret = cmd_init(
        type(
            "Args",
            (),
            {
                "project": "demo",
                "description": "d",
                "stack": "python",
                "force": False,
            },
        )()
    )
    assert ret == 0
    assert (tmp_cwd / "demo").is_dir()
    assert (tmp_cwd / "demo" / "SOUL.md").exists()
    assert (tmp_cwd / "demo" / "AGENTS.md").exists()
    assert (tmp_cwd / "demo" / "progress.md").exists()
    assert (tmp_cwd / "demo" / "features.json").exists()
    assert (tmp_cwd / "demo" / "src").is_dir()
    assert (tmp_cwd / "demo" / "tests").is_dir()


def test_cmd_init_refuses_existing(tmp_cwd: Path) -> None:
    (tmp_cwd / "existing").mkdir()
    ret = cmd_init(
        type(
            "Args",
            (),
            {
                "project": "existing",
                "description": "",
                "stack": "",
                "force": False,
            },
        )()
    )
    assert ret == 1


def test_cmd_check_on_init_project(init_project: str, tmp_cwd: Path, capsys) -> None:
    ret = cmd_check(
        type("Args", (), {"project": init_project})()
    )
    captured = capsys.readouterr()
    assert ret == 0
    assert "PASS" in captured.out


def test_cmd_advance_blocked_when_check_fails(init_project: str, tmp_cwd: Path, capsys, monkeypatch) -> None:
    # 先推进到 develop（init -> develop 会自动通过 check_init）
    cmd_develop(type("Args", (), {"project": init_project})())
    # 此时在 develop，但 develop_started 已通过 cmd_develop 设置
    # 先设置 code_written 使 develop -> review 通过
    store = StateStore(tmp_cwd / init_project)
    state = store.load(init_project)
    assert state is not None
    state.check_results["code_written"] = True
    store.save(state)
    cmd_advance(type("Args", (), {"project": init_project})())
    # 现在在 review，不设置 tests_passed，advance -> test 应该被 BLOCK
    ret = cmd_advance(
        type("Args", (), {"project": init_project})()
    )
    captured = capsys.readouterr()
    assert ret == 1
    assert "BLOCKED" in captured.out or "blocked" in captured.out.lower()


def test_cmd_advance_passes_when_check_passes(init_project: str, tmp_cwd: Path, capsys) -> None:
    # 推进到 develop
    ret = cmd_develop(type("Args", (), {"project": init_project})())
    assert ret == 0

    # 手动标记 code_written 使 review 通过
    store = StateStore(tmp_cwd / init_project)
    state = store.load(init_project)
    assert state is not None
    state.check_results["code_written"] = True
    store.save(state)

    # 现在 advance 从 develop → review 应该通过
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0, f"advance 失败: {captured.out}"
    assert "review" in captured.out

    # 再手动标记 tests_passed 使 test 通过
    state = store.load(init_project)
    assert state is not None
    state.check_results["tests_passed"] = True
    store.save(state)

    # advance 从 review → test
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0, f"advance 失败: {captured.out}"
    assert "test" in captured.out


def test_cmd_status_outputs_json(init_project: str, tmp_cwd: Path, capsys) -> None:
    ret = cmd_status(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0
    data = json.loads(captured.out)
    assert data["name"] == init_project
    assert data["phase"] == "init"


# ───────────────────────────────────────────────────────────────
# 状态机流转端到端测试
# ───────────────────────────────────────────────────────────────

def test_full_phase_transition(init_project: str, tmp_cwd: Path, capsys) -> None:
    """完整状态机流转：init → develop → review → test"""
    store = StateStore(tmp_cwd / init_project)

    # 1. init 阶段
    state = store.load(init_project)
    assert state is not None
    assert state.phase == Phase("init")

    # 2. develop
    ret = cmd_develop(type("Args", (), {"project": init_project})())
    assert ret == 0
    state = store.load(init_project)
    assert state.phase == Phase("develop")

    # 3. review（需要 code_written）
    state.check_results["code_written"] = True
    store.save(state)
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    assert ret == 0
    state = store.load(init_project)
    assert state.phase == Phase("review")

    # 4. test（需要 tests_passed）
    state.check_results["tests_passed"] = True
    store.save(state)
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    assert ret == 0
    state = store.load(init_project)
    assert state.phase == Phase("test")

    # 5. 最终阶段无法继续推进
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0  # 最终阶段返回 0 但提示无法推进
    assert "最终阶段" in captured.out or "test" in captured.out


def test_advance_blocks_without_check(init_project: str, tmp_cwd: Path, capsys) -> None:
    """advance 在未通过 check 时 BLOCK"""
    # 先推进到 develop
    cmd_develop(type("Args", (), {"project": init_project})())
    store = StateStore(tmp_cwd / init_project)
    state = store.load(init_project)
    assert state is not None
    assert state.phase == Phase("develop")

    # 设置 develop_started=True 已通过 cmd_develop 自动设置
    # 但不设置 code_written，这样 advance develop -> review 时 check_review 会失败
    # 注意：cmd_develop 已经设置了 develop_started=True
    # 直接 advance → 应该被 BLOCK（因为 check_review 失败）
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 1
    assert "BLOCKED" in captured.out or "blocked" in captured.out.lower()
    assert "not pass" in captured.out.lower() or "code_written" in captured.out

    # 确认 phase 没有变化
    state = store.load(init_project)
    assert state.phase == Phase("develop")
