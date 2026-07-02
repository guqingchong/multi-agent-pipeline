"""Smoke tests for W3-E02: condition_engine.py

Covers:
  - BUILTIN_PREDICATES: all 7 exist and fire correctly at thresholds
  - evaluate() returns triggered results only
  - evaluate_all() returns all results
  - determine_phase_insertions() maps actions → phase insertions
  - Custom rule loading (JSON/YAML/dict)
  - Custom predicate registration
  - Rule management (register/remove/get/list/clear/count)
  - _pred_custom_expr expression evaluator
  - enrich_context_from_layers()
  - evaluate_context() convenience
  - ConditionResult.__bool__
  - Edge cases: empty context, zero thresholds, missing keys
"""
import json
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from condition_engine import (
    ConditionResult,
    ConditionEngine,
    BUILTIN_PREDICATES,
    DEFAULT_RULES,
    load_rules_from_json,
    load_rules_from_yaml,
    evaluate_context,
    _pred_custom_expr,
    _eval_simple_cmp,
    _split_expr,
)


# ── ConditionResult ──────────────────────────────────────────────

class TestConditionResult:
    def test_dataclass_fields(self):
        cr = ConditionResult(triggered=True, action="pause", reason="test")
        assert cr.triggered is True
        assert cr.action == "pause"
        assert cr.reason == "test"
        assert cr.rule_name == ""
        assert cr.context_snapshot == {}

    def test_bool_conversion(self):
        assert bool(ConditionResult(triggered=True, action="x", reason="y")) is True
        assert bool(ConditionResult(triggered=False, action="x", reason="y")) is False

    def test_defaults(self):
        cr = ConditionResult(triggered=False, action="a", reason="r")
        assert cr.rule_name == ""
        assert cr.context_snapshot == {}


# ── BUILTIN_PREDICATES ───────────────────────────────────────────

class TestBuiltinPredicates:
    def test_all_seven_exist(self):
        expected = {
            "code_lines_gt_500",
            "test_failures_gt_3",
            "budget_consumed_80pct",
            "test_coverage_lt_80",
            "review_not_passed",
            "file_count_gt_50",
            "has_errors",
        }
        assert set(BUILTIN_PREDICATES.keys()) == expected

    def test_code_lines_gt_500(self):
        pred = BUILTIN_PREDICATES["code_lines_gt_500"]
        assert pred({"code_lines": 600}) is True
        assert pred({"code_lines": 500}) is False
        assert pred({"code_lines": 100}) is False
        assert pred({}) is False  # missing key

    def test_test_failures_gt_3(self):
        pred = BUILTIN_PREDICATES["test_failures_gt_3"]
        assert pred({"test_failures": 4}) is True
        assert pred({"test_failures": 3}) is False
        assert pred({"test_failures": 0}) is False
        assert pred({}) is False

    def test_budget_consumed_80pct(self):
        pred = BUILTIN_PREDICATES["budget_consumed_80pct"]
        assert pred({"budget_total": 100, "budget_spent": 80}) is True
        assert pred({"budget_total": 100, "budget_spent": 90}) is True
        assert pred({"budget_total": 100, "budget_spent": 79}) is False
        assert pred({"budget_total": 0, "budget_spent": 0}) is False
        assert pred({}) is False

    def test_test_coverage_lt_80(self):
        pred = BUILTIN_PREDICATES["test_coverage_lt_80"]
        assert pred({"test_coverage": 79.9}) is True
        assert pred({"test_coverage": 80.0}) is False
        assert pred({"test_coverage": 95.0}) is False
        assert pred({}) is False  # default 100.0

    def test_review_not_passed(self):
        pred = BUILTIN_PREDICATES["review_not_passed"]
        assert pred({"review_passed": False}) is True
        assert pred({"review_passed": True}) is False
        assert pred({}) is True  # default False != True → True

    def test_file_count_gt_50(self):
        pred = BUILTIN_PREDICATES["file_count_gt_50"]
        assert pred({"file_count": 51}) is True
        assert pred({"file_count": 50}) is False
        assert pred({}) is False

    def test_has_errors(self):
        pred = BUILTIN_PREDICATES["has_errors"]
        assert pred({"errors": ["err1"]}) is True
        assert pred({"errors": []}) is False
        assert pred({}) is False


# ── _pred_custom_expr ────────────────────────────────────────────

class TestCustomExpr:
    def test_simple_comparison(self):
        assert _pred_custom_expr({"code_lines": 600}, "code_lines > 500") is True
        assert _pred_custom_expr({"code_lines": 400}, "code_lines > 500") is False

    def test_and_expression(self):
        state = {"code_lines": 600, "test_failures": 5}
        assert _pred_custom_expr(state, "code_lines > 500 and test_failures > 3") is True
        assert _pred_custom_expr(state, "code_lines > 500 and test_failures > 10") is False

    def test_or_expression(self):
        state = {"code_lines": 100, "test_failures": 10}
        assert _pred_custom_expr(state, "code_lines > 500 or test_failures > 3") is True
        assert _pred_custom_expr(state, "code_lines > 500 or test_failures > 100") is False

    def test_empty_expr(self):
        assert _pred_custom_expr({"x": 1}, "") is False

    def test_not_expression(self):
        assert _pred_custom_expr({"flag": False}, "not flag") is True
        assert _pred_custom_expr({"flag": True}, "not flag") is False

    def test_operators(self):
        s = {"v": 10}
        assert _pred_custom_expr(s, "v >= 10") is True
        assert _pred_custom_expr(s, "v <= 5") is False
        assert _pred_custom_expr(s, "v == 10") is True
        assert _pred_custom_expr(s, "v != 10") is False


class TestSplitExpr:
    def test_simple_split(self):
        assert _split_expr("a and b", " and ") == ["a", "b"]

    def test_or_split(self):
        assert _split_expr("a or b", " or ") == ["a", "b"]

    def test_parentheses_respected(self):
        parts = _split_expr("(a or b) and c", " and ")
        assert parts == ["(a or b)", "c"]


class TestEvalSimpleCmp:
    def test_various_ops(self):
        s = {"x": 10}
        assert _eval_simple_cmp(s, "x > 5") is True
        assert _eval_simple_cmp(s, "x < 5") is False
        assert _eval_simple_cmp(s, "x >= 10") is True
        assert _eval_simple_cmp(s, "x <= 10") is True
        assert _eval_simple_cmp(s, "x == 10") is True
        assert _eval_simple_cmp(s, "x != 10") is False

    def test_boolean_check(self):
        assert _eval_simple_cmp({"a": True}, "a") is True
        assert _eval_simple_cmp({"a": False}, "a") is False

    def test_not_with_parens(self):
        assert _eval_simple_cmp({"a": True}, "not(a)") is False
        assert _eval_simple_cmp({"a": False}, "not(a)") is True


# ── DEFAULT_RULES ────────────────────────────────────────────────

class TestDefaultRules:
    def test_count(self):
        assert len(DEFAULT_RULES) == 3

    def test_names(self):
        names = {r.name for r in DEFAULT_RULES}
        assert names == {"code_lines>500", "test_failures>3", "budget_80pct"}

    def test_actions(self):
        actions = {r.action for r in DEFAULT_RULES}
        assert actions == {"trigger_deep_review", "insert_fix_loop", "pause"}


# ── ConditionEngine ──────────────────────────────────────────────

class TestConditionEngine:
    def test_init_with_defaults(self):
        engine = ConditionEngine()
        assert engine.rule_count() == 3
        assert "code_lines>500" in engine.list_rules()

    def test_init_without_defaults(self):
        engine = ConditionEngine(auto_load_defaults=False)
        assert engine.rule_count() == 0

    def test_init_with_custom_rules(self):
        from workflow_template import ConditionRule
        r = ConditionRule(
            name="test",
            predicate=lambda s: True,
            action="pause",
        )
        engine = ConditionEngine(rules=[r], auto_load_defaults=False)
        assert engine.rule_count() == 1

    # ── Rule management ──

    def test_register_rule(self):
        from workflow_template import ConditionRule
        engine = ConditionEngine(auto_load_defaults=False)
        r = ConditionRule(name="r1", predicate=lambda s: True, action="a1")
        engine.register_rule(r)
        assert engine.rule_count() == 1
        assert engine.get_rule("r1") is r

    def test_register_overwrites(self):
        from workflow_template import ConditionRule
        engine = ConditionEngine(auto_load_defaults=False)
        r1 = ConditionRule(name="r1", predicate=lambda s: False, action="a1")
        r2 = ConditionRule(name="r1", predicate=lambda s: True, action="a2")
        engine.register_rule(r1)
        engine.register_rule(r2)
        assert engine.get_rule("r1") is r2

    def test_remove_rule(self):
        engine = ConditionEngine()
        assert engine.remove_rule("code_lines>500") is True
        assert engine.rule_count() == 2
        assert engine.remove_rule("nonexistent") is False

    def test_get_rule(self):
        engine = ConditionEngine()
        assert engine.get_rule("budget_80pct") is not None
        assert engine.get_rule("nonexistent") is None

    def test_list_rules(self):
        engine = ConditionEngine()
        rules = engine.list_rules()
        assert "code_lines>500" in rules
        assert "test_failures>3" in rules
        assert "budget_80pct" in rules

    def test_clear_rules(self):
        engine = ConditionEngine()
        engine.clear_rules()
        assert engine.rule_count() == 0

    def test_rule_count(self):
        engine = ConditionEngine()
        assert engine.rule_count() == 3
        engine.clear_rules()
        assert engine.rule_count() == 0


class TestConditionEngineEvaluation:
    """Test evaluate() logic on various contexts."""

    def test_evaluate_code_lines_trigger(self):
        engine = ConditionEngine()
        results = engine.evaluate({"code_lines": 600, "test_failures": 0})
        assert len(results) == 1
        assert results[0].triggered is True
        assert results[0].action == "trigger_deep_review"
        assert results[0].rule_name == "code_lines>500"

    def test_evaluate_test_failures_trigger(self):
        engine = ConditionEngine()
        results = engine.evaluate({"code_lines": 100, "test_failures": 5})
        assert len(results) == 1
        assert results[0].action == "insert_fix_loop"

    def test_evaluate_budget_trigger(self):
        engine = ConditionEngine()
        results = engine.evaluate(
            {"code_lines": 100, "test_failures": 0, "budget_total": 100, "budget_spent": 85}
        )
        assert len(results) == 1
        assert results[0].action == "pause"

    def test_evaluate_multiple_triggers(self):
        engine = ConditionEngine()
        context = {
            "code_lines": 600,
            "test_failures": 5,
            "budget_total": 100,
            "budget_spent": 85,
        }
        results = engine.evaluate(context)
        assert len(results) == 3
        actions = {r.action for r in results}
        assert actions == {"trigger_deep_review", "insert_fix_loop", "pause"}

    def test_evaluate_no_trigger(self):
        engine = ConditionEngine()
        results = engine.evaluate({"code_lines": 100, "test_failures": 1})
        assert len(results) == 0

    def test_evaluate_empty_context(self):
        engine = ConditionEngine()
        results = engine.evaluate({})
        assert len(results) == 0

    def test_evaluate_result_structure(self):
        engine = ConditionEngine()
        results = engine.evaluate({"code_lines": 600})
        r = results[0]
        assert r.triggered is True
        assert r.action == "trigger_deep_review"
        assert "500" in r.reason
        assert r.rule_name == "code_lines>500"
        assert "code_lines" in r.context_snapshot

    def test_evaluate_all_returns_untriggered(self):
        engine = ConditionEngine()
        results = engine.evaluate_all({"code_lines": 100, "test_failures": 1})
        assert len(results) == 3  # all 3 rules evaluated
        triggered = [r for r in results if r.triggered]
        assert len(triggered) == 0

    def test_evaluate_all_returns_all(self):
        engine = ConditionEngine()
        results = engine.evaluate_all({"code_lines": 600})
        assert len(results) == 3
        triggered = [r for r in results if r.triggered]
        assert len(triggered) == 1

    def test_get_triggered_actions(self):
        engine = ConditionEngine()
        actions = engine.get_triggered_actions({"code_lines": 600, "test_failures": 4})
        assert "trigger_deep_review" in actions
        assert "insert_fix_loop" in actions


class TestPhaseInsertions:
    """Test determine_phase_insertions() action → phase mapping."""

    def test_trigger_deep_review(self):
        engine = ConditionEngine()
        context = {"code_lines": 600, "test_failures": 0}
        insertions = engine.determine_phase_insertions(context, "DEVELOP")
        assert len(insertions) == 1
        assert insertions[0] == ("DEEP_REVIEW", "DEVELOP")

    def test_trigger_deep_review_after_develop(self):
        engine = ConditionEngine()
        context = {"code_lines": 600, "test_failures": 0}
        insertions = engine.determine_phase_insertions(context, "CODE_REVIEW")
        assert len(insertions) == 1
        assert insertions[0] == ("DEEP_REVIEW", "CODE_REVIEW")

    def test_insert_fix_loop(self):
        engine = ConditionEngine()
        context = {"test_failures": 5, "code_lines": 100}
        insertions = engine.determine_phase_insertions(context, "DEVELOP")
        assert len(insertions) == 1
        assert insertions[0] == ("FIX_LOOP", "TEST")

    def test_pause_no_insertion(self):
        engine = ConditionEngine()
        context = {"budget_total": 100, "budget_spent": 85, "code_lines": 100, "test_failures": 0}
        insertions = engine.determine_phase_insertions(context, "DEVELOP")
        assert len(insertions) == 0

    def test_multiple_insertions(self):
        engine = ConditionEngine()
        context = {"code_lines": 600, "test_failures": 5}
        insertions = engine.determine_phase_insertions(context, "DEVELOP")
        assert len(insertions) == 2
        actions = {a for a, _ in insertions}
        assert actions == {"DEEP_REVIEW", "FIX_LOOP"}


class TestCustomPredicates:
    def test_register_and_use(self):
        engine = ConditionEngine(auto_load_defaults=False)
        engine.register_predicate("always_true", lambda s: True)
        assert engine.get_predicate("always_true") is not None

    def test_remove_predicate(self):
        engine = ConditionEngine(auto_load_defaults=False)
        engine.register_predicate("p1", lambda s: True)
        assert engine.remove_predicate("p1") is True
        assert engine.remove_predicate("nonexistent") is False

    def test_get_predicate_fallback_to_builtin(self):
        engine = ConditionEngine(auto_load_defaults=False)
        pred = engine.get_predicate("code_lines_gt_500")
        assert pred is not None


class TestRuleLoading:
    def test_load_from_json_file(self):
        engine = ConditionEngine(auto_load_defaults=False)
        rules_json = json.dumps([
            {
                "name": "custom_rule",
                "predicate": "code_lines_gt_500",
                "action": "trigger_deep_review",
                "description": "custom",
            }
        ])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(rules_json)
            f.flush()
            path = f.name

        try:
            count = engine.load_rules_from_json(path)
            assert count == 1
            assert engine.rule_count() == 1
            assert engine.get_rule("custom_rule") is not None
        finally:
            os.unlink(path)

    def test_load_from_nonexistent_json(self):
        engine = ConditionEngine(auto_load_defaults=False)
        count = engine.load_rules_from_json("/nonexistent/path.json")
        assert count == 0

    def test_load_from_yaml_file(self):
        engine = ConditionEngine(auto_load_defaults=False)
        yaml_content = """\
- name: yaml_rule
  predicate: has_errors
  action: pause
  description: yaml test
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(yaml_content)
            f.flush()
            path = f.name

        try:
            # Try with yaml (may fall back to JSON if pyyaml not installed)
            count = engine.load_rules_from_yaml(path)
            if count > 0:
                assert engine.rule_count() == 1
                assert engine.get_rule("yaml_rule") is not None
        finally:
            os.unlink(path)

    def test_load_rules_from_dicts(self):
        engine = ConditionEngine(auto_load_defaults=False)
        dicts = [
            {"name": "dict_rule", "action": "pause", "predicate": "has_errors"},
            {"name": "expr_rule", "action": "trigger_deep_review", "predicate": "code_lines > 500 and test_failures > 0"},
        ]
        count = engine.load_rules_from_dicts(dicts)
        assert count == 2
        assert engine.rule_count() == 2

    def test_load_rules_from_dicts_no_name(self):
        engine = ConditionEngine(auto_load_defaults=False)
        count = engine.load_rules_from_dicts([{"action": "x", "predicate": "has_errors"}])
        assert count == 0

    def test_module_load_rules_from_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(json.dumps([
                {"name": "mod_rule", "predicate": "has_errors", "action": "pause"}
            ]))
            f.flush()
            path = f.name
        try:
            rules = load_rules_from_json(path)
            assert len(rules) == 1
            assert rules[0].name == "mod_rule"
        finally:
            os.unlink(path)


class TestEvaluateContext:
    def test_convenience_function(self):
        results = evaluate_context({"code_lines": 600, "test_failures": 0})
        assert len(results) == 1
        assert results[0].action == "trigger_deep_review"

    def test_convenience_with_custom_rules(self):
        from workflow_template import ConditionRule
        r = ConditionRule(name="always", predicate=lambda s: True, action="pause")
        results = evaluate_context({"x": 1}, rules=[r])
        assert len(results) == 1
        assert results[0].action == "pause"


class TestEnrichContext:
    def test_enrich_code_layer(self):
        engine = ConditionEngine()
        layers = {
            "src": {"content": "line1\nline2\nline3\nline4\nline5", "tags": []},
        }
        ctx = engine.enrich_context_from_layers(layers)
        assert ctx.get("code_lines") == 5
        assert ctx.get("file_count") == 1

    def test_enrich_test_failures(self):
        engine = ConditionEngine()
        layers = {
            "test_results": {"content": "FAIL: test_foo\nERROR: test_bar\nPASS: test_baz", "tags": []},
        }
        ctx = engine.enrich_context_from_layers(layers)
        assert ctx.get("test_failures", 0) >= 2

    def test_enrich_with_extra(self):
        engine = ConditionEngine()
        layers = {}
        ctx = engine.enrich_context_from_layers(layers, extra={"code_lines": 999})
        assert ctx["code_lines"] == 999


class TestSerialization:
    def test_to_dict(self):
        engine = ConditionEngine()
        d = engine.to_dict()
        assert "rules" in d
        assert d["rule_count"] == 3
        assert len(d["rules"]) == 3

    def test_evaluation_log(self):
        engine = ConditionEngine(auto_load_defaults=False)
        engine.evaluate({"code_lines": 600})
        log = engine.get_evaluation_log()
        assert len(log) > 0

    def test_clear_log(self):
        engine = ConditionEngine(auto_load_defaults=False)
        engine.evaluate({"x": 1})
        engine.clear_log()
        assert engine.get_evaluation_log() == []
