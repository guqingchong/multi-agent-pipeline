"""tests/test_adapters_fallback.py — Tests for adapter fallback channels."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from adapters import FileBasedChannel, MCPChannel, AgentResult, AdapterStatus


class TestFileBasedChannel:
    def test_send_creates_task_file(self):
        inbox = Path(tempfile.mkdtemp()) / "inbox"
        try:
            ch = FileBasedChannel(inbox, agent_name="test_agent")
            r = ch.send("do something", {"key": "val"})
            assert r.success
            assert r.structured["channel"] == "file"
            task_id = r.structured["task_id"]
            task_file = inbox / f"task_{task_id}.json"
            assert task_file.exists()
            data = json.loads(task_file.read_text(encoding="utf-8"))
            assert data["task"] == "do something"
            assert data["context"]["key"] == "val"
        finally:
            shutil.rmtree(inbox.parent, ignore_errors=True)

    def test_receive_reads_result_file(self):
        inbox = Path(tempfile.mkdtemp()) / "inbox"
        try:
            ch = FileBasedChannel(inbox, agent_name="test_agent")
            r = ch.send("task")
            task_id = r.structured["task_id"]
            result_path = inbox / f"result_{task_id}.json"
            result_path.write_text(
                json.dumps({"success": True, "output": "done", "structured": {"x": 1}}),
                encoding="utf-8",
            )
            r2 = ch.receive(timeout=5)
            assert r2 is not None
            assert r2.success
            assert r2.output == "done"
        finally:
            shutil.rmtree(inbox.parent, ignore_errors=True)

    def test_receive_timeout_returns_none(self):
        inbox = Path(tempfile.mkdtemp()) / "inbox"
        try:
            ch = FileBasedChannel(inbox, agent_name="test_agent")
            r = ch.receive(timeout=1)
            assert r is None
        finally:
            shutil.rmtree(inbox.parent, ignore_errors=True)


class TestMCPChannel:
    def test_send_returns_failed_stub(self):
        """MCP send should return failed stub status."""
        mcp = MCPChannel(endpoint="http://localhost:8080", agent_name="a")
        r = mcp.send("task")
        assert r.success is False
        assert "not yet implemented" in r.output.lower()
        assert r.structured["channel"] == "mcp"

    def test_receive_stub_returns_none(self):
        mcp = MCPChannel(endpoint="http://localhost:8080", agent_name="a")
        r = mcp.receive(timeout=1)
        assert r is not None
        assert r.success is False
        assert "not yet implemented" in r.output.lower()
        assert r.structured["channel"] == "mcp"
