"""tests/test_bridge_cli.py — Tests for bridge_cli.py"""

import os
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bridge_cli import (
    get_base_dir,
    cmd_load,
    cmd_route,
    cmd_check_hermes,
    cmd_suggest,
    cmd_inspect,
    cmd_audit_report,
    cmd_init,
    cmd_advance,
    cmd_status,
    cmd_rollback_phase,
    cmd_approve,
    cmd_mark_tests,
)
from state_store import StateStore


class TestGetBaseDir:
    def test_default_base_dir(self):
        """Default base_dir should be PROJECT_ROOT.parent."""
        bd = get_base_dir()
        assert bd.name == "tmp" or str(bd).endswith("multi-agent-pipeline")

    def test_env_override(self, monkeypatch):
        """MULTI_AGENT_PIPELINE_BASE_DIR should override default."""
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", "D:/projects")
        bd = get_base_dir()
        assert str(bd) == r"D:\projects"  # Windows path normalization


class TestCmdRoute:
    def test_route_code(self):
        """Route 'code' task should return a target agent."""
        result = cmd_route("code", "F005")
        assert result["command"] == "route"
        assert result["task_type"] == "code"
        assert "target_agent" in result

    def test_route_unknown(self):
        """Route unknown task should still return a result."""
        result = cmd_route("unknown_task")
        assert result["command"] == "route"
        assert "allowed" in result


class TestCmdCheckHermes:
    def test_hermes_cannot_execute_code(self):
        """Hermes should not be allowed to execute 'code'."""
        result = cmd_check_hermes("code")
        assert result["hermes_allowed"] is False
        assert "must_delegate_to" in result

    def test_hermes_can_execute_orchestration(self):
        """Hermes should be allowed to execute 'orchestrate'."""
        result = cmd_check_hermes("orchestrate")
        assert result["hermes_allowed"] is True


class TestImports:
    def test_all_commands_importable(self):
        """All command functions should be importable."""
        from bridge_cli import cmd_load, cmd_route, cmd_check_hermes, cmd_suggest, cmd_full
        assert callable(cmd_load)
        assert callable(cmd_route)
        assert callable(cmd_check_hermes)
        assert callable(cmd_suggest)
        assert callable(cmd_full)


class TestCmdInspect:
    def test_inspect_returns_audit_report(self, tmp_path, monkeypatch):
        """inspect command should run Inspector.audit and return a report dict."""
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        project_dir = tmp_path / "demo"
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "prd.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")
        (docs_dir / "plan.md").write_text("我们将优化响应时间。", encoding="utf-8")

        result = cmd_inspect("demo", phase="plan")

        assert result["command"] == "inspect"
        assert result["project"] == "demo"
        assert result["phase"] == "plan"
        assert "report" in result
        assert result["report"]["verdict"] == "pass"


class TestCmdAuditReport:
    def test_audit_report_returns_inspector_logs(self, tmp_path, monkeypatch):
        """audit-report command should return inspector_audit events from StateStore."""
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        project_dir = tmp_path / "demo"
        project_dir.mkdir(parents=True)
        db_path = project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.log_audit(
            project_id="demo",
            phase="plan",
            event="inspector_audit",
            details={"verdict": "pass", "findings": []},
        )

        result = cmd_audit_report("demo")

        assert result["command"] == "audit-report"
        assert result["project"] == "demo"
        assert len(result["logs"]) == 1
        assert result["logs"][0]["phase"] == "plan"
        assert result["logs"][0]["event"] == "inspector_audit"
        assert result["logs"][0]["details"]["verdict"] == "pass"


class TestDirectPipelineCommands:
    """Bridge CLI should call PhaseFlow/StateStore directly, not pipeline.py."""

    def test_cmd_init_creates_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        result = cmd_init("demo", description="Test project", stack="Python")

        assert result["command"] == "init"
        assert result["return_code"] == 0
        assert (tmp_path / "demo" / "pipeline_state.db").exists()
        assert (tmp_path / "demo" / "SOUL.md").exists()

    def test_cmd_status_after_init(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        cmd_init("demo")
        result = cmd_status("demo")

        assert result["command"] == "status"
        assert result["return_code"] == 0
        assert result["phase"] == "init"

    def test_cmd_advance_init_to_prd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        cmd_init("demo")
        (tmp_path / "demo" / "docs").mkdir(parents=True)
        (tmp_path / "demo" / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")

        result = cmd_advance("demo")
        assert result["command"] == "advance"
        assert result["success"] is True
        assert "init" in result["message"]
        assert "prd" in result["message"]
        assert cmd_status("demo")["phase"] == "prd"

    def test_cmd_rollback_phase_requires_approval(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        cmd_init("demo")
        # Advance to design via state store so rollback to init requires approval.
        from state_store import StateStore
        db_path = tmp_path / "demo" / "pipeline_state.db"
        store = StateStore(db_path)
        raw = store.legacy_load("state")
        state = json.loads(raw) if raw else {}
        state["phase"] = "design"
        store.legacy_save("state", json.dumps(state, ensure_ascii=False))
        store.update_project_phase("demo", "design")

        result = cmd_rollback_phase("demo", "init", approved=False)
        assert result["success"] is False
        assert result["return_code"] == 1

    def test_cmd_approve_design_and_accept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        cmd_init("demo")

        result = cmd_approve("demo", "design")
        assert result["success"] is True
        result = cmd_approve("demo", "accept")
        assert result["success"] is True

    def test_cmd_mark_tests(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
        cmd_init("demo")

        result = cmd_mark_tests("demo", passed=True)
        assert result["success"] is True
        assert result["passed"] is True

    def test_no_pipeline_proxy_imports(self):
        """bridge_cli.py must not import pipeline command functions."""
        import bridge_cli as bc
        assert not hasattr(bc, "pipeline_cmd_init")
        assert not hasattr(bc, "pipeline_cmd_advance")
        assert not hasattr(bc, "pipeline_cmd_status")
