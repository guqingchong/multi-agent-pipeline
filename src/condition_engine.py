"""src/condition_engine.py — Condition Engine for dynamic pipeline branching.

Evaluates template conditions against feature context to determine
whether to trigger additional pipeline steps:
  - code_lines > 500    → trigger_deep_review
  - test_failures > 3   → insert_fix_loop
  - budget consumed >80% → pause

Provides:
  - ConditionResult: dataclass with triggered(bool), action(str), reason(str)
  - ConditionEngine: main engine class
  - Built-in predicate registry
  - Configurable condition rules (JSON/YAML)

Depends on W3-E01 (ContextManager) — uses feature context from context layers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml
    _YAML_ERROR = yaml.YAMLError
except ImportError:
    _YAML_ERROR = Exception

# ───────────────────────────────────────────────────────────────
# 内部依赖：workflow_template 的 ConditionRule
# ───────────────────────────────────────────────────────────────

try:
    from workflow_template import ConditionRule, evaluate_conditions
except ModuleNotFoundError:
    from src.workflow_template import ConditionRule, evaluate_conditions


__all__ = [
    "ConditionResult",
    "ConditionEngine",
    "BUILTIN_PREDICATES",
    "DEFAULT_RULES",
    "load_rules_from_json",
    "load_rules_from_yaml",
    "evaluate_context",
]


# ───────────────────────────────────────────────────────────────
# ConditionResult — 条件求值结果
# ───────────────────────────────────────────────────────────────

@dataclass
class ConditionResult:
    """单个条件的求值结果。

    Attributes:
        triggered: 条件是否被触发
        action: 触发的动作（trigger_deep_review / insert_fix_loop / pause / …）
        reason: 触发原因的人可读描述
        rule_name: 触发的规则名称
        context_snapshot: 触发时的上下文快照（可选，用于审计）
    """
    triggered: bool
    action: str
    reason: str
    rule_name: str = ""
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.triggered


# ───────────────────────────────────────────────────────────────
# Built-in predicate functions
# ───────────────────────────────────────────────────────────────

def _pred_code_lines_gt(state: Dict[str, Any], threshold: int = 500) -> bool:
    """code_lines > threshold → true"""
    return state.get("code_lines", 0) > threshold


def _pred_test_failures_gt(state: Dict[str, Any], threshold: int = 3) -> bool:
    """test_failures > threshold → true"""
    return state.get("test_failures", 0) > threshold


def _pred_budget_consumed_pct(state: Dict[str, Any], threshold: float = 80.0) -> bool:
    """budget consumed percentage >= threshold → true"""
    total = state.get("budget_total", 0)
    spent = state.get("budget_spent", 0)
    if total <= 0:
        return False
    return (spent / total) * 100 >= threshold


def _pred_test_coverage_lt(state: Dict[str, Any], threshold: float = 80.0) -> bool:
    """test_coverage < threshold → true"""
    return state.get("test_coverage", 100.0) < threshold


def _pred_review_passed(state: Dict[str, Any], required: bool = True) -> bool:
    """review_passed != required → true (i.e. review hasn't passed)"""
    return state.get("review_passed", False) != required


def _pred_file_count_gt(state: Dict[str, Any], threshold: int = 50) -> bool:
    """file_count > threshold → true"""
    return state.get("file_count", 0) > threshold


def _pred_has_errors(state: Dict[str, Any], _unused: Any = None) -> bool:
    """errors list is non-empty → true"""
    errors = state.get("errors", [])
    return bool(errors)


def _pred_phase_is(state: Dict[str, Any], phase_name: str = "") -> bool:
    """current_phase == phase_name → true"""
    if not phase_name:
        return False
    return state.get("current_phase", "").lower() == phase_name.lower()


def _pred_custom_expr(state: Dict[str, Any], expr: str = "") -> bool:
    """Evaluate a simple expression like 'code_lines>1000 and test_failures>0'.

    Supports: >, <, >=, <=, ==, !=, and, or, not, parentheses.
    Variable names map to state keys.
    This is a limited evaluator for config-file expressions — not a full Python eval.
    """
    if not expr:
        return False

    expr = expr.strip()
    # Replace variable references with their values
    # We parse state keys and replace them with numeric literals
    # Sort keys by length descending to avoid partial replacements
    sorted_keys = sorted(state.keys(), key=len, reverse=True)

    # Simple safe evaluator: parse comparison operators
    # First handle AND/OR at top level
    or_parts = _split_expr(expr.lower(), " or ")
    for part in or_parts:
        and_parts = _split_expr(part.strip(), " and ")
        all_true = True
        for ap in and_parts:
            if not _eval_simple_cmp(state, ap.strip()):
                all_true = False
                break
        if all_true:
            return True
    return False


def _split_expr(expr: str, sep: str) -> List[str]:
    """Split expression by sep respecting parentheses."""
    parts = []
    depth = 0
    current = ""
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif depth == 0 and expr[i:i+len(sep)] == sep:
            parts.append(current.strip())
            current = ""
            i += len(sep) - 1
        else:
            current += ch
        i += 1
    parts.append(current.strip())
    return parts


def _eval_simple_cmp(state: Dict[str, Any], cmp_expr: str) -> bool:
    """Evaluate a simple comparison like 'code_lines > 500' or 'not test_failures'."""
    cmp_expr = cmp_expr.strip()

    # Handle 'not <var>'
    if cmp_expr.startswith("not "):
        var = cmp_expr[4:].strip()
        return not bool(state.get(var, False))

    # Handle 'not <expr>' with parentheses
    if cmp_expr.startswith("not(") and cmp_expr.endswith(")"):
        inner = cmp_expr[4:-1].strip()
        return not _eval_simple_cmp(state, inner)

    # Try to find a comparison operator
    for op in (">=", "<=", "!=", "==", ">", "<"):
        if op in cmp_expr:
            parts = cmp_expr.split(op, 1)
            var_name = parts[0].strip()
            raw_val = parts[1].strip()

            var_val = state.get(var_name, 0)
            try:
                if isinstance(var_val, bool):
                    cmp_val = raw_val.lower() in ("true", "1", "yes")
                elif isinstance(var_val, (int, float)):
                    cmp_val = float(raw_val) if "." in raw_val else int(raw_val)
                else:
                    cmp_val = raw_val
            except (ValueError, TypeError):
                cmp_val = raw_val

            if op == ">":
                return var_val > cmp_val
            elif op == "<":
                return var_val < cmp_val
            elif op == ">=":
                return var_val >= cmp_val
            elif op == "<=":
                return var_val <= cmp_val
            elif op == "==":
                return var_val == cmp_val
            elif op == "!=":
                return var_val != cmp_val

    # No operator found: treat as boolean check
    return bool(state.get(cmp_expr, False))


# ───────────────────────────────────────────────────────────────
# Predicate Registry
# ───────────────────────────────────────────────────────────────

BUILTIN_PREDICATES: Dict[str, Callable[..., bool]] = {
    "code_lines_gt_500": lambda s: _pred_code_lines_gt(s, 500),
    "test_failures_gt_3": lambda s: _pred_test_failures_gt(s, 3),
    "budget_consumed_80pct": lambda s: _pred_budget_consumed_pct(s, 80.0),
    "test_coverage_lt_80": lambda s: _pred_test_coverage_lt(s, 80.0),
    "review_not_passed": lambda s: _pred_review_passed(s, True),
    "file_count_gt_50": lambda s: _pred_file_count_gt(s, 50),
    "has_errors": _pred_has_errors,
}


# ───────────────────────────────────────────────────────────────
# Default condition rules (mirror workflow_template.DEFAULT_CONDITIONS)
# ───────────────────────────────────────────────────────────────

DEFAULT_RULES: List[ConditionRule] = [
    ConditionRule(
        name="code_lines>500",
        predicate=BUILTIN_PREDICATES["code_lines_gt_500"],
        action="trigger_deep_review",
        description="当总代码行数超过 500 行时，触发深度代码审查阶段。",
    ),
    ConditionRule(
        name="test_failures>3",
        predicate=BUILTIN_PREDICATES["test_failures_gt_3"],
        action="insert_fix_loop",
        description="当测试失败数超过 3 个时，插入修复循环。",
    ),
    ConditionRule(
        name="budget_80pct",
        predicate=BUILTIN_PREDICATES["budget_consumed_80pct"],
        action="pause",
        description="当预算消耗达到 80% 时，暂停流水线等待人工审批。",
    ),
]


# ───────────────────────────────────────────────────────────────
# ConditionEngine
# ───────────────────────────────────────────────────────────────

class ConditionEngine:
    """条件边求值引擎。

    职责：
      1. 根据 feature context 评估条件规则
      2. 返回触发的动作列表
      3. 支持动态插入 phase（如 auto insert fix loop）
      4. 支持外部配置条件规则（JSON / YAML）
      5. 提供条件规则注册、移除、查询接口

    用法::

        engine = ConditionEngine()
        engine.register_rule(my_rule)
        results = engine.evaluate({"code_lines": 600, "test_failures": 0})
        for r in results:
            if r.triggered:
                print(f"触发: {r.action} — {r.reason}")
    """

    def __init__(
        self,
        rules: Optional[List[ConditionRule]] = None,
        auto_load_defaults: bool = True,
    ) -> None:
        """初始化条件引擎。

        Args:
            rules: 初始条件规则列表（与默认规则合并）
            auto_load_defaults: 是否自动加载 DEFAULT_RULES
        """
        self._rules: Dict[str, ConditionRule] = {}
        self._custom_predicates: Dict[str, Callable[[Dict[str, Any]], bool]] = {}
        self._evaluation_log: List[Dict[str, Any]] = []

        if auto_load_defaults:
            for rule in DEFAULT_RULES:
                self._rules[rule.name] = rule

        if rules:
            for rule in rules:
                self._rules[rule.name] = rule

    # ── Rule management ──

    def register_rule(self, rule: ConditionRule) -> None:
        """注册（或覆盖）一个条件规则。"""
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        """移除指定名称的规则。返回是否成功。"""
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    def get_rule(self, name: str) -> Optional[ConditionRule]:
        """获取指定名称的规则。"""
        return self._rules.get(name)

    def list_rules(self) -> List[str]:
        """列出所有已注册规则名称。"""
        return list(self._rules.keys())

    def clear_rules(self) -> None:
        """清空所有规则。"""
        self._rules.clear()

    def rule_count(self) -> int:
        """返回已注册规则数量。"""
        return len(self._rules)

    # ── Custom predicates ──

    def register_predicate(
        self, name: str, predicate: Callable[[Dict[str, Any]], bool]
    ) -> None:
        """注册自定义谓词函数。"""
        self._custom_predicates[name] = predicate

    def remove_predicate(self, name: str) -> bool:
        """移除自定义谓词。"""
        if name in self._custom_predicates:
            del self._custom_predicates[name]
            return True
        return False

    def get_predicate(self, name: str) -> Optional[Callable[[Dict[str, Any]], bool]]:
        """获取谓词函数（先查自定义，再查内置）。"""
        if name in self._custom_predicates:
            return self._custom_predicates[name]
        return BUILTIN_PREDICATES.get(name)

    # ── Rule loading from external config ──

    def load_rules_from_json(self, path: str) -> int:
        """从 JSON 文件加载条件规则。

        JSON 格式::

            [
                {
                    "name": "code_lines>1000",
                    "predicate": "code_lines_gt_500",
                    "action": "trigger_deep_review",
                    "description": "..."
                }
            ]

        返回加载的规则数量。
        """
        return _load_rules_from_json_file(path, self)

    def load_rules_from_yaml(self, path: str) -> int:
        """从 YAML 文件加载条件规则。返回加载的规则数量。"""
        return _load_rules_from_yaml_file(path, self)

    def load_rules_from_dicts(self, rule_dicts: List[Dict[str, Any]]) -> int:
        """从 dict 列表批量注册规则。

        每个 dict 必须包含 name、action 字段。
        predicate 可以是：
          - 字符串：引用 BUILTIN_PREDICATES 或已注册自定义谓词的名称
          - 字符串表达式：如 "code_lines>500 and test_failures>0"
          - callable：直接用作谓词

        返回成功注册的规则数量。
        """
        count = 0
        for rd in rule_dicts:
            name = rd.get("name", "")
            action = rd.get("action", "")
            if not name or not action:
                continue

            predicate_raw = rd.get("predicate", "")
            predicate = self._resolve_predicate(predicate_raw)
            if predicate is None:
                continue

            rule = ConditionRule(
                name=name,
                predicate=predicate,
                action=action,
                description=rd.get("description", ""),
            )
            self.register_rule(rule)
            count += 1
        return count

    def _resolve_predicate(
        self, predicate_raw: Any
    ) -> Optional[Callable[[Dict[str, Any]], bool]]:
        """解析谓词：字符串名称 → 已注册谓词，字符串表达式 → lambda，callable → 直接使用。"""
        if callable(predicate_raw):
            return predicate_raw

        if isinstance(predicate_raw, str):
            # 先尝试按名称查找
            pred = self.get_predicate(predicate_raw)
            if pred is not None:
                return pred
            # 尝试作为表达式解析
            if any(op in predicate_raw for op in (">", "<", "=", "!", "and", "or")):
                return lambda s, expr=predicate_raw: _pred_custom_expr(s, expr)
            # 如果都匹配不到，返回 None
            return None

        return None

    # ── Evaluation ──

    def evaluate(self, context: Dict[str, Any]) -> List[ConditionResult]:
        """评估所有已注册的条件规则。

        Args:
            context: feature 上下文字典，包含 code_lines、test_failures、
                     budget_total、budget_spent 等键。

        Returns:
            ConditionResult 列表（按规则注册顺序）。仅包含 triggered=True 的结果。
        """
        results: List[ConditionResult] = []

        for rule in self._rules.values():
            try:
                fired = rule.predicate(context)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
                # 记录错误但不中断评估
                self._evaluation_log.append({
                    "rule": rule.name,
                    "context_keys": list(context.keys()),
                    "error": str(e),
                })
                continue

            if fired:
                result = ConditionResult(
                    triggered=True,
                    action=rule.action,
                    reason=rule.description or f"条件 '{rule.name}' 被触发",
                    rule_name=rule.name,
                    context_snapshot={
                        k: context.get(k)
                        for k in self._relevant_keys(rule.name)
                        if k in context
                    },
                )
                results.append(result)

        self._evaluation_log.append({
            "context_keys": list(context.keys()),
            "rules_checked": len(self._rules),
            "rules_triggered": len(results),
        })
        return results

    def evaluate_all(self, context: Dict[str, Any]) -> List[ConditionResult]:
        """评估所有规则并返回全部结果（包括未触发的）。

        与 evaluate() 不同，此方法返回所有规则的 ConditionResult，
        包括 triggered=False 的结果。用于调试和审计。
        """
        results: List[ConditionResult] = []
        for rule in self._rules.values():
            try:
                fired = rule.predicate(context)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                fired = False

            results.append(ConditionResult(
                triggered=fired,
                action=rule.action,
                reason=rule.description if fired else f"条件 '{rule.name}' 未触发",
                rule_name=rule.name,
                context_snapshot={k: context.get(k) for k in self._relevant_keys(rule.name) if k in context},
            ))
        return results

    def _relevant_keys(self, rule_name: str) -> List[str]:
        """根据规则名称推断相关的上下文键。"""
        mapping = {
            "code_lines": ["code_lines"],
            "test_failures": ["test_failures"],
            "budget": ["budget_total", "budget_spent"],
            "coverage": ["test_coverage"],
            "review": ["review_passed"],
            "file_count": ["file_count"],
            "errors": ["errors"],
            "phase": ["current_phase"],
        }
        for key, keys in mapping.items():
            if key in rule_name:
                return keys
        return []

    # ── Action resolution ──

    def get_triggered_actions(self, context: Dict[str, Any]) -> List[str]:
        """快速获取所有触发动作的名称列表。"""
        return [r.action for r in self.evaluate(context)]

    def determine_phase_insertions(
        self, context: Dict[str, Any], current_phase: str
    ) -> List[Tuple[str, str]]:
        """根据触发的条件决定需要插入哪些额外 phase。

        Args:
            context: feature 上下文
            current_phase: 当前 phase 名称

        Returns:
            [(phase_to_insert, insert_after_phase), ...] 列表。
            例如 [("DEEP_REVIEW", "DEVELOP"), ("FIX_LOOP", "TEST")]。

        Action → phase mapping::

            trigger_deep_review → DEEP_REVIEW（插入在 DEVELOP 之后、CODE_REVIEW 之前）
            insert_fix_loop    → FIX_LOOP（插入在 TEST 之后）
            pause              → 不插入 phase，返回空列表（由调用方处理暂停逻辑）
        """
        results = self.evaluate(context)
        insertions: List[Tuple[str, str]] = []

        for r in results:
            if r.action == "trigger_deep_review":
                # Deep review should go after DEVELOP, before CODE_REVIEW
                insert_after = "DEVELOP"
                # If we're already past DEVELOP, insert after current phase
                phase_order = [
                    "INIT", "PRD", "RESEARCH", "DESIGN", "DESIGN_REVIEW",
                    "DECOMPOSE", "DEVELOP", "CODE_REVIEW", "TEST",
                    "FIX_LOOP", "ACCEPT", "DEPLOY",
                ]
                try:
                    current_idx = phase_order.index(current_phase.upper())
                    develop_idx = phase_order.index("DEVELOP")
                    if current_idx > develop_idx:
                        insert_after = current_phase.upper()
                except ValueError:
                    pass
                insertions.append(("DEEP_REVIEW", insert_after))

            elif r.action == "insert_fix_loop":
                insert_after = "TEST"
                insertions.append(("FIX_LOOP", insert_after))

            # "pause" action: don't insert a phase; caller handles it

        return insertions

    # ── Context enrichment ──

    def enrich_context_from_layers(
        self,
        layers: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从 ContextManager 的层字典中提取 feature 上下文。

        Args:
            layers: ContextManager.to_dict()["layers"] 或类似结构
            extra: 额外的上下文键值对

        Returns:
            适合传给 evaluate() 的上下文字典
        """
        context: Dict[str, Any] = {}

        # 尝试从各层提取信息
        for layer_name, layer_data in layers.items():
            content = ""
            if isinstance(layer_data, dict):
                content = layer_data.get("content", "")
                tags = layer_data.get("tags", [])
            else:
                content = str(layer_data)
                tags = []

            # 代码行数估算
            if "code" in layer_name.lower() or "src" in layer_name.lower():
                lines = content.count("\n") + (1 if content else 0)
                context["code_lines"] = context.get("code_lines", 0) + lines
                context["file_count"] = context.get("file_count", 0) + 1

            # 测试失败
            if "test" in layer_name.lower() and "fail" in content.lower():
                # 粗糙估算：统计 failure/FAIL/Error 出现次数
                fail_count = len(re.findall(r"(?i)\b(?:FAIL|ERROR|failure)\b", content))
                context["test_failures"] = context.get("test_failures", 0) + fail_count

        if extra:
            context.update(extra)

        return context

    # ── Evaluation log ──

    def get_evaluation_log(self) -> List[Dict[str, Any]]:
        """获取评估日志（最近 100 条）。"""
        return self._evaluation_log[-100:]

    def clear_log(self) -> None:
        """清空评估日志。"""
        self._evaluation_log.clear()

    # ── Serialization ──

    def to_dict(self) -> Dict[str, Any]:
        """序列化引擎配置（规则列表）。"""
        return {
            "rules": [
                {
                    "name": r.name,
                    "action": r.action,
                    "description": r.description,
                }
                for r in self._rules.values()
            ],
            "rule_count": len(self._rules),
            "custom_predicate_count": len(self._custom_predicates),
        }


# ───────────────────────────────────────────────────────────────
# Module-level helper: load rules from JSON file
# ───────────────────────────────────────────────────────────────

def load_rules_from_json(path: str) -> List[ConditionRule]:
    """从 JSON 文件加载条件规则列表（不注册到引擎）。

    JSON 格式::

        [
            {
                "name": "code_lines>1000",
                "predicate": "code_lines_gt_500",
                "action": "trigger_deep_review",
                "description": "..."
            }
        ]

    返回 ConditionRule 列表。
    """
    rules: List[ConditionRule] = []
    file_path = Path(path)
    if not file_path.exists():
        return rules

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        name = item.get("name", "")
        action = item.get("action", "")
        if not name or not action:
            continue

        pred_name = item.get("predicate", "")
        predicate = BUILTIN_PREDICATES.get(pred_name)
        if predicate is None and pred_name:
            # Try as expression
            if any(op in pred_name for op in (">", "<", "=", "!", "and", "or")):
                predicate = lambda s, expr=pred_name: _pred_custom_expr(s, expr)

        if predicate is None:
            continue

        rules.append(ConditionRule(
            name=name,
            predicate=predicate,
            action=action,
            description=item.get("description", ""),
        ))

    return rules


def _load_rules_from_json_file(path: str, engine: ConditionEngine) -> int:
    """内部辅助：从 JSON 文件加载规则到引擎。"""
    rules = load_rules_from_json(path)
    for rule in rules:
        engine.register_rule(rule)
    return len(rules)


# ───────────────────────────────────────────────────────────────
# Module-level helper: load rules from YAML file
# ───────────────────────────────────────────────────────────────

def load_rules_from_yaml(path: str) -> List[ConditionRule]:
    """从 YAML 文件加载条件规则列表（不注册到引擎）。"""
    rules: List[ConditionRule] = []
    file_path = Path(path)
    if not file_path.exists():
        return rules

    try:
        import yaml
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except ImportError:
        # yaml 不可用：回退到 JSON 解析（YAML 是 JSON 的超集）
        return load_rules_from_json(path)
    except (OSError, _YAML_ERROR):
        return rules

    for item in data:
        name = item.get("name", "")
        action = item.get("action", "")
        if not name or not action:
            continue

        pred_name = item.get("predicate", "")
        predicate = BUILTIN_PREDICATES.get(pred_name)
        if predicate is None and pred_name:
            if any(op in pred_name for op in (">", "<", "=", "!", "and", "or")):
                predicate = lambda s, expr=pred_name: _pred_custom_expr(s, expr)

        if predicate is None:
            continue

        rules.append(ConditionRule(
            name=name,
            predicate=predicate,
            action=action,
            description=item.get("description", ""),
        ))

    return rules


def _load_rules_from_yaml_file(path: str, engine: ConditionEngine) -> int:
    """内部辅助：从 YAML 文件加载规则到引擎。"""
    rules = load_rules_from_yaml(path)
    for rule in rules:
        engine.register_rule(rule)
    return len(rules)


# ───────────────────────────────────────────────────────────────
# Module-level convenience: evaluate_context
# ───────────────────────────────────────────────────────────────

def evaluate_context(
    context: Dict[str, Any],
    rules: Optional[List[ConditionRule]] = None,
) -> List[ConditionResult]:
    """模块级便捷函数：一次性评估上下文。

    Args:
        context: feature 上下文字典
        rules: 可选的自定义规则列表（默认使用 DEFAULT_RULES）

    Returns:
        触发的 ConditionResult 列表
    """
    engine = ConditionEngine(rules=rules)
    return engine.evaluate(context)
