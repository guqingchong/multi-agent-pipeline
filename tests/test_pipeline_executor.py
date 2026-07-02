"""tests/test_pipeline_executor.py — PipelineExecutor + AgentAdapter integration tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline_executor import PipelineExecutor, create_executor, MCPStatus


class TestAgentAdapterIntegration:
    def test_create_executor_registers_defaults(self, tmp_path) -> None:
        """create_executor should register all default endpoints without real CLIs."""
        os.environ["AGENT_MOCK"] = "true"
        executor = create_executor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
        )
        assert len(executor._cli_endpoints) >= 3
        for name in ("claude-code", "codewhale", "qwen-code"):
            assert name in executor._cli_endpoints

    def test_dispatch_and_wait_sync_path_uses_agent_adapter(self, tmp_path) -> None:
        """dispatch_and_wait should succeed in sync path using AgentAdapter mock."""
        os.environ["AGENT_MOCK"] = "true"
        executor = create_executor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
        )

        result = executor.dispatch_and_wait(
            "codewhale",
            "review",
            {"prompt": "review this code"},
            timeout_sec=60,
        )

        assert result.success is True
        assert result.agent_id == "codewhale"
        assert "MOCK" in result.output

    def test_dispatch_and_wait_claude_code(self, tmp_path) -> None:
        """dispatch_and_wait should work for claude-code via AgentAdapter mock."""
        os.environ["AGENT_MOCK"] = "true"
        executor = create_executor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
        )

        result = executor.dispatch_and_wait(
            "claude-code",
            "code",
            {"prompt": "implement feature"},
            timeout_sec=60,
        )

        assert result.success is True
        assert result.agent_id == "claude-code"

    def test_dispatch_and_wait_qwen_code(self, tmp_path) -> None:
        """dispatch_and_wait should work for qwen-code via AgentAdapter mock."""
        os.environ["AGENT_MOCK"] = "true"
        executor = create_executor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
        )

        result = executor.dispatch_and_wait(
            "qwen-code",
            "test",
            {"prompt": "run tests"},
            timeout_sec=60,
        )

        assert result.success is True
        assert result.agent_id == "qwen-code"

    def test_dispatch_and_wait_unknown_adapter(self, tmp_path) -> None:
        """dispatch_and_wait should raise ValueError for unknown adapter."""
        os.environ["AGENT_MOCK"] = "true"
        executor = create_executor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
        )

        with pytest.raises(ValueError, match="Unknown"):
            executor.dispatch_and_wait("unknown-agent", "code", {})

    def test_executor_status_includes_endpoints(self, tmp_path) -> None:
        """executor.status() should list registered CLI endpoints."""
        os.environ["AGENT_MOCK"] = "true"
        executor = PipelineExecutor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
            enable_heartbeat=False,
            enable_streaming=False,
            enable_stdio=False,
        )
        executor.register_all_defaults()

        status = executor.status()
        assert "cli_endpoints" in status
        assert set(status["cli_endpoints"]) >= {"claude-code", "codewhale", "qwen-code"}

    def test_dispatch_and_wait_reports_timeout(self, tmp_path, monkeypatch) -> None:
        """When subprocess.run raises TimeoutExpired, status must be MCPStatus.TIMEOUT."""
        os.environ["AGENT_MOCK"] = "false"
        import adapters

        def raise_timeout(*args, **kwargs):
            timeout = kwargs.get("timeout", 1)
            raise subprocess.TimeoutExpired("mock-cmd", timeout)

        monkeypatch.setattr(adapters.subprocess, "run", raise_timeout)

        executor = PipelineExecutor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
            enable_heartbeat=False,
            enable_streaming=False,
            enable_stdio=False,
        )
        executor.register_all_defaults()

        result = executor.dispatch_and_wait(
            "codewhale",
            "review",
            {"prompt": "review this code"},
            timeout_sec=1,
        )

        assert result.success is False
        assert result.status == MCPStatus.TIMEOUT
        assert "Timeout" in result.error

    def test_mock_mode_does_not_call_subprocess_run(self, tmp_path, monkeypatch) -> None:
        """When AGENT_MOCK=true, AgentAdapter must never invoke subprocess.run."""
        os.environ["AGENT_MOCK"] = "true"
        import adapters

        called = []

        def fake_run(*args, **kwargs):
            called.append((args, kwargs))
            raise AssertionError("subprocess.run should not be called in mock mode")

        monkeypatch.setattr(adapters.subprocess, "run", fake_run)

        executor = PipelineExecutor(
            db_path=str(tmp_path / "pipeline.db"),
            work_dir=str(tmp_path),
            enable_heartbeat=False,
            enable_streaming=False,
            enable_stdio=False,
        )
        executor.register_all_defaults()

        result = executor.dispatch_and_wait(
            "codewhale",
            "review",
            {"prompt": "review this code"},
            timeout_sec=60,
        )

        assert result.success is True
        assert result.status == MCPStatus.COMPLETED
        assert called == []
