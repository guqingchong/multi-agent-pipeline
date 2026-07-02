"""tests/test_registry.py — Registry single-source-of-truth tests."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from registry import REGISTRY, AgentDef, PhaseDef, TaskTypeDef, _resolve_cli_path


class TestRegistryBasics:
    def test_registry_is_ready(self):
        assert REGISTRY.is_ready() is True

    def test_agents_registered(self):
        names = REGISTRY.list_agents()
        assert "claude-code" in names
        assert "codewhale" in names
        assert "qwen-code" in names

    def test_phases_registered(self):
        names = REGISTRY.list_phases()
        assert "init" in names
        assert "design" in names
        assert "develop" in names
        assert "deploy" in names

    def test_task_types_registered(self):
        names = REGISTRY.list_task_types()
        assert "code" in names
        assert "review" in names
        assert "test" in names


class TestAgentRegistration:
    def test_claude_code_capabilities(self):
        agent = REGISTRY.get_agent("claude-code")
        assert agent is not None
        assert "code" in agent.capabilities
        assert agent.cli_command == "-p {prompt}"

    def test_codewhale_capabilities(self):
        agent = REGISTRY.get_agent("codewhale")
        assert agent is not None
        assert "review" in agent.capabilities

    def test_qwen_code_capabilities(self):
        agent = REGISTRY.get_agent("qwen-code")
        assert agent is not None
        assert "test" in agent.capabilities
        assert "doc" in agent.capabilities


class TestPhaseRegistration:
    def test_init_phase(self):
        phase = REGISTRY.get_phase("init")
        assert phase is not None
        assert phase.check_func == "check_init"
        assert phase.requires_evidence is False

    def test_design_phase_requires_evidence(self):
        phase = REGISTRY.get_phase("design")
        assert phase is not None
        assert phase.requires_evidence is True


class TestTaskTypeRegistration:
    def test_code_defaults_to_claude_code(self):
        tt = REGISTRY.get_task_type("code")
        assert tt is not None
        assert tt.default_agent == "claude-code"

    def test_shutdown_defaults_to_empty(self):
        tt = REGISTRY.get_task_type("shutdown")
        assert tt is not None
        assert tt.default_agent == ""

    def test_invalid_task_type_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid task type name"):
            REGISTRY.register_task_type(TaskTypeDef(name="BadName"))

        with pytest.raises(ValueError, match="Invalid task type name"):
            REGISTRY.register_task_type(TaskTypeDef(name="bad'name"))

        with pytest.raises(ValueError, match="Invalid task type name"):
            REGISTRY.register_task_type(TaskTypeDef(name=""))

    def test_valid_task_type_name_accepted(self):
        REGISTRY.register_task_type(TaskTypeDef(name="new_type"))
        assert "new_type" in REGISTRY.list_task_types()


class TestCliPathResolution:
    def test_resolve_env_path(self, tmp_path, monkeypatch):
        fake_cli = tmp_path / "fake-cli.cmd"
        fake_cli.write_text("@echo off\n")
        monkeypatch.setenv("AGENT_CLI_PATH_FAKE_AGENT", str(fake_cli))
        resolved = _resolve_cli_path("fake-agent", "nonexistent.exe")
        assert Path(resolved).resolve() == fake_cli.resolve()

    def test_resolve_fallback_path(self, tmp_path):
        fake_cli = tmp_path / "fallback.exe"
        fake_cli.write_text("")
        resolved = _resolve_cli_path("missing-agent", str(fake_cli))
        assert Path(resolved).resolve() == fake_cli.resolve()

    def test_resolve_returns_fallback_when_missing(self):
        resolved = _resolve_cli_path("missing-agent", "missing-fallback.exe")
        assert resolved == "missing-fallback.exe"

    def test_no_hardcoded_user_path_in_source(self):
        # The old registry embedded a user-specific Windows path.  Verify it is gone.
        registry_src = Path(__file__).resolve().parent.parent / "src" / "registry.py"
        source = registry_src.read_text(encoding="utf-8")
        assert "顾庆冲" not in source, "registry.py still contains hardcoded user path"
        assert "AppData\\Roaming\\npm" not in source, "registry.py still contains hardcoded npm path"
