"""tests/test_models.py — Tests for models.py (extracted shared models)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models import Phase, ProjectState, PipelineError, PhaseBlockedError, ProjectNotFoundError, CheckpointNotFoundError, ApprovalRequiredError


class TestPhase:
    def test_from_name_init(self):
        p = Phase.from_name("init")
        assert p == Phase("init")
        assert str(p) == "init"

    def test_from_name_develop(self):
        p = Phase.from_name("develop")
        assert p == Phase("develop")
        assert str(p) == "develop"

    def test_next_from_init_greenfield(self):
        # Greenfield order: init -> prd -> research -> design -> ...
        assert Phase("init").next() == Phase("prd")

    def test_prev_from_design_greenfield(self):
        assert Phase("design").prev() == Phase("research")

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            Phase.from_name("nonexistent")

    def test_phase_equality_and_hash(self):
        p1 = Phase("init")
        p2 = Phase("init")
        p3 = Phase("develop")
        assert p1 == p2
        assert p1 != p3
        assert hash(p1) == hash(p2)

    def test_is_start_and_terminal(self):
        assert Phase("init").is_start("greenfield") is True
        assert Phase("deploy").is_terminal("greenfield") is True
        assert Phase("develop").is_terminal("greenfield") is False


class TestProjectState:
    def test_to_dict(self):
        s = ProjectState(name="test", phase=Phase("init"), description="desc", stack="py")
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["phase"] == "init"
        assert d["description"] == "desc"
        assert d["stack"] == "py"

    def test_from_dict(self):
        d = {
            "name": "test",
            "phase": "develop",
            "description": "",
            "stack": "",
            "created": True,
            "git_init": False,
            "metadata_files": ["SOUL.md"],
            "db_created": True,
            "check_results": {},
            "design_approved": False,
            "accept_approved": False,
            "tests_passed": False,
        }
        s = ProjectState.from_dict(d)
        assert s.name == "test"
        assert s.phase == Phase("develop")
        assert s.created is True

    def test_roundtrip(self):
        s1 = ProjectState(name="proj", phase=Phase("design"), description="d", stack="ts", created=True)
        d = s1.to_dict()
        s2 = ProjectState.from_dict(d)
        assert s2.name == s1.name
        assert s2.phase == s1.phase
        assert s2.created == s1.created


class TestExceptions:
    def test_exception_hierarchy(self):
        assert issubclass(PhaseBlockedError, PipelineError)
        assert issubclass(ProjectNotFoundError, PipelineError)
        assert issubclass(CheckpointNotFoundError, PipelineError)
        assert issubclass(ApprovalRequiredError, PipelineError)

    def test_raise_and_catch(self):
        try:
            raise PhaseBlockedError("blocked")
        except PipelineError:
            pass

        try:
            raise ProjectNotFoundError("missing")
        except PipelineError:
            pass
