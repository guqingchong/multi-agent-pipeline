"""src/approval.py — Human-in-the-loop approval system (three-level approval + summary generation)

Implements the three-level approval defined in PRD sections 11.1-11.3:
  - BlockingApproval: Agent pauses and waits, times out after 30 min then saves state
  - AsyncApproval: Agent continues other tasks, times out after 2 hours then skips
  - AutoApproval: Low-risk operations, auto-pass after 5 minutes

Supports automatic summary generation for approval context (3-5 lines with key data).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from state_store import StateStore
except ModuleNotFoundError:
    from src.state_store import StateStore


# ───────────────────────────────────────────────────────────────
# Constants / Config
# ───────────────────────────────────────────────────────────────

DEFAULT_BLOCKING_TIMEOUT = 30 * 60          # 30 分钟
DEFAULT_ASYNC_TIMEOUT = 2 * 60 * 60          # 2 小时
DEFAULT_AUTO_TIMEOUT = 5 * 60                # 5 分钟

SUMMARY_MAX_LENGTH = 200


# ───────────────────────────────────────────────────────────────
# Enums / State definitions
# ───────────────────────────────────────────────────────────────

class ApprovalLevel(Enum):
    """Approval level"""
    BLOCKING = auto()   # 阻塞式：Phase 1/5，30分钟Timeout
    ASYNC = auto()      # 异步式：依赖安装/git push，2小时Timeout
    AUTO = auto()       # 默认放行式：Low-risk operation，5 minutes then auto-pass


class ApprovalStatus(Enum):
    """Approval status"""
    PENDING = auto()    # 等待审批
    APPROVED = auto()   # Approved
    REJECTED = auto()   # Rejected
    EXPIRED = auto()    # 已Timeout
    AUTO_PASSED = auto()  # 自动放行
    SKIPPED = auto()    # 异步Timeout后跳过


# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────

@dataclass
class ApprovalContext:
    """Approval context"""
    operation: str                        # Operation description
    level: ApprovalLevel                  # Approval level
    risk: str = "low"                     # Risk level low/medium/high
    cost: float = 0.0                     # Estimated cost (USD)
    alternatives: List[str] = field(default_factory=list)  # Alternative solutions
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extra metadata


@dataclass
class ApprovalRecord:
    """Approval record"""
    id: str
    context: ApprovalContext
    status: ApprovalStatus
    created_at: float
    resolved_at: Optional[float] = None
    summary: str = ""
    project_id: str = ""
    checkpoint_id: Optional[int] = None


# ───────────────────────────────────────────────────────────────
# Summary generation
# ───────────────────────────────────────────────────────────────

def generate_summary(
    context: ApprovalContext,
    cost: float = 0.0,
    risk: str = "low",
    alternatives: Optional[List[str]] = None,
    max_length: int = SUMMARY_MAX_LENGTH,
) -> str:
    """Generate approval context auto-summary (3-5 lines with key data)

    Parameters:
        context: Approval context object or base info
        cost: Estimated cost (USD)
        risk: Risk level
        alternatives: Alternative solutions list
        max_length: Maximum length limit

    Returns:
        3-5 lines of decision summary string
    """
    lines: List[str] = []
    lines.append(f"Operation: {context.operation}")
    lines.append(f"Risk: {risk} | Est. cost: ${cost:.2f}")

    if alternatives:
        alt_str = ", ".join(alternatives[:3])
        lines.append(f"Alternatives: {alt_str}")

    # Add timeout info by level
    if context.level == ApprovalLevel.BLOCKING:
        lines.append("Timeout policy: 30 minutes then pause and save state")
    elif context.level == ApprovalLevel.ASYNC:
        lines.append("Timeout policy: 2 hours then skip this operation")
    elif context.level == ApprovalLevel.AUTO:
        lines.append("Timeout policy: 5 minutes then auto-pass")

    # Recommendation
    if risk == "high":
        lines.append("Recommendation: Please review carefully before deciding")
    elif risk == "medium":
        lines.append("Recommendation: Quick confirmation is sufficient")
    else:
        lines.append("Recommendation: Low-risk operation, can auto-pass")

    summary = "\n".join(lines)
    if len(summary) > max_length:
        summary = summary[: max_length - 3] + "..."
    return summary


# ───────────────────────────────────────────────────────────────
# Base approval class
# ───────────────────────────────────────────────────────────────

class BaseApproval:
    """Approval base class

    职责：
      1. Manage approval lifecycle（request → wait → resolve）
      2. Timeout detection
      3. State persistence（通过 state_store）
    """

    def __init__(
        self,
        level: ApprovalLevel,
        timeout: int = DEFAULT_BLOCKING_TIMEOUT,
        project_id: str = "",
        store: Optional[StateStore] = None,
    ) -> None:
        self.level = level
        self.timeout = timeout
        self.project_id = project_id
        self.store = store
        self._records: Dict[str, ApprovalRecord] = {}
        self._state_saved = False
        # Load existing records from DB if store is available
        if self.store is not None and self.project_id:
            self._load_from_db()

    def _load_from_db(self) -> None:
        """Load approval records from SQLite store into memory cache."""
        if self.store is None or not self.project_id:
            return
        db_records = self.store.list_approval_records(self.project_id)
        for rec in db_records:
            context = ApprovalContext(
                operation=rec["operation"],
                level=ApprovalLevel[rec["level"]] if rec["level"] in ApprovalLevel.__members__ else self.level,
                risk=rec["risk"],
                cost=rec["cost"],
                alternatives=rec["alternatives"],
                metadata=rec["metadata"],
            )
            record = ApprovalRecord(
                id=rec["id"],
                context=context,
                status=ApprovalStatus[rec["status"]] if rec["status"] in ApprovalStatus.__members__ else ApprovalStatus.PENDING,
                created_at=rec["created_at"],
                resolved_at=rec["resolved_at"],
                summary=rec["summary"],
                project_id=rec["project_id"],
                checkpoint_id=rec["checkpoint_id"],
            )
            self._records[record.id] = record

    def _save_to_db(self, record: ApprovalRecord) -> None:
        """Persist an approval record to the SQLite store."""
        if self.store is None or not self.project_id:
            return
        self.store.save_approval_record(
            record_id=record.id,
            project_id=record.project_id or self.project_id,
            operation=record.context.operation,
            level=record.context.level.name,
            risk=record.context.risk,
            cost=record.context.cost,
            alternatives=record.context.alternatives,
            metadata=record.context.metadata,
            status=record.status.name,
            summary=record.summary,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
            checkpoint_id=record.checkpoint_id,
        )

    def _update_status_in_db(self, record: ApprovalRecord) -> None:
        """Update an approval record's status in the SQLite store."""
        if self.store is None or not self.project_id:
            return
        self.store.update_approval_status(
            record_id=record.id,
            status=record.status.name,
            resolved_at=record.resolved_at,
            checkpoint_id=record.checkpoint_id,
        )

    # ── 核心 API ──

    def request(self, operation: str, **kwargs: Any) -> str:
        """发起审批请求，返回Approval record ID"""
        record_id = f"approval_{int(time.time() * 1000)}"
        context = ApprovalContext(
            operation=operation,
            level=self.level,
            risk=kwargs.get("risk", "low"),
            cost=kwargs.get("cost", 0.0),
            alternatives=kwargs.get("alternatives", []),
            metadata=kwargs.get("metadata", {}),
        )
        summary = generate_summary(
            context=context,
            cost=context.cost,
            risk=context.risk,
            alternatives=context.alternatives,
        )
        record = ApprovalRecord(
            id=record_id,
            context=context,
            status=ApprovalStatus.PENDING,
            created_at=time.time(),
            summary=summary,
            project_id=self.project_id,
        )
        self._records[record_id] = record
        self._save_to_db(record)
        return record_id

    def approve(self, record_id: str) -> bool:
        """批准指定Approval record"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.APPROVED
        record.resolved_at = time.time()
        self._update_status_in_db(record)
        return True

    def reject(self, record_id: str) -> bool:
        """拒绝指定Approval record"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.REJECTED
        record.resolved_at = time.time()
        self._update_status_in_db(record)
        return True

    def get_status(self, record_id: str) -> Optional[ApprovalStatus]:
        """获取Approval record状态"""
        record = self._records.get(record_id)
        if record is None:
            return None
        return record.status

    def is_expired(self, record_id: str) -> bool:
        """检查Approval record是否已Timeout"""
        record = self._records.get(record_id)
        if record is None:
            return False
        elapsed = time.time() - record.created_at
        return elapsed > self.timeout

    def get_summary(self, record_id: str) -> str:
        """Get approval summary"""
        record = self._records.get(record_id)
        if record is None:
            return ""
        return record.summary

    def get_record(self, record_id: str) -> Optional[ApprovalRecord]:
        """获取完整Approval record"""
        return self._records.get(record_id)

    def list_records(self) -> List[ApprovalRecord]:
        """列出所有Approval record"""
        return list(self._records.values())

    # ── State persistence ──

    def save_state(self, record_id: str) -> bool:
        """Timeout后将Approval status保存到 state_store"""
        if self.store is None:
            return False
        record = self._records.get(record_id)
        if record is None:
            return False
        state_dict = {
            "approval_id": record.id,
            "operation": record.context.operation,
            "level": record.context.level.name,
            "status": record.status.name,
            "created_at": record.created_at,
            "summary": record.summary,
            "project_id": record.project_id,
        }
        checkpoint_id = self.store.write_checkpoint(
            project_id=self.project_id or "default",
            phase="approval",
            state_dict=state_dict,
            feature_id=record_id,
            agent="approval_system",
            action="timeout_save",
            result=record.status.name,
        )
        record.checkpoint_id = checkpoint_id
        self._state_saved = True
        self._update_status_in_db(record)
        return True

    def load_state(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """从 checkpoint 恢复Approval status"""
        if self.store is None:
            return None
        return self.store.restore_checkpoint(checkpoint_id)


# ───────────────────────────────────────────────────────────────
# Three-level approval implementation
# ───────────────────────────────────────────────────────────────

class BlockingApproval(BaseApproval):
    """Blocking approval

    Applicable scenario：Phase 1 architecture design, Phase 5 final acceptance
    Behavior：Agent pauses and waits
    Timeout：30minutes then pause and save state
    """

    def __init__(
        self,
        timeout: int = DEFAULT_BLOCKING_TIMEOUT,
        project_id: str = "",
        store: Optional[StateStore] = None,
    ) -> None:
        super().__init__(
            level=ApprovalLevel.BLOCKING,
            timeout=timeout,
            project_id=project_id,
            store=store,
        )

    def check_and_timeout(self, record_id: str) -> Tuple[ApprovalStatus, str]:
        """Check approval status, auto-mark EXPIRED and save state after timeout"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "Record does not exist"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "Approved"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "Rejected"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.EXPIRED
            record.resolved_at = time.time()
            self.save_state(record_id)
            self._update_status_in_db(record)
            return record.status, "Timed out, state saved"

        return record.status, "Waiting for approval"


class AsyncApproval(BaseApproval):
    """Async approval

    Applicable scenario：Dependency install, git push
    Behavior：Agent continues other tasks
    Timeout：2hours then skip this operation
    """

    def __init__(
        self,
        timeout: int = DEFAULT_ASYNC_TIMEOUT,
        project_id: str = "",
        store: Optional[StateStore] = None,
    ) -> None:
        super().__init__(
            level=ApprovalLevel.ASYNC,
            timeout=timeout,
            project_id=project_id,
            store=store,
        )

    def check_and_timeout(self, record_id: str) -> Tuple[ApprovalStatus, str]:
        """Check approval status, auto-mark SKIPPED after timeout"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "Record does not exist"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "Approved"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "Rejected"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.SKIPPED
            record.resolved_at = time.time()
            self.save_state(record_id)
            self._update_status_in_db(record)
            return record.status, "Timed out and skipped, state saved"

        return record.status, "Async waiting, Agent can continue other tasks"


class AutoApproval(BaseApproval):
    """Auto approval

    Applicable scenario：Low-risk operation
    Behavior：Notify user but do not wait
    Timeout：5minutes then auto-pass
    """

    def __init__(
        self,
        timeout: int = DEFAULT_AUTO_TIMEOUT,
        project_id: str = "",
        store: Optional[StateStore] = None,
    ) -> None:
        super().__init__(
            level=ApprovalLevel.AUTO,
            timeout=timeout,
            project_id=project_id,
            store=store,
        )

    def request(self, operation: str, **kwargs: Any) -> str:
        """Initiate auto approval request, auto-pass after 5 minutes"""
        record_id = super().request(operation, **kwargs)
        # Auto-mark as pending pass
        return record_id

    def check_and_timeout(self, record_id: str) -> Tuple[ApprovalStatus, str]:
        """Check approval status, auto-mark AUTO_PASSED after timeout"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "Record does not exist"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "Approved"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "Rejected"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.AUTO_PASSED
            record.resolved_at = time.time()
            self._update_status_in_db(record)
            return record.status, "Auto-passed"

        return record.status, "Waiting for auto-pass"

    def auto_pass(self, record_id: str) -> bool:
        """Manually auto-pass immediately"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.AUTO_PASSED
        record.resolved_at = time.time()
        return True


# ───────────────────────────────────────────────────────────────
# Approval factory
# ───────────────────────────────────────────────────────────────

def create_approval(
    level: ApprovalLevel,
    timeout: Optional[int] = None,
    project_id: str = "",
    store: Optional[StateStore] = None,
) -> BaseApproval:
    """Create approval instance by level"""
    if level == ApprovalLevel.BLOCKING:
        return BlockingApproval(
            timeout=timeout or DEFAULT_BLOCKING_TIMEOUT,
            project_id=project_id,
            store=store,
        )
    if level == ApprovalLevel.ASYNC:
        return AsyncApproval(
            timeout=timeout or DEFAULT_ASYNC_TIMEOUT,
            project_id=project_id,
            store=store,
        )
    if level == ApprovalLevel.AUTO:
        return AutoApproval(
            timeout=timeout or DEFAULT_AUTO_TIMEOUT,
            project_id=project_id,
            store=store,
        )
    raise ValueError(f"Unknown approval level: {level}")


# ───────────────────────────────────────────────────────────────
# Convenience functions (for external use)
# ───────────────────────────────────────────────────────────────

def request_blocking_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, BlockingApproval]:
    """发起Blocking approval请求，返回 (record_id, approval_instance)"""
    approval = BlockingApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval


def request_async_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, AsyncApproval]:
    """发起Async approval请求，返回 (record_id, approval_instance)"""
    approval = AsyncApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval




def request_auto_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, AutoApproval]:
    """Initiate auto approval request, return (record_id, approval_instance)."""
    approval = AutoApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval


class ApprovalMode(Enum):
    """Approval mode: granular (per-operation) or blanket (user-wide authorization)."""
    GRANULAR = auto()   # Each operation requires individual approval
    BLANKET = auto()    # User grants blanket authorization; subsequent ops auto-approved


class ApprovalSystem:
    """Unified approval system supporting both granular and blanket modes.

    Responsibilities:
      1. Manage approval lifecycle (request → wait → resolve)
      2. Support Granular mode (L1/L2/L3) and Blanket mode (user-wide auth)
      3. Timeout detection and state persistence
    """

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.GRANULAR,
        project_id: str = "",
        store: Optional[StateStore] = None,
    ) -> None:
        self.mode = mode
        self.project_id = project_id
        self.store = store
        self._blanket_authorized = False
        self._records: Dict[str, ApprovalRecord] = {}

    def authorize_blanket(self) -> None:
        """Enable blanket authorization (user grants overall approval)."""
        self._blanket_authorized = True

    def revoke_blanket(self) -> None:
        """Revoke blanket authorization."""
        self._blanket_authorized = False

    
    def _save_to_db(self, record: ApprovalRecord) -> None:
        """Persist an approval record to the SQLite store."""
        if self.store is None or not self.project_id:
            return
        self.store.save_approval_record(
            record_id=record.id,
            project_id=record.project_id or self.project_id,
            operation=record.context.operation,
            level=record.context.level.name,
            risk=record.context.risk,
            cost=record.context.cost,
            alternatives=record.context.alternatives,
            metadata=record.context.metadata,
            status=record.status.name,
            summary=record.summary,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
            checkpoint_id=record.checkpoint_id,
        )
    def is_blanket_authorized(self) -> bool:
        """Return whether blanket authorization is active."""
        return self._blanket_authorized

    def request(self, operation: str, level: ApprovalLevel = ApprovalLevel.AUTO, **kwargs: Any) -> str:
        """Request approval. In BLANKET mode, auto-approve if authorized."""
        if self.mode == ApprovalMode.BLANKET and self._blanket_authorized:
            record_id = f"approval_{int(time.time() * 1000)}"
            context = ApprovalContext(
                operation=operation,
                level=level,
                risk=kwargs.get("risk", "low"),
                cost=kwargs.get("cost", 0.0),
                alternatives=kwargs.get("alternatives", []),
                metadata=kwargs.get("metadata", {}),
            )
            summary = generate_summary(context=context, cost=context.cost, risk=context.risk, alternatives=context.alternatives)
            record = ApprovalRecord(
                id=record_id,
                context=context,
                status=ApprovalStatus.AUTO_PASSED,
                created_at=time.time(),
                resolved_at=time.time(),
                summary=summary,
                project_id=self.project_id,
            )
            self._records[record_id] = record
            self._save_to_db(record)
            return record_id

        # Granular mode: delegate to factory-created approval
        approval = create_approval(level, project_id=self.project_id, store=self.store)
        record_id = approval.request(operation, **kwargs)
        self._records[record_id] = approval.get_record(record_id)  # type: ignore[arg-type]
        return record_id

    def approve(self, record_id: str) -> bool:
        """Approve a specific record."""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.APPROVED
        record.resolved_at = time.time()
        return True

    def reject(self, record_id: str) -> bool:
        """Reject a specific record."""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.REJECTED
        record.resolved_at = time.time()
        return True

    def get_status(self, record_id: str) -> Optional[ApprovalStatus]:
        """Get record status."""
        record = self._records.get(record_id)
        if record is None:
            return None
        return record.status

    def get_record(self, record_id: str) -> Optional[ApprovalRecord]:
        """Get full record."""
        return self._records.get(record_id)

    def list_records(self) -> List[ApprovalRecord]:
        """List all records."""
        return list(self._records.values())
