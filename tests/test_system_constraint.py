"""tests/test_system_constraint.py — 系统级约束层单元测试 (F024)

验收标准：
1. 约束层自动拦截违规操作
2. 约束层单元测试通过（20+ 测试）
3. Hermes 尝试编码被自动拦截
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.system_constraint import (
    SystemConstraint,
    ConstraintConfig,
    ConstraintViolation,
    HermesPermissionDenied,
    TaskType,
    Agent,
    TASK_AGENT_MAP,
    AGENT_CAPABILITIES,
    get_task_agent,
    assert_hermes_orchestration,
    route_task,
)


# ───────────────────────────────────────────────────────────────
# 基础路由测试
# ───────────────────────────────────────────────────────────────

class TestTaskRouting:
    """任务路由测试"""

    def test_route_code_to_claude(self):
        sc = SystemConstraint()
        result = sc.route_task("code", {"feature_id": "F001"})
        assert result["target_agent"] == "claude"
        assert result["task_type"] == "code"
        assert result["constraint_enforced"] is True

    def test_route_review_to_codewhale(self):
        sc = SystemConstraint()
        result = sc.route_task("review", {"pr_id": "PR-42"})
        assert result["target_agent"] == "codewhale"
        assert result["task_type"] == "review"

    def test_route_test_to_qwen(self):
        sc = SystemConstraint()
        result = sc.route_task("test", {"test_suite": "unit"})
        assert result["target_agent"] == "qwen"
        assert result["task_type"] == "test"

    def test_route_e2e_to_qwen(self):
        sc = SystemConstraint()
        result = sc.route_task("e2e", {"browser": "playwright"})
        assert result["target_agent"] == "qwen"
        assert result["task_type"] == "e2e"

    def test_route_doc_to_qwen(self):
        sc = SystemConstraint()
        result = sc.route_task("doc", {"lang": "zh"})
        assert result["target_agent"] == "qwen"
        assert result["task_type"] == "doc"

    def test_route_orchestrate_to_hermes(self):
        sc = SystemConstraint()
        result = sc.route_task("orchestrate", {"pipeline": "wave1"})
        assert result["target_agent"] == "hermes"
        assert result["task_type"] == "orchestrate"

    def test_route_deploy_to_hermes(self):
        sc = SystemConstraint()
        result = sc.route_task("deploy", {"env": "prod"})
        assert result["target_agent"] == "hermes"
        assert result["task_type"] == "deploy"

    def test_route_analyze_to_hermes(self):
        sc = SystemConstraint()
        result = sc.route_task("analyze", {"target": "metrics"})
        assert result["target_agent"] == "hermes"
        assert result["task_type"] == "analyze"

    def test_route_with_spec_passthrough(self):
        sc = SystemConstraint()
        spec = {"feature_id": "F024", "files": ["a.py", "b.py"], "priority": "high"}
        result = sc.route_task("code", spec)
        assert result["spec"] == spec

    def test_route_increments_route_count(self):
        sc = SystemConstraint()
        assert sc.route_count == 0
        sc.route_task("code", {})
        assert sc.route_count == 1
        sc.route_task("test", {})
        assert sc.route_count == 2

    def test_route_with_requested_agent_match(self):
        sc = SystemConstraint()
        result = sc.route_task("code", {}, requested_agent="claude")
        assert result["target_agent"] == "claude"

    def test_route_with_requested_agent_mismatch(self):
        sc = SystemConstraint()
        with pytest.raises(ConstraintViolation) as exc_info:
            sc.route_task("code", {}, requested_agent="qwen")
        assert "claude" in str(exc_info.value)
        assert "qwen" in str(exc_info.value)

    def test_route_unknown_task_type(self):
        sc = SystemConstraint()
        with pytest.raises(ConstraintViolation) as exc_info:
            sc.route_task("unknown_task", {})
        assert "Unknown task type" in str(exc_info.value)

    def test_route_unknown_requested_agent(self):
        sc = SystemConstraint()
        with pytest.raises(ConstraintViolation) as exc_info:
            sc.route_task("code", {}, requested_agent="unknown_agent")
        assert "Unknown requested agent" in str(exc_info.value)


# ───────────────────────────────────────────────────────────────
# Hermes 权限约束测试
# ───────────────────────────────────────────────────────────────

class TestHermesPermission:
    """Hermes 权限约束测试"""

    def test_hermes_allowed_orchestrate(self):
        sc = SystemConstraint()
        # 不应抛出异常
        sc.hermes_only_orchestration("orchestrate")
        sc.hermes_only_orchestration("route")
        sc.hermes_only_orchestration("delegate")
        sc.hermes_only_orchestration("monitor")
        sc.hermes_only_orchestration("init")
        sc.hermes_only_orchestration("advance")
        sc.hermes_only_orchestration("check")
        sc.hermes_only_orchestration("resume")
        sc.hermes_only_orchestration("status")
        sc.hermes_only_orchestration("coordinate")
        sc.hermes_only_orchestration("schedule")

    def test_hermes_forbidden_code(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("code")
        assert "Hermes is not allowed" in str(exc_info.value)
        assert "code" in str(exc_info.value)

    def test_hermes_forbidden_write(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("write")
        assert "Hermes is not allowed" in str(exc_info.value)

    def test_hermes_forbidden_implement(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("implement")
        assert "implement" in str(exc_info.value)

    def test_hermes_forbidden_review(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("review")
        assert "review" in str(exc_info.value)

    def test_hermes_forbidden_test(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("test")
        assert "test" in str(exc_info.value)

    def test_hermes_forbidden_e2e_test(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("e2e_test")
        assert "e2e_test" in str(exc_info.value)

    def test_hermes_forbidden_doc(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("doc")
        assert "doc" in str(exc_info.value)

    def test_hermes_forbidden_playwright(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("playwright")
        assert "playwright" in str(exc_info.value)

    def test_hermes_forbidden_develop(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("develop")
        assert "develop" in str(exc_info.value)

    def test_hermes_forbidden_program(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("program")
        assert "program" in str(exc_info.value)

    def test_hermes_forbidden_audit(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("audit")
        assert "audit" in str(exc_info.value)

    def test_hermes_forbidden_run_test(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("run_test")
        assert "run_test" in str(exc_info.value)

    def test_hermes_forbidden_check_code(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("check_code")
        assert "check_code" in str(exc_info.value)

    def test_hermes_forbidden_document(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("document")
        assert "document" in str(exc_info.value)

    def test_hermes_forbidden_generate_doc(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("generate_doc")
        assert "generate_doc" in str(exc_info.value)

    def test_hermes_forbidden_inspect(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("inspect")
        assert "inspect" in str(exc_info.value)

    def test_hermes_violation_increments_count(self):
        sc = SystemConstraint()
        assert sc.violation_count == 0
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("code")
        assert sc.violation_count == 1
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("test")
        assert sc.violation_count == 2

    def test_hermes_forbidden_case_insensitive(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("CODE")
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("Code")
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("Review")

    def test_hermes_forbidden_with_whitespace(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("  code  ")
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration(" test ")

    def test_hermes_strict_mode_unknown_action(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=True))
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("some_unknown_action")
        assert "not in the allowed" in str(exc_info.value)

    def test_hermes_non_strict_mode_unknown_action(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=False))
        # 不应抛出异常
        sc.hermes_only_orchestration("some_unknown_action")

    def test_check_hermes_task_code(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.check_hermes_task("code")
        assert "claude" in str(exc_info.value)

    def test_check_hermes_task_review(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.check_hermes_task("review")
        assert "codewhale" in str(exc_info.value)

    def test_check_hermes_task_test(self):
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.check_hermes_task("test")
        assert "qwen" in str(exc_info.value)

    def test_check_hermes_task_orchestrate_allowed(self):
        sc = SystemConstraint()
        # 不应抛出异常
        sc.check_hermes_task("orchestrate")

    def test_check_hermes_task_deploy_allowed(self):
        sc = SystemConstraint()
        # 不应抛出异常
        sc.check_hermes_task("deploy")

    def test_check_hermes_task_unknown_strict(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=True))
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.check_hermes_task("unknown_task")
        assert "Unknown task type" in str(exc_info.value)

    def test_check_hermes_task_unknown_non_strict(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=False))
        # 不应抛出异常
        sc.check_hermes_task("unknown_task")


# ───────────────────────────────────────────────────────────────
# 约束映射常量测试
# ───────────────────────────────────────────────────────────────

class TaskAgentMap:
    """任务到Agent映射测试"""

    def test_code_maps_to_claude(self):
        assert TASK_AGENT_MAP[TaskType.CODE] == Agent.CLAUDE

    def test_review_maps_to_codewhale(self):
        assert TASK_AGENT_MAP[TaskType.REVIEW] == Agent.CODEWHALE

    def test_test_maps_to_qwen(self):
        assert TASK_AGENT_MAP[TaskType.TEST] == Agent.QWEN

    def test_doc_maps_to_qwen(self):
        assert TASK_AGENT_MAP[TaskType.DOC] == Agent.QWEN

    def test_e2e_maps_to_qwen(self):
        assert TASK_AGENT_MAP[TaskType.E2E] == Agent.QWEN

    def test_orchestrate_maps_to_hermes(self):
        assert TASK_AGENT_MAP[TaskType.ORCHESTRATE] == Agent.HERMES

    def test_deploy_maps_to_hermes(self):
        assert TASK_AGENT_MAP[TaskType.DEPLOY] == Agent.HERMES

    def test_analyze_maps_to_hermes(self):
        assert TASK_AGENT_MAP[TaskType.ANALYZE] == Agent.HERMES

    def test_claude_capabilities(self):
        assert TaskType.CODE in AGENT_CAPABILITIES[Agent.CLAUDE]

    def test_codewhale_capabilities(self):
        assert TaskType.REVIEW in AGENT_CAPABILITIES[Agent.CODEWHALE]

    def test_qwen_capabilities(self):
        assert TaskType.TEST in AGENT_CAPABILITIES[Agent.QWEN]
        assert TaskType.DOC in AGENT_CAPABILITIES[Agent.QWEN]
        assert TaskType.E2E in AGENT_CAPABILITIES[Agent.QWEN]

    def test_hermes_capabilities(self):
        assert TaskType.ORCHESTRATE in AGENT_CAPABILITIES[Agent.HERMES]
        assert TaskType.DEPLOY in AGENT_CAPABILITIES[Agent.HERMES]
        assert TaskType.ANALYZE in AGENT_CAPABILITIES[Agent.HERMES]


# ───────────────────────────────────────────────────────────────
# 查询方法测试
# ───────────────────────────────────────────────────────────────

class TestQueryMethods:
    """查询方法测试"""

    def test_get_agent_for_task_code(self):
        assert get_task_agent("code") == "claude"

    def test_get_agent_for_task_review(self):
        assert get_task_agent("review") == "codewhale"

    def test_get_agent_for_task_test(self):
        assert get_task_agent("test") == "qwen"

    def test_get_agent_for_task_unknown(self):
        assert get_task_agent("unknown") is None

    def test_get_allowed_tasks_for_claude(self):
        sc = SystemConstraint()
        tasks = sc.get_allowed_tasks_for_agent("claude")
        assert "code" in tasks
        assert "review" not in tasks

    def test_get_allowed_tasks_for_codewhale(self):
        sc = SystemConstraint()
        tasks = sc.get_allowed_tasks_for_agent("codewhale")
        assert "review" in tasks
        assert "code" not in tasks

    def test_get_allowed_tasks_for_qwen(self):
        sc = SystemConstraint()
        tasks = sc.get_allowed_tasks_for_agent("qwen")
        assert "test" in tasks
        assert "doc" in tasks
        assert "e2e" in tasks

    def test_get_allowed_tasks_for_hermes(self):
        sc = SystemConstraint()
        tasks = sc.get_allowed_tasks_for_agent("hermes")
        assert "orchestrate" in tasks
        assert "deploy" in tasks
        assert "analyze" in tasks

    def test_get_allowed_tasks_for_unknown(self):
        sc = SystemConstraint()
        assert sc.get_allowed_tasks_for_agent("unknown") == []

    def test_can_agent_execute_true(self):
        sc = SystemConstraint()
        assert sc.can_agent_execute("claude", "code") is True
        assert sc.can_agent_execute("codewhale", "review") is True
        assert sc.can_agent_execute("qwen", "test") is True

    def test_can_agent_execute_false(self):
        sc = SystemConstraint()
        assert sc.can_agent_execute("claude", "review") is False
        assert sc.can_agent_execute("codewhale", "code") is False
        assert sc.can_agent_execute("qwen", "code") is False

    def test_can_agent_execute_unknown(self):
        sc = SystemConstraint()
        assert sc.can_agent_execute("unknown", "code") is False
        assert sc.can_agent_execute("claude", "unknown") is False

    def test_is_hermes_action_allowed_true(self):
        sc = SystemConstraint()
        assert sc.is_hermes_action_allowed("orchestrate") is True
        assert sc.is_hermes_action_allowed("route") is True

    def test_is_hermes_action_allowed_false(self):
        sc = SystemConstraint()
        assert sc.is_hermes_action_allowed("code") is False
        assert sc.is_hermes_action_allowed("test") is False
        assert sc.is_hermes_action_allowed("review") is False


# ───────────────────────────────────────────────────────────────
# 批量路由测试
# ───────────────────────────────────────────────────────────────

class TestBatchRouting:
    """批量路由测试"""

    def test_route_batch_all_valid(self):
        sc = SystemConstraint()
        tasks = [
            {"task_type": "code", "spec": {"f": "F001"}},
            {"task_type": "review", "spec": {"pr": "PR-1"}},
            {"task_type": "test", "spec": {"suite": "unit"}},
        ]
        results = sc.route_batch(tasks)
        assert len(results) == 3
        assert results[0]["target_agent"] == "claude"
        assert results[1]["target_agent"] == "codewhale"
        assert results[2]["target_agent"] == "qwen"

    def test_route_batch_with_invalid_strict(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=True))
        tasks = [
            {"task_type": "code", "spec": {}},
            {"task_type": "unknown", "spec": {}},
        ]
        with pytest.raises(ConstraintViolation):
            sc.route_batch(tasks)

    def test_route_batch_with_invalid_non_strict(self):
        sc = SystemConstraint(ConstraintConfig(strict_mode=False))
        tasks = [
            {"task_type": "code", "spec": {}},
            {"task_type": "unknown", "spec": {}},
        ]
        results = sc.route_batch(tasks)
        assert len(results) == 2
        assert results[0]["constraint_enforced"] is True
        assert results[1]["routed"] is False
        assert "error" in results[1]


# ───────────────────────────────────────────────────────────────
# 紧急模式测试
# ───────────────────────────────────────────────────────────────

class TestEmergencyMode:
    """紧急模式测试"""

    def test_emergency_not_active_by_default(self):
        sc = SystemConstraint()
        assert sc.is_emergency_active is False

    def test_activate_emergency_with_correct_password(self):
        sc = SystemConstraint()
        sc.config.set_emergency_password("secret123")
        assert sc.activate_emergency("secret123", duration_seconds=60) is True
        assert sc.is_emergency_active is True

    def test_activate_emergency_with_wrong_password(self):
        sc = SystemConstraint()
        sc.config.set_emergency_password("secret123")
        assert sc.activate_emergency("wrong") is False
        assert sc.is_emergency_active is False

    def test_emergency_expires(self):
        sc = SystemConstraint()
        sc.config.set_emergency_password("secret123")
        sc.activate_emergency("secret123", duration_seconds=0.1)
        assert sc.is_emergency_active is True
        time.sleep(0.2)
        assert sc.is_emergency_active is False

    def test_deactivate_emergency(self):
        sc = SystemConstraint()
        sc.config.set_emergency_password("secret123")
        sc.activate_emergency("secret123", duration_seconds=300)
        assert sc.is_emergency_active is True
        sc.deactivate_emergency()
        assert sc.is_emergency_active is False

    def test_bypass_check_requires_emergency(self):
        sc = SystemConstraint()
        with pytest.raises(ConstraintViolation) as exc_info:
            sc.route_task("code", {}, bypass_check=True)
        assert "Bypass check requires active emergency mode" in str(exc_info.value)

    def test_bypass_check_with_emergency(self):
        sc = SystemConstraint()
        sc.config.set_emergency_password("secret123")
        sc.activate_emergency("secret123", duration_seconds=60)
        result = sc.route_task("code", {}, bypass_check=True)
        assert result["target_agent"] == "claude"


# ───────────────────────────────────────────────────────────────
# 回调测试
# ───────────────────────────────────────────────────────────────

class TestCallbacks:
    """回调测试"""

    def test_violation_callback(self):
        violations: List[ConstraintViolation] = []

        def on_violation(v: ConstraintViolation) -> None:
            violations.append(v)

        config = ConstraintConfig(violation_callbacks=[on_violation])
        sc = SystemConstraint(config)

        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("code")

        assert len(violations) == 1
        assert violations[0].task_type == "code"
        assert violations[0].attempted_agent == "hermes"

    def test_route_callback(self):
        routes: List[tuple] = []

        def on_route(tt: TaskType, agent: Agent, spec: Any) -> None:
            routes.append((tt, agent, spec))

        config = ConstraintConfig(route_callbacks=[on_route])
        sc = SystemConstraint(config)
        sc.route_task("code", {"feature_id": "F001"})

        assert len(routes) == 1
        assert routes[0][0] == TaskType.CODE
        assert routes[0][1] == Agent.CLAUDE
        assert routes[0][2] == {"feature_id": "F001"}

    def test_multiple_violation_callbacks(self):
        count1 = [0]
        count2 = [0]

        def cb1(v: ConstraintViolation) -> None:
            count1[0] += 1

        def cb2(v: ConstraintViolation) -> None:
            count2[0] += 1

        config = ConstraintConfig(violation_callbacks=[cb1, cb2])
        sc = SystemConstraint(config)

        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("code")

        assert count1[0] == 1
        assert count2[0] == 1

    def test_callback_exception_ignored(self):
        def bad_cb(v: ConstraintViolation) -> None:
            raise RuntimeError("callback error")

        config = ConstraintConfig(violation_callbacks=[bad_cb])
        sc = SystemConstraint(config)
        # 不应因回调异常而失败
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("code")


# ───────────────────────────────────────────────────────────────
# 约束异常测试
# ───────────────────────────────────────────────────────────────

class TestConstraintViolation:
    """约束异常测试"""

    def test_exception_message(self):
        exc = ConstraintViolation("test message", task_type="code", attempted_agent="hermes")
        assert str(exc) == "test message"
        assert exc.task_type == "code"
        assert exc.attempted_agent == "hermes"

    def test_exception_to_dict(self):
        exc = ConstraintViolation(
            "test message",
            task_type="code",
            attempted_agent="hermes",
            required_agent="claude",
            action="route_task",
        )
        d = exc.to_dict()
        assert d["message"] == "test message"
        assert d["task_type"] == "code"
        assert d["attempted_agent"] == "hermes"
        assert d["required_agent"] == "claude"
        assert d["action"] == "route_task"

    def test_hermes_permission_denied_is_constraint_violation(self):
        exc = HermesPermissionDenied("denied")
        assert isinstance(exc, ConstraintViolation)

    def test_hermes_permission_denied_attributes(self):
        exc = HermesPermissionDenied(
            "denied",
            task_type="code",
            attempted_agent="hermes",
            required_agent="claude",
        )
        assert exc.task_type == "code"
        assert exc.required_agent == "claude"


# ───────────────────────────────────────────────────────────────
# 全局便捷函数测试
# ───────────────────────────────────────────────────────────────

class TestGlobalFunctions:
    """全局便捷函数测试"""

    def test_get_task_agent_code(self):
        assert get_task_agent("code") == "claude"

    def test_get_task_agent_review(self):
        assert get_task_agent("review") == "codewhale"

    def test_get_task_agent_test(self):
        assert get_task_agent("test") == "qwen"

    def test_get_task_agent_unknown(self):
        assert get_task_agent("unknown") is None

    def test_assert_hermes_orchestration_allowed(self):
        # 不应抛出异常
        assert_hermes_orchestration("orchestrate")
        assert_hermes_orchestration("route")

    def test_assert_hermes_orchestration_denied(self):
        with pytest.raises(HermesPermissionDenied):
            assert_hermes_orchestration("code")
        with pytest.raises(HermesPermissionDenied):
            assert_hermes_orchestration("test")
        with pytest.raises(HermesPermissionDenied):
            assert_hermes_orchestration("review")

    def test_route_task_global(self):
        result = route_task("code", {"feature_id": "F001"})
        assert result["target_agent"] == "claude"


# ───────────────────────────────────────────────────────────────
# 统计和重置测试
# ───────────────────────────────────────────────────────────────

class TestStats:
    """统计测试"""

    def test_reset_stats(self):
        sc = SystemConstraint()
        sc.route_task("code", {})
        with pytest.raises(HermesPermissionDenied):
            sc.hermes_only_orchestration("code")
        assert sc.route_count == 1
        assert sc.violation_count == 1
        sc.reset_stats()
        assert sc.route_count == 0
        assert sc.violation_count == 0

    def test_stats_independence(self):
        sc1 = SystemConstraint()
        sc2 = SystemConstraint()
        sc1.route_task("code", {})
        assert sc1.route_count == 1
        assert sc2.route_count == 0


# ───────────────────────────────────────────────────────────────
# 配置测试
# ───────────────────────────────────────────────────────────────

class TestConfig:
    """配置测试"""

    def test_default_config(self):
        config = ConstraintConfig()
        assert config.strict_mode is True
        assert config.emergency_override is False
        assert config._emergency_password_hash is None

    def test_set_emergency_password(self):
        config = ConstraintConfig()
        config.set_emergency_password("my_password")
        assert config._emergency_password_hash is not None
        assert config.verify_emergency_password("my_password") is True
        assert config.verify_emergency_password("wrong") is False

    def test_custom_config(self):
        config = ConstraintConfig(strict_mode=False, emergency_override=True)
        assert config.strict_mode is False
        assert config.emergency_override is True

    def test_config_with_callbacks(self):
        def cb(v: ConstraintViolation) -> None:
            pass

        config = ConstraintConfig(violation_callbacks=[cb])
        assert len(config.violation_callbacks) == 1


# ───────────────────────────────────────────────────────────────
# 集成场景测试
# ───────────────────────────────────────────────────────────────

class TestIntegrationScenarios:
    """集成场景测试"""

    def test_full_pipeline_routing(self):
        """模拟完整流水线任务路由"""
        sc = SystemConstraint()
        pipeline_tasks = [
            ("code", {"feature_id": "F024", "files": ["system_constraint.py"]}),
            ("review", {"pr_id": "PR-24", "diff": "..."}),
            ("test", {"suite": "unit", "files": ["test_system_constraint.py"]}),
            ("orchestrate", {"pipeline": "wave5", "phase": "develop"}),
        ]
        expected_agents = ["claude", "codewhale", "qwen", "hermes"]
        for (task_type, spec), expected in zip(pipeline_tasks, expected_agents):
            result = sc.route_task(task_type, spec)
            assert result["target_agent"] == expected

    def test_hermes_attempts_code_intercepted(self):
        """Hermes 尝试编码被自动拦截 — 验收标准 3"""
        sc = SystemConstraint()
        # 路由层面：Hermes 不能路由 code 任务给自己
        result = sc.route_task("code", {"feature_id": "F024"})
        assert result["target_agent"] == "claude"
        assert result["target_agent"] != "hermes"
        # 权限层面：Hermes 尝试执行 code 操作被拦截
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("code")
        assert "Hermes is not allowed" in str(exc_info.value)
        assert sc.violation_count == 1

    def test_hermes_attempts_review_intercepted(self):
        """Hermes 尝试审核被自动拦截"""
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("review")
        assert "codewhale" in str(exc_info.value) or "not allowed" in str(exc_info.value)

    def test_hermes_attempts_test_intercepted(self):
        """Hermes 尝试测试被自动拦截"""
        sc = SystemConstraint()
        with pytest.raises(HermesPermissionDenied) as exc_info:
            sc.hermes_only_orchestration("test")
        assert "qwen" in str(exc_info.value) or "not allowed" in str(exc_info.value)

    def test_constraint_enforced_flag(self):
        """所有路由结果都标记约束已执行"""
        sc = SystemConstraint()
        for task_type in ["code", "review", "test", "doc", "e2e", "orchestrate", "deploy", "analyze"]:
            result = sc.route_task(task_type, {})
            assert result["constraint_enforced"] is True

    def test_routed_at_timestamp(self):
        """路由结果包含时间戳"""
        sc = SystemConstraint()
        before = time.time()
        result = sc.route_task("code", {})
        after = time.time()
        assert before <= result["routed_at"] <= after

    def test_multiple_violations_tracked(self):
        """多次违规被正确追踪"""
        sc = SystemConstraint()
        forbidden_actions = ["code", "review", "test", "doc", "e2e"]
        for action in forbidden_actions:
            with pytest.raises(HermesPermissionDenied):
                sc.hermes_only_orchestration(action)
        assert sc.violation_count == len(forbidden_actions)

    def test_task_type_enum_coverage(self):
        """所有任务类型都有映射"""
        for task_type in TaskType:
            agent = TASK_AGENT_MAP.get(task_type)
            assert agent is not None, f"TaskType {task_type} has no agent mapping"

    def test_agent_capabilities_coverage(self):
        """所有 Agent 都有能力定义"""
        for agent in Agent:
            caps = AGENT_CAPABILITIES.get(agent)
            assert caps is not None, f"Agent {agent} has no capabilities"
            assert len(caps) > 0, f"Agent {agent} has empty capabilities"

    def test_no_overlapping_exclusive_tasks(self):
        """专属任务不重叠：code 只给 claude，review 只给 codewhale"""
        assert TASK_AGENT_MAP[TaskType.CODE] == Agent.CLAUDE
        assert TASK_AGENT_MAP[TaskType.REVIEW] == Agent.CODEWHALE
        assert Agent.CLAUDE not in [TASK_AGENT_MAP[TaskType.REVIEW], TASK_AGENT_MAP[TaskType.TEST]]
        assert Agent.CODEWHALE not in [TASK_AGENT_MAP[TaskType.CODE], TASK_AGENT_MAP[TaskType.TEST]]

    def test_hermes_can_only_orchestrate(self):
        """Hermes 只能执行编排类任务"""
        sc = SystemConstraint()
        for action in ["orchestrate", "route", "delegate", "monitor", "init", "advance", "check"]:
            sc.hermes_only_orchestration(action)  # 不应抛出异常
        for action in ["code", "review", "test", "write", "implement", "develop"]:
            with pytest.raises(HermesPermissionDenied):
                sc.hermes_only_orchestration(action)
