"""tests/integration/test_full_flow.py — End-to-end init → advance flow."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def test_full_flow_init_advance(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", str(tmp_path))
    from phase_flow import PhaseFlow
    from state_store import StateStore
    from config import get_config

    flow = PhaseFlow("demo")
    flow.init(description="demo project", stack="python")
    assert flow.current_phase() == "init"

    flow.advance()
    assert flow.current_phase() == "prd"

    project_dir = tmp_path / "demo"
    db_path = get_config().db_path(project_dir)
    store = StateStore(db_path)
    record = store.get_project("demo")
    assert record is not None
    assert record.current_phase == "prd"
