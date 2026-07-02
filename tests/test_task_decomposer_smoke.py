"""Smoke tests for W3-E04: task_decomposer.py

Covers:
  - Feature: to_dict/from_dict round-trip
  - FeaturesManifest: to_dict/from_dict
  - TokenBudget: limit, spent, check(), charge(), remaining, usage_ratio, can_allocate()
  - DAGScheduler: topological_sort, validate_dag, compute_execution_order
  - Cycle detection
  - TaskDecomposer: heuristic PRD parsing (decompose_heuristic)
  - Budget enforcement in check_budget()
  - generate_features_json() with validation
  - Wave computation
  - decompose_prd_to_features convenience
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from task_decomposer import (
    Feature,
    FeaturesManifest,
    TokenBudget,
    DAGScheduler,
    TaskDecomposer,
    decompose_prd_to_features,
)


# ── Feature ──────────────────────────────────────────────────────

class TestFeature:
    def test_defaults(self):
        f = Feature()
        assert f.id == ""
        assert f.title == ""
        assert f.estimated_complexity == "medium"
        assert f.owner_agent == "claude-code"
        assert f.reviewer_agent == "codewhale"
        assert f.tester_agent == "qwen-code"
        assert f.status == "pending"
        assert f.wave == 0
        assert f.expected_lines == 200
        assert f.max_token_budget == 0
        assert f.dependencies == []
        assert f.files_changed == []
        assert f.context == {}

    def test_to_dict(self):
        f = Feature(id="W3-E02", title="Test Feature", expected_lines=250)
        d = f.to_dict()
        assert d["id"] == "W3-E02"
        assert d["title"] == "Test Feature"
        assert d["expected_lines"] == 250
        assert d["estimated_complexity"] == "medium"

    def test_to_dict_max_token_budget_zero_omitted(self):
        f = Feature(id="F001", max_token_budget=0)
        d = f.to_dict()
        assert "max_token_budget" not in d

    def test_to_dict_max_token_budget_included(self):
        f = Feature(id="F001", max_token_budget=10000)
        d = f.to_dict()
        assert "max_token_budget" in d
        assert d["max_token_budget"] == 10000

    def test_to_dict_empty_context_omitted(self):
        f = Feature(id="F001")
        d = f.to_dict()
        assert "context" not in d

    def test_to_dict_nonempty_context_included(self):
        f = Feature(id="F001", context={"key": "val"})
        d = f.to_dict()
        assert d["context"] == {"key": "val"}

    def test_from_dict(self):
        data = {
            "id": "W3-E04",
            "title": "Task Decomposer",
            "description": "Generates features.json",
            "dependencies": ["W3-E01"],
            "estimated_complexity": "complex",
            "wave": 3,
            "expected_lines": 300,
        }
        f = Feature.from_dict(data)
        assert f.id == "W3-E04"
        assert f.estimated_complexity == "complex"
        assert f.dependencies == ["W3-E01"]
        assert f.wave == 3

    def test_round_trip(self):
        f1 = Feature(
            id="W3-E02",
            title="Condition Engine",
            description="Dynamic branching",
            dependencies=["W3-E01"],
            estimated_complexity="medium",
            wave=3,
            expected_lines=180,
            max_token_budget=30000,
            files_changed=["src/condition_engine.py"],
            context={"language": "python"},
        )
        f2 = Feature.from_dict(f1.to_dict())
        assert f2.id == f1.id
        assert f2.title == f1.title
        assert f2.dependencies == f1.dependencies
        assert f2.expected_lines == f1.expected_lines
        assert f2.max_token_budget == f1.max_token_budget
        assert f2.files_changed == f1.files_changed
        assert f2.context == f1.context


# ── FeaturesManifest ─────────────────────────────────────────────

class TestFeaturesManifest:
    def test_defaults(self):
        m = FeaturesManifest()
        assert m.project == ""
        assert m.version == "1.0.0"
        assert m.features == []
        assert m.generated_by == "task_decomposer"
        assert m.generation_date == ""

    def test_to_dict(self):
        f = Feature(id="F001", title="Test")
        m = FeaturesManifest(project="test-proj", features=[f])
        d = m.to_dict()
        assert d["project"] == "test-proj"
        assert len(d["features"]) == 1
        assert d["features"][0]["id"] == "F001"

    def test_from_dict(self):
        data = {
            "project": "demo",
            "version": "2.0.0",
            "features": [{"id": "F001", "title": "Feature 1"}],
            "waves": {"1": {"features": ["F001"], "status": "pending"}},
        }
        m = FeaturesManifest.from_dict(data)
        assert m.project == "demo"
        assert len(m.features) == 1
        assert m.features[0].id == "F001"
        assert "1" in m.waves

    def test_round_trip(self):
        f = Feature(id="W3-E02", title="CondEngine")
        m1 = FeaturesManifest(
            project="test",
            version="1.0.0",
            features=[f],
            waves={"1": {"features": ["W3-E02"], "status": "pending"}},
            generation_date="2026-07-01T00:00:00Z",
        )
        m2 = FeaturesManifest.from_dict(m1.to_dict())
        assert m2.project == m1.project
        assert len(m2.features) == 1
        assert m2.features[0].id == "W3-E02"


# ── TokenBudget ──────────────────────────────────────────────────

class TestTokenBudget:
    def test_defaults(self):
        tb = TokenBudget()
        assert tb.limit == 0
        assert tb.spent == 0
        assert tb.remaining == -1  # unlimited
        assert tb.usage_ratio == 0.0
        assert tb.check() == "ok"

    def test_check_levels(self):
        # unlimited
        tb = TokenBudget(limit=0)
        assert tb.check() == "ok"

        # under 80%
        tb = TokenBudget(limit=1000, spent=500)
        assert tb.check() == "ok"

        # warning (80%)
        tb = TokenBudget(limit=1000, spent=800)
        assert tb.check() == "warning"

        # soft_cap (100%)
        tb = TokenBudget(limit=1000, spent=1000)
        assert tb.check() == "soft_cap"

        # hard_cap (150%)
        tb = TokenBudget(limit=1000, spent=1500)
        assert tb.check() == "hard_cap"

    def test_remaining(self):
        tb = TokenBudget(limit=1000, spent=600)
        assert tb.remaining == 400

        tb = TokenBudget(limit=1000, spent=1200)
        assert tb.remaining == 0  # floor at 0

    def test_usage_ratio(self):
        tb = TokenBudget(limit=1000, spent=250)
        assert tb.usage_ratio == 0.25

    def test_can_allocate(self):
        tb = TokenBudget(limit=1000, spent=500)
        assert tb.can_allocate(400) is True  # 900 <= 1500
        assert tb.can_allocate(1100) is False  # 1600 > 1500

        # unlimited
        tb2 = TokenBudget(limit=0)
        assert tb2.can_allocate(999999) is True

    def test_charge(self):
        tb = TokenBudget(limit=1000, spent=700)
        status = tb.charge(200)
        assert status == "warning"  # 900 → >= 80%
        assert tb.spent == 900

    def test_to_dict(self):
        tb = TokenBudget(limit=5000, spent=1000)
        d = tb.to_dict()
        assert d["limit"] == 5000
        assert d["spent"] == 1000
        assert d["remaining"] == 4000
        assert "status" in d


# ── DAGScheduler ─────────────────────────────────────────────────

class TestDAGScheduler:
    def test_empty(self):
        sorted_f, cycles = DAGScheduler.topological_sort([])
        assert sorted_f == []
        assert cycles == []

    def test_simple_linear_chain(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        f_c = Feature(id="C", dependencies=["B"])

        sorted_f, cycles = DAGScheduler.topological_sort([f_c, f_b, f_a])
        assert cycles == []
        ids = [f.id for f in sorted_f]
        assert ids.index("A") < ids.index("B")
        assert ids.index("B") < ids.index("C")

    def test_parallel_dependencies(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        f_c = Feature(id="C", dependencies=["A"])
        f_d = Feature(id="D", dependencies=["B", "C"])

        sorted_f, cycles = DAGScheduler.topological_sort([f_d, f_c, f_b, f_a])
        assert cycles == []
        ids = [f.id for f in sorted_f]
        assert ids[0] == "A"
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")

    def test_cycle_detection(self):
        f_a = Feature(id="A", dependencies=["B"])
        f_b = Feature(id="B", dependencies=["A"])

        sorted_f, cycles = DAGScheduler.topological_sort([f_a, f_b])
        assert "A" in cycles or "B" in cycles

    def test_self_dependency_detection(self):
        f = Feature(id="A", dependencies=["A"])
        valid, errors = DAGScheduler.validate_dag([f])
        assert not valid
        assert any("itself" in e for e in errors)

    def test_unknown_dependency(self):
        f = Feature(id="A", dependencies=["B"])  # B not in feature list
        valid, errors = DAGScheduler.validate_dag([f])
        assert not valid
        assert any("unknown" in e.lower() for e in errors)

    def test_empty_id(self):
        f = Feature(id="", dependencies=[])
        valid, errors = DAGScheduler.validate_dag([f])
        assert not valid
        assert any("empty id" in e.lower() for e in errors)

    def test_validate_dag_valid(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        valid, errors = DAGScheduler.validate_dag([f_a, f_b])
        assert valid
        assert errors == []

    def test_compute_execution_order_simple(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        waves = DAGScheduler.compute_execution_order([f_a, f_b])
        assert len(waves) == 2
        assert [f.id for f in waves[0]] == ["A"]
        assert [f.id for f in waves[1]] == ["B"]

    def test_compute_execution_order_parallel(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        f_c = Feature(id="C", dependencies=["A"])
        waves = DAGScheduler.compute_execution_order([f_a, f_b, f_c])
        assert len(waves) == 2
        assert all(f.id in ["B", "C"] for f in waves[1])

    def test_compute_execution_order_empty(self):
        assert DAGScheduler.compute_execution_order([]) == []


# ── TaskDecomposer Heuristic ─────────────────────────────────────

SAMPLE_PRD = """# Multi-Agent Pipeline PRD

## Wave 1: Core Features

### W1-C01: Implement context manager
Context management for the multi-agent pipeline.

Dependencies: none
Complexity: simple

### W1-C02: Implement condition engine
Dynamic branching based on context.

Depends on: none
Complexity: medium
Expected lines: 250

## Wave 2: Advanced Features

### W2-A01: Task Decomposer
Task decomposition with DAG scheduling.

Depends on: W1-C01, W1-C02
Complexity: complex
Expected lines: 300
"""

class TestHeuristicDecomposition:
    def test_decompose_heuristic_extracts_features(self):
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic(SAMPLE_PRD)
        assert len(features) >= 2

        ids = [f.id for f in features]
        assert "W1-C01" in ids
        assert "W1-C02" in ids

    def test_decompose_heuristic_wave_assignment(self):
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic(SAMPLE_PRD)
        w1_features = [f for f in features if f.wave == 1]
        assert len(w1_features) >= 1

    def test_decompose_heuristic_complexity_detection(self):
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic(SAMPLE_PRD)
        # W2-A01 should be complex
        complex_feats = [f for f in features if f.estimated_complexity == "complex"]
        assert len(complex_feats) >= 1

    def test_decompose_heuristic_extracts_dependencies(self):
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic(SAMPLE_PRD)
        id_map = {f.id: f for f in features}
        if "W2-A01" in id_map:
            deps = id_map["W2-A01"].dependencies
            assert "W1-C01" in deps or "W1-C02" in deps

    def test_decompose_heuristic_assigns_agents(self):
        decomposer = TaskDecomposer(
            project_name="test",
            default_agents={"owner": "hermes", "reviewer": "claude-code", "tester": "codewhale"},
        )
        features = decomposer.decompose_heuristic("## Feature F001\nTest feature")
        for f in features:
            assert f.owner_agent == "hermes"
            assert f.reviewer_agent == "claude-code"

    def test_decompose_empty_prd_creates_fallback(self):
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic("just some text with no structure")
        assert len(features) >= 1  # should create at least F001

    def test_decompose_with_fallback_sections(self):
        prd = """## Architecture
Some architecture text.

## Implementation
Implement the core module.

## Testing
Write tests.
"""
        decomposer = TaskDecomposer(project_name="test")
        features = decomposer.decompose_heuristic(prd)
        assert len(features) >= 1
        # section headers are used to create feature IDs like F001, F002, etc.
        ids = [f.id for f in features]
        assert all(id.startswith("F") for id in ids)

    def test_decompose_from_prd_file(self):
        prd_path = Path(tempfile.mktemp(suffix=".md"))
        prd_path.write_text(SAMPLE_PRD, encoding="utf-8")
        try:
            decomposer = TaskDecomposer(project_name="test")
            features = decomposer.decompose_from_prd(prd_path)
            assert len(features) >= 2
        finally:
            prd_path.unlink(missing_ok=True)

    def test_decompose_from_prd_nonexistent(self):
        decomposer = TaskDecomposer(project_name="test")
        with pytest.raises(FileNotFoundError):
            decomposer.decompose_from_prd(Path("/nonexistent/prd.md"))


# ── Topological Sort via TaskDecomposer ──────────────────────────

class TestDecomposerTopoSort:
    def test_sort(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        f_c = Feature(id="C", dependencies=["B"])
        decomposer = TaskDecomposer(project_name="test")
        sorted_f, cycles = decomposer.topological_sort([f_c, f_b, f_a])
        assert cycles == []
        ids = [f.id for f in sorted_f]
        assert ids == ["A", "B", "C"]

    def test_compute_waves(self):
        f_a = Feature(id="A", dependencies=[])
        f_b = Feature(id="B", dependencies=["A"])
        f_c = Feature(id="C", dependencies=["A"])
        f_d = Feature(id="D", dependencies=["B", "C"])
        decomposer = TaskDecomposer(project_name="test")
        sorted_f, _ = decomposer.topological_sort([f_d, f_c, f_b, f_a])
        waves = decomposer.compute_waves(sorted_f)
        assert len(waves) == 3
        assert [f.id for f in waves[0]] == ["A"]


# ── Budget Enforcement ───────────────────────────────────────────

class TestBudgetEnforcement:
    def test_check_budget_within_limit(self):
        features = [
            Feature(id="A", max_token_budget=10000),
            Feature(id="B", max_token_budget=20000),
        ]
        decomposer = TaskDecomposer(project_name="test", total_token_budget=100000)
        ok, msg = decomposer.check_budget(features)
        assert ok
        assert "OK" in msg

    def test_check_budget_exceeds_limit(self):
        features = [
            Feature(id="A", max_token_budget=60000),
            Feature(id="B", max_token_budget=60000),
        ]
        decomposer = TaskDecomposer(project_name="test", total_token_budget=100000)
        ok, msg = decomposer.check_budget(features)
        assert not ok
        assert "exceeds" in msg

    def test_check_budget_warning_threshold(self):
        features = [
            Feature(id="A", max_token_budget=85000),
        ]
        decomposer = TaskDecomposer(project_name="test", total_token_budget=100000)
        ok, msg = decomposer.check_budget(features)
        assert ok
        assert "WARNING" in msg

    def test_check_budget_no_limit(self):
        decomposer = TaskDecomposer(project_name="test", total_token_budget=0)
        features = [
            Feature(id="A", max_token_budget=999999),
        ]
        ok, msg = decomposer.check_budget(features)
        assert ok
        assert "no project limit" in msg

    def test_check_budget_zero_tokens_allocated(self):
        decomposer = TaskDecomposer(project_name="test", total_token_budget=100000)
        features = [
            Feature(id="A", max_token_budget=0),  # zero means not counted
        ]
        ok, msg = decomposer.check_budget(features)
        assert ok
        # 0 tokens allocated, should be "no project limit" but total_token_budget>0
        assert "OK" in msg


# ── Validation ───────────────────────────────────────────────────

class TestValidation:
    def test_validate_valid_features(self):
        f1 = Feature(id="A", title="Feature A", dependencies=[])
        f2 = Feature(id="B", title="Feature B", dependencies=["A"])
        decomposer = TaskDecomposer(project_name="test", total_token_budget=100000)
        valid, errors = decomposer.validate([f1, f2])
        assert valid
        assert errors == []

    def test_validate_duplicate_id(self):
        f1 = Feature(id="A", title="T1")
        f2 = Feature(id="A", title="T2")
        decomposer = TaskDecomposer(project_name="test")
        valid, errors = decomposer.validate([f1, f2])
        assert not valid
        assert any("duplicate" in e.lower() for e in errors)

    def test_validate_missing_title(self):
        f = Feature(id="A", title="")
        decomposer = TaskDecomposer(project_name="test")
        valid, errors = decomposer.validate([f])
        assert not valid
        assert any("title" in e.lower() for e in errors)

    def test_validate_empty_id(self):
        f = Feature(id="", title="No ID")
        decomposer = TaskDecomposer(project_name="test")
        valid, errors = decomposer.validate([f])
        assert not valid


# ── generate_features_json ───────────────────────────────────────

class TestGenerateFeaturesJson:
    def test_generates_valid_json(self):
        f1 = Feature(id="A", title="Feature A", dependencies=[])
        f2 = Feature(id="B", title="Feature B", dependencies=["A"])
        decomposer = TaskDecomposer(project_name="test-proj", total_token_budget=100000)
        output_path = Path(tempfile.mktemp(suffix=".json"))
        try:
            manifest = decomposer.generate_features_json(
                [f2, f1], output_path  # unsorted — should be topo-sorted
            )
            assert output_path.exists()
            data = json.loads(output_path.read_text(encoding="utf-8"))
            assert data["project"] == "test-proj"
            assert len(data["features"]) == 2
            # Should be topologically sorted: A before B
            ids = [f["id"] for f in data["features"]]
            assert ids.index("A") < ids.index("B")
        finally:
            output_path.unlink(missing_ok=True)

    def test_raises_on_validation_failure(self):
        f = Feature(id="A", title="")  # missing title
        decomposer = TaskDecomposer(project_name="test")
        output_path = Path(tempfile.mktemp(suffix=".json"))
        try:
            with pytest.raises(ValueError):
                decomposer.generate_features_json([f], output_path)
        finally:
            output_path.unlink(missing_ok=True)

    def test_waves_in_output(self):
        f1 = Feature(id="A", title="Feature A", wave=1)
        f2 = Feature(id="B", title="Feature B", wave=2)
        decomposer = TaskDecomposer(project_name="test")
        output_path = Path(tempfile.mktemp(suffix=".json"))
        try:
            manifest = decomposer.generate_features_json([f1, f2], output_path)
            assert "1" in manifest.waves or "2" in manifest.waves
            data = json.loads(output_path.read_text(encoding="utf-8"))
            assert "waves" in data
        finally:
            output_path.unlink(missing_ok=True)


# ── Convenience Function ─────────────────────────────────────────

class TestConvenienceFunction:
    def test_decompose_prd_to_features(self):
        prd_path = Path(tempfile.mktemp(suffix=".md"))
        prd_path.write_text(SAMPLE_PRD, encoding="utf-8")
        output_path = Path(tempfile.mktemp(suffix=".json"))
        try:
            features = decompose_prd_to_features(
                prd_path=prd_path,
                output_path=output_path,
                project_name="demo",
                total_token_budget=1_000_000,
            )
            assert len(features) >= 2
            assert output_path.exists()
            data = json.loads(output_path.read_text(encoding="utf-8"))
            assert data["project"] == "demo"
            assert len(data["features"]) >= 2
        finally:
            prd_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
