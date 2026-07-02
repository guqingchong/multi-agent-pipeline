"""tests/test_pipeline.py — Tests for src/pipeline.py CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline import build_parser, main, _phase_choices
from state_store import StateStore


@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """Run in a temporary directory."""
    tmpdir = tempfile.mkdtemp(prefix="pipeline_test_")
    monkeypatch.chdir(tmpdir)
    monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


def _get_subparsers_action(parser):
    """Return the argparse _SubParsersAction via a stable lookup."""
    import argparse
    return next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def test_build_parser_lists_registry_phases(tmp_cwd: Path) -> None:
    """Parser choices must come from REGISTRY.list_phases()."""
    parser = build_parser()
    subparsers_action = _get_subparsers_action(parser)
    rollback_parser = subparsers_action.choices["rollback-phase"]
    to_action = next(a for a in rollback_parser._actions if getattr(a, "dest", None) == "to")
    choices = to_action.choices
    expected = _phase_choices()
    assert choices == expected
    assert "init" in choices
    assert "design" in choices
    assert "deploy" in choices


def test_parser_approve_only_design_and_accept(tmp_cwd: Path) -> None:
    parser = build_parser()
    subparsers_action = _get_subparsers_action(parser)
    approve_parser = subparsers_action.choices["approve"]
    phase_action = next(a for a in approve_parser._actions if getattr(a, "dest", None) == "phase")
    choices = phase_action.choices
    assert set(choices) == {"design", "accept"}


def test_init_command(tmp_cwd: Path) -> None:
    ret = main(["init", "demo"])
    assert ret == 0
    proj_dir = tmp_cwd / "demo"
    assert proj_dir.exists()
    assert (proj_dir / "SOUL.md").exists()
    assert (proj_dir / "features.json").exists()
    db_path = proj_dir / "pipeline_state.db"
    assert db_path.exists()


def test_status_command_after_init(tmp_cwd: Path) -> None:
    main(["init", "demo"])
    ret = main(["status", "demo"])
    assert ret == 0


def test_advance_command_blocked_without_check(tmp_cwd: Path, capsys) -> None:
    main(["init", "demo"])
    # Remove a required init-phase artifact so the init check fails.
    (tmp_cwd / "demo" / "SOUL.md").unlink()
    ret = main(["advance", "demo"])
    captured = capsys.readouterr()
    assert ret == 1
    assert "BLOCKED" in captured.out or "blocked" in captured.out.lower()


def test_rollback_phase_requires_approval(tmp_cwd: Path) -> None:
    main(["init", "demo"])
    # Put project into design phase so rollback to init is meaningful.
    db_path = tmp_cwd / "demo" / "pipeline_state.db"
    store = StateStore(db_path)
    raw = store.legacy_load("state")
    state = json.loads(raw) if raw else {}
    state["phase"] = "design"
    store.legacy_save("state", json.dumps(state, ensure_ascii=False))
    store.update_project_phase("demo", "design")

    ret = main(["rollback-phase", "demo", "--to", "init"])
    assert ret == 1


def test_approve_design_and_accept(tmp_cwd: Path) -> None:
    main(["init", "demo"])
    ret = main(["approve", "demo", "--phase", "design"])
    assert ret == 0
    ret = main(["approve", "demo", "--phase", "accept"])
    assert ret == 0


def test_mark_tests(tmp_cwd: Path) -> None:
    main(["init", "demo"])
    ret = main(["mark-tests", "demo", "--passed"])
    assert ret == 0


def test_legacy_develop_command_removed(tmp_cwd: Path) -> None:
    """The old 'develop' subcommand must no longer exist."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["develop", "demo"])


def test_no_legacy_check_functions_in_module() -> None:
    """Legacy 3-state check_* helpers must not be exposed by pipeline.py."""
    import pipeline as pipeline_mod
    assert not hasattr(pipeline_mod, "check_init")
    assert not hasattr(pipeline_mod, "check_develop")
    assert not hasattr(pipeline_mod, "check_review")
    assert not hasattr(pipeline_mod, "check_test")
