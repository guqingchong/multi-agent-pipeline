"""tests/test_adapters.py — Adapter 解析层单元测试

F012 验收标准：
- 所有 Agent Adapter 可导入
- 解析层单元测试通过
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from adapters import (
    AgentResult,
    ClaudeCodeAdapter,
    CodeWhaleAdapter,
    QwenCodeAdapter,
    AgentAdapterBase,
    OutputParser,
    ToleranceLayer,
    TimeoutError,
    CrashError,
    TruncationError,
    ParseError,
    AgentNotReadyError,
)


# ───────────────────────────────────────────────────────────────
# AgentResult 基础测试
# ───────────────────────────────────────────────────────────────

class TestAgentResult:
    def test_create_success(self) -> None:
        r = AgentResult(success=True, output="hello", exit_code=0)
        assert r.success is True
        assert r.output == "hello"
        assert r.exit_code == 0
        assert r.structured is None
        assert r.error_message is None

    def test_create_failure(self) -> None:
        r = AgentResult(success=False, output="", exit_code=1, error_message="err")
        assert r.success is False
        assert r.error_message == "err"

    def test_create_with_structured(self) -> None:
        r = AgentResult(success=True, output="ok", exit_code=0, structured={"key": "val"})
        assert r.structured == {"key": "val"}

    def test_defaults(self) -> None:
        r = AgentResult(success=True, output="")
        assert r.tokens_used == 0
        assert r.cost_usd == 0.0
        assert r.latency_ms == 0
        assert r.exit_code == 0

    def test_to_dict(self) -> None:
        r = AgentResult(success=True, output="o", exit_code=0, structured={"a": 1})
        d = r.to_dict()
        assert d["success"] is True
        assert d["output"] == "o"
        assert d["structured"] == {"a": 1}

    def test_from_dict(self) -> None:
        d = {
            "success": False,
            "output": "fail",
            "structured": None,
            "tokens_used": 10,
            "cost_usd": 0.01,
            "latency_ms": 100,
            "exit_code": 1,
            "error_message": "bad",
        }
        r = AgentResult.from_dict(d)
        assert r.success is False
        assert r.tokens_used == 10
        assert r.cost_usd == 0.01


# ───────────────────────────────────────────────────────────────
# OutputParser 测试（解析层）
# ───────────────────────────────────────────────────────────────

class TestOutputParser:
    def test_parse_json_block_valid(self) -> None:
        text = 'some text\n```json\n{"a": 1}\n```\nmore text'
        result = OutputParser.parse_json_block(text)
        assert result == {"a": 1}

    def test_parse_json_block_no_markers(self) -> None:
        text = '{"b": 2}'
        result = OutputParser.parse_json_block(text)
        assert result == {"b": 2}

    def test_parse_json_block_invalid_json(self) -> None:
        with pytest.raises(ParseError):
            OutputParser.parse_json_block("not json")

    def test_parse_json_block_empty(self) -> None:
        with pytest.raises(ParseError):
            OutputParser.parse_json_block("")

    def test_extract_code_blocks(self) -> None:
        text = "```python\nprint(1)\n```\n```bash\necho hi\n```"
        blocks = OutputParser.extract_code_blocks(text)
        assert len(blocks) == 2
        assert blocks[0] == ("python", "print(1)")
        assert blocks[1] == ("bash", "echo hi")

    def test_extract_code_blocks_no_lang(self) -> None:
        text = "```\ncode\n```"
        blocks = OutputParser.extract_code_blocks(text)
        assert blocks[0] == (None, "code")

    def test_extract_code_blocks_empty(self) -> None:
        assert OutputParser.extract_code_blocks("no code") == []

    def test_heuristic_extract_p0_issues(self) -> None:
        text = """
## 审查报告

**P0** 文件 app.py 第 42 行: 空指针风险
**P0** 文件 utils.py 第 10 行: 除以零
"""
        issues = OutputParser.heuristic_extract_issues(text)
        assert len(issues) == 2
        assert issues[0]["level"] == "P0"
        assert issues[0]["file"] == "app.py"
        assert issues[0]["line"] == 42

    def test_heuristic_extract_p1_p2(self) -> None:
        text = "**P1** 文件 main.py 第 5 行: 性能问题\n**P2** 文件 main.py 第 8 行: 命名不佳"
        issues = OutputParser.heuristic_extract_issues(text)
        assert len(issues) == 2
        assert issues[0]["level"] == "P1"
        assert issues[1]["level"] == "P2"

    def test_heuristic_extract_no_issues(self) -> None:
        assert OutputParser.heuristic_extract_issues("clean code") == []

    def test_heuristic_extract_various_formats(self) -> None:
        texts = [
            "P0: file.py line 10: bug",
            "**P0** file.py line 10: bug",
            "- P0 file.py:10 bug",
            "P0 | file.py | 10 | bug",
        ]
        for t in texts:
            issues = OutputParser.heuristic_extract_issues(t)
            assert len(issues) >= 1, f"failed for {t!r}"
            assert issues[0]["level"] == "P0"

    def test_extract_diff_stats(self) -> None:
        text = " app.py | 3 +++\n utils.py | 1 -"
        stats = OutputParser.extract_diff_stats(text)
        assert stats == {"app.py": {"add": 3, "del": 0}, "utils.py": {"add": 0, "del": 1}}

    def test_extract_diff_stats_empty(self) -> None:
        assert OutputParser.extract_diff_stats("") == {}

    def test_extract_git_commit_hash(self) -> None:
        text = "[main abc1234] commit message"
        assert OutputParser.extract_git_commit_hash(text) == "abc1234"

    def test_extract_git_commit_hash_none(self) -> None:
        assert OutputParser.extract_git_commit_hash("no commit") is None

    def test_sanitize_truncation_markers(self) -> None:
        text = "output... [truncated] more"
        clean = OutputParser.sanitize_truncation_markers(text)
        assert "[truncated]" not in clean
        assert "..." not in clean

    def test_sanitize_unicode_replacement(self) -> None:
        text = "output\ufffdmore"
        clean = OutputParser.sanitize_unicode_replacement(text)
        assert "\ufffd" not in clean

    def test_detect_truncation_by_marker(self) -> None:
        assert OutputParser.detect_truncation("abc [truncated]") is True
        assert OutputParser.detect_truncation("abc") is False

    def test_detect_truncation_by_incomplete(self) -> None:
        assert OutputParser.detect_truncation("{\"a\": 1", is_json=True) is True
        assert OutputParser.detect_truncation("{\"a\": 1}", is_json=True) is False


# ───────────────────────────────────────────────────────────────
# ClaudeCodeAdapter 解析层测试
# ───────────────────────────────────────────────────────────────

class TestClaudeCodeAdapterParsing:
    def test_parse_print_mode_json(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = '{"success": true, "output": "done", "exit_code": 0}'
        result = adapter.parse_output(raw)
        assert result.success is True
        assert result.output == "done"
        assert result.exit_code == 0

    def test_parse_plain_text(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = "some plain text output\nwith multiple lines"
        result = adapter.parse_output(raw)
        assert result.success is True
        assert result.output == raw
        assert result.exit_code == 0

    def test_parse_with_code_blocks(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = "```python\nprint(1)\n```\nDone"
        result = adapter.parse_output(raw)
        assert "print(1)" in result.output

    def test_parse_with_diff_stats(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = " app.py | 3 +++\n utils.py | 1 -"
        result = adapter.parse_output(raw)
        assert result.structured is not None
        assert result.structured.get("diff_stats") == {
            "app.py": {"add": 3, "del": 0},
            "utils.py": {"add": 0, "del": 1},
        }

    def test_parse_git_commit(self) -> None:
        adapter = ClaudeCodeAdapter()
        raw = "[main abc1234] feat: add auth"
        result = adapter.parse_output(raw)
        assert result.structured is not None
        assert result.structured.get("commit_hash") == "abc1234"

    def test_parse_empty_output(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.parse_output("")
        assert result.success is True
        assert result.output == ""

    def test_build_command_print_mode(self) -> None:
        adapter = ClaudeCodeAdapter()
        cmd = adapter.build_command("write a function", timeout=60)
        assert "claude" in cmd[0] or "claude.exe" in cmd[0]
        assert "claude" in cmd[0] or "claude.exe" in cmd[0]
        assert "-p" in cmd
        assert "write a function" in cmd

    def test_build_command_custom_timeout(self) -> None:
        adapter = ClaudeCodeAdapter()
        cmd = adapter.build_command("task", timeout=120)
        assert "claude" in cmd[0] or "claude.exe" in cmd[0]
        assert "-p" in cmd

    def test_adapter_name(self) -> None:
        assert ClaudeCodeAdapter().name == "claude"

    def test_version(self) -> None:
        assert ClaudeCodeAdapter().version == "1.0"

    def test_capabilities(self) -> None:
        caps = ClaudeCodeAdapter().capabilities()
        assert "code" in caps
        assert "review" not in caps

    def test_format_input_prompt(self) -> None:
        adapter = ClaudeCodeAdapter()
        inp = adapter.format_input("do X", context={"file": "app.py"})
        assert "do X" in inp
        assert "app.py" in inp

    def test_format_input_empty_context(self) -> None:
        adapter = ClaudeCodeAdapter()
        inp = adapter.format_input("do Y")
        assert "do Y" in inp


# ───────────────────────────────────────────────────────────────
# CodeWhaleAdapter 解析层测试
# ───────────────────────────────────────────────────────────────

class TestCodeWhaleAdapterParsing:
    def test_parse_review_report(self) -> None:
        adapter = CodeWhaleAdapter()
        raw = """
## 审查报告

**P0** 文件 app.py 第 42 行: 空指针风险
建议: 添加检查

**P1** 文件 utils.py 第 10 行: 性能问题
建议: 使用缓存
"""
        result = adapter.parse_output(raw)
        assert result.success is True
        assert result.structured is not None
        issues = result.structured.get("issues", [])
        assert len(issues) == 2
        assert issues[0]["level"] == "P0"
        assert issues[1]["level"] == "P1"

    def test_parse_no_issues(self) -> None:
        adapter = CodeWhaleAdapter()
        raw = "代码审查通过，无问题。"
        result = adapter.parse_output(raw)
        assert result.success is True
        assert result.structured is not None
        assert result.structured.get("issues", []) == []
        assert result.structured.get("passed") is True

    def test_parse_with_json_block(self) -> None:
        adapter = CodeWhaleAdapter()
        raw = '```json\n{"summary": "ok", "issues": []}\n```'
        result = adapter.parse_output(raw)
        assert result.structured is not None
        assert result.structured.get("summary") == "ok"

    def test_build_command_auto_mode(self) -> None:
        adapter = CodeWhaleAdapter()
        cmd = adapter.build_command("review diff", timeout=60)
        assert "codewhale" in cmd[0] or "CodeWhale" in cmd[0]
        assert "exec" in cmd and "--auto" in cmd

    def test_adapter_name(self) -> None:
        assert CodeWhaleAdapter().name == "codewhale"

    def test_capabilities(self) -> None:
        caps = CodeWhaleAdapter().capabilities()
        assert "review" in caps
        assert "code" not in caps

    def test_format_input_with_diff(self) -> None:
        adapter = CodeWhaleAdapter()
        inp = adapter.format_input("review", context={"diff": "+line"})
        assert "review" in inp
        assert "+line" in inp

    def test_format_input_no_thinking(self) -> None:
        adapter = CodeWhaleAdapter()
        inp = adapter.format_input("review", context={"thinking": "secret"})
        assert "secret" not in inp


# ───────────────────────────────────────────────────────────────
# QwenCodeAdapter 解析层测试
# ───────────────────────────────────────────────────────────────

class TestQwenCodeAdapterParsing:
    def test_parse_json_output(self) -> None:
        adapter = QwenCodeAdapter()
        raw = '{"success": true, "result": "done"}'
        result = adapter.parse_output(raw)
        assert result.success is True
        assert result.structured is not None
        assert result.structured.get("result") == "done"

    def test_parse_markdown_output(self) -> None:
        adapter = QwenCodeAdapter()
        raw = "## 结果\n\n测试通过。\n"
        result = adapter.parse_output(raw)
        assert result.success is True
        assert "测试通过" in result.output

    def test_parse_with_code_blocks(self) -> None:
        adapter = QwenCodeAdapter()
        raw = "```python\ndef foo(): pass\n```"
        result = adapter.parse_output(raw)
        assert result.structured is not None
        blocks = result.structured.get("code_blocks", [])
        assert len(blocks) == 1
        assert blocks[0] == ("python", "def foo(): pass")

    def test_build_command_y_mode(self) -> None:
        adapter = QwenCodeAdapter()
        cmd = adapter.build_command("run test", timeout=60)
        assert "qwen" in cmd[0] or "Qwen" in cmd[0]
        assert "run test" in cmd or "qwen" in cmd[0]

    def test_build_command_json_output(self) -> None:
        adapter = QwenCodeAdapter()
        cmd = adapter.build_command("task", timeout=60)
        # output-format no longer used with -p flag or "json" in cmd

    def test_adapter_name(self) -> None:
        assert QwenCodeAdapter().name == "qwen"

    def test_capabilities(self) -> None:
        caps = QwenCodeAdapter().capabilities()
        assert "code" in caps
        assert "test" in caps
        assert "e2e" in caps

    def test_format_input_simple_task(self) -> None:
        adapter = QwenCodeAdapter()
        inp = adapter.format_input("fix bug", context={"max_lines": 20})
        assert "fix bug" in inp
        assert "20" in inp


# ───────────────────────────────────────────────────────────────
# Adapter 基类测试
# ───────────────────────────────────────────────────────────────

class TestAgentAdapterBase:
    def test_base_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            AgentAdapterBase()

    def test_subclass_must_implement(self) -> None:
        class BadAdapter(AgentAdapterBase):
            pass

        with pytest.raises(TypeError):
            BadAdapter()

    def test_good_subclass(self) -> None:
        class GoodAdapter(AgentAdapterBase):
            @property
            def name(self) -> str:
                return "good"

            def build_command(self, prompt: str, *, timeout: int = 60) -> list[str]:
                return ["echo", prompt]

            def parse_output(self, raw_output: str) -> AgentResult:
                return AgentResult(success=True, output=raw_output)

            def format_input(self, prompt: str, context: dict | None = None) -> str:
                return prompt

            def capabilities(self) -> list[str]:
                return ["test"]

        adapter = GoodAdapter()
        assert adapter.name == "good"
        assert adapter.build_command("hi") == ["echo", "hi"]
        assert adapter.parse_output("ok").success is True

    def test_default_timeout(self) -> None:
        class GoodAdapter(AgentAdapterBase):
            @property
            def name(self) -> str:
                return "good"

            def build_command(self, prompt: str, *, timeout: int = 60) -> list[str]:
                return ["echo", str(timeout)]

            def parse_output(self, raw_output: str) -> AgentResult:
                return AgentResult(success=True, output=raw_output)

            def format_input(self, prompt: str, context: dict | None = None) -> str:
                return prompt

            def capabilities(self) -> list[str]:
                return ["test"]

        adapter = GoodAdapter()
        assert adapter.build_command("hi") == ["echo", "60"]


# ───────────────────────────────────────────────────────────────
# 导入测试
# ───────────────────────────────────────────────────────────────

class TestImports:
    def test_all_adapters_importable(self) -> None:
        from adapters import ClaudeCodeAdapter, CodeWhaleAdapter, QwenCodeAdapter
        assert ClaudeCodeAdapter is not None
        assert CodeWhaleAdapter is not None
        assert QwenCodeAdapter is not None

    def test_all_exceptions_importable(self) -> None:
        from adapters import (
            TimeoutError,
            CrashError,
            TruncationError,
            ParseError,
            AgentNotReadyError,
        )
        assert TimeoutError is not None
        assert CrashError is not None
        assert TruncationError is not None
        assert ParseError is not None
        assert AgentNotReadyError is not None

    def test_all_classes_importable(self) -> None:
        from adapters import AgentResult, OutputParser, ToleranceLayer
        assert AgentResult is not None
        assert OutputParser is not None
        assert ToleranceLayer is not None


# ───────────────────────────────────────────────────────────────
# AgentAdapter 统一适配器测试（Task 7）
# ───────────────────────────────────────────────────────────────

class TestAgentAdapter:
    def test_adapter_importable(self) -> None:
        from adapters import AgentAdapter
        assert AgentAdapter is not None

    def test_version_in_mock_mode(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        ok, msg = adapter.version()
        assert ok is True
        assert "MOCK" in msg
        assert "test-agent" in msg

    def test_health_in_mock_mode(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        health = adapter.health()
        assert health["ok"] is True
        assert "MOCK" in health["version"]
        assert health["cli_path"] == "/nonexistent/cli"

    def test_run_uses_mock_output_and_parses(self) -> None:
        from adapters import AgentAdapter, AdapterStatus
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        result = adapter.run("code", {"prompt": "hello"}, work_dir=".")
        assert result.success is True
        assert result.status == AdapterStatus.SUCCESS
        assert "MOCK" in result.output
        assert result.structured is not None
        assert result.exit_code == 0

    def test_run_failure_path_with_nonzero_exit(self) -> None:
        from adapters import AgentAdapter, AdapterStatus
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        result = adapter._parse_and_validate(
            raw_stdout="",
            raw_stderr="something went wrong",
            exit_code=1,
        )
        assert result.success is False
        assert result.status == AdapterStatus.FAILED
        assert result.exit_code == 1
        assert "something went wrong" in result.error_message

    def test_run_truncated_output(self) -> None:
        from adapters import AgentAdapter, AdapterStatus
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        truncated_stdout = "incomplete output [truncated]"
        result = adapter._parse_and_validate(
            raw_stdout=truncated_stdout,
            raw_stderr="",
            exit_code=0,
        )
        assert result.success is False
        assert result.status == AdapterStatus.FAILED
        assert "truncated" in result.error_message.lower()

    def test_build_command_with_prompt_placeholder(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/bin/agent",
            cli_command="exec --auto {prompt}",
            env_vars={},
        )
        cmd = adapter._build_command("code", {"prompt": "do work"})
        assert cmd == ["/bin/agent", "exec", "--auto", "do work"]

    def test_build_command_without_prompt_placeholder(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/bin/agent",
            cli_command="run",
            env_vars={},
        )
        cmd = adapter._build_command("code", {"prompt": "do work"})
        assert cmd == ["/bin/agent", "run"]

    def test_parser_extract_runs_in_mock_path(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        parsed = adapter._parser.extract(
            "Tests: 5 passed, 0 failed\n4200 tokens",
            "",
        )
        assert parsed["test_stats"] == {"passed": 5, "failed": 0, "skipped": 0, "errors": 0}
        assert parsed["tokens"] == 4200

    def test_tolerance_handle_failure(self) -> None:
        from adapters import AgentAdapter, AdapterStatus
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        result = adapter._tolerance.handle_failure("stderr msg", 42)
        assert result.success is False
        assert result.status == AdapterStatus.FAILED
        assert result.exit_code == 42
        assert "stderr msg" in result.error_message

    def test_tolerance_is_truncated(self) -> None:
        from adapters import AgentAdapter
        adapter = AgentAdapter(
            name="test-agent",
            cli_path="/nonexistent/cli",
            cli_command="{prompt}",
            env_vars={},
        )
        assert adapter._tolerance.is_truncated("abc [truncated]") is True
        assert adapter._tolerance.is_truncated("complete output") is False
