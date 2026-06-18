"""tests/test_context_manager.py — F011 ContextManager 上下文管理器单元测试

验收标准：
1. [command] ContextManager 模块可导入
2. [test] 安全指令在上下文压缩后仍然保留
3. [test] Agentic Search 按需加载测试通过
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from context_manager import (
    ContextManager,
    ContextLayer,
    LayerPriority,
    ReinforcementPrompt,
    SearchResult,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_SAFETY_RESERVE_TOKENS,
    DEFAULT_TASK_RESERVE_TOKENS,
    TOKEN_ESTIMATE_FACTOR,
)


# ───────────────────────────────────────────────────────────────
# 1. 模块可导入验证（验收标准 1）
# ───────────────────────────────────────────────────────────────

def test_module_importable() -> None:
    """[command] ContextManager 模块可导入"""
    from context_manager import ContextManager, LayerPriority, ReinforcementPrompt
    assert ContextManager is not None
    assert LayerPriority is not None
    assert ReinforcementPrompt is not None


def test_constants_defined() -> None:
    assert DEFAULT_MAX_CONTEXT_TOKENS > 0
    assert DEFAULT_SAFETY_RESERVE_TOKENS > 0
    assert DEFAULT_TASK_RESERVE_TOKENS > 0
    assert 0 < TOKEN_ESTIMATE_FACTOR <= 1


# ───────────────────────────────────────────────────────────────
# 2. 分层上下文注入测试
# ───────────────────────────────────────────────────────────────

class TestLayerManagement:
    def test_set_and_get_layer(self) -> None:
        cm = ContextManager()
        cm.set_layer("test_layer", "test content", priority=LayerPriority.FEATURE_SPEC)
        layer = cm.get_layer("test_layer")
        assert layer is not None
        assert layer.name == "test_layer"
        assert layer.content == "test content"
        assert layer.priority == LayerPriority.FEATURE_SPEC

    def test_remove_layer(self) -> None:
        cm = ContextManager()
        cm.set_layer("to_remove", "content")
        assert cm.remove_layer("to_remove") is True
        assert cm.get_layer("to_remove") is None
        assert cm.remove_layer("nonexistent") is False

    def test_list_layers(self) -> None:
        cm = ContextManager()
        cm.set_layer("layer_a", "a")
        cm.set_layer("layer_b", "b")
        layers = cm.list_layers()
        assert "layer_a" in layers
        assert "layer_b" in layers

    def test_layer_priority_order(self) -> None:
        cm = ContextManager()
        # 按不同优先级设置层
        cm.set_layer("history", "history content", priority=LayerPriority.HISTORY)
        cm.set_layer("safety", "safety content", priority=LayerPriority.SAFETY)
        cm.set_layer("feature", "feature content", priority=LayerPriority.FEATURE_SPEC)

        ordered = cm._priority_order()
        names = [l.name for l in ordered]
        assert names.index("safety") < names.index("feature")
        assert names.index("feature") < names.index("history")

    def test_layer_token_estimation(self) -> None:
        layer = ContextLayer(
            name="test",
            priority=LayerPriority.HISTORY,
            content="a" * 1000,
        )
        estimated = layer.estimated_tokens()
        assert estimated > 0
        assert estimated == int(1000 * TOKEN_ESTIMATE_FACTOR)


# ───────────────────────────────────────────────────────────────
# 3. 安全指令永不压缩测试（验收标准 2）
# ───────────────────────────────────────────────────────────────

class TestSafetyInstructions:
    def test_safety_instructions_set_and_get(self) -> None:
        cm = ContextManager()
        instructions = (
            "[安全指令] 禁止删除生产数据库\n"
            "[安全指令] 禁止执行 rm -rf /\n"
            "[安全指令] 所有代码修改必须通过测试"
        )
        cm.set_safety_instructions(instructions)
        retrieved = cm.get_safety_instructions()
        assert retrieved == instructions

    def test_safety_layer_is_not_compressible(self) -> None:
        cm = ContextManager()
        cm.set_safety_instructions("critical safety rules")
        layer = cm.get_layer("safety_instructions")
        assert layer is not None
        assert layer.compressible is False
        assert layer.priority == LayerPriority.SAFETY

    def test_safety_instructions_present_in_context(self) -> None:
        cm = ContextManager()
        instructions = (
            "[安全指令] 禁止删除生产数据库\n"
            "[安全指令] 禁止执行 rm -rf /\n"
            "[安全指令] 所有代码修改必须通过测试"
        )
        cm.set_safety_instructions(instructions)
        context = cm.build_context()
        assert cm.safety_instructions_present(context) is True

    def test_safety_instructions_preserved_after_compression(self) -> None:
        """[test] 安全指令在上下文压缩后仍然保留"""
        cm = ContextManager(max_context_tokens=500, safety_reserve_tokens=100)

        # 设置安全指令
        safety_text = (
            "[安全指令] 禁止删除生产数据库\n"
            "[安全指令] 禁止执行 rm -rf /\n"
            "[安全指令] 所有代码修改必须通过测试"
        )
        cm.set_safety_instructions(safety_text)

        # 填充大量可压缩层，迫使压缩
        large_content = "x" * 10000  # 远大于 max_context_tokens
        cm.set_layer(
            "progress_history",
            large_content,
            priority=LayerPriority.HISTORY,
            compressible=True,
        )
        cm.set_layer(
            "memory",
            large_content,
            priority=LayerPriority.MEMORY,
            compressible=True,
        )
        cm.set_layer(
            "feature_spec",
            "feature requirements",
            priority=LayerPriority.FEATURE_SPEC,
            compressible=True,
        )

        # 压缩前安全指令存在
        context_before = cm.build_context()
        assert cm.safety_instructions_present(context_before) is True

        # 执行压缩
        log = cm.compress(target_tokens=500)
        assert log["action"] == "compress"
        assert len(log["dropped_layers"]) > 0

        # 压缩后安全指令仍然存在
        context_after = cm.build_context()
        assert cm.safety_instructions_present(context_after) is True

        # 验证安全指令层内容未被修改
        safety_layer = cm.get_layer("safety_instructions")
        assert safety_layer is not None
        assert safety_layer.content == safety_text
        assert "[已压缩]" not in safety_layer.content

    def test_compression_drops_history_not_safety(self) -> None:
        cm = ContextManager(max_context_tokens=300)
        cm.set_safety_instructions("SAFETY RULES")
        cm.set_layer("history", "a" * 5000, priority=LayerPriority.HISTORY, compressible=True)

        log = cm.compress(target_tokens=300)
        dropped_names = [d["name"] for d in log["dropped_layers"]]
        assert "history" in dropped_names
        assert "safety_instructions" not in dropped_names

    def test_safety_layer_never_appears_in_compression_log(self) -> None:
        cm = ContextManager(max_context_tokens=100)
        cm.set_safety_instructions("SAFETY")
        # 添加其他可压缩层
        for i in range(5):
            cm.set_layer(
                f"history_{i}",
                "x" * 1000,
                priority=LayerPriority.HISTORY,
                compressible=True,
            )

        log = cm.compress(target_tokens=100)
        for dropped in log["dropped_layers"]:
            assert dropped["name"] != "safety_instructions"


# ───────────────────────────────────────────────────────────────
# 4. Reinforcement 强化机制测试
# ───────────────────────────────────────────────────────────────

class TestReinforcement:
    def test_reinforcement_prompt_build(self) -> None:
        rp = ReinforcementPrompt(
            current_task="实现 F001 用户注册接口",
            acceptance_criteria="注册成功返回 201，重复邮箱返回 409",
            completed_steps="代码编写完成，已 git commit",
            current_step="运行测试验证",
            tool_result="3 passed, 0 failed",
            reminder="请根据测试结果决定下一步操作",
        )
        prompt = rp.build()
        assert "[当前任务] 实现 F001 用户注册接口" in prompt
        assert "[验收标准] 注册成功返回 201，重复邮箱返回 409" in prompt
        assert "[已完成] 代码编写完成，已 git commit" in prompt
        assert "[当前步骤] 运行测试验证" in prompt
        assert "[工具结果] 3 passed, 0 failed" in prompt
        assert "[提醒] 请根据测试结果决定下一步操作" in prompt

    def test_set_reinforcement(self) -> None:
        cm = ContextManager()
        cm.set_reinforcement(
            current_task="实现 F011 ContextManager",
            acceptance_criteria="安全指令保留 + Agentic Search 工作",
            current_step="编写测试用例",
            reminder="确保所有验收标准通过",
        )
        reinf = cm.get_reinforcement()
        assert reinf is not None
        assert reinf.current_task == "实现 F011 ContextManager"
        assert reinf.acceptance_criteria == "安全指令保留 + Agentic Search 工作"

    def test_build_reinforcement_prompt_with_tool_result(self) -> None:
        cm = ContextManager()
        cm.set_reinforcement(
            current_task="实现 F011",
            acceptance_criteria="测试通过",
            current_step="运行测试",
            reminder="检查覆盖率",
        )
        prompt = cm.build_reinforcement_prompt(tool_result="pytest: 5 passed")
        assert "[当前任务] 实现 F011" in prompt
        assert "[工具结果] pytest: 5 passed" in prompt
        assert "[提醒] 检查覆盖率" in prompt

    def test_reinforcement_included_in_context(self) -> None:
        cm = ContextManager()
        cm.set_layer("feature_spec", "spec content", priority=LayerPriority.FEATURE_SPEC)
        cm.set_reinforcement(
            current_task="实现 F011",
            acceptance_criteria="测试通过",
        )
        context = cm.build_context(tool_result="ok")
        assert "--- reinforcement ---" in context
        assert "[当前任务] 实现 F011" in context

    def test_reinforcement_not_included_when_disabled(self) -> None:
        cm = ContextManager()
        cm.set_layer("feature_spec", "spec", priority=LayerPriority.FEATURE_SPEC)
        cm.set_reinforcement(current_task="task")
        context = cm.build_context(include_reinforcement=False)
        assert "reinforcement" not in context


# ───────────────────────────────────────────────────────────────
# 5. Agentic Search 按需加载测试（验收标准 3）
# ───────────────────────────────────────────────────────────────

class TestAgenticSearch:
    def test_index_and_search_document(self) -> None:
        cm = ContextManager()
        cm.index_document(
            "MEMORY.md",
            "已知的认证坑点：1. 密码哈希必须用 bcrypt\n"
            "2. JWT token 必须设置过期时间\n"
            "3. 禁止在日志中打印密码",
            tags=["auth", "security", "memory"],
        )
        results = cm.search("认证坑点")
        assert len(results) > 0
        assert any("bcrypt" in r.content for r in results)

    def test_search_by_tag(self) -> None:
        cm = ContextManager()
        cm.index_document("doc1", "content about authentication", tags=["auth"])
        cm.index_document("doc2", "content about database", tags=["db"])
        results = cm.search("auth")
        assert len(results) > 0
        # 标签匹配应该提高相关性
        assert any(r.source == "doc1" for r in results)

    def test_search_returns_relevance_score(self) -> None:
        cm = ContextManager()
        cm.index_document("doc1", "exact match phrase here", tags=[])
        results = cm.search("exact match")
        assert len(results) == 1
        assert results[0].relevance_score > 0

    def test_search_no_results(self) -> None:
        cm = ContextManager()
        cm.index_document("doc1", "some content")
        results = cm.search("nonexistent query xyz")
        assert len(results) == 0

    def test_search_max_results_limit(self) -> None:
        cm = ContextManager()
        for i in range(10):
            cm.index_document(f"doc{i}", f"content about search topic {i}")
        results = cm.search("search topic", max_results=3)
        assert len(results) <= 3

    def test_search_and_inject(self) -> None:
        """[test] Agentic Search 按需加载测试通过"""
        cm = ContextManager()
        cm.index_document(
            "specs/auth.md",
            "## 认证模块\n\n"
            "- 使用 bcrypt 进行密码哈希\n"
            "- JWT 过期时间 24 小时\n"
            "- 刷新令牌 7 天",
            tags=["auth", "spec"],
        )
        cm.index_document(
            "src/auth.py",
            "def login(username, password):\n"
            "    # TODO: implement\n"
            "    pass",
            tags=["auth", "code"],
        )

        # 按需搜索并注入上下文
        success = cm.search_and_inject("认证实现", layer_name="auth_search", max_results=2)
        assert success is True

        layer = cm.get_layer("auth_search")
        assert layer is not None
        assert "Agentic Search 结果" in layer.content
        assert "bcrypt" in layer.content or "JWT" in layer.content
        assert layer.priority == LayerPriority.CODE_FILES

    def test_search_and_inject_no_results(self) -> None:
        cm = ContextManager()
        success = cm.search_and_inject("nonexistent", layer_name="empty_search")
        assert success is False
        assert cm.get_layer("empty_search") is None

    def test_search_snippet_extraction(self) -> None:
        cm = ContextManager()
        long_content = "prefix " * 50 + "target phrase" + " suffix " * 50
        cm.index_document("long_doc", long_content)
        results = cm.search("target phrase")
        assert len(results) == 1
        # 应该返回包含匹配位置的片段
        assert "target phrase" in results[0].content
        # 不应该返回完整内容（太长）
        assert len(results[0].content) < len(long_content)

    def test_search_multiple_documents_ranked(self) -> None:
        cm = ContextManager()
        cm.index_document("doc1", "some content", tags=["relevant"])
        cm.index_document("doc2", "exact match exact match", tags=[])
        cm.index_document("doc3", "exact match", tags=[])

        results = cm.search("exact match")
        # doc2 应该最相关（出现两次 + 精确匹配）
        assert results[0].source == "doc2"
        assert results[0].relevance_score >= results[1].relevance_score


# ───────────────────────────────────────────────────────────────
# 6. 上下文压缩测试
# ───────────────────────────────────────────────────────────────

class TestContextCompression:
    def test_compress_when_under_limit(self) -> None:
        cm = ContextManager(max_context_tokens=100000)
        cm.set_layer("small", "tiny content", priority=LayerPriority.HISTORY)
        log = cm.compress()
        assert log["action"] == "none"
        assert len(log["dropped_layers"]) == 0

    def test_compress_drops_lowest_priority_first(self) -> None:
        cm = ContextManager(max_context_tokens=100)
        cm.set_safety_instructions("SAFETY")
        cm.set_layer("history", "x" * 5000, priority=LayerPriority.HISTORY, compressible=True)
        cm.set_layer("memory", "y" * 5000, priority=LayerPriority.MEMORY, compressible=True)
        cm.set_layer("feature", "z" * 5000, priority=LayerPriority.FEATURE_SPEC, compressible=True)

        log = cm.compress(target_tokens=100)
        dropped = [d["name"] for d in log["dropped_layers"]]
        # HISTORY 应该最先被压缩（最低优先级）
        assert "history" in dropped
        # SAFETY 不应该被压缩
        assert "safety_instructions" not in dropped

    def test_compression_log_recorded(self) -> None:
        cm = ContextManager(max_context_tokens=100)
        cm.set_layer("big", "x" * 5000, priority=LayerPriority.HISTORY, compressible=True)
        cm.compress(target_tokens=100)
        logs = cm.get_compression_log()
        assert len(logs) == 1
        assert logs[0]["action"] == "compress"
        assert logs[0]["before_tokens"] > logs[0]["after_tokens"]

    def test_compressed_layer_has_summary(self) -> None:
        cm = ContextManager(max_context_tokens=5)
        cm.set_layer("history", "original content here that is much longer than five tokens", priority=LayerPriority.HISTORY, compressible=True)
        cm.compress(target_tokens=5)
        layer = cm.get_layer("history")
        assert "[已压缩]" in layer.content

    def test_build_context_with_token_check(self) -> None:
        cm = ContextManager(max_context_tokens=200)
        cm.set_safety_instructions("SAFETY RULES")
        cm.set_layer("history", "x" * 10000, priority=LayerPriority.HISTORY, compressible=True)

        context, metadata = cm.build_context_with_token_check()
        assert metadata["compressed"] is True
        assert metadata["compression_log"] is not None
        assert metadata["total_tokens"] <= cm.max_context_tokens + 100  # 允许估算误差


# ───────────────────────────────────────────────────────────────
# 7. 上下文组装测试
# ───────────────────────────────────────────────────────────────

class TestContextBuilding:
    def test_build_context_orders_by_priority(self) -> None:
        cm = ContextManager()
        cm.set_layer("history", "history content", priority=LayerPriority.HISTORY)
        cm.set_layer("safety", "safety content", priority=LayerPriority.SAFETY)
        cm.set_layer("feature", "feature content", priority=LayerPriority.FEATURE_SPEC)

        context = cm.build_context(include_reinforcement=False)
        safety_pos = context.find("safety content")
        feature_pos = context.find("feature content")
        history_pos = context.find("history content")
        assert safety_pos < feature_pos < history_pos

    def test_build_context_with_reinforcement(self) -> None:
        cm = ContextManager()
        cm.set_layer("feature", "feature", priority=LayerPriority.FEATURE_SPEC)
        cm.set_reinforcement(current_task="task", reminder="reminder")
        context = cm.build_context(tool_result="result")
        assert "--- reinforcement ---" in context
        assert "[当前任务] task" in context
        assert "[工具结果] result" in context

    def test_build_context_empty_layers_skipped(self) -> None:
        cm = ContextManager()
        cm.set_layer("empty", "", priority=LayerPriority.HISTORY)
        cm.set_layer("nonempty", "content", priority=LayerPriority.FEATURE_SPEC)
        context = cm.build_context(include_reinforcement=False)
        # 空层不应该出现在上下文中（strip 后为空）
        assert "--- empty ---" not in context


# ───────────────────────────────────────────────────────────────
# 8. 序列化 / 反序列化测试
# ───────────────────────────────────────────────────────────────

class TestSerialization:
    def test_roundtrip(self) -> None:
        cm = ContextManager(max_context_tokens=5000, agent_name="test_agent")
        cm.set_safety_instructions("DO NOT DELETE")
        cm.set_layer("feature", "feature spec", priority=LayerPriority.FEATURE_SPEC)
        cm.set_reinforcement(
            current_task="task",
            acceptance_criteria="criteria",
            current_step="step",
        )
        cm.index_document("doc1", "content", tags=["tag"])

        data = cm.to_dict()
        cm2 = ContextManager.from_dict(data)

        assert cm2.agent_name == "test_agent"
        assert cm2.max_context_tokens == 5000
        assert cm2.get_safety_instructions() == "DO NOT DELETE"
        assert cm2.get_layer("feature") is not None
        assert cm2.get_layer("feature").priority == LayerPriority.FEATURE_SPEC
        reinf = cm2.get_reinforcement()
        assert reinf is not None
        assert reinf.current_task == "task"
        assert reinf.acceptance_criteria == "criteria"

    def test_serialization_preserves_compression_log(self) -> None:
        cm = ContextManager(max_context_tokens=100)
        cm.set_layer("big", "x" * 5000, priority=LayerPriority.HISTORY, compressible=True)
        cm.compress(target_tokens=100)

        data = cm.to_dict()
        cm2 = ContextManager.from_dict(data)
        assert len(cm2.get_compression_log()) == 1
        assert cm2.get_compression_log()[0]["action"] == "compress"

    def test_empty_reinforcement_serialization(self) -> None:
        cm = ContextManager()
        data = cm.to_dict()
        assert data["reinforcement"] is None
        cm2 = ContextManager.from_dict(data)
        assert cm2.get_reinforcement() is None


# ───────────────────────────────────────────────────────────────
# 9. 端到端：模拟长对话上下文压缩后安全指令保留
# ───────────────────────────────────────────────────────────────

def test_end_to_end_safety_preserved_after_many_turns() -> None:
    """端到端：模拟多轮对话后压缩，安全指令仍然保留"""
    cm = ContextManager(max_context_tokens=1000, safety_reserve_tokens=200)

    # 设置安全指令
    cm.set_safety_instructions(
        "[安全指令] 禁止删除生产数据库\n"
        "[安全指令] 禁止执行 rm -rf /\n"
        "[安全指令] 所有代码修改必须通过测试"
    )

    # 模拟 100+ 轮对话历史
    for i in range(100):
        cm.set_layer(
            f"turn_{i}",
            f"User: 请帮我实现功能 {i}\nAgent: 好的，这是实现代码...\n" + "x" * 50,
            priority=LayerPriority.HISTORY,
            compressible=True,
        )

    # 压缩前验证
    context_before = cm.build_context()
    assert cm.safety_instructions_present(context_before) is True

    # 执行压缩（模拟上下文溢出）
    log = cm.compress()
    assert log["action"] == "compress"
    assert len(log["dropped_layers"]) > 0

    # 压缩后验证安全指令仍然存在
    context_after = cm.build_context()
    assert cm.safety_instructions_present(context_after) is True

    # 验证安全指令内容完整
    safety = cm.get_safety_instructions()
    assert "禁止删除生产数据库" in safety
    assert "禁止执行 rm -rf /" in safety
    assert "所有代码修改必须通过测试" in safety


# ───────────────────────────────────────────────────────────────
# 10. 端到端：Agentic Search 按需加载 + Reinforcement 协同
# ───────────────────────────────────────────────────────────────

def test_end_to_end_agentic_search_with_reinforcement() -> None:
    """端到端：Agentic Search 按需加载 + Reinforcement 强化"""
    cm = ContextManager()

    # 索引项目知识库
    cm.index_document(
        "specs/F011.md",
        "## F011 ContextManager\n\n"
        "- 分层注入策略\n"
        "- Reinforcement 强化机制\n"
        "- Agentic Search 按需加载",
        tags=["F011", "spec"],
    )
    cm.index_document(
        "MEMORY.md",
        "已知坑点：上下文压缩会丢失安全指令，必须分层管理",
        tags=["memory", "pitfall"],
    )

    # 设置 Reinforcement
    cm.set_reinforcement(
        current_task="实现 F011 ContextManager",
        acceptance_criteria="安全指令保留 + Agentic Search 工作 + Reinforcement 注入",
        current_step="验证 Agentic Search 功能",
        reminder="按需加载，不要注入完整文件",
    )

    # Agentic Search 按需加载
    success = cm.search_and_inject("ContextManager 需求", layer_name="cm_spec", max_results=2)
    assert success is True

    # 验证搜索结果已注入
    layer = cm.get_layer("cm_spec")
    assert layer is not None
    assert "分层注入策略" in layer.content or "Reinforcement" in layer.content

    # 构建完整上下文（含 Reinforcement）
    context, metadata = cm.build_context_with_token_check(tool_result="搜索完成")

    # 验证 Reinforcement 在上下文中
    assert "[当前任务] 实现 F011 ContextManager" in context
    assert "[工具结果] 搜索完成" in context
    assert "[提醒] 按需加载，不要注入完整文件" in context

    # 验证 Agentic Search 结果在上下文中
    assert "Agentic Search 结果" in context

    # 验证 token 在限制内
    assert metadata["total_tokens"] <= cm.max_context_tokens + 100
