"""src/approval.py — 人机审批分级系统（三级审批+摘要生成）

实现 PRD 11.1-11.3 节定义的三级审批：
  - 阻塞式（BlockingApproval）：Agent 暂停等待，30分钟超时后暂停保存状态
  - 异步式（AsyncApproval）：Agent 继续做其他任务，2小时超时后跳过该操作
  - 默认放行式（AutoApproval）：低风险操作，5分钟自动放行

支持审批上下文自动摘要生成（3-5行，包含关键数据）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from state_store import StateStore


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DEFAULT_BLOCKING_TIMEOUT = 30 * 60          # 30 分钟
DEFAULT_ASYNC_TIMEOUT = 2 * 60 * 60          # 2 小时
DEFAULT_AUTO_TIMEOUT = 5 * 60                # 5 分钟

SUMMARY_MAX_LENGTH = 200


# ───────────────────────────────────────────────────────────────
# 枚举 / 状态定义
# ───────────────────────────────────────────────────────────────

class ApprovalLevel(Enum):
    """审批级别"""
    BLOCKING = auto()   # 阻塞式：Phase 1/5，30分钟超时
    ASYNC = auto()      # 异步式：依赖安装/git push，2小时超时
    AUTO = auto()       # 默认放行式：低风险操作，5分钟自动放行


class ApprovalStatus(Enum):
    """审批状态"""
    PENDING = auto()    # 等待审批
    APPROVED = auto()   # 已批准
    REJECTED = auto()   # 已拒绝
    EXPIRED = auto()    # 已超时
    AUTO_PASSED = auto()  # 自动放行
    SKIPPED = auto()    # 异步超时后跳过


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class ApprovalContext:
    """审批上下文"""
    operation: str                        # 操作描述
    level: ApprovalLevel                  # 审批级别
    risk: str = "low"                     # 风险等级 low/medium/high
    cost: float = 0.0                     # 预估成本（USD）
    alternatives: List[str] = field(default_factory=list)  # 替代方案
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据


@dataclass
class ApprovalRecord:
    """审批记录"""
    id: str
    context: ApprovalContext
    status: ApprovalStatus
    created_at: float
    resolved_at: Optional[float] = None
    summary: str = ""
    project_id: str = ""
    checkpoint_id: Optional[int] = None


# ───────────────────────────────────────────────────────────────
# 摘要生成
# ───────────────────────────────────────────────────────────────

def generate_summary(
    context: ApprovalContext,
    cost: float = 0.0,
    risk: str = "low",
    alternatives: Optional[List[str]] = None,
    max_length: int = SUMMARY_MAX_LENGTH,
) -> str:
    """生成审批上下文自动摘要（3-5行，包含关键数据）

    参数:
        context: 审批上下文对象或基础信息
        cost: 预估成本（USD）
        risk: 风险等级
        alternatives: 替代方案列表
        max_length: 最大长度限制

    返回:
        3-5 行的决策摘要字符串
    """
    lines: List[str] = []
    lines.append(f"操作: {context.operation}")
    lines.append(f"风险: {risk} | 预估成本: ${cost:.2f}")

    if alternatives:
        alt_str = ", ".join(alternatives[:3])
        lines.append(f"替代方案: {alt_str}")

    # 根据级别添加超时信息
    if context.level == ApprovalLevel.BLOCKING:
        lines.append("超时策略: 30分钟后暂停保存状态")
    elif context.level == ApprovalLevel.ASYNC:
        lines.append("超时策略: 2小时后跳过该操作")
    elif context.level == ApprovalLevel.AUTO:
        lines.append("超时策略: 5分钟后自动放行")

    # 推荐操作
    if risk == "high":
        lines.append("推荐: 请仔细审查后决定")
    elif risk == "medium":
        lines.append("推荐: 快速确认即可")
    else:
        lines.append("推荐: 低风险操作，可自动放行")

    summary = "\n".join(lines)
    if len(summary) > max_length:
        summary = summary[: max_length - 3] + "..."
    return summary


# ───────────────────────────────────────────────────────────────
# 基础审批类
# ───────────────────────────────────────────────────────────────

class BaseApproval:
    """审批基类

    职责：
      1. 管理审批生命周期（请求 → 等待 → 决议）
      2. 超时检测
      3. 状态持久化（通过 state_store）
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

    # ── 核心 API ──

    def request(self, operation: str, **kwargs: Any) -> str:
        """发起审批请求，返回审批记录 ID"""
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
        return record_id

    def approve(self, record_id: str) -> bool:
        """批准指定审批记录"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.APPROVED
        record.resolved_at = time.time()
        return True

    def reject(self, record_id: str) -> bool:
        """拒绝指定审批记录"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.REJECTED
        record.resolved_at = time.time()
        return True

    def get_status(self, record_id: str) -> Optional[ApprovalStatus]:
        """获取审批记录状态"""
        record = self._records.get(record_id)
        if record is None:
            return None
        return record.status

    def is_expired(self, record_id: str) -> bool:
        """检查审批记录是否已超时"""
        record = self._records.get(record_id)
        if record is None:
            return False
        elapsed = time.time() - record.created_at
        return elapsed > self.timeout

    def get_summary(self, record_id: str) -> str:
        """获取审批摘要"""
        record = self._records.get(record_id)
        if record is None:
            return ""
        return record.summary

    def get_record(self, record_id: str) -> Optional[ApprovalRecord]:
        """获取完整审批记录"""
        return self._records.get(record_id)

    def list_records(self) -> List[ApprovalRecord]:
        """列出所有审批记录"""
        return list(self._records.values())

    # ── 状态持久化 ──

    def save_state(self, record_id: str) -> bool:
        """超时后将审批状态保存到 state_store"""
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
        return True

    def load_state(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """从 checkpoint 恢复审批状态"""
        if self.store is None:
            return None
        return self.store.restore_checkpoint(checkpoint_id)


# ───────────────────────────────────────────────────────────────
# 三级审批实现
# ───────────────────────────────────────────────────────────────

class BlockingApproval(BaseApproval):
    """阻塞式审批

    适用场景：Phase 1 架构设计、Phase 5 最终验收
    行为：Agent 暂停等待
    超时：30分钟后暂停并保存状态
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
        """检查审批状态，超时后自动标记为 EXPIRED 并保存状态"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "记录不存在"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "已批准"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "已拒绝"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.EXPIRED
            record.resolved_at = time.time()
            self.save_state(record_id)
            return record.status, "超时已过期，状态已保存"

        return record.status, "等待审批中"


class AsyncApproval(BaseApproval):
    """异步式审批

    适用场景：依赖安装、git push
    行为：Agent 继续做其他任务
    超时：2小时后跳过该操作
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
        """检查审批状态，超时后自动标记为 SKIPPED"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "记录不存在"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "已批准"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "已拒绝"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.SKIPPED
            record.resolved_at = time.time()
            self.save_state(record_id)
            return record.status, "超时已跳过，状态已保存"

        return record.status, "异步等待中，Agent 可继续其他任务"


class AutoApproval(BaseApproval):
    """默认放行式审批

    适用场景：低风险操作
    行为：通知用户但不等待
    超时：5分钟后自动放行
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
        """发起自动审批请求，5分钟后自动放行"""
        record_id = super().request(operation, **kwargs)
        # 自动标记为待放行
        return record_id

    def check_and_timeout(self, record_id: str) -> Tuple[ApprovalStatus, str]:
        """检查审批状态，超时后自动标记为 AUTO_PASSED"""
        record = self._records.get(record_id)
        if record is None:
            return ApprovalStatus.REJECTED, "记录不存在"

        if record.status == ApprovalStatus.APPROVED:
            return record.status, "已批准"
        if record.status == ApprovalStatus.REJECTED:
            return record.status, "已拒绝"

        if self.is_expired(record_id):
            record.status = ApprovalStatus.AUTO_PASSED
            record.resolved_at = time.time()
            return record.status, "已自动放行"

        return record.status, "等待自动放行中"

    def auto_pass(self, record_id: str) -> bool:
        """手动立即自动放行"""
        record = self._records.get(record_id)
        if record is None:
            return False
        if record.status != ApprovalStatus.PENDING:
            return False
        record.status = ApprovalStatus.AUTO_PASSED
        record.resolved_at = time.time()
        return True


# ───────────────────────────────────────────────────────────────
# 审批工厂
# ───────────────────────────────────────────────────────────────

def create_approval(
    level: ApprovalLevel,
    timeout: Optional[int] = None,
    project_id: str = "",
    store: Optional[StateStore] = None,
) -> BaseApproval:
    """根据级别创建对应的审批实例"""
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
    raise ValueError(f"未知审批级别: {level}")


# ───────────────────────────────────────────────────────────────
# 便捷函数（供外部调用）
# ───────────────────────────────────────────────────────────────

def request_blocking_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, BlockingApproval]:
    """发起阻塞式审批请求，返回 (record_id, approval_instance)"""
    approval = BlockingApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval


def request_async_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, AsyncApproval]:
    """发起异步式审批请求，返回 (record_id, approval_instance)"""
    approval = AsyncApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval


def request_auto_approval(
    operation: str,
    project_id: str = "",
    store: Optional[StateStore] = None,
    **kwargs: Any,
) -> Tuple[str, AutoApproval]:
    """发起默认放行式审批请求，返回 (record_id, approval_instance)"""
    approval = AutoApproval(project_id=project_id, store=store)
    record_id = approval.request(operation, **kwargs)
    return record_id, approval
