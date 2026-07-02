"""tests/test_models.py — Tests for models.py (extracted shared models)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models import Phase, ProjectState, PipelineError, PhaseBlockedError, ProjectNotFoundError, CheckpointNotFoundError, ApprovalRequiredError


class TestPhase:
    def test_from_name_init(self):
        p = Phase.from_name("init")
        assert p == Phase.INIT
        assert str(p) == "init"

    def test_from_name_review_legacy(self):
        p = Phase.from_name("review")
        assert p == Phase.REVIEW
        assert str(p) == "review"

    def test_next_from_init(self):
        assert Phase.INIT.next() == Phase.DEVELOP

    def test_prev_from_test(self):
        assert Phase.TEST.prev() == Phase.REVIEW

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            Phase.from_name("nonexistent")


class TestProjectState:
    def test_to_dict(self):
        s = ProjectState(name="test", phase=Phase.INIT, description="desc", stack="py")
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
        assert s.phase == Phase.DEVELOP
        assert s.created is True

    def test_roundtrip(self):
        s1 = ProjectState(name="proj", phase=Phase.DESIGN, description="d", stack="ts", created=True)
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
