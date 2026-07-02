"""tests/test_inspector.py — Inspector / Veto mechanism tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from inspector import AuditReport, AuditVerdict, Inspector


def test_inspector_veto_when_plan_missing_target(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")
    (tmp_path / "docs" / "plan.md").write_text("我们将优化内存占用。", encoding="utf-8")

    inspector = Inspector(tmp_path)
    report = inspector.audit("plan")

    assert report.verdict == AuditVerdict.VETO
    assert any("响应时间" in f for f in report.findings)
    assert "prd.md" in report.evidence_files


def test_inspector_pass_when_plan_matches_prd_target(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")
    (tmp_path / "docs" / "plan.md").write_text(
        "我们将优化响应时间，目标 3 秒以内。", encoding="utf-8"
    )

    inspector = Inspector(tmp_path)
    report = inspector.audit("plan")

    assert report.verdict == AuditVerdict.PASS
    assert not report.findings


def test_inspector_veto_when_plan_doc_missing(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")

    inspector = Inspector(tmp_path)
    report = inspector.audit("plan")

    assert report.verdict == AuditVerdict.VETO
    assert any("plan.md 缺失" in f for f in report.findings)


def test_inspector_collects_evidence_files(tmp_path):
    (tmp_path / "docs").mkdir()
    for name in ["prd.md", "architecture.md", "journey.md", "acceptance.md"]:
        (tmp_path / "docs" / name).write_text(f"# {name}", encoding="utf-8")

    inspector = Inspector(tmp_path)
    report = inspector.audit("design")

    assert set(report.evidence_files) == {"prd.md", "architecture.md", "journey.md", "acceptance.md"}
    assert report.verdict == AuditVerdict.PASS


def test_inspector_execute_phase_detects_api_risk(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text(
        "系统包含外部 API 接口，请勿随意修改契约。", encoding="utf-8"
    )

    inspector = Inspector(tmp_path)
    report = inspector.audit(
        "execute", evidence={"changed_files": ["src/api_client.py"]}
    )

    assert report.verdict == AuditVerdict.PASS
    assert any("api" in r for r in report.risks)


def test_inspector_report_to_dict_round_trip():
    report = AuditReport(
        phase="plan",
        verdict=AuditVerdict.VETO,
        findings=["f1"],
        risks=["r1"],
        suggestions=["s1"],
    )
    data = report.to_dict()

    assert data["phase"] == "plan"
    assert data["verdict"] == "veto"
    assert data["findings"] == ["f1"]
    assert data["risks"] == ["r1"]
    assert data["suggestions"] == ["s1"]
    assert data["human_can_override"] is True


def test_inspector_defaults_to_pass_for_unaudited_phase(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "prd.md").write_text("需求。", encoding="utf-8")

    inspector = Inspector(tmp_path)
    report = inspector.audit("init")

    assert report.verdict == AuditVerdict.PASS
    assert report.phase == "init"


def test_phase_flow_advance_blocked_by_inspector_veto(tmp_path):
    """Integration: advance() should be vetoed when plan.md is missing."""
    import json
    import sqlite3

    from phase_flow import PhaseFlow

    project_name = "brownfield_plan"
    base_dir = tmp_path
    proj_dir = base_dir / project_name
    proj_dir.mkdir()

    # Documents required by check_plan
    docs_dir = proj_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "PRD.md").write_text("必须将响应时间降到 3 秒以内。", encoding="utf-8")
    (docs_dir / "architecture.md").write_text("# architecture", encoding="utf-8")

    # Mark project as brownfield so 'plan' is in phase_order
    (proj_dir / "features.json").write_text(
        json.dumps({"project": project_name, "project_type": "brownfield", "features": []},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    # State store with phase=plan
    db_path = proj_dir / "pipeline_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    state = {"name": project_name, "phase": "plan"}
    conn.execute(
        "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
        ("state", json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    flow = PhaseFlow(project_name, base_dir)
    passed, msg = flow.advance()

    assert passed is False
    assert "Inspector veto" in msg
    assert flow.current_phase() == "plan"
