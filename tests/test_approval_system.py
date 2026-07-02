"""tests/test_approval_system.py — F014 人机审批分级系统单元测试

验收标准：
1. [test] 三级审批单元测试通过
2. [command] 阻塞式审批超时后暂停保存状态
3. [command] 审批摘要自动生成
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from approval import (
    ApprovalLevel,
    ApprovalStatus,
    ApprovalContext,
    ApprovalRecord,
    BaseApproval,
    BlockingApproval,
    AsyncApproval,
    AutoApproval,
    ApprovalSystem,
    ApprovalMode,
    generate_summary,
    create_approval,
    request_blocking_approval,
    request_async_approval,
    DEFAULT_BLOCKING_TIMEOUT,
    DEFAULT_ASYNC_TIMEOUT,
    DEFAULT_AUTO_TIMEOUT,
    SUMMARY_MAX_LENGTH,
)
from state_store import StateStore


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_cwd(monkeypatch) -> Generator[Path, None, None]:
    """在临时目录中运行测试"""
    tmpdir = tempfile.mkdtemp(prefix="approval_test_")
    monkeypatch.chdir(tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def store(tmp_cwd: Path) -> StateStore:
    """创建临时 StateStore"""
    db_path = tmp_cwd / "test_approval.db"
    return StateStore(db_path)


# ───────────────────────────────────────────────────────────────
# 枚举与数据模型测试
# ───────────────────────────────────────────────────────────────

class TestEnumsAndModels:
    def test_approval_level_values(self) -> None:
        assert ApprovalLevel.BLOCKING.name == "BLOCKING"
        assert ApprovalLevel.ASYNC.name == "ASYNC"
        assert ApprovalLevel.AUTO.name == "AUTO"

    def test_approval_status_values(self) -> None:
        assert ApprovalStatus.PENDING.name == "PENDING"
        assert ApprovalStatus.APPROVED.name == "APPROVED"
        assert ApprovalStatus.REJECTED.name == "REJECTED"
        assert ApprovalStatus.EXPIRED.name == "EXPIRED"
        assert ApprovalStatus.AUTO_PASSED.name == "AUTO_PASSED"
        assert ApprovalStatus.SKIPPED.name == "SKIPPED"

    def test_approval_context_defaults(self) -> None:
        ctx = ApprovalContext(operation="git push", level=ApprovalLevel.ASYNC)
        assert ctx.operation == "git push"
        assert ctx.level == ApprovalLevel.ASYNC
        assert ctx.risk == "low"
        assert ctx.cost == 0.0
        assert ctx.alternatives == []
        assert ctx.metadata == {}

    def test_approval_record_defaults(self) -> None:
        ctx = ApprovalContext(operation="test", level=ApprovalLevel.BLOCKING)
        rec = ApprovalRecord(
            id="r1",
            context=ctx,
            status=ApprovalStatus.PENDING,
            created_at=time.time(),
        )
        assert rec.resolved_at is None
        assert rec.summary == ""
        assert rec.project_id == ""
        assert rec.checkpoint_id is None


# ───────────────────────────────────────────────────────────────
# 摘要生成测试
# ───────────────────────────────────────────────────────────────

class TestGenerateSummary:
    def test_basic_summary(self) -> None:
        ctx = ApprovalContext(operation="安装依赖", level=ApprovalLevel.ASYNC)
        summary = generate_summary(ctx, cost=5.0, risk="medium")
        assert "Operation: 安装依赖" in summary
        assert "Risk: medium" in summary
        assert "Est. cost: $5.00" in summary
        assert "Timeout policy: 2 hours then skip this operation" in summary

    def test_blocking_summary(self) -> None:
        ctx = ApprovalContext(operation="Architecture design approval", level=ApprovalLevel.BLOCKING)
        summary = generate_summary(ctx, cost=12.5, risk="high")
        assert "Operation: Architecture design approval" in summary
        assert "Risk: high" in summary
        assert "Timeout policy: 30 minutes then pause and save state" in summary
        assert "Recommendation: Please review carefully before deciding" in summary

    def test_auto_summary(self) -> None:
        ctx = ApprovalContext(operation="Read file", level=ApprovalLevel.AUTO)
        summary = generate_summary(ctx, cost=0.1, risk="low")
        assert "Operation: Read file" in summary
        assert "Timeout policy: 5 minutes then auto-pass" in summary
        assert "Recommendation: Low-risk operation, can auto-pass" in summary

    def test_summary_with_alternatives(self) -> None:
        ctx = ApprovalContext(operation="部署", level=ApprovalLevel.BLOCKING)
        alts = ["staging", "canary"]
        summary = generate_summary(ctx, cost=20.0, risk="high", alternatives=alts)
        assert "Alternatives: staging, canary" in summary

    def test_summary_max_length(self) -> None:
        ctx = ApprovalContext(operation="x" * 300, level=ApprovalLevel.BLOCKING)
        summary = generate_summary(ctx, cost=0.0, risk="low", max_length=50)
        assert len(summary) <= 50

    def test_summary_lines_count(self) -> None:
        ctx = ApprovalContext(operation="test", level=ApprovalLevel.AUTO)
        summary = generate_summary(ctx, cost=1.0, risk="low")
        lines = summary.split("\n")
        assert 3 <= len(lines) <= 5


# ───────────────────────────────────────────────────────────────
# BaseApproval 基础测试
# ───────────────────────────────────────────────────────────────

class TestBaseApproval:
    def test_request_returns_id(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        assert rid.startswith("approval_")
        assert rid in ba._records

    def test_approve_success(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        assert ba.approve(rid) is True
        assert ba.get_status(rid) == ApprovalStatus.APPROVED

    def test_approve_non_pending_fails(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        ba.approve(rid)
        assert ba.approve(rid) is False

    def test_reject_success(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        assert ba.reject(rid) is True
        assert ba.get_status(rid) == ApprovalStatus.REJECTED

    def test_reject_non_pending_fails(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        ba.reject(rid)
        assert ba.reject(rid) is False

    def test_get_status_missing(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        assert ba.get_status("nonexistent") is None

    def test_is_expired_false(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        assert ba.is_expired(rid) is False

    def test_is_expired_true(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=1)
        rid = ba.request("test op")
        time.sleep(1.5)
        assert ba.is_expired(rid) is True

    def test_get_summary(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        summary = ba.get_summary(rid)
        assert "Operation: test op" in summary

    def test_get_record(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.id == rid

    def test_list_records(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid1 = ba.request("op1")
        time.sleep(0.01)
        rid2 = ba.request("op2")
        records = ba.list_records()
        assert len(records) == 2

    def test_save_state_without_store(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("test op")
        assert ba.save_state(rid) is False

    def test_load_state_without_store(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        assert ba.load_state(1) is None

    def test_save_and_load_state_with_store(self, store: StateStore) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60, store=store)
        rid = ba.request("test op")
        ba._records[rid].status = ApprovalStatus.EXPIRED
        assert ba.save_state(rid) is True
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.checkpoint_id is not None
        loaded = ba.load_state(rec.checkpoint_id)
        assert loaded is not None
        assert loaded["approval_id"] == rid
        assert loaded["operation"] == "test op"

    def test_request_with_kwargs(self) -> None:
        ba = BaseApproval(level=ApprovalLevel.BLOCKING, timeout=60)
        rid = ba.request("deploy", risk="high", cost=15.0, alternatives=["staging"])
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.context.risk == "high"
        assert rec.context.cost == 15.0
        assert rec.context.alternatives == ["staging"]


# ───────────────────────────────────────────────────────────────
# BlockingApproval 测试
# ───────────────────────────────────────────────────────────────

class TestBlockingApproval:
    def test_default_timeout(self) -> None:
        ba = BlockingApproval()
        assert ba.timeout == DEFAULT_BLOCKING_TIMEOUT
        assert ba.level == ApprovalLevel.BLOCKING

    def test_custom_timeout(self) -> None:
        ba = BlockingApproval(timeout=120)
        assert ba.timeout == 120

    def test_check_pending(self) -> None:
        ba = BlockingApproval(timeout=60)
        rid = ba.request("Architecture design approval")
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.PENDING
        assert "Waiting for approval" in msg

    def test_check_approved(self) -> None:
        ba = BlockingApproval(timeout=60)
        rid = ba.request("Architecture design approval")
        ba.approve(rid)
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.APPROVED
        assert "Approved" in msg

    def test_check_rejected(self) -> None:
        ba = BlockingApproval(timeout=60)
        rid = ba.request("Architecture design approval")
        ba.reject(rid)
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.REJECTED
        assert "Rejected" in msg

    def test_check_expired_saves_state(self, store: StateStore) -> None:
        ba = BlockingApproval(timeout=1, store=store)
        rid = ba.request("Architecture design approval")
        time.sleep(1.5)
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.EXPIRED
        assert "Timed out" in msg
        assert "state saved" in msg
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.checkpoint_id is not None

    def test_check_nonexistent(self) -> None:
        ba = BlockingApproval(timeout=60)
        status, msg = ba.check_and_timeout("nonexistent")
        assert status == ApprovalStatus.REJECTED
        assert "Record does not exist" in msg

    def test_timeout_with_project_id(self, store: StateStore) -> None:
        ba = BlockingApproval(timeout=1, project_id="proj1", store=store)
        rid = ba.request("Architecture design approval")
        time.sleep(1.5)
        ba.check_and_timeout(rid)
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.checkpoint_id is not None
        loaded = ba.load_state(rec.checkpoint_id)
        assert loaded is not None
        assert loaded["project_id"] == "proj1"


# ───────────────────────────────────────────────────────────────
# AsyncApproval 测试
# ───────────────────────────────────────────────────────────────

class TestAsyncApproval:
    def test_default_timeout(self) -> None:
        aa = AsyncApproval()
        assert aa.timeout == DEFAULT_ASYNC_TIMEOUT
        assert aa.level == ApprovalLevel.ASYNC

    def test_custom_timeout(self) -> None:
        aa = AsyncApproval(timeout=300)
        assert aa.timeout == 300

    def test_check_pending(self) -> None:
        aa = AsyncApproval(timeout=60)
        rid = aa.request("git push")
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.PENDING
        assert "Async waiting" in msg

    def test_check_approved(self) -> None:
        aa = AsyncApproval(timeout=60)
        rid = aa.request("git push")
        aa.approve(rid)
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.APPROVED

    def test_check_skipped_after_timeout(self, store: StateStore) -> None:
        aa = AsyncApproval(timeout=1, store=store)
        rid = aa.request("git push")
        time.sleep(1.5)
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.SKIPPED
        assert "Timed out and skipped" in msg
        assert "state saved" in msg

    def test_check_nonexistent(self) -> None:
        aa = AsyncApproval(timeout=60)
        status, msg = aa.check_and_timeout("nonexistent")
        assert status == ApprovalStatus.REJECTED
        assert "Record does not exist" in msg


# ───────────────────────────────────────────────────────────────
# AutoApproval 测试
# ───────────────────────────────────────────────────────────────

class TestAutoApproval:
    def test_default_timeout(self) -> None:
        aa = AutoApproval()
        assert aa.timeout == DEFAULT_AUTO_TIMEOUT
        assert aa.level == ApprovalLevel.AUTO

    def test_custom_timeout(self) -> None:
        aa = AutoApproval(timeout=30)
        assert aa.timeout == 30

    def test_check_pending(self) -> None:
        aa = AutoApproval(timeout=60)
        rid = aa.request("读取文件")
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.PENDING
        assert "Waiting for auto-pass" in msg

    def test_check_auto_passed_after_timeout(self) -> None:
        aa = AutoApproval(timeout=1)
        rid = aa.request("读取文件")
        time.sleep(1.5)
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.AUTO_PASSED
        assert "Auto-passed" in msg

    def test_manual_auto_pass(self) -> None:
        aa = AutoApproval(timeout=60)
        rid = aa.request("读取文件")
        assert aa.auto_pass(rid) is True
        assert aa.get_status(rid) == ApprovalStatus.AUTO_PASSED

    def test_manual_auto_pass_non_pending_fails(self) -> None:
        aa = AutoApproval(timeout=60)
        rid = aa.request("读取文件")
        aa.auto_pass(rid)
        assert aa.auto_pass(rid) is False

    def test_check_approved(self) -> None:
        aa = AutoApproval(timeout=60)
        rid = aa.request("读取文件")
        aa.approve(rid)
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.APPROVED

    def test_check_nonexistent(self) -> None:
        aa = AutoApproval(timeout=60)
        status, msg = aa.check_and_timeout("nonexistent")
        assert status == ApprovalStatus.REJECTED
        assert "Record does not exist" in msg


# ───────────────────────────────────────────────────────────────
# 工厂函数测试
# ───────────────────────────────────────────────────────────────

class TestCreateApproval:
    def test_create_blocking(self) -> None:
        a = create_approval(ApprovalLevel.BLOCKING)
        assert isinstance(a, BlockingApproval)
        assert a.timeout == DEFAULT_BLOCKING_TIMEOUT

    def test_create_async(self) -> None:
        a = create_approval(ApprovalLevel.ASYNC)
        assert isinstance(a, AsyncApproval)
        assert a.timeout == DEFAULT_ASYNC_TIMEOUT

    def test_create_auto(self) -> None:
        a = create_approval(ApprovalLevel.AUTO)
        assert isinstance(a, AutoApproval)
        assert a.timeout == DEFAULT_AUTO_TIMEOUT

    def test_create_with_custom_timeout(self) -> None:
        a = create_approval(ApprovalLevel.BLOCKING, timeout=120)
        assert a.timeout == 120

    def test_create_with_store(self, store: StateStore) -> None:
        a = create_approval(ApprovalLevel.BLOCKING, store=store)
        assert a.store is store

    def test_create_unknown_level(self) -> None:
        with pytest.raises(ValueError):
            create_approval("unknown")  # type: ignore[arg-type]


# ───────────────────────────────────────────────────────────────
# 便捷函数测试
# ───────────────────────────────────────────────────────────────

class TestConvenienceFunctions:
    def test_request_blocking_approval(self, store: StateStore) -> None:
        rid, approval = request_blocking_approval("架构设计", store=store)
        assert isinstance(approval, BlockingApproval)
        assert rid in approval._records
        assert approval.get_record(rid) is not None

    def test_request_async_approval(self, store: StateStore) -> None:
        rid, approval = request_async_approval("git push", store=store)
        assert isinstance(approval, AsyncApproval)
        assert rid in approval._records

    def test_request_auto_approval(self, store: StateStore) -> None:
        approval = AutoApproval(store=store)
        rid = approval.request("读取文件")
        assert isinstance(approval, AutoApproval)
        assert rid in approval._records

    def test_request_blocking_with_kwargs(self, store: StateStore) -> None:
        rid, approval = request_blocking_approval(
            "部署", store=store, risk="high", cost=20.0
        )
        rec = approval.get_record(rid)
        assert rec is not None
        assert rec.context.risk == "high"
        assert rec.context.cost == 20.0


# ───────────────────────────────────────────────────────────────
# 集成测试：端到端场景
# ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_blocking_full_lifecycle(self, store: StateStore) -> None:
        """阻塞式审批完整生命周期：请求 → 等待 → 超时 → 保存状态"""
        ba = BlockingApproval(timeout=1, project_id="p1", store=store)
        rid = ba.request("Phase 1 架构设计", risk="high", cost=10.0)

        # 初始状态
        assert ba.get_status(rid) == ApprovalStatus.PENDING
        assert "Phase 1 架构设计" in ba.get_summary(rid)

        # 等待超时
        time.sleep(1.5)
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.EXPIRED
        assert ba._state_saved is True

        # 验证 checkpoint
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.checkpoint_id is not None
        loaded = ba.load_state(rec.checkpoint_id)
        assert loaded is not None
        assert loaded["level"] == "BLOCKING"
        assert loaded["status"] == "EXPIRED"
        assert loaded["project_id"] == "p1"

    def test_async_full_lifecycle(self, store: StateStore) -> None:
        """异步式审批完整生命周期：请求 → 等待 → 超时 → 跳过"""
        aa = AsyncApproval(timeout=1, project_id="p2", store=store)
        rid = aa.request("安装依赖", risk="medium", cost=5.0)

        assert aa.get_status(rid) == ApprovalStatus.PENDING
        time.sleep(1.5)
        status, msg = aa.check_and_timeout(rid)
        assert status == ApprovalStatus.SKIPPED
        assert aa._state_saved is True

    def test_auto_full_lifecycle(self) -> None:
        """默认放行式审批完整生命周期：请求 → 等待 → 自动放行"""
        auto = AutoApproval(timeout=1)
        rid = auto.request("读取日志", risk="low", cost=0.5)

        assert auto.get_status(rid) == ApprovalStatus.PENDING
        time.sleep(1.5)
        status, msg = auto.check_and_timeout(rid)
        assert status == ApprovalStatus.AUTO_PASSED

    def test_manual_approve_blocking(self) -> None:
        """阻塞式审批手动批准"""
        ba = BlockingApproval(timeout=60)
        rid = ba.request("Phase 5 验收")
        ba.approve(rid)
        status, msg = ba.check_and_timeout(rid)
        assert status == ApprovalStatus.APPROVED
        assert "Approved" in msg

    def test_multiple_records(self) -> None:
        """同一审批器管理多个记录"""
        ba = BlockingApproval(timeout=60)
        rid1 = ba.request("操作1")
        time.sleep(0.01)
        rid2 = ba.request("操作2")
        time.sleep(0.01)
        rid3 = ba.request("操作3")

        ba.approve(rid1)
        ba.reject(rid2)

        assert ba.get_status(rid1) == ApprovalStatus.APPROVED
        assert ba.get_status(rid2) == ApprovalStatus.REJECTED
        assert ba.get_status(rid3) == ApprovalStatus.PENDING

        records = ba.list_records()
        assert len(records) == 3

    def test_summary_contains_key_data(self) -> None:
        """摘要包含关键数据（成本、风险、替代方案）"""
        ctx = ApprovalContext(
            operation="合并到主分支",
            level=ApprovalLevel.BLOCKING,
            risk="high",
            cost=25.0,
            alternatives=["feature branch", "staging"],
        )
        summary = generate_summary(ctx, cost=25.0, risk="high", alternatives=ctx.alternatives)
        assert "合并到主分支" in summary
        assert "high" in summary
        assert "$25.00" in summary
        assert "feature branch" in summary
        assert "staging" in summary

    def test_approval_level_from_request(self) -> None:
        """审批记录保留正确的级别信息"""
        ba = BlockingApproval(timeout=60)
        rid = ba.request("test")
        rec = ba.get_record(rid)
        assert rec is not None
        assert rec.context.level == ApprovalLevel.BLOCKING

        aa = AsyncApproval(timeout=60)
        rid2 = aa.request("test2")
        rec2 = aa.get_record(rid2)
        assert rec2 is not None
        assert rec2.context.level == ApprovalLevel.ASYNC

        au = AutoApproval(timeout=60)
        rid3 = au.request("test3")
        rec3 = au.get_record(rid3)
        assert rec3 is not None
        assert rec3.context.level == ApprovalLevel.AUTO
