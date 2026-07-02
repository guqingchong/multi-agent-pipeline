"""tests/test_phase_model.py — Registry-driven Phase model tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from phase_model import Phase, get_phase_order, phase_names


class TestPhaseCreation:
    def test_create_from_name(self):
        p = Phase("init")
        assert p.name == "init"
        assert str(p) == "init"

    def test_create_is_case_insensitive(self):
        p = Phase("INIT")
        assert p.name == "init"

    def test_unknown_phase_raises(self):
        with pytest.raises(ValueError, match="Unknown phase"):
            Phase("nonexistent")

    def test_from_name_classmethod(self):
        p = Phase.from_name("develop")
        assert p == Phase("develop")

    def test_repr(self):
        assert repr(Phase("init")) == "Phase('init')"


class TestPhaseComparison:
    def test_equality(self):
        assert Phase("init") == Phase("init")
        assert Phase("init") != Phase("develop")

    def test_hashable(self):
        s = {Phase("init"), Phase("init"), Phase("develop")}
        assert len(s) == 2

    def test_can_use_as_dict_key(self):
        d = {Phase("init"): 1, Phase("develop"): 2}
        assert d[Phase("init")] == 1


class TestPhaseOrder:
    def test_greenfield_order(self):
        order = get_phase_order("greenfield")
        assert order[0] == "init"
        assert "design" in order
        assert "develop" in order
        assert order[-1] == "deploy"

    def test_brownfield_order(self):
        order = get_phase_order("brownfield")
        assert order[0] == "discover"
        assert order[-1] == "deliver"

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown pipeline mode"):
            get_phase_order("unknown")

    def test_phase_names_matches_greenfield(self):
        assert phase_names() == get_phase_order("greenfield")


class TestPhaseNavigation:
    def test_next_greenfield(self):
        assert Phase("init").next() == Phase("prd")
        assert Phase("prd").next() == Phase("research")
        assert Phase("research").next() == Phase("design")

    def test_prev_greenfield(self):
        assert Phase("design").prev() == Phase("research")
        assert Phase("research").prev() == Phase("prd")
        assert Phase("prd").prev() == Phase("init")

    def test_next_at_terminal_returns_none(self):
        assert Phase("deploy").next() is None

    def test_prev_at_start_returns_none(self):
        assert Phase("init").prev() is None

    def test_next_for_unordered_phase_returns_none(self):
        # "discover" is registered but not part of the greenfield order.
        assert Phase("discover").next() is None


class TestPhaseQueries:
    def test_is_init(self):
        assert Phase("init").is_init() is True
        assert Phase("develop").is_init() is False

    def test_is_start(self):
        assert Phase("init").is_start("greenfield") is True
        assert Phase("develop").is_start("greenfield") is False
        assert Phase("discover").is_start("brownfield") is True

    def test_is_terminal(self):
        assert Phase("deploy").is_terminal("greenfield") is True
        assert Phase("init").is_terminal("greenfield") is False
        assert Phase("deliver").is_terminal("brownfield") is True

    def test_list_all(self):
        phases = Phase.list_all()
        assert Phase("init") in phases
        assert Phase("develop") in phases

    def test_str_is_name(self):
        assert str(Phase("test")) == "test"
        assert str(Phase("accept")) == "accept"
