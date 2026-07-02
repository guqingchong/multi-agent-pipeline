"""tests/test_workflow.py — Workflow template registry and project-type detection.

Covers the merged ``workflow.py`` public API:
  - build_workflows / get_template / list_templates / register_template
  - GraphTemplate / WorkflowTemplate / ConditionRule / evaluate_conditions
  - detect_project_type
  - brownfield 7-phase chain
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow import (
    build_workflows,
    get_template,
    list_templates,
    register_template,
    detect_project_type,
    GraphTemplate,
    WorkflowTemplate,
    ConditionRule,
    evaluate_conditions,
)

from config import get_config


# ───────────────────────────────────────────────────────────────
# Template registry tests
# ───────────────────────────────────────────────────────────────


def test_build_workflows_has_only_greenfield_and_brownfield() -> None:
    templates = build_workflows()
    assert set(templates.keys()) == {"greenfield", "brownfield"}


def test_get_template_greenfield() -> None:
    tmpl = get_template("greenfield")
    assert tmpl is not None
    assert isinstance(tmpl, GraphTemplate)
    assert tmpl.name == "greenfield"


def test_get_template_brownfield() -> None:
    tmpl = get_template("brownfield")
    assert tmpl is not None
    assert isinstance(tmpl, GraphTemplate)
    assert tmpl.name == "brownfield"


def test_get_template_unknown() -> None:
    assert get_template("nonexistent") is None


def test_list_templates() -> None:
    names = list_templates()
    assert "greenfield" in names
    assert "brownfield" in names


def test_register_template() -> None:
    custom = GraphTemplate(name="custom", phases=["a", "b"])
    register_template(custom)
    assert get_template("custom") is custom
    # restore registry for other tests
    templates = build_workflows()
    for name in ("greenfield", "brownfield"):
        register_template(templates[name])


# ───────────────────────────────────────────────────────────────
# Phase order tests
# ───────────────────────────────────────────────────────────────


def test_greenfield_phase_order_matches_config() -> None:
    cfg = get_config()
    tmpl = get_template("greenfield")
    assert tmpl is not None
    assert tmpl.phases == cfg.greenfield_phase_order


def test_brownfield_is_single_7_phase_chain() -> None:
    tmpl = get_template("brownfield")
    assert tmpl is not None
    assert tmpl.phases == [
        "discover",
        "benchmark",
        "analyze",
        "plan",
        "execute",
        "verify",
        "deliver",
    ]


def test_workflow_template_alias() -> None:
    """WorkflowTemplate must be an alias for GraphTemplate."""
    assert WorkflowTemplate is GraphTemplate


def test_workflow_template_validate_passes_for_known_phases() -> None:
    tmpl = GraphTemplate(name="valid", phases=["init", "design"])
    tmpl.validate()


def test_workflow_template_validate_fails_for_unknown_phases() -> None:
    tmpl = GraphTemplate(name="invalid", phases=["init", "not_a_phase"])
    with pytest.raises(ValueError) as exc_info:
        tmpl.validate()
    assert "invalid" in str(exc_info.value)
    assert "not_a_phase" in str(exc_info.value)


# ───────────────────────────────────────────────────────────────
# Project-type detection tests
# ───────────────────────────────────────────────────────────────


def test_detect_nonexistent_project_is_greenfield(tmp_path: Path) -> None:
    assert detect_project_type(tmp_path / "new_project") == "greenfield"


def test_detect_existing_src_with_python_is_brownfield(tmp_path: Path) -> None:
    proj = tmp_path / "existing"
    src = proj / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("print('hello')\n", encoding="utf-8")
    assert detect_project_type(proj) == "brownfield"


def test_detect_audit_sentinel_is_brownfield(tmp_path: Path) -> None:
    proj = tmp_path / "audit_project"
    proj.mkdir()
    (proj / ".audit").write_text("", encoding="utf-8")
    assert detect_project_type(proj) == "brownfield"


def test_detect_legacy_two_arg_signature(tmp_path: Path) -> None:
    """Backward compat: detect_project_type(project_name, base_dir) still works."""
    proj_dir = tmp_path / "legacy"
    src = proj_dir / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert detect_project_type("legacy", tmp_path) == "brownfield"


# ───────────────────────────────────────────────────────────────
# Condition evaluation tests
# ───────────────────────────────────────────────────────────────


def test_condition_code_lines_triggers() -> None:
    tmpl = get_template("greenfield")
    triggered = evaluate_conditions(tmpl, {"code_lines": 501})
    assert any(rule.name == "code_lines>500" for rule in triggered)


def test_condition_test_failures_triggers() -> None:
    tmpl = get_template("greenfield")
    triggered = evaluate_conditions(tmpl, {"test_failures": 5})
    assert any(rule.name == "test_failures>3" for rule in triggered)


def test_condition_budget_80pct_triggers() -> None:
    tmpl = get_template("greenfield")
    triggered = evaluate_conditions(tmpl, {"budget_total": 100, "budget_spent": 80})
    assert any(rule.name == "budget_80pct" for rule in triggered)


def test_condition_no_trigger() -> None:
    tmpl = get_template("greenfield")
    triggered = evaluate_conditions(tmpl, {"code_lines": 100})
    assert triggered == []


def test_custom_condition_rule() -> None:
    rule = ConditionRule(
        name="always",
        predicate=lambda state: True,
        action="noop",
        description="test",
    )
    tmpl = GraphTemplate(name="custom", phases=["a"], conditions=[rule])
    triggered = evaluate_conditions(tmpl, {})
    assert len(triggered) == 1
    assert triggered[0].name == "always"
