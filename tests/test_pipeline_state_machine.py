"""tests/test_pipeline_state_machine.py — pipeline.py registry-driven state machine tests"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import Phase, ProjectState
from state_store import StateStore
from pipeline import cmd_init, cmd_check, cmd_advance, cmd_status


@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """Run in a temporary directory."""
    tmpdir = tempfile.mkdtemp(prefix="pipeline_sm_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def init_project(tmp_cwd: Path) -> str:
    """Initialize a test project and return its name."""
    project_name = "test_project"
    ret = cmd_init(
        type(
            "Args",
            (),
            {
                "project": project_name,
                "description": "test",
                "stack": "python",
                "force": False,
            },
        )()
    )
    assert ret == 0
    return project_name


# ───────────────────────────────────────────────────────────────
# Phase tests (registry-driven)
# ───────────────────────────────────────────────────────────────

def test_phase_names() -> None:
    assert str(Phase("init")) == "init"
    assert str(Phase("develop")) == "develop"
    assert str(Phase("test")) == "test"
    assert str(Phase("deploy")) == "deploy"


def test_phase_from_name() -> None:
    assert Phase.from_name("init") == Phase("init")
    assert Phase.from_name("design") == Phase("design")


def test_phase_next_greenfield() -> None:
    """Greenfield order is the default for the registry-driven Phase class."""
    assert Phase("init").next() == Phase("prd")
    assert Phase("prd").next() == Phase("research")
    assert Phase("research").next() == Phase("design")
    assert Phase("deploy").next() is None


# ───────────────────────────────────────────────────────────────
# ProjectState serialization
# ───────────────────────────────────────────────────────────────

def test_project_state_roundtrip() -> None:
    state = ProjectState(
        name="demo",
        phase=Phase("develop"),
        description="desc",
        stack="python",
        created=True,
        git_init=True,
        metadata_files=["SOUL.md"],
        db_created=True,
        check_results={"check_init": True},
    )
    data = state.to_dict()
    restored = ProjectState.from_dict(data)
    assert restored.name == "demo"
    assert restored.phase == Phase("develop")
    assert restored.check_results["check_init"] is True


# ───────────────────────────────────────────────────────────────
# StateStore tests
# ───────────────────────────────────────────────────────────────

def test_state_store_save_load(tmp_cwd: Path) -> None:
    store = StateStore(tmp_cwd)
    state = ProjectState(name="x", phase=Phase("init"))
    store.save(state)

    loaded = store.load("x")
    assert loaded is not None
    assert loaded.name == "x"
    assert loaded.phase == Phase("init")


def test_state_store_db_created(tmp_cwd: Path) -> None:
    store = StateStore(tmp_cwd)
    assert (tmp_cwd / "pipeline_state.db").exists()


# ───────────────────────────────────────────────────────────────
# Command integration tests
# ───────────────────────────────────────────────────────────────

def test_cmd_init_creates_project(tmp_cwd: Path) -> None:
    ret = cmd_init(
        type(
            "Args",
            (),
            {
                "project": "demo",
                "description": "d",
                "stack": "python",
                "force": False,
            },
        )()
    )
    assert ret == 0
    assert (tmp_cwd / "demo").is_dir()
    assert (tmp_cwd / "demo" / "SOUL.md").exists()
    assert (tmp_cwd / "demo" / "AGENTS.md").exists()
    assert (tmp_cwd / "demo" / "progress.md").exists()
    assert (tmp_cwd / "demo" / "features.json").exists()
    assert (tmp_cwd / "demo" / "src").is_dir()
    assert (tmp_cwd / "demo" / "tests").is_dir()


def test_cmd_init_refuses_existing(tmp_cwd: Path) -> None:
    (tmp_cwd / "existing").mkdir()
    ret = cmd_init(
        type(
            "Args",
            (),
            {
                "project": "existing",
                "description": "",
                "stack": "",
                "force": False,
            },
        )()
    )
    assert ret == 1


def test_cmd_check_on_init_project(init_project: str, tmp_cwd: Path, capsys) -> None:
    ret = cmd_check(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0
    assert "PASS" in captured.out


def test_cmd_advance_init_to_prd(init_project: str, tmp_cwd: Path, capsys) -> None:
    # Add PRD doc so init -> prd passes
    (tmp_cwd / init_project / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_cwd / init_project / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")

    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0, f"advance failed: {captured.out}"
    assert "init" in captured.out
    assert "prd" in captured.out

    store = StateStore(tmp_cwd / init_project)
    state = store.load(init_project)
    assert state is not None
    assert state.phase == Phase("prd")


def test_cmd_advance_blocked_without_check(init_project: str, tmp_cwd: Path, capsys) -> None:
    # Advance to prd first (init check passes)
    docs_dir = tmp_cwd / init_project / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    prd_file = docs_dir / "PRD.md"
    prd_file.write_text("# PRD\n", encoding="utf-8")
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    assert ret == 0

    # Remove PRD doc so check_prd fails: advance from prd should be blocked
    prd_file.unlink()
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 1
    assert "BLOCKED" in captured.out or "blocked" in captured.out.lower()

    store = StateStore(tmp_cwd / init_project)
    state = store.load(init_project)
    assert state is not None
    assert state.phase == Phase("prd")


def test_cmd_status_outputs_json(init_project: str, tmp_cwd: Path, capsys) -> None:
    ret = cmd_status(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 0
    data = json.loads(captured.out)
    assert data["name"] == init_project
    assert data["phase"] == "init"


# ───────────────────────────────────────────────────────────────
# End-to-end registry-driven phase transition
# ───────────────────────────────────────────────────────────────

def test_full_phase_transition_init_to_prd(init_project: str, tmp_cwd: Path, capsys) -> None:
    """Registry-driven flow: init -> prd, then blocked by failing prd check."""
    store = StateStore(tmp_cwd / init_project)

    # init
    state = store.load(init_project)
    assert state is not None
    assert state.phase == Phase("init")

    # prd (needs PRD doc)
    docs_dir = tmp_cwd / init_project / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    prd_file = docs_dir / "PRD.md"
    prd_file.write_text("# PRD\n", encoding="utf-8")
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    assert ret == 0
    state = store.load(init_project)
    assert state.phase == Phase("prd")

    # Remove PRD doc so check_prd fails: advance from prd should be blocked
    prd_file.unlink()
    ret = cmd_advance(type("Args", (), {"project": init_project})())
    captured = capsys.readouterr()
    assert ret == 1
    assert "BLOCKED" in captured.out or "blocked" in captured.out.lower()
