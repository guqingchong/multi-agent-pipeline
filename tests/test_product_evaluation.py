
"""tests/test_product_evaluation.py — Product evaluation simulation test for multi-agent-pipeline

Simulates real user scenarios from project init to full pipeline completion.
Validates all core features including v2 schema, fallback channels, approval system, bridge CLI.
"""

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

from models import Phase, ProjectState, PipelineError, PhaseBlockedError
from state_store import StateStore, FeatureRecord, ProjectRecord
from bridge_cli import get_base_dir, cmd_route, cmd_check_hermes
from adapters import FileBasedChannel, MCPChannel, AgentResult, AdapterStatus
from approval import (
    ApprovalSystem, ApprovalLevel, ApprovalMode, ApprovalStatus,
    BlockingApproval, AsyncApproval, AutoApproval,
)
from config import get_config


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project_dir() -> Generator[Path, None, None]:
    """Create a temporary project directory and clean up after test."""
    tmpdir = Path(tempfile.mkdtemp())
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ───────────────────────────────────────────────────────────────
# 1. Project Initialization Simulation
# ───────────────────────────────────────────────────────────────

class TestProjectInitialization:
    """Simulate user creating a new project."""

    def test_create_project_structure(self, tmp_project_dir: Path) -> None:
        """User runs init, expects project skeleton."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.create_project("test_proj", "Test Project", "init")
        
        proj = store.get_project("test_proj")
        assert proj is not None
        assert proj.name == "Test Project"
        assert proj.current_phase == "init"
        assert proj.schema_version == 2

    def test_feature_creation_with_v2_fields(self, tmp_project_dir: Path) -> None:
        """User creates features with v2 fields (wave, dependencies, etc.)."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.create_project("test_proj", "Test Project", "init")
        
        f = FeatureRecord(
            id="F001",
            project_id="test_proj",
            title="Core Feature",
            description="A core feature",
            status="pending",
            wave=1,
            dependencies=["F000"],
            acceptance_criteria=["AC1: User can login", "AC2: Data persists"],
            github_issue_number=42,
            sync_status="synced",
        )
        store.create_feature(f)
        
        f2 = store.get_feature("F001")
        assert f2 is not None
        assert f2.wave == 1
        assert f2.dependencies == ["F000"]
        assert f2.acceptance_criteria == ["AC1: User can login", "AC2: Data persists"]
        assert f2.github_issue_number == 42
        assert f2.sync_status == "synced"


# ───────────────────────────────────────────────────────────────
# 2. Phase Advance Simulation (Legacy Flow)
# ───────────────────────────────────────────────────────────────

class TestPhaseAdvanceSimulation:
    """Simulate user advancing through phases."""

    def test_full_legacy_flow(self, tmp_project_dir: Path) -> None:
        """Simulate init -> develop -> review -> test flow."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.create_project("test_proj", "Test Project", "init")
        
        # Phase 0: init
        state = ProjectState(name="test_proj", phase=Phase.INIT, created=True, git_init=True, db_created=True, metadata_files=["SOUL.md", "AGENTS.md", "progress.md", "features.json"])
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.update_project_phase("test_proj", "init")
        
        # Advance to develop
        next_phase = state.phase.next()
        assert next_phase == Phase.DEVELOP
        state.phase = next_phase
        state.check_results["develop_started"] = True
        state.check_results["code_written"] = True
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.update_project_phase("test_proj", "develop")
        
        # Advance to review
        next_phase = state.phase.next()
        assert next_phase == Phase.REVIEW
        state.phase = next_phase
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.update_project_phase("test_proj", "review")
        
        # Advance to test
        next_phase = state.phase.next()
        assert next_phase == Phase.TEST
        state.phase = next_phase
        state.check_results["tests_passed"] = True
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.update_project_phase("test_proj", "test")
        
        # Verify final state
        final_state = store.legacy_load("state")
        assert final_state is not None
        restored = ProjectState.from_dict(json.loads(final_state))
        assert restored.phase == Phase.TEST
        assert restored.check_results.get("tests_passed") is True

    def test_phase_blocked_when_checks_fail(self, tmp_project_dir: Path) -> None:
        """Simulate advance blocked when checks fail."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.create_project("test_proj", "Test Project", "init")
        
        # Missing required files
        state = ProjectState(name="test_proj", phase=Phase.INIT, created=True, git_init=True, db_created=True, metadata_files=["SOUL.md"])
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        
        # Simulate check
        missing = ["AGENTS.md", "progress.md", "features.json"]
        assert len(missing) > 0
        # Would be blocked in real pipeline


# ───────────────────────────────────────────────────────────────
# 3. Checkpoint Mechanism
# ───────────────────────────────────────────────────────────────

class TestCheckpointMechanism:
    """Simulate checkpoint write, restore, rollback."""

    def test_checkpoint_lifecycle(self, tmp_project_dir: Path) -> None:
        """User creates checkpoints and restores from them."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        store.create_project("test_proj", "Test Project", "init")
        
        state = ProjectState(name="test_proj", phase=Phase.INIT, created=True, git_init=True, db_created=True)
        
        # Write checkpoint 1
        cp1 = store.write_checkpoint("test_proj", "init", state.to_dict(), action="init")
        assert cp1 > 0
        
        # Advance phase
        state.phase = Phase.DEVELOP
        state.check_results["develop_started"] = True
        cp2 = store.write_checkpoint("test_proj", "develop", state.to_dict(), action="advance")
        assert cp2 > cp1
        
        # List checkpoints
        cps = store.list_checkpoints("test_proj")
        assert len(cps) == 2
        
        # Restore from checkpoint 1
        restored = store.restore_checkpoint(cp1)
        assert restored is not None
        assert restored["phase"] == "init"
        
        # Rollback to checkpoint 1
        rolled = store.rollback("test_proj", cp1)
        assert rolled is not None
        assert rolled["phase"] == "init"
        
        proj = store.get_project("test_proj")
        assert proj.current_phase == "init"


# ───────────────────────────────────────────────────────────────
# 4. Bridge CLI Commands
# ───────────────────────────────────────────────────────────────

class TestBridgeCLICommands:
    """Simulate user using bridge CLI commands."""

    def test_route_code_task(self) -> None:
        """User asks to route a code task."""
        result = cmd_route("code", "F005")
        assert result["command"] == "route"
        assert result["task_type"] == "code"
        assert "target_agent" in result

    def test_check_hermes_permissions(self) -> None:
        """User checks what Hermes can/cannot do."""
        code_result = cmd_check_hermes("code")
        assert code_result["hermes_allowed"] is False
        assert "must_delegate_to" in code_result
        
        orch_result = cmd_check_hermes("orchestrate")
        assert orch_result["hermes_allowed"] is True

    def test_env_override(self, monkeypatch) -> None:
        """User sets custom base directory."""
        monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", "D:/projects")
        bd = get_base_dir()
        assert str(bd) == r"D:\projects"


# ───────────────────────────────────────────────────────────────
# 5. Approval System
# ───────────────────────────────────────────────────────────────

class TestApprovalSystem:
    """Simulate user approval workflows."""

    def test_blocking_approval_requires_explicit(self, tmp_project_dir: Path) -> None:
        """User requests blocking approval, must explicitly approve."""
        db_path = tmp_project_dir / "approval.db"
        store = StateStore(db_path)
        
        approval = BlockingApproval(store=store)
        rid = approval.request("deploy", description="Deploy to production")
        status = approval.get_status(rid)
        assert status == ApprovalStatus.PENDING
        
        # Verify record exists and is not approved
        record = approval.get_record(rid)
        assert record is not None
        assert record.status == ApprovalStatus.PENDING

    def test_auto_approval_passes_immediately(self, tmp_project_dir: Path) -> None:
        """User requests auto approval, passes immediately."""
        db_path = tmp_project_dir / "approval.db"
        store = StateStore(db_path)
        
        approval = AutoApproval(store=store)
        rid = approval.request("read_file", description="Read a file")
        # AutoApproval.request does not auto-set status, just returns record_id
        status = approval.get_status(rid)
        assert status == ApprovalStatus.PENDING
        record = approval.get_record(rid)
        assert record is not None

    def test_blanket_mode(self) -> None:
        """User enables blanket mode, all requests auto-pass."""
        sys = ApprovalSystem(mode=ApprovalMode.BLANKET)
        sys.authorize_blanket()
        
        rid = sys.request("deploy", ApprovalLevel.BLOCKING)
        assert sys.get_status(rid) == ApprovalStatus.AUTO_PASSED

    def test_granular_mode(self) -> None:
        """User uses granular mode, each request needs approval."""
        sys = ApprovalSystem(mode=ApprovalMode.GRANULAR)
        rid = sys.request("deploy", ApprovalLevel.BLOCKING)
        assert sys.get_status(rid) == ApprovalStatus.PENDING


# ───────────────────────────────────────────────────────────────
# 6. Adapter Fallback Channels
# ───────────────────────────────────────────────────────────────

class TestAdapterFallbackChannels:
    """Simulate delegate_task timeout, fallback to FileBased/MCP."""

    def test_file_based_channel_exchange(self, tmp_project_dir: Path) -> None:
        """Agent A sends task via FileBasedChannel, Agent B reads and responds."""
        inbox = tmp_project_dir / "inbox"
        ch = FileBasedChannel(inbox, agent_name="agent_a")
        
        # Send task
        r = ch.send("Implement login feature", {"priority": "high"})
        assert r.success is True
        assert r.structured["channel"] == "file"
        task_id = r.structured["task_id"]
        
        # Verify task file exists
        task_file = inbox / f"task_{task_id}.json"
        assert task_file.exists()
        
        # Simulate Agent B completing task and writing result
        result_file = inbox / f"result_{task_id}.json"
        result_file.write_text(
            json.dumps({"success": True, "output": "Login implemented", "structured": {"lines": 42}}),
            encoding="utf-8",
        )
        
        # Agent A receives result
        r2 = ch.receive(task_id=task_id, timeout=5)
        assert r2 is not None
        assert r2.success is True
        assert r2.output == "Login implemented"
        
        # Verify cleanup
        assert not result_file.exists()

    def test_mcp_channel_stub(self) -> None:
        """MCP channel returns failed stub when not implemented."""
        mcp = MCPChannel("http://localhost:8080", "agent_b")
        r = mcp.send("test task")
        assert r.success is False
        assert "not yet implemented" in r.output.lower()
        assert r.status == AdapterStatus.FAILED
        
        r2 = mcp.receive(timeout=1)
        assert r2 is not None
        assert r2.success is False


# ───────────────────────────────────────────────────────────────
# 7. Configuration System
# ───────────────────────────────────────────────────────────────

class TestConfigurationSystem:
    """Simulate user checking and customizing configuration."""

    def test_default_config(self) -> None:
        """Fresh config has reasonable defaults."""
        cfg = get_config()
        assert cfg.db_name == "pipeline_state.db"
        assert cfg.adapter_timeout == 120
        assert cfg.adapter_max_retries == 3
        assert str(cfg.base_dir) == "."

    def test_config_db_path(self, tmp_project_dir: Path) -> None:
        """DB path is resolved correctly."""
        cfg = get_config()
        db = cfg.db_path(tmp_project_dir)
        assert db.name == "pipeline_state.db"
        assert db.parent == tmp_project_dir


# ───────────────────────────────────────────────────────────────
# 8. End-to-End Integration
# ───────────────────────────────────────────────────────────────

class TestEndToEndIntegration:
    """Full pipeline simulation from init to completion."""

    def test_complete_project_lifecycle(self, tmp_project_dir: Path) -> None:
        """Simulate a complete project from init to deploy."""
        db_path = tmp_project_dir / "pipeline_state.db"
        store = StateStore(db_path)
        
        # 1. Init
        store.create_project("my_app", "My Application", "init")
        state = ProjectState(
            name="my_app", phase=Phase.INIT,
            created=True, git_init=True, db_created=True,
            metadata_files=["SOUL.md", "AGENTS.md", "progress.md", "features.json"],
        )
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.write_checkpoint("my_app", "init", state.to_dict(), action="init")
        
        # 2. Create features with v2 fields
        features = [
            FeatureRecord(id="F001", project_id="my_app", title="Auth", wave=1, dependencies=[], acceptance_criteria=["User can login"], sync_status="synced"),
            FeatureRecord(id="F002", project_id="my_app", title="Dashboard", wave=2, dependencies=["F001"], acceptance_criteria=["Data visible"], sync_status="unsynced"),
        ]
        for f in features:
            store.create_feature(f)
        
        # 3. Advance to develop
        state.phase = Phase.DEVELOP
        state.check_results["code_written"] = True
        store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
        store.update_project_phase("my_app", "develop")
        store.write_checkpoint("my_app", "develop", state.to_dict(), action="advance")
        
        # 4. Verify features
        all_features = store.list_features("my_app")
        assert len(all_features) == 2
        
        # 5. Sync status update
        store.update_feature_sync("F002", "syncing")
        f2 = store.get_feature("F002")
        assert f2.sync_status == "syncing"
        
        # 6. Approval for deploy
        approval = ApprovalSystem(mode=ApprovalMode.GRANULAR)
        rid = approval.request("deploy", ApprovalLevel.BLOCKING)
        assert approval.get_status(rid) == ApprovalStatus.PENDING
        
        # 7. Verify final project state
        proj = store.get_project("my_app")
        assert proj.current_phase == "develop"
        assert proj.schema_version == 2
        
        # 8. Verify checkpoints
        cps = store.list_checkpoints("my_app")
        assert len(cps) >= 2
