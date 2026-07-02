"""src/phase_flow.py — Phase 0-6 完整流程编排（v3.0 12 Phase）

PhaseFlow 类管理 v3.0 12 Phase 流转：
  init → design → decompose → research → prd → journey →
  develop → integrate → test → evaluate → accept → deploy

每个 advance 必须通过 check 函数，否则 BLOCK。
支持 rollback-phase 回退（需人工审批）。

对外提供高层函数：
  phase_check, phase_advance, phase_rollback,
  phase_approve_design, phase_approve_accept, phase_mark_tests
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from phase_checks import (
        CHECK_REGISTRY,
        run_check,
        get_all_phase_names,
    )
except ModuleNotFoundError:
    from src.phase_checks import (
        CHECK_REGISTRY,
        run_check,
        get_all_phase_names,
    )

try:
    from state_store import StateStore
except ModuleNotFoundError:
    from src.state_store import StateStore

try:
    from config import get_config
except (ModuleNotFoundError, ImportError):
    from src.config import get_config

try:
    from workflow import get_template
except (ModuleNotFoundError, ImportError):
    from src.workflow import get_template


# ───────────────────────────────────────────────────────────────
# Phase order resolution
# ───────────────────────────────────────────────────────────────

def get_phase_order(project_type: Optional[str] = None) -> List[str]:
    """Return the ordered phase chain for a project type.

    When *project_type* is omitted, the default chain from ``config.py`` is
    returned.  This function does not touch the filesystem.
    """
    if project_type is None:
        return list(get_config().phase_order)

    pt = project_type.lower()
    if pt.startswith("brownfield"):
        return list(get_config().brownfield_phase_order)
    if pt == "greenfield":
        return list(get_config().greenfield_phase_order)

    # Unknown or legacy template name: try workflow template.
    tmpl = get_template(pt)
    if tmpl is not None:
        return list(tmpl.phases)

    return list(get_config().phase_order)


DB_FILENAME = get_config().db_name

# Backward-compatible module-level alias.
PHASE_ORDER = get_phase_order()


# ───────────────────────────────────────────────────────────────
# PhaseFlow 类
# ───────────────────────────────────────────────────────────────

class PhaseFlow:
    """Phase 0-6 流转编排器

    职责：
      1. 管理 phase 顺序（init → design → ... → deploy）
      2. 执行 advance 前自动调用对应 check 函数
      3. 支持 rollback 到指定 phase（需人工审批）
      4. 维护Approval status（design_approved / accept_approved）
      5. 持久化到 SQLite（通过 state_store）
    """

    def __init__(self, project_name: str, base_dir: Path) -> None:
        self.project_name = project_name
        self.base_dir = base_dir
        self.proj_dir = base_dir / project_name
        self.store = self._get_store()
        self.project_type = self._detect_project_type()
        self.phase_order = get_phase_order(self.project_type)

    def _detect_project_type(self) -> str:
        """Infer project workflow type from features.json or config default."""
        features_path = self.proj_dir / "features.json"
        if features_path.exists():
            try:
                data = json.loads(features_path.read_text(encoding="utf-8"))
                ptype = data.get("project_type")
                if ptype in ("greenfield", "brownfield"):
                    return ptype
            except (json.JSONDecodeError, OSError):
                pass
        # Default to the configured pipeline mode without touching other files.
        return get_config().pipeline_mode

    def _get_store(self) -> StateStore:
        db_path = self.proj_dir / DB_FILENAME
        return StateStore(db_path)

    def _load_state(self) -> Optional[Dict[str, Any]]:
        """从 legacy 表加载状态字典"""
        try:
            raw = self.store.legacy_load("state")
            if raw is not None:
                return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def _save_state(self, state: Dict[str, Any]) -> None:
        """保存状态到 legacy 表 + 更新 projects 表 phase"""
        self.store.legacy_save("state", json.dumps(state, ensure_ascii=False))
        self.store.update_project_phase(self.project_name, state.get("phase", "init"))

    def _write_checkpoint(self, state: Dict[str, Any], action: str) -> int:
        return self.store.write_checkpoint(
            project_id=self.project_name,
            phase=state.get("phase", "init"),
            state_dict=state,
            agent="phase_flow",
            action=action,
            result="ok",
        )

    def current_phase(self) -> str:
        """返回当前 phase 名称"""
        state = self._load_state()
        if state is not None:
            return state.get("phase", "init")
        # 回退到 projects 表
        rec = self.store.get_project(self.project_name)
        if rec is not None:
            return rec.current_phase
        return "init"

    def check(self) -> Tuple[bool, str]:
        """检查当前 phase 是否满足 advance 条件

        返回: (passed, message)
        """
        phase = self.current_phase()
        result = run_check(phase, self.project_name, self.base_dir)
        if result["passed"]:
            return True, result["reason"]
        return False, result["reason"]

    def advance(self) -> Tuple[bool, str]:
        """推进到下一 phase

        1. 对当前 phase 执行 check
        2. 未通过则 BLOCK
        3. 通过则更新 phase 并写入 checkpoint
        """
        phase = self.current_phase()
        idx = self.phase_order.index(phase)
        if idx >= len(self.phase_order) - 1:
            return True, f"已在最终阶段 {phase}，无需推进"

        # 执行 check
        passed, msg = self.check()
        if not passed:
            return False, f"check 未通过，无法从 {phase} 推进: {msg}"

        next_phase = self.phase_order[idx + 1]

        # 更新状态
        state = self._load_state() or {}
        state["phase"] = next_phase
        state["name"] = self.project_name
        self._save_state(state)
        self._write_checkpoint(state, f"advance:{phase}->{next_phase}")

        return True, f"从 {phase} 推进到 {next_phase}"

    def rollback(self, target_phase: str, approved: bool = False) -> Tuple[bool, str]:
        """回退到指定 phase

        需要人工审批（approved=True）。
        """
        if target_phase not in self.phase_order:
            return False, f"未知 phase: {target_phase}"

        current = self.current_phase()
        if target_phase == current:
            return True, f"当前已在 {current}，无需回退"

        if not approved:
            return False, (
                f"回退到 {target_phase} 需要人工审批。"
                "请使用 --approved 确认已审批。"
            )

        # 更新状态
        state = self._load_state() or {}
        state["phase"] = target_phase
        state["name"] = self.project_name
        self._save_state(state)
        self._write_checkpoint(state, f"rollback:{current}->{target_phase}")

        return True, f"从 {current} 回退到 {target_phase}"

    def approve_design(self) -> Tuple[bool, str]:
        """审批 design phase"""
        state = self._load_state() or {}
        state["design_approved"] = True
        state["name"] = self.project_name
        state.setdefault("phase", self.current_phase())
        self._save_state(state)
        self._write_checkpoint(state, "approve:design")
        return True, "design 已审批通过"

    def approve_accept(self) -> Tuple[bool, str]:
        """审批 accept phase"""
        state = self._load_state() or {}
        state["accept_approved"] = True
        state["name"] = self.project_name
        state.setdefault("phase", self.current_phase())
        self._save_state(state)
        self._write_checkpoint(state, "approve:accept")
        return True, "accept 已审批通过"

    def mark_tests(self, passed: bool) -> Tuple[bool, str]:
        """标记端到端测试状态"""
        state = self._load_state() or {}
        state["tests_passed"] = passed
        state["name"] = self.project_name
        state.setdefault("phase", self.current_phase())
        self._save_state(state)
        self._write_checkpoint(state, f"mark_tests:{passed}")
        return True, f"tests_passed 标记为 {passed}"


# ───────────────────────────────────────────────────────────────
# 高层便捷函数（供 pipeline.py CLI 调用）
# ───────────────────────────────────────────────────────────────

def phase_check(project_name: str, base_dir: Path) -> Tuple[bool, str]:
    """检查当前 phase 条件"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.check()


def phase_advance(project_name: str, base_dir: Path) -> Tuple[bool, str]:
    """推进到下一 phase"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.advance()


def phase_rollback(
    project_name: str, base_dir: Path, target_phase: str, approved: bool = False
) -> Tuple[bool, str]:
    """回退到指定 phase"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.rollback(target_phase, approved=approved)


def phase_approve_design(project_name: str, base_dir: Path) -> Tuple[bool, str]:
    """审批 design"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.approve_design()


def phase_approve_accept(project_name: str, base_dir: Path) -> Tuple[bool, str]:
    """审批 accept"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.approve_accept()


def phase_mark_tests(project_name: str, base_dir: Path, passed: bool) -> Tuple[bool, str]:
    """标记测试状态"""
    flow = PhaseFlow(project_name, base_dir)
    return flow.mark_tests(passed)
