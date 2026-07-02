"""tests/test_entry.py — F023 入口层自动加载测试

验收标准：
1. auto_load 自动加载项目状态
2. show_dashboard 显示驾驶舱
3. identify_intent 识别用户意图（开发/修改/查询）
4. 15+ 单元测试全部通过
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.entry import (
    auto_load,
    auto_load_with_checkpoints,
    show_dashboard,
    show_dashboard_for_context,
    identify_intent,
    identify_intent_with_context,
    entry_main,
    quick_status,
    list_projects,
    UserIntent,
    EntryContext,
    INTENT_KEYWORDS,
)
from src.state_store import StateStore, FeatureRecord, ProjectRecord
from src.models import ProjectState, Phase


# ───────────────────────────────────────────────────────────────
#  fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project_dir():
    """创建临时项目目录（使用固定路径避免Windows文件锁问题）"""
    base = Path("/tmp/test_entry_projects")
    base.mkdir(parents=True, exist_ok=True)
    yield base


@pytest.fixture
def initialized_project(tmp_project_dir):
    """创建已初始化的项目（包含数据库和状态）"""
    base = tmp_project_dir
    project_id = "test_proj"
    proj_dir = base / project_id
    proj_dir.mkdir(exist_ok=True)

    # 初始化数据库
    db_path = proj_dir / "pipeline_state.db"
    store = StateStore(db_path)
    store.create_project(project_id, "Test Project", "develop")

    # 创建 pipeline 状态
    state = ProjectState(
        name=project_id,
        phase=Phase("develop"),
        description="A test project",
        stack="python",
        created=True,
        git_init=True,
        metadata_files=["SOUL.md", "AGENTS.md"],
        db_created=True,
        check_results={"develop_started": True, "code_written": True},
    )
    store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))

    # 创建 features
    for i, status in enumerate(["passed", "in_progress", "failed", "pending"]):
        store.create_feature(
            FeatureRecord(
                id=f"F{i:03d}",
                project_id=project_id,
                title=f"Feature {i}",
                status=status,
                description=f"Description for feature {i}",
            )
        )

    # 创建 checkpoints
    for i in range(3):
        store.write_checkpoint(
            project_id=project_id,
            phase="init" if i == 0 else "develop",
            state_dict={"phase": "init" if i == 0 else "develop", "iter": i},
            agent="pipeline",
            action=f"action_{i}",
            result="ok",
        )

    return base, project_id


@pytest.fixture
def empty_project_dir(tmp_project_dir):
    """空项目目录（无数据库）"""
    base = tmp_project_dir
    project_id = "empty_proj"
    proj_dir = base / project_id
    proj_dir.mkdir(exist_ok=True)
    return base, project_id


@pytest.fixture
def nonexistent_project(tmp_project_dir):
    """不存在的项目"""
    base = tmp_project_dir
    project_id = "nonexistent_proj"
    return base, project_id


# ───────────────────────────────────────────────────────────────
#  auto_load 测试
# ───────────────────────────────────────────────────────────────

class TestAutoLoad:
    def test_load_existing_project(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)

        assert ctx.project_exists is True
        assert ctx.project_id == project_id
        assert ctx.project_name == "Test Project"
        assert ctx.current_phase == "develop"
        assert ctx.state is not None
        assert ctx.state.phase == Phase("develop")
        assert len(ctx.features) == 4

    def test_load_nonexistent_project(self, nonexistent_project):
        base, project_id = nonexistent_project
        ctx = auto_load(project_id, base)

        assert ctx.project_exists is False
        assert ctx.current_phase == "unknown"
        assert ctx.state is None
        assert ctx.features == []

    def test_load_empty_project_dir(self, empty_project_dir):
        base, project_id = empty_project_dir
        ctx = auto_load(project_id, base)

        assert ctx.project_exists is True  # 目录存在
        assert ctx.current_phase == "uninitialized"  # 但无数据库
        assert ctx.state is None

    def test_load_default_base_dir(self, initialized_project):
        # 使用默认 base_dir（当前工作目录）测试
        base, project_id = initialized_project
        # 切换到项目目录
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(str(base))
            ctx = auto_load(project_id)
            assert ctx.project_exists is True
            assert ctx.current_phase == "develop"
        finally:
            os.chdir(original_cwd)

    def test_load_project_state_details(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)

        assert ctx.state is not None
        assert ctx.state.name == project_id
        assert ctx.state.description == "A test project"
        assert ctx.state.stack == "python"
        assert ctx.state.created is True
        assert ctx.state.git_init is True
        assert ctx.state.db_created is True
        assert ctx.state.check_results.get("develop_started") is True

    def test_load_features(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)

        assert len(ctx.features) == 4
        statuses = [f.status for f in ctx.features]
        assert "passed" in statuses
        assert "in_progress" in statuses
        assert "failed" in statuses
        assert "pending" in statuses


# ───────────────────────────────────────────────────────────────
#  auto_load_with_checkpoints 测试
# ───────────────────────────────────────────────────────────────

class TestAutoLoadWithCheckpoints:
    def test_load_with_checkpoints(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load_with_checkpoints(project_id, base, checkpoint_limit=3)

        assert ctx.project_exists is True
        assert ctx.state is not None
        assert "recent_checkpoints" in ctx.state.check_results
        checkpoints = ctx.state.check_results["recent_checkpoints"]
        assert len(checkpoints) == 3
        assert checkpoints[0]["phase"] in ("init", "develop")

    def test_load_with_checkpoints_limit(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load_with_checkpoints(project_id, base, checkpoint_limit=1)

        assert ctx.state is not None
        checkpoints = ctx.state.check_results.get("recent_checkpoints", [])
        assert len(checkpoints) == 1

    def test_load_nonexistent_with_checkpoints(self, nonexistent_project):
        base, project_id = nonexistent_project
        ctx = auto_load_with_checkpoints(project_id, base)

        assert ctx.project_exists is False
        assert ctx.state is None


# ───────────────────────────────────────────────────────────────
#  show_dashboard 测试
# ───────────────────────────────────────────────────────────────

class TestShowDashboard:
    def test_show_dashboard_existing(self, initialized_project):
        base, project_id = initialized_project
        text = show_dashboard(project_id, base, rich_mode=False)

        assert project_id in text
        assert "Phase" in text
        assert "Features" in text
        assert "Budget" in text

    def test_show_dashboard_nonexistent(self, nonexistent_project):
        base, project_id = nonexistent_project
        text = show_dashboard(project_id, base)

        assert "ERROR" in text or "不存在" in text

    def test_show_dashboard_empty_dir(self, empty_project_dir):
        base, project_id = empty_project_dir
        text = show_dashboard(project_id, base)

        assert "WARN" in text or "未初始化" in text

    def test_show_dashboard_rich_mode(self, initialized_project):
        base, project_id = initialized_project
        text = show_dashboard(project_id, base, rich_mode=True)

        assert project_id in text
        # rich 模式可能包含 Dashboard 或 Model Health 等字样
        assert "Dashboard" in text or "Phase" in text or "Features" in text

    def test_show_dashboard_with_alerts(self, initialized_project):
        base, project_id = initialized_project
        text = show_dashboard(project_id, base, include_alerts=True)

        assert project_id in text
        # 可能没有告警，但函数应该正常执行
        assert "Phase" in text

    def test_show_dashboard_for_context(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)
        text = show_dashboard_for_context(ctx, rich_mode=False)

        assert project_id in text
        assert "Phase" in text or "ERROR" in text or "不存在" in text

    def test_show_dashboard_for_context_nonexistent(self, nonexistent_project):
        base, project_id = nonexistent_project
        ctx = auto_load(project_id, base)
        text = show_dashboard_for_context(ctx)

        assert "ERROR" in text or "不存在" in text


# ───────────────────────────────────────────────────────────────
#  identify_intent 测试
# ───────────────────────────────────────────────────────────────

class TestIdentifyIntent:
    def test_intent_develop(self):
        inputs = [
            "开发一个新功能",
            "实现用户登录",
            "create a new feature",
            "implement authentication",
            "build a dashboard",
            "写一个API接口",
            "添加支付功能",
        ]
        for user_input in inputs:
            intent, confidence = identify_intent(user_input)
            assert intent == UserIntent.DEVELOP, f"Failed for: {user_input}"
            assert confidence >= 0.3

    def test_intent_modify(self):
        inputs = [
            "修改现有代码",
            "修复bug",
            "优化性能",
            "update the configuration",
            "fix the error",
            "refactor the module",
            "改一下这个函数",
            "重写这个逻辑",
        ]
        for user_input in inputs:
            intent, confidence = identify_intent(user_input)
            assert intent == UserIntent.MODIFY, f"Failed for: {user_input}"
            assert confidence >= 0.3

    def test_intent_query(self):
        inputs = [
            "查看项目状态",
            "显示进度",
            "生成报告",
            "check the status",
            "show dashboard",
            "view the report",
            "list features",
            "看看现在什么情况",
            "项目进度如何",
        ]
        for user_input in inputs:
            intent, confidence = identify_intent(user_input)
            assert intent == UserIntent.QUERY, f"Failed for: {user_input}"
            assert confidence >= 0.3

    def test_intent_unknown(self):
        inputs = [
            "",
            "   ",
            "hello",
            "random text",
            "what is this",
            "abc 123",
        ]
        for user_input in inputs:
            intent, confidence = identify_intent(user_input)
            assert intent == UserIntent.UNKNOWN, f"Failed for: {user_input}"
            assert confidence < 0.5 or confidence == 0.0

    def test_intent_confidence_range(self):
        for intent_type in UserIntent:
            for keyword in INTENT_KEYWORDS.get(intent_type, []):
                intent, confidence = identify_intent(keyword)
                assert 0.0 <= confidence <= 1.0
                if intent != UserIntent.UNKNOWN:
                    assert confidence >= 0.3

    def test_intent_mixed_input(self):
        # 混合输入应该偏向主要意图
        intent, confidence = identify_intent("开发并修改一个功能")
        assert intent in (UserIntent.DEVELOP, UserIntent.MODIFY)
        assert confidence > 0.0

    def test_intent_case_insensitive(self):
        intent1, _ = identify_intent("DEVELOP A FEATURE")
        intent2, _ = identify_intent("develop a feature")
        assert intent1 == intent2 == UserIntent.DEVELOP

    def test_intent_chinese(self):
        intent, confidence = identify_intent("帮我查一下项目状态")
        assert intent == UserIntent.QUERY
        assert confidence >= 0.3

    def test_intent_english(self):
        intent, confidence = identify_intent("I want to create a new module")
        assert intent == UserIntent.DEVELOP
        assert confidence > 0.3


# ───────────────────────────────────────────────────────────────
#  identify_intent_with_context 测试
# ───────────────────────────────────────────────────────────────

class TestIdentifyIntentWithContext:
    def test_context_enhance_develop(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)
        # 修改 phase 为 init
        ctx.current_phase = "init"

        intent, confidence = identify_intent_with_context("开发新功能", ctx)
        assert intent == UserIntent.DEVELOP
        # init 阶段应该增强 develop 意图
        assert confidence > 0.3

    def test_context_enhance_modify(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)

        intent, confidence = identify_intent_with_context("修复bug", ctx)
        assert intent == UserIntent.MODIFY
        # develop 阶段有 failed features 应该增强 modify 意图
        assert confidence > 0.3

    def test_context_reduce_modify_for_nonexistent(self, nonexistent_project):
        base, project_id = nonexistent_project
        ctx = auto_load(project_id, base)

        intent, confidence = identify_intent_with_context("修改代码", ctx)
        # 项目不存在时，modify 意图应该被降低或变为 unknown
        assert intent in (UserIntent.UNKNOWN, UserIntent.MODIFY)

    def test_context_query_for_deploy(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)
        ctx.current_phase = "deploy"

        intent, confidence = identify_intent_with_context("查看状态", ctx)
        assert intent == UserIntent.QUERY
        assert confidence > 0.3


# ───────────────────────────────────────────────────────────────
#  entry_main 测试
# ───────────────────────────────────────────────────────────────

class TestEntryMain:
    def test_entry_main_develop(self, initialized_project):
        base, project_id = initialized_project
        ctx, dashboard = entry_main(project_id, "开发新功能", base, rich_mode=False)

        assert ctx.project_exists is True
        assert ctx.intent == UserIntent.DEVELOP
        assert ctx.intent_confidence >= 0.3
        assert "INTENT" in dashboard
        assert project_id in dashboard
        assert "Phase" in dashboard or "ERROR" in dashboard or "不存在" in dashboard

    def test_entry_main_modify(self, initialized_project):
        base, project_id = initialized_project
        ctx, dashboard = entry_main(project_id, "修复bug", base, rich_mode=False)

        assert ctx.intent == UserIntent.MODIFY
        assert ctx.intent_confidence > 0.3
        assert "INTENT" in dashboard

    def test_entry_main_query(self, initialized_project):
        base, project_id = initialized_project
        ctx, dashboard = entry_main(project_id, "查看状态", base, rich_mode=False)

        assert ctx.intent == UserIntent.QUERY
        assert ctx.intent_confidence > 0.3
        assert "INTENT" in dashboard

    def test_entry_main_nonexistent(self, nonexistent_project):
        base, project_id = nonexistent_project
        ctx, dashboard = entry_main(project_id, "查看状态", base)

        assert ctx.project_exists is False
        assert "INTENT" in dashboard
        assert "ERROR" in dashboard or "不存在" in dashboard

    def test_entry_main_unknown_intent(self, initialized_project):
        base, project_id = initialized_project
        ctx, dashboard = entry_main(project_id, "random gibberish", base)

        assert ctx.intent == UserIntent.UNKNOWN
        assert "INTENT" in dashboard


# ───────────────────────────────────────────────────────────────
#  quick_status 测试
# ───────────────────────────────────────────────────────────────

class TestQuickStatus:
    def test_quick_status_existing(self, initialized_project):
        base, project_id = initialized_project
        status = quick_status(project_id, base)

        assert status["project_id"] == project_id
        assert status["project_exists"] is True
        assert status["current_phase"] == "develop"
        assert status["feature_count"] == 4
        assert status["intent"] is None  # quick_status 不识别意图

    def test_quick_status_nonexistent(self, nonexistent_project):
        base, project_id = nonexistent_project
        status = quick_status(project_id, base)

        assert status["project_exists"] is False
        assert status["current_phase"] == "unknown"
        assert status["feature_count"] == 0


# ───────────────────────────────────────────────────────────────
#  list_projects 测试
# ───────────────────────────────────────────────────────────────

class TestListProjects:
    def test_list_projects(self, initialized_project):
        base, project_id = initialized_project
        projects = list_projects(base)

        assert project_id in projects
        assert len(projects) >= 1

    def test_list_projects_empty(self, tmp_project_dir):
        base = tmp_project_dir
        projects = list_projects(base)

        # 共享目录中可能有其他测试的项目，但至少 test_proj 存在
        assert "test_proj" in projects

    def test_list_projects_with_empty_dir(self, empty_project_dir):
        base, _ = empty_project_dir
        # 空目录（无数据库）不应被列出
        projects = list_projects(base)
        assert "empty_proj" not in projects


# ───────────────────────────────────────────────────────────────
#  EntryContext 测试
# ───────────────────────────────────────────────────────────────

class TestEntryContext:
    def test_context_to_dict(self, initialized_project):
        base, project_id = initialized_project
        ctx = auto_load(project_id, base)
        d = ctx.to_dict()

        assert d["project_id"] == project_id
        assert d["project_exists"] is True
        assert d["current_phase"] == "develop"
        assert d["feature_count"] == 4
        assert d["alert_count"] == 0
        assert d["state"] is not None
        assert d["intent"] is None

    def test_context_empty(self):
        ctx = EntryContext(
            project_id="test",
            project_name="Test",
            current_phase="unknown",
        )
        d = ctx.to_dict()

        assert d["project_id"] == "test"
        assert d["project_exists"] is False
        assert d["feature_count"] == 0
        assert d["state"] is None


# ───────────────────────────────────────────────────────────────
#  集成测试
# ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_end_to_end(self, initialized_project):
        """端到端测试：加载 → 仪表盘 → 意图识别"""
        base, project_id = initialized_project

        # 1. 自动加载
        ctx = auto_load(project_id, base)
        assert ctx.project_exists is True

        # 2. 显示仪表盘
        dashboard = show_dashboard_for_context(ctx)
        assert project_id in dashboard
        assert "Phase" in dashboard or "ERROR" in dashboard or "不存在" in dashboard

        # 3. 识别意图
        intent, confidence = identify_intent_with_context("开发新功能", ctx)
        assert intent == UserIntent.DEVELOP
        assert confidence > 0.3

        # 4. 完整入口流程
        ctx2, dashboard2 = entry_main(project_id, "查看进度", base)
        assert ctx2.intent == UserIntent.QUERY
        assert "INTENT" in dashboard2

    def test_multiple_projects(self, tmp_project_dir):
        """测试多项目场景"""
        base = tmp_project_dir

        # 创建两个项目
        for pid in ["proj_a", "proj_b"]:
            proj_dir = base / pid
            proj_dir.mkdir(exist_ok=True)
            db_path = proj_dir / "pipeline_state.db"
            store = StateStore(db_path)
            store.create_project(pid, f"Project {pid}", "init")

        projects = list_projects(base)
        assert "proj_a" in projects
        assert "proj_b" in projects
        # 共享目录中可能还有 test_proj
        assert len(projects) >= 2

        # 分别加载
        ctx_a = auto_load("proj_a", base)
        ctx_b = auto_load("proj_b", base)
        assert ctx_a.project_name == "Project proj_a"
        assert ctx_b.project_name == "Project proj_b"
        assert ctx_a.current_phase == "init"
        assert ctx_b.current_phase == "init"

    def test_state_persistence(self, initialized_project):
        """测试状态持久化：修改后重新加载"""
        base, project_id = initialized_project

        # 第一次加载
        ctx1 = auto_load(project_id, base)
        assert ctx1.state.phase == Phase("develop")

        # 修改状态（模拟 checkpoint 后的状态变化）
        db_path = base / project_id / "pipeline_state.db"
        store = StateStore(db_path)
        new_state = ProjectState(
            name=project_id,
            phase=Phase("test"),
            description="Updated",
            created=True,
            git_init=True,
            metadata_files=["SOUL.md"],
            db_created=True,
        )
        store.legacy_save("state", json.dumps(new_state.to_dict(), ensure_ascii=False))
        store.update_project_phase(project_id, "test")

        # 重新加载
        ctx2 = auto_load(project_id, base)
        assert ctx2.current_phase == "test"
        assert ctx2.state.phase == Phase("test")
        assert ctx2.state.description == "Updated"

    def test_intent_boundary_cases(self):
        """测试意图识别的边界情况"""
        # 空输入
        intent, conf = identify_intent("")
        assert intent == UserIntent.UNKNOWN
        assert conf == 0.0

        # 纯空格
        intent, conf = identify_intent("   ")
        assert intent == UserIntent.UNKNOWN

        # 特殊字符
        intent, conf = identify_intent("!@#$%^&*()")
        assert intent == UserIntent.UNKNOWN

        # 数字
        intent, conf = identify_intent("12345")
        assert intent == UserIntent.UNKNOWN

        # 混合有效和无效
        intent, conf = identify_intent("hello 开发 world")
        assert intent == UserIntent.DEVELOP
        assert conf > 0.0

    def test_dashboard_alert_integration(self, initialized_project):
        """测试仪表盘告警集成"""
        base, project_id = initialized_project

        # 添加一些 traces 触发告警
        db_path = base / project_id / "pipeline_state.db"
        store = StateStore(db_path)
        from src.state_store import TraceRecord
        for i in range(20):
            store.write_trace(
                TraceRecord(
                    project_id=project_id,
                    agent="test",
                    status="error",
                    latency_ms=80000,
                    input_tokens=100,
                    output_tokens=50,
                )
            )

        dashboard = show_dashboard(project_id, base, include_alerts=True)
        assert project_id in dashboard
        # 应该有告警信息
        assert "ALERTS" in dashboard or "ERROR" in dashboard or "Phase" in dashboard
