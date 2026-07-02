"""tests/test_state_store.py — F008 Layer 2 SQLite 状态持久化测试

验收标准：
1. SQLite DB 能创建所有核心表
2. checkpoint 写入和恢复测试通过
3. pipeline.py resume 能从 checkpoint 恢复
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

from state_store import (
    StateStore,
    ProjectRecord,
    FeatureRecord,
    CheckpointRecord,
    TraceRecord,
    AuditLogRecord,
    DispatchHistoryRecord,
    SCHEMA_VERSION,
)
from pipeline import (
    Phase,
    ProjectState,
    cmd_init,
    cmd_develop,
    cmd_advance,
    cmd_resume,
    cmd_rollback,
    _get_store,
    _save_state,
    _load_state,
    _write_checkpoint,
)


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """在临时目录中运行测试"""
    tmpdir = tempfile.mkdtemp(prefix="state_store_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def db_path(tmp_cwd: Path) -> Path:
    return tmp_cwd / "test.db"


@pytest.fixture
def store(db_path: Path) -> StateStore:
    return StateStore(db_path)


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
# 1. 核心表创建测试
# ───────────────────────────────────────────────────────────────

def test_all_core_tables_created(store: StateStore, db_path: Path) -> None:
    """验收标准 1: SQLite DB 能创建所有核心表"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    expected = {
        "projects",
        "features",
        "checkpoints",
        "traces",
        "audit_logs",
        "model_health",
        "project_state",  # 向后兼容表
    }
    assert expected.issubset(tables), f"缺少表: {expected - tables}"


def test_projects_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(projects)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "id" in cols
    assert "name" in cols
    assert "current_phase" in cols
    assert "schema_version" in cols


def test_features_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(features)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "id" in cols
    assert "project_id" in cols
    assert "status" in cols
    assert "owner_agent" in cols


def test_checkpoints_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(checkpoints)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "id" in cols
    assert "project_id" in cols
    assert "phase" in cols
    assert "state_json" in cols


def test_traces_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(traces)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "project_id" in cols
    assert "input_tokens" in cols
    assert "cost_usd" in cols
    assert "cache_hit" in cols


def test_audit_logs_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(audit_logs)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "project_id" in cols
    assert "agent" in cols
    assert "command" in cols
    assert "allowed" in cols


def test_model_health_table_schema(store: StateStore, db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("PRAGMA table_info(model_health)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert "model" in cols
    assert "response_time_ms" in cols
    assert "success" in cols


# ───────────────────────────────────────────────────────────────
# 2. projects CRUD 测试
# ───────────────────────────────────────────────────────────────

def test_create_and_get_project(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    proj = store.get_project("p1")
    assert proj is not None
    assert proj.id == "p1"
    assert proj.name == "Project One"
    assert proj.current_phase == "init"
    assert proj.schema_version == SCHEMA_VERSION


def test_update_project_phase(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    store.update_project_phase("p1", "develop")
    proj = store.get_project("p1")
    assert proj is not None
    assert proj.current_phase == "develop"


# ───────────────────────────────────────────────────────────────
# 3. features CRUD 测试
# ───────────────────────────────────────────────────────────────

def test_create_and_get_feature(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    feat = FeatureRecord(
        id="F001",
        project_id="p1",
        title="Feature One",
        description="desc",
        status="in_progress",
        owner_agent="Claude Code",
        token_cost=1000,
    )
    store.create_feature(feat)
    loaded = store.get_feature("F001")
    assert loaded is not None
    assert loaded.id == "F001"
    assert loaded.title == "Feature One"
    assert loaded.status == "in_progress"
    assert loaded.owner_agent == "Claude Code"


def test_list_features(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    for i in range(3):
        store.create_feature(
            FeatureRecord(
                id=f"F00{i+1}",
                project_id="p1",
                title=f"Feature {i+1}",
                status="pending",
            )
        )
    feats = store.list_features("p1")
    assert len(feats) == 3


def test_update_feature_status(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    store.create_feature(
        FeatureRecord(id="F001", project_id="p1", title="T", status="pending")
    )
    store.update_feature_status("F001", "passed")
    feat = store.get_feature("F001")
    assert feat is not None
    assert feat.status == "passed"


# ───────────────────────────────────────────────────────────────
# 4. checkpoint 写入 / 恢复 / 回滚 测试
# ───────────────────────────────────────────────────────────────

def test_write_checkpoint(store: StateStore) -> None:
    """checkpoint 写入测试"""
    store.create_project("p1", "Project One", "init")
    state_dict = {"name": "p1", "phase": "init", "check_results": {"a": True}}
    cp_id = store.write_checkpoint(
        project_id="p1",
        phase="init",
        state_dict=state_dict,
        agent="pipeline",
        action="test_action",
    )
    assert cp_id > 0

    cp = store.get_checkpoint(cp_id)
    assert cp is not None
    assert cp.project_id == "p1"
    assert cp.phase == "init"
    assert cp.agent == "pipeline"
    assert cp.action == "test_action"
    assert json.loads(cp.state_json) == state_dict


def test_list_checkpoints(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    for i in range(5):
        store.write_checkpoint(
            project_id="p1",
            phase="init",
            state_dict={"seq": i},
        )
    cps = store.list_checkpoints("p1", limit=3)
    assert len(cps) == 3
    # 按 id DESC，所以第一个是最后写入的
    assert json.loads(cps[0].state_json)["seq"] == 4


def test_get_latest_checkpoint(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    store.write_checkpoint("p1", "init", {"phase": "init"})
    store.write_checkpoint("p1", "develop", {"phase": "develop"})
    latest = store.get_latest_checkpoint("p1")
    assert latest is not None
    assert latest.phase == "develop"


def test_restore_checkpoint(store: StateStore) -> None:
    """checkpoint 恢复测试"""
    store.create_project("p1", "Project One", "init")
    state_dict = {"name": "p1", "phase": "review", "check_results": {"code_written": True}}
    cp_id = store.write_checkpoint("p1", "review", state_dict)

    restored = store.restore_checkpoint(cp_id)
    assert restored is not None
    assert restored["phase"] == "review"
    assert restored["check_results"]["code_written"] is True


def test_rollback(store: StateStore) -> None:
    """rollback 测试"""
    store.create_project("p1", "Project One", "init")
    store.write_checkpoint("p1", "init", {"phase": "init"})
    cp_id = store.write_checkpoint("p1", "develop", {"phase": "develop"})

    state = store.rollback("p1", cp_id)
    assert state is not None
    assert state["phase"] == "develop"

    proj = store.get_project("p1")
    assert proj is not None
    assert proj.current_phase == "develop"


# ───────────────────────────────────────────────────────────────
# 5. traces / audit_logs / model_health 测试
# ───────────────────────────────────────────────────────────────

def test_write_trace(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    trace = TraceRecord(
        project_id="p1",
        feature_id="F001",
        agent="Claude Code",
        model="Kimi K2.6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.05,
        latency_ms=1200,
        status="success",
        cache_hit=True,
    )
    tid = store.write_trace(trace)
    assert tid > 0

    traces = store.list_traces("p1")
    assert len(traces) == 1
    assert traces[0].agent == "Claude Code"
    assert traces[0].cache_hit is True


def test_write_audit_log(store: StateStore) -> None:
    store.create_project("p1", "Project One", "init")
    log = AuditLogRecord(
        project_id="p1",
        agent="CodeWhale",
        command="git push",
        allowed=True,
    )
    lid = store.write_audit_log(log)
    assert lid > 0

    logs = store.list_audit_logs("p1")
    assert len(logs) == 1
    assert logs[0].command == "git push"
    # SQLite BOOLEAN 存储为 INTEGER (0/1)，需要 bool() 转换
    assert bool(logs[0].allowed) is True


def test_write_model_health(store: StateStore) -> None:
    mid = store.write_model_health("Kimi K2.6", 1200, True)
    assert mid > 0


# ───────────────────────────────────────────────────────────────
# 5.5 dispatch_history 测试
# ───────────────────────────────────────────────────────────────

def test_write_dispatch_history(store: StateStore) -> None:
    hid = store.write_dispatch_history(
        task_id="task-001",
        agent="claude-code",
        task_type="code",
        success=True,
        latency_ms=1500,
        exec_mode="sync",
        output="done",
        error="",
    )
    assert hid > 0


def test_list_dispatch_history(store: StateStore) -> None:
    store.write_dispatch_history(agent="claude-code", task_type="code", exec_mode="sync")
    store.write_dispatch_history(agent="qwen-code", task_type="review", exec_mode="async")
    rows = store.list_dispatch_history(agent="claude-code")
    assert len(rows) == 1
    assert rows[0].agent == "claude-code"
    assert rows[0].exec_mode == "sync"


def test_count_dispatch_history(store: StateStore) -> None:
    assert store.count_dispatch_history() == 0
    store.write_dispatch_history(agent="claude-code", task_type="code")
    store.write_dispatch_history(agent="qwen-code", task_type="review")
    assert store.count_dispatch_history() == 2


# ───────────────────────────────────────────────────────────────
# 6. 向后兼容 F005 测试
# ───────────────────────────────────────────────────────────────

def test_legacy_save_load(store: StateStore) -> None:
    store.legacy_save("state", '{"name": "p1", "phase": "init"}')
    loaded = store.legacy_load("state")
    assert loaded == '{"name": "p1", "phase": "init"}'


def test_legacy_load_missing(store: StateStore) -> None:
    assert store.legacy_load("nonexistent") is None


# ───────────────────────────────────────────────────────────────
# 7. schema 版本测试
# ───────────────────────────────────────────────────────────────

def test_schema_version_on_empty_v0(store: StateStore) -> None:
    """Schema version on initialized DB should be 2 (v2 schema)."""
    assert store.get_schema_version() == 2


def test_schema_version_on_empty_v2(store: StateStore) -> None:
    """Schema version on initialized DB should be >= 0 (v2 inferred from columns)."""
    assert store.get_schema_version() >= 0


# ───────────────────────────────────────────────────────────────
# 8. pipeline.py resume / rollback 集成测试
# ───────────────────────────────────────────────────────────────

def test_pipeline_resume_from_latest_checkpoint(init_project: str, tmp_cwd: Path, capsys) -> None:
    """验收标准 3: pipeline.py resume 能从 checkpoint 恢复"""
    project_name = init_project
    base_dir = tmp_cwd / project_name

    # 推进到 develop 并写入 checkpoint
    ret = cmd_develop(type("Args", (), {"project": project_name})())
    assert ret == 0

    # 模拟"崩溃"：直接修改 legacy state 为损坏状态
    store = _get_store(base_dir)
    corrupted = {"name": project_name, "phase": "init", "check_results": {}}
    store.legacy_save("state", json.dumps(corrupted))

    # resume 应该从最新 checkpoint 恢复
    ret = cmd_resume(type("Args", (), {"project": project_name, "checkpoint_id": None})())
    captured = capsys.readouterr()
    assert ret == 0, f"resume 失败: {captured.out}"
    assert ret == 0
    assert "develop" in captured.out

    # 验证状态已恢复
    state = _load_state(store, project_name)
    assert state is not None
    assert state.phase == Phase.DEVELOP


def test_pipeline_resume_with_specific_checkpoint(init_project: str, tmp_cwd: Path, capsys) -> None:
    """指定 checkpoint_id 恢复"""
    project_name = init_project
    base_dir = tmp_cwd / project_name
    store = _get_store(base_dir)

    # 写入多个 checkpoint
    state = _load_state(store, project_name)
    assert state is not None
    cp1 = _write_checkpoint(store, project_name, state, "init")

    state.phase = Phase.DEVELOP
    state.check_results["develop_started"] = True
    cp2 = _write_checkpoint(store, project_name, state, "develop")

    # 回滚到 cp1
    ret = cmd_resume(
        type("Args", (), {"project": project_name, "checkpoint_id": cp1})()
    )
    captured = capsys.readouterr()
    assert ret == 0
    assert str(cp1) in captured.out

    restored = _load_state(store, project_name)
    assert restored is not None
    assert restored.phase == Phase.INIT


def test_pipeline_resume_no_checkpoint(init_project: str, tmp_cwd: Path, capsys) -> None:
    """没有 checkpoint 时 resume 应该报错"""
    project_name = init_project
    base_dir = tmp_cwd / project_name
    store = _get_store(base_dir)

    # 删除所有 checkpoints
    conn = sqlite3.connect(str(store.db_path))
    conn.execute("DELETE FROM checkpoints")
    conn.commit()
    conn.close()

    ret = cmd_resume(type("Args", (), {"project": project_name, "checkpoint_id": None})())
    captured = capsys.readouterr()
    assert ret == 1
    assert "checkpoint" in captured.out.lower() or "does not exist" in captured.out.lower()


def test_pipeline_rollback(init_project: str, tmp_cwd: Path, capsys) -> None:
    """rollback 命令测试"""
    project_name = init_project
    base_dir = tmp_cwd / project_name
    store = _get_store(base_dir)

    # 推进到 develop
    cmd_develop(type("Args", (), {"project": project_name})())

    # 获取 init 阶段的 checkpoint id
    cps = store.list_checkpoints(project_name)
    init_cp = [c for c in cps if c.phase == "init"][0]

    # rollback 到 init
    ret = cmd_rollback(
        type("Args", (), {"project": project_name, "checkpoint_id": init_cp.id})()
    )
    captured = capsys.readouterr()
    assert ret == 0, f"rollback 失败: {captured.out}"
    assert "rollback" in captured.out.lower()
    assert "success" in captured.out.lower() or "ok" in captured.out.lower()

    state = _load_state(store, project_name)
    assert state is not None
    assert state.phase == Phase.INIT


# ───────────────────────────────────────────────────────────────
# 9. 端到端 checkpoint 流转测试
# ───────────────────────────────────────────────────────────────

def test_checkpoint_written_on_every_action(init_project: str, tmp_cwd: Path) -> None:
    """每个有意义 action 后自动写入 checkpoint"""
    project_name = init_project
    base_dir = tmp_cwd / project_name
    store = _get_store(base_dir)

    # init 已写入 1 个 checkpoint
    cps = store.list_checkpoints(project_name)
    assert len(cps) >= 1

    # develop
    cmd_develop(type("Args", (), {"project": project_name})())
    cps = store.list_checkpoints(project_name)
    develop_cps = [c for c in cps if c.action == "develop"]
    assert len(develop_cps) == 1

    # advance 到 review（需要 code_written）
    # 创建代码文件使 check_develop 通过
    (tmp_cwd / project_name / "src" / "test.py").write_text("# test code")
    
    # 创建 git commit（check_develop 需要）
    import subprocess
    proj_dir = tmp_cwd / project_name
    subprocess.run(["git", "-C", str(proj_dir), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj_dir), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj_dir), "commit", "-m", "test commit"], check=True, capture_output=True)
    
    # 更新 progress.md（check_develop 需要）
    progress_file = proj_dir / "progress.md"
    progress_content = progress_file.read_text()
    progress_file.write_text(progress_content + "\n## develop\n- 代码已编写\n")
    
    cmd_advance(type("Args", (), {"project": project_name})())
    cps = store.list_checkpoints(project_name)
    advance_cps = [c for c in cps if c.action == "advance:develop->integrate"]
    assert len(advance_cps) == 1


def test_resume_restores_full_state(init_project: str, tmp_cwd: Path) -> None:
    """resume 恢复完整状态包括 check_results"""
    project_name = init_project
    base_dir = tmp_cwd / project_name
    store = _get_store(base_dir)

    # 推进并设置复杂状态
    cmd_develop(type("Args", (), {"project": project_name})())
    state = _load_state(store, project_name)
    assert state is not None
    state.check_results["code_written"] = True
    state.check_results["tests_passed"] = True
    _save_state(store, project_name, state, "set_all_checks")

    cmd_advance(type("Args", (), {"project": project_name})())
    cmd_advance(type("Args", (), {"project": project_name})())

    # 模拟崩溃：删除 legacy state
    conn = sqlite3.connect(str(store.db_path))
    conn.execute("DELETE FROM project_state")
    conn.commit()
    conn.close()

    # resume
    ret = cmd_resume(type("Args", (), {"project": project_name, "checkpoint_id": None})())
    assert ret == 0

    restored = _load_state(store, project_name)
    assert restored is not None
    assert restored.phase == Phase.TEST
    assert restored.check_results.get("code_written") is True
    assert restored.check_results.get("tests_passed") is True


class TestStateStoreV2:
    """Tests for StateStore v2 schema fields."""

    def test_create_feature_with_v2_fields(self, tmp_cwd: Path) -> None:
        """FeatureRecord with wave, dependencies, acceptance_criteria, github_issue_number, sync_status."""
        db_path = tmp_cwd / "test_v2.db"
        store = StateStore(db_path)
        store.create_project("p1", "Test", "init")
        f = FeatureRecord(
            id="F1",
            project_id="p1",
            title="T",
            description="desc",
            status="pending",
            wave=1,
            dependencies=["F0"],
            acceptance_criteria=["AC1", "AC2"],
            github_issue_number=42,
            sync_status="synced",
        )
        store.create_feature(f)
        f2 = store.get_feature("F1")
        assert f2 is not None
        assert f2.wave == 1
        assert f2.dependencies == ["F0"]
        assert f2.acceptance_criteria == ["AC1", "AC2"]
        assert f2.github_issue_number == 42
        assert f2.sync_status == "synced"

    def test_update_feature_sync(self, tmp_cwd: Path) -> None:
        """update_feature_sync changes sync_status and updated_at."""
        db_path = tmp_cwd / "test_sync.db"
        store = StateStore(db_path)
        store.create_project("p1", "Test", "init")
        f = FeatureRecord(
            id="F1",
            project_id="p1",
            title="T",
            status="pending",
            sync_status="unsynced",
        )
        store.create_feature(f)
        store.update_feature_sync("F1", "syncing")
        f2 = store.get_feature("F1")
        assert f2.sync_status == "syncing"
        # Invalid sync_status should be rejected by DB CHECK
        with pytest.raises(sqlite3.IntegrityError):
            store.update_feature_sync("F1", "invalid_status")

    def test_schema_migration_v1_to_v2(self, tmp_cwd: Path) -> None:
        """Old DB without v2 columns gets migrated automatically."""
        db_path = tmp_cwd / "test_migrate.db"
        # Create a v1-style DB manually
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE features (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT,
                description TEXT,
                status TEXT
            )
        """)
        conn.execute("INSERT INTO features VALUES ('F1', 'p1', 'T', 'd', 'pending')")
        conn.commit()
        conn.close()

        # Opening with StateStore should trigger migration
        store = StateStore(db_path)
        store.create_project("p1", "Test", "init")
        # After migration, new columns should exist
        f = store.get_feature("F1")
        assert f is not None
        assert f.wave == 0  # default
        assert f.dependencies == []
        assert f.acceptance_criteria == []
        assert f.github_issue_number is None
        assert f.sync_status == "unsynced"

    def test_feature_record_defaults(self, tmp_cwd: Path) -> None:
        """FeatureRecord without v2 fields uses defaults."""
        db_path = tmp_cwd / "test_defaults.db"
        store = StateStore(db_path)
        store.create_project("p1", "Test", "init")
        f = FeatureRecord(id="F1", project_id="p1", title="T")
        store.create_feature(f)
        f2 = store.get_feature("F1")
        assert f2.wave == 0
        assert f2.dependencies == []
        assert f2.acceptance_criteria == []
        assert f2.github_issue_number is None
        assert f2.sync_status == "unsynced"

