"""src/suggestion_engine.py — 建议模式调度层 (F025)

建议模式调度层：生成建议等待用户确认，不自动推进。

核心职责：
  1. 分析当前项目状态，生成建议（advance / blocker）
  2. 检查 Phase 是否完成（check_phase_complete）
  3. 检查阻塞条件（check_blockers）
  4. 获取下一个 Phase（get_next_phase）
  5. 与 PhaseFlow / phase_checks 集成，复用现有检查逻辑
  6. 与 system_constraint 集成，验证任务路由约束

建议类型：
  - advance: 当前 phase 检查通过，建议推进到下一 phase
  - blocker: 当前 phase 检查未通过，列出阻塞项

使用示例：
    engine = SuggestionEngine(project_name, base_dir)
    suggestion = engine.suggest_next_phase(state)
    # suggestion = {
    #     "type": "advance",
    #     "current_phase": "init",
    #     "next_phase": "design",
    #     "reason": "所有检查通过",
    #     "blockers": [],
    #     "details": {...},
    # }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from phase_checks import run_check, get_all_phase_names
except ModuleNotFoundError:
    from src.phase_checks import run_check, get_all_phase_names

try:
    from phase_flow import PhaseFlow, PHASE_ORDER
except ModuleNotFoundError:
    from src.phase_flow import PhaseFlow, PHASE_ORDER

try:
    from system_constraint import SystemConstraint, ConstraintViolation
except ModuleNotFoundError:
    from src.system_constraint import SystemConstraint, ConstraintViolation


# ───────────────────────────────────────────────────────────────
# 建议类型枚举
# ───────────────────────────────────────────────────────────────

class SuggestionType(Enum):
    """Suggestion type: advisory only, not enforced."""
    ADVANCE = "advance"   # Suggest advancing to next phase
    BLOCKER = "blocker"     # Blockers exist, cannot advance
    INFO = "info"           # Informational (already at final phase, etc.)


# ───────────────────────────────────────────────────────────────
# 建议数据类
# ───────────────────────────────────────────────────────────────

@dataclass
class Suggestion:
    """建议对象"""
    type: SuggestionType
    current_phase: str
    next_phase: Optional[str]
    reason: str
    blockers: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    can_advance: bool = False
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "current_phase": self.current_phase,
            "next_phase": self.next_phase,
            "reason": self.reason,
            "blockers": self.blockers,
            "details": self.details,
            "can_advance": self.can_advance,
            "requires_approval": self.requires_approval,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Suggestion:
        return cls(
            type=SuggestionType(data.get("type", "info")),
            current_phase=data.get("current_phase", ""),
            next_phase=data.get("next_phase"),
            reason=data.get("reason", ""),
            blockers=data.get("blockers", []),
            details=data.get("details", {}),
            can_advance=data.get("can_advance", False),
            requires_approval=data.get("requires_approval", False),
        )


# ───────────────────────────────────────────────────────────────
# 建议引擎核心类
# ───────────────────────────────────────────────────────────────

class SuggestionEngine:
    """建议模式调度层

    生成建议等待用户确认，不自动推进。

    职责：
      1. suggest_next_phase(state) — 生成建议
      2. check_phase_complete(state) — 检查 Phase 是否完成
      3. check_blockers(state) — 检查阻塞
      4. get_next_phase(state) — 获取下一个 Phase
    """

    def __init__(
        self,
        project_name: str,
        base_dir: Path,
        constraint: Optional[SystemConstraint] = None,
    ) -> None:
        self.project_name = project_name
        self.base_dir = base_dir
        self.flow = PhaseFlow(project_name, base_dir)
        self.constraint = constraint or SystemConstraint()

    # ───────────────────────────────────────────────────────────
    # 核心方法：生成建议
    # ───────────────────────────────────────────────────────────

    def suggest_next_phase(self, state: Optional[Dict[str, Any]] = None) -> Suggestion:
        """生成建议

        分析当前状态，返回 advance / blocker / info 类型的建议。
        不自动推进，仅生成建议供用户确认。

        Args:
            state: 可选的状态字典（若 None 则从 flow 加载）

        Returns:
            Suggestion 对象
        """
        if state is None:
            state = self._load_state() or {}

        current_phase = state.get("phase", self.flow.current_phase())

        # 1. 检查是否在最终阶段
        if current_phase == PHASE_ORDER[-1]:
            return Suggestion(
                type=SuggestionType.INFO,
                current_phase=current_phase,
                next_phase=None,
                reason=f"已在最终阶段 {current_phase}，无需推进",
                can_advance=False,
                requires_approval=False,
            )

        # 2. 检查 phase 是否完成
        complete, check_details = self.check_phase_complete(state)

        if not complete:
            # 存在阻塞，生成 blocker 建议
            blockers = self.check_blockers(state)
            return Suggestion(
                type=SuggestionType.BLOCKER,
                current_phase=current_phase,
                next_phase=self.get_next_phase(state),
                reason=f"当前 phase '{current_phase}' 检查未通过",
                blockers=blockers,
                details=check_details,
                can_advance=False,
                requires_approval=False,
            )

        # 3. phase 已完成，检查是否需要审批
        next_phase = self.get_next_phase(state)
        requires_approval = self._requires_approval(current_phase, state)

        # 4. 检查系统约束（任务路由）
        constraint_ok, constraint_msg = self._check_constraints(current_phase, state)
        if not constraint_ok:
            return Suggestion(
                type=SuggestionType.BLOCKER,
                current_phase=current_phase,
                next_phase=next_phase,
                reason=f"系统约束检查未通过: {constraint_msg}",
                blockers=[constraint_msg],
                details={"constraint_violation": True},
                can_advance=False,
                requires_approval=False,
            )

        # 5. 生成 advance 建议
        return Suggestion(
            type=SuggestionType.ADVANCE,
            current_phase=current_phase,
            next_phase=next_phase,
            reason=f"当前 phase '{current_phase}' 检查通过，建议推进到 '{next_phase}'",
            blockers=[],
            details=check_details,
            can_advance=True,
            requires_approval=requires_approval,
        )

    # ───────────────────────────────────────────────────────────
    # 检查 Phase 是否完成
    # ───────────────────────────────────────────────────────────

    def check_phase_complete(self, state: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """检查当前 Phase 是否完成

        调用 phase_checks 的 check 函数，返回是否通过及详细结果。

        Args:
            state: 可选的状态字典

        Returns:
            (is_complete, details)
        """
        if state is None:
            state = self._load_state() or {}

        current_phase = state.get("phase", self.flow.current_phase())

        # 使用 phase_checks 的 run_check
        result = run_check(current_phase, self.project_name, self.base_dir)

        is_complete = result.get("passed", False)
        details = {
            "phase": current_phase,
            "check_result": result,
            "passed": is_complete,
        }

        return is_complete, details

    # ───────────────────────────────────────────────────────────
    # 检查阻塞
    # ───────────────────────────────────────────────────────────

    def check_blockers(self, state: Optional[Dict[str, Any]] = None) -> List[str]:
        """检查阻塞项

        返回当前 phase 所有未满足条件的阻塞项列表。

        Args:
            state: 可选的状态字典

        Returns:
            阻塞项字符串列表
        """
        if state is None:
            state = self._load_state() or {}

        current_phase = state.get("phase", self.flow.current_phase())
        result = run_check(current_phase, self.project_name, self.base_dir)

        blockers: List[str] = []

        if not result.get("passed", False):
            reason = result.get("reason", "")
            if reason:
                # 按 " | " 分割多个错误
                blockers = [b.strip() for b in reason.split(" | ") if b.strip()]

        # 补充状态层面的阻塞检查
        blockers.extend(self._check_state_blockers(current_phase, state))

        return blockers

    # ───────────────────────────────────────────────────────────
    # 获取下一个 Phase
    # ───────────────────────────────────────────────────────────

    def get_next_phase(self, state: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """获取下一个 Phase

        根据当前 phase 和 PHASE_ORDER 顺序返回下一个 phase 名称。

        Args:
            state: 可选的状态字典

        Returns:
            下一个 phase 名称，或 None（如果在最终阶段）
        """
        if state is None:
            state = self._load_state() or {}

        current_phase = state.get("phase", self.flow.current_phase())

        try:
            idx = PHASE_ORDER.index(current_phase)
        except ValueError:
            return None

        if idx >= len(PHASE_ORDER) - 1:
            return None

        return PHASE_ORDER[idx + 1]

    # ───────────────────────────────────────────────────────────
    # 批量生成建议（用于多项目/多 phase 场景）
    # ───────────────────────────────────────────────────────────

    def suggest_all_phases(self, state: Optional[Dict[str, Any]] = None) -> List[Suggestion]:
        """为所有 phase 生成建议（主要用于诊断/报告）

        返回当前及后续所有 phase 的建议列表。

        Args:
            state: 可选的状态字典

        Returns:
            Suggestion 列表
        """
        if state is None:
            state = self._load_state() or {}

        current_phase = state.get("phase", self.flow.current_phase())
        suggestions: List[Suggestion] = []

        try:
            current_idx = PHASE_ORDER.index(current_phase)
        except ValueError:
            return suggestions

        # 当前 phase 的建议
        suggestions.append(self.suggest_next_phase(state))

        # 后续 phase 的预览建议（仅信息性）
        for idx in range(current_idx + 1, len(PHASE_ORDER)):
            phase = PHASE_ORDER[idx]
            suggestions.append(
                Suggestion(
                    type=SuggestionType.INFO,
                    current_phase=phase,
                    next_phase=PHASE_ORDER[idx + 1] if idx + 1 < len(PHASE_ORDER) else None,
                    reason=f"后续 phase: {phase}",
                    can_advance=False,
                    requires_approval=False,
                )
            )

        return suggestions

    # ───────────────────────────────────────────────────────────
    # 内部辅助方法
    # ───────────────────────────────────────────────────────────

    def _load_state(self) -> Optional[Dict[str, Any]]:
        """从 flow 加载状态字典"""
        try:
            return self.flow._load_state()
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
            return None

    def _requires_approval(self, phase: str, state: Dict[str, Any]) -> bool:
        """检查当前 phase 推进是否需要人工审批"""
        # design → decompose 需要 design_approved
        if phase == "design":
            return not state.get("design_approved", False)
        # accept → deploy 需要 accept_approved
        if phase == "accept":
            return not state.get("accept_approved", False)
        return False

    def _check_constraints(self, phase: str, state: Dict[str, Any]) -> Tuple[bool, str]:
        """检查系统约束是否满足

        验证当前 phase 的任务路由是否符合 system_constraint 约束。
        """
        try:
            # 根据 phase 映射到任务类型
            # Greenfield 阶段映射
            greenfield_phase_task_map = {
                "init": "orchestrate",
                "design": "analyze",
                "decompose": "analyze",
                "develop": "code",
                "test": "test",
                "accept": "review",
                "deploy": "deploy",
            }
            
            # Brownfield 阶段映射
            brownfield_phase_task_map = {
                "discover": "analyze",      # 发现阶段 - 分析现有系统
                "benchmark": "analyze",     # 对标阶段 - 分析基准数据
                "analyze": "analyze",       # 分析阶段 - 深入分析问题
                "plan": "analyze",          # 计划阶段 - 制定优化方案
                "execute": "code",          # 执行阶段 - 实施优化
                "verify": "test",           # 验证阶段 - 验证优化效果
                "deliver": "review",        # 交付阶段 - 审核优化成果
            }
            
            # 合并两个映射
            phase_task_map = {**greenfield_phase_task_map, **brownfield_phase_task_map}
            
            task_type = phase_task_map.get(phase)
            if task_type is not None:
                target_agent = self.constraint.get_agent_for_task(task_type)
                if target_agent is None:
                    return False, f"phase '{phase}' 没有对应的路由 Agent"
            return True, ""
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            return False, str(e)

    def _check_state_blockers(self, phase: str, state: Dict[str, Any]) -> List[str]:
        """检查状态层面的额外阻塞"""
        blockers: List[str] = []

        # design 阶段需要 design_approved
        if phase == "design" and not state.get("design_approved", False):
            blocker = "design 未通过人类审批 (design_approved=false)"
            if blocker not in blockers:
                blockers.append(blocker)

        # accept 阶段需要 accept_approved
        if phase == "accept" and not state.get("accept_approved", False):
            blocker = "accept 未通过人类审批 (accept_approved=false)"
            if blocker not in blockers:
                blockers.append(blocker)

        # test 阶段需要 tests_passed
        if phase == "test" and not state.get("tests_passed", False):
            blocker = "tests_passed 标记为 false"
            if blocker not in blockers:
                blockers.append(blocker)

        return blockers


# ───────────────────────────────────────────────────────────────
# 高层便捷函数
# ───────────────────────────────────────────────────────────────

def suggest_next_phase(
    project_name: str,
    base_dir: Path,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成建议（全局便捷函数）

    返回字典格式的建议，便于序列化和 CLI 输出。
    """
    engine = SuggestionEngine(project_name, base_dir)
    suggestion = engine.suggest_next_phase(state)
    return suggestion.to_dict()


def check_phase_complete(
    project_name: str,
    base_dir: Path,
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """检查 Phase 是否完成（全局便捷函数）"""
    engine = SuggestionEngine(project_name, base_dir)
    return engine.check_phase_complete(state)


def check_blockers(
    project_name: str,
    base_dir: Path,
    state: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """检查阻塞项（全局便捷函数）"""
    engine = SuggestionEngine(project_name, base_dir)
    return engine.check_blockers(state)


def get_next_phase(
    project_name: str,
    base_dir: Path,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """获取下一个 Phase（全局便捷函数）"""
    engine = SuggestionEngine(project_name, base_dir)
    return engine.get_next_phase(state)
