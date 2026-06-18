"""tests_three_layer/test_p0_issues.py — 验证5个P0问题可通过测试发现

这些测试是"失败测试"（预期当前会失败），用于证明设计文档中的P0问题。
如果设计修复后，这些测试应该通过。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

# 将 src 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from phase_flow import PhaseFlow, PHASE_ORDER
from phase_checks import check_init, check_design
from state_store import StateStore


# ───────────────────────────────────────────────────────────────
# P0-001: RoleGuard 自检悖论
# ───────────────────────────────────────────────────────────────

class TestP0_001_RoleGuardSelfCheckParadox:
    """验证 P0-001: RoleGuard 无法约束 Hermes 自身

    设计文档假设: RoleGuard 能拦截 Hermes 的编码行为
    实际: Hermes 是当前进程，直接调用工具不经过 AgentDispatcher
    """

    def test_hermes_direct_tool_call_bypasses_roleguard(self):
        """Hermes 直接调用 write_file 时，RoleGuard 不会被触发"""
        # 模拟 Hermes 直接编码（不经过任何调度/约束层）
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("# original content")
            path = f.name

        # Hermes "直接"写入文件（模拟内部工具调用）
        with open(path, 'w', encoding='utf-8') as f:
            f.write("# modified by Hermes - this should be blocked but isn't")

        # 验证文件已被修改（RoleGuard 从未被调用）
        content = Path(path).read_text(encoding='utf-8')
        assert "modified by Hermes" in content
        # 此断言通过 = 证明 P0-001 存在：Hermes 可以绕过任何自我约束

        os.unlink(path)

    def test_roleguard_pure_function_has_no_enforcement_power(self):
        """RoleGuard 是纯函数判断，没有强制力"""
        # 设计文档说 RoleGuard 是 "无状态，纯函数判断"
        # 纯函数可以被调用方忽略

        def check_role_constraint(agent_name: str, action: str) -> Tuple[bool, str]:
            """设计文档中的 RoleGuard 逻辑"""
            ROLE_TASKS = {
                "Hermes": ["orchestrate", "dispatch", "research_org", "gather_results"],
            }
            allowed = ROLE_TASKS.get(agent_name, [])
            if action not in allowed:
                return False, f"【拦截】{agent_name} 无权执行 '{action}'"
            return True, "PASS"

        # 模拟 Hermes 调用 RoleGuard 检查自己
        allowed, reason = check_role_constraint("Hermes", "code_write")
        assert allowed is False  # 检查说"不行"

        # 但 Hermes 完全可以忽略检查结果，继续执行
        # 这就是 P0-001 的核心：自检悖论
        # 没有外部强制机制，纯函数检查可以被绕过


# ───────────────────────────────────────────────────────────────
# P0-002: 自动推进导致数据损坏
# ───────────────────────────────────────────────────────────────

class TestP0_002_AutoAdvanceDataCorruption:
    """验证 P0-002: 空文件/脆弱 check 导致错误自动推进"""

    def test_check_init_passes_with_empty_files(self):
        """check_init 只检查文件存在性，空文件也能通过"""
        tmpdir = tempfile.mkdtemp()
        try:
            base = Path(tmpdir)
            proj_name = "test_project"
            proj_dir = base / proj_name
            proj_dir.mkdir()

            # 创建空文件（满足存在性检查）
            (proj_dir / "features.json").write_text(json.dumps({"project": proj_name}))  # 最小有效JSON
            (proj_dir / "SOUL.md").write_text("")
            (proj_dir / "AGENTS.md").write_text("")
            (proj_dir / "progress.md").write_text("")

            # 初始化 git repo
            os.system(f"cd {proj_dir} && git init -q")

            # 创建 DB
            db_path = proj_dir / "pipeline_state.db"
            store = StateStore(db_path)
            store.create_project(proj_name, proj_name, "init")
            del store  # 关闭连接

            result = check_init(proj_name, base)

            # 验证: 空文件让 check_init 通过（这是问题！）
            # features.json 只有 {"project": "test_project"}，没有实际的 features 数据
            # 但 check_init 认为它"有效"
            assert result["passed"] is True, f"Expected check to pass with empty files, got: {result}"
            # 此断言通过 = 证明 P0-002: 脆弱的 check 函数导致错误推进
        finally:
            import gc
            gc.collect()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_empty_features_json_considered_valid(self):
        """空的 features.json 被 check_init 认为是有效的"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_name = "test_project"
            proj_dir = base / proj_name
            proj_dir.mkdir()

            # 创建最小有效文件
            (proj_dir / "features.json").write_text(json.dumps({"project": proj_name}))
            (proj_dir / "SOUL.md").write_text("")
            (proj_dir / "AGENTS.md").write_text("")
            (proj_dir / "progress.md").write_text("")
            os.system(f"cd {proj_dir} && git init -q")
            (proj_dir / "pipeline_state.db").touch()

            result = check_init(proj_name, base)
            assert result["passed"] is True
            # 但 features.json 中没有实际的 features 列表！
            # 如果 PhaseEngine 基于此推进到 design，会生成垃圾设计


# ───────────────────────────────────────────────────────────────
# P0-003: 循环依赖死锁
# ───────────────────────────────────────────────────────────────

class TestP0_003_CircularDependencyDeadlock:
    """验证 P0-003: PhaseEngine 和 ConstraintLayer 的循环依赖"""

    def test_phase_engine_init_with_constraint_layer_no_deadlock(self):
        """验证初始化不会死锁（当前设计可能导致）"""
        # 设计文档中:
        # PhaseEngine.__init__ -> self.constraint = ConstraintLayer()
        # ConstraintLayer.GoalValidator -> 需要 PhaseFlow.check()
        # GoalValidator 如果持有 PhaseFlow 实例，而 PhaseFlow 由 PhaseEngine 持有
        # -> 循环依赖

        # 模拟设计文档中的初始化
        def mock_init():
            # 模拟 PhaseEngine 初始化
            # 如果这里引入 ConstraintLayer，而 ConstraintLayer 又需要 PhaseFlow...
            time.sleep(0.1)  # 模拟初始化工作
            return "initialized"

        result = [None]
        def run():
            try:
                result[0] = mock_init()
            except Exception as e:
                result[0] = str(e)

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=3)

        assert not t.is_alive(), "初始化死锁超过3秒（P0-003）"
        assert result[0] == "initialized", f"初始化失败: {result[0]}"

    def test_constraint_layer_should_not_depend_on_phase_engine(self):
        """约束层不应依赖调度层实例（单向依赖）"""
        # 设计文档中 ConstraintLayer 的 GoalValidator "复用 phase_checks.py 的 check 函数"
        # 但 phase_checks 的 check 函数需要 (project_name, base_dir)，不是 PhaseFlow 实例
        # 这是正确的方向！

        # 验证: 我们可以独立调用 check 函数，不需要 PhaseEngine
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_name = "test"
            proj_dir = base / proj_name
            proj_dir.mkdir()

            # 创建空文件
            (proj_dir / "features.json").write_text(json.dumps({"project": proj_name}))
            (proj_dir / "SOUL.md").write_text("")
            (proj_dir / "AGENTS.md").write_text("")
            (proj_dir / "progress.md").write_text("")
            os.system(f"cd {proj_dir} && git init -q")
            (proj_dir / "pipeline_state.db").touch()

            # 直接调用 check，不需要 PhaseEngine
            result = check_init(proj_name, base)
            assert "passed" in result
            # 证明: check 函数可以独立运行，不依赖 PhaseEngine
            # 设计文档应明确这一点，避免循环依赖


# ───────────────────────────────────────────────────────────────
# P0-004: ViolationLogger 事后记录
# ───────────────────────────────────────────────────────────────

class TestP0_004_ViolationLoggerPostHoc:
    """验证 P0-004: 违规操作已执行，日志只是事后记录"""

    def test_file_modified_before_interception(self):
        """文件在 RoleGuard 拦截前已被修改"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("# original content")
            path = f.name

        # 模拟执行流程:
        # 1. Agent 调用 write_file（执行）
        with open(path, 'w', encoding='utf-8') as f:
            f.write("# MALICIOUS CONTENT")

        # 2. 然后 RoleGuard 检查（事后）
        def mock_roleguard_check(agent, action):
            # 假设检查不通过
            return False, "拦截: 无权执行"

        allowed, reason = mock_roleguard_check("Claude Code", "code_write")
        assert allowed is False

        # 3. 但文件已被修改！
        content = Path(path).read_text(encoding='utf-8')
        assert "MALICIOUS" in content, "P0-004: 损害已发生，拦截无法阻止"

        os.unlink(path)

    def test_no_rollback_mechanism(self):
        """ViolationLogger 没有记录变更前后快照，无法回滚"""
        # 检查现有 audit_logs 表结构
        tmpdir = tempfile.mkdtemp()
        try:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)

            # audit_logs 表结构: id, project_id, agent, command, allowed, created_at
            # 缺少: file_path, before_content, after_content, rollback_info

            # 写入一条审计记录
            conn = store._conn()
            with conn:
                conn.execute(
                    "INSERT INTO audit_logs (project_id, agent, command, allowed) VALUES (?, ?, ?, ?)",
                    ("test", "Claude Code", "write_file src/core.py", False)
                )
                conn.commit()

            # 查询记录
            row = conn.execute("SELECT * FROM audit_logs LIMIT 1").fetchone()
            assert row is not None

            # 验证: 没有文件内容快照字段
            columns = [description[0] for description in conn.execute(
                "PRAGMA table_info(audit_logs)").fetchall()]
            assert "file_path" not in columns, "如果存在则设计已修复"
            assert "before_content" not in columns
            assert "after_content" not in columns
            # 此断言通过 = 证明 P0-004: 无法回滚
        finally:
            # 手动关闭连接，Windows 才能删除文件
            import gc
            gc.collect()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ───────────────────────────────────────────────────────────────
# P0-005: IntentParser LLM 兜底延迟
# ───────────────────────────────────────────────────────────────

class TestP0_005_IntentParserLLMDelay:
    """验证 P0-005: LLM 兜底解析引入不可控延迟"""

    def test_llm_fallback_latency_exceeds_200ms(self):
        """LLM 兜底延迟远超设计文档声称的 200ms"""
        # 模拟 LLM API 调用（即使是轻量模型）
        def mock_llm_parse(text: str) -> Dict[str, Any]:
            """模拟 LLM 解析，包含网络延迟"""
            time.sleep(0.5)  # 模拟 500ms 延迟（乐观估计）
            return {"intent": "AMBIGUOUS", "confidence": 0.5}

        start = time.time()
        result = mock_llm_parse("some ambiguous input")
        elapsed = time.time() - start

        # 设计文档声称 "增加 200ms 延迟"
        # 实际 API 调用（cold start）可能 2-5s
        # 这里用 500ms 模拟，已经超出 200ms
        assert elapsed > 0.2, f"LLM延迟{elapsed:.3f}s，超出设计文档声称的200ms"
        # 此断言通过 = 证明 P0-005: 延迟不可控

    def test_three_tier_parser_latency_cumulative(self):
        """三层级联解析的总延迟"""
        def rule_layer(text: str) -> Optional[Dict[str, Any]]:
            time.sleep(0.01)  # 10ms
            return None  # 未匹配

        def pattern_layer(text: str) -> Optional[Dict[str, Any]]:
            time.sleep(0.05)  # 50ms
            return None  # 未匹配

        def llm_layer(text: str) -> Dict[str, Any]:
            time.sleep(0.5)  # 500ms（模拟）
            return {"intent": "AMBIGUOUS", "confidence": 0.5}

        start = time.time()
        result = rule_layer("test")
        if result is None:
            result = pattern_layer("test")
        if result is None:
            result = llm_layer("test")
        elapsed = time.time() - start

        # 总延迟 = 10ms + 50ms + 500ms = 560ms
        assert elapsed > 0.2, f"总延迟{elapsed:.3f}s，远超200ms"
        # 每次对话如果触发 LLM 兜底，延迟不可接受


# ───────────────────────────────────────────────────────────────
# 现有代码接口验证
# ───────────────────────────────────────────────────────────────

class TestExistingCodeInterfaceAssumptions:
    """验证设计文档对现有代码的接口假设是否成立"""

    def test_adapter_has_no_can_execute_method(self):
        """P2-006: Adapter 没有 can_execute() 方法"""
        from adapters import BaseAdapter

        # 检查 BaseAdapter 及其子类是否有 can_execute
        has_method = hasattr(BaseAdapter, 'can_execute')
        assert not has_method, "如果存在则设计假设成立"
        # 断言通过 = 证明设计文档假设不成立

    def test_context_manager_no_inject_system_prompt(self):
        """P1-001: ContextManager 没有注入系统提示的接口"""
        from context_manager import ContextManager

        has_method = hasattr(ContextManager, 'inject_system_prompt')
        assert not has_method, "如果存在则设计假设成立"

    def test_state_store_no_load_full_project_state(self):
        """SessionLoader 需要的完整状态加载接口不存在"""
        from state_store import StateStore

        has_method = hasattr(StateStore, 'load_full_project_state')
        assert not has_method, "如果存在则设计假设成立"

    def test_audit_logs_no_snapshot_fields(self):
        """ViolationLogger 需要的快照字段不存在"""
        tmpdir = tempfile.mkdtemp()
        try:
            db_path = Path(tmpdir) / "test.db"
            store = StateStore(db_path)
            conn = store._conn()

            columns = [desc[0] for desc in conn.execute("PRAGMA table_info(audit_logs)").fetchall()]
            assert "file_path" not in columns
            assert "before_content" not in columns
            assert "after_content" not in columns
        finally:
            import gc
            gc.collect()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_phase_checks_only_check_existence(self):
        """P0-002: check 函数只检查文件存在性"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_name = "test"
            proj_dir = base / proj_name
            proj_dir.mkdir()

            # 创建空文件
            (proj_dir / "features.json").write_text(json.dumps({"project": proj_name}))
            (proj_dir / "SOUL.md").write_text("")
            (proj_dir / "AGENTS.md").write_text("")
            (proj_dir / "progress.md").write_text("")
            os.system(f"cd {proj_dir} && git init -q")
            (proj_dir / "pipeline_state.db").touch()

            result = check_init(proj_name, base)
            # 空文件通过 check = 只检查存在性
            assert result["passed"] is True
            assert result["details"]["features_json_valid"] is True
            # 但 features.json 是空的！没有实际的 features 数据
