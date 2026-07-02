"""tests/test_bridge_cli.py — Tests for bridge_cli.py"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bridge_cli import get_base_dir, cmd_load, cmd_route, cmd_check_hermes, cmd_suggest


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
