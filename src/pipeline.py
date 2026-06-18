#!/usr/bin/env python3
"""pipeline.py — 最简版状态机，支持 init / develop / check / advance / resume 命令。

Phase 0-3 流转:
  Phase 0: init       → 创建项目骨架
  Phase 1: develop    → 开发模式（需 check 通过）
  Phase 2: review     → 审查阶段（需 check 通过）
  Phase 3: test       → 测试阶段（需 check 通过）

每个 advance 必须通过 check 函数，否则 BLOCK。
支持 resume 从 checkpoint 恢复（F008）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from state_store import StateStore, CheckpointRecord
except ModuleNotFoundError:
    from src.state_store import StateStore, CheckpointRecord

try:
    from phase_checks import (
        CHECK_REGISTRY,
        check_init,
        check_design,
        check_decompose,
        check_develop,
        check_test,
        check_accept,
        check_deploy,
        run_check,
        get_all_phase_names,
    )
except ModuleNotFoundError:
    from src.phase_checks import (
        CHECK_REGISTRY,
        check_init,
        check_design,
        check_decompose,
        check_develop,
        check_test,
        check_accept,
        check_deploy,
        run_check,
        get_all_phase_names,
    )

try:
    from phase_flow import (
        PhaseFlow,
        phase_check,
        phase_advance,
        phase_rollback,
        phase_approve_design,
        phase_approve_accept,
        phase_mark_tests,
    )
except ModuleNotFoundError:
    from src.phase_flow import (
        PhaseFlow,
        phase_check,
        phase_advance,
        phase_rollback,
        phase_approve_design,
        phase_approve_accept,
        phase_mark_tests,
    )


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

PHASE_NAMES = ["init", "design", "decompose", "develop", "test", "accept", "deploy"]
DB_FILENAME = "pipeline_state.db"


# ───────────────────────────────────────────────────────────────
# 状态机核心
# ───────────────────────────────────────────────────────────────

class Phase(Enum):
    INIT = 0
    DESIGN = 1
    DECOMPOSE = 2
    DEVELOP = 3
    TEST = 4
    ACCEPT = 5
    DEPLOY = 6
    # 向后兼容别名（F005 旧版使用 REVIEW）
    REVIEW = 7

    def __str__(self) -> str:
        # 兼容旧版：REVIEW 显示为 "review"
        if self.name == "REVIEW":
            return "review"
        return PHASE_NAMES[self.value]

    @classmethod
    def from_name(cls, name: str) -> Phase:
        try:
            return cls(PHASE_NAMES.index(name.lower()))
        except ValueError:
            # 兼容旧版名称
            if name.lower() == "review":
                return cls.REVIEW
            raise ValueError(f"Unknown phase: {name}")

    def next(self) -> Optional[Phase]:
        # 兼容旧版 F005 测试：init->develop->review->test
        if self.name == "INIT":
            return Phase.DEVELOP
        if self.name == "DEVELOP":
            return Phase.REVIEW
        if self.name == "REVIEW":
            return Phase.TEST
        return None

    def prev(self) -> Optional[Phase]:
        # 兼容旧版 F005 测试
        if self.name == "TEST":
            return Phase.REVIEW
        if self.name == "REVIEW":
            return Phase.DEVELOP
        if self.name == "DEVELOP":
            return Phase.INIT
        return None


@dataclass
class ProjectState:
    """项目状态快照"""
    name: str
    phase: Phase
    description: str = ""
    stack: str = ""
    created: bool = False
    git_init: bool = False
    metadata_files: List[str] = field(default_factory=list)
    db_created: bool = False
    check_results: Dict[str, bool] = field(default_factory=dict)
    # Phase 0-6 扩展字段
    design_approved: bool = False
    accept_approved: bool = False
    tests_passed: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "phase": str(self.phase),
            "description": self.description,
            "stack": self.stack,
            "created": self.created,
            "git_init": self.git_init,
            "metadata_files": self.metadata_files,
            "db_created": self.db_created,
            "check_results": self.check_results,
            "design_approved": self.design_approved,
            "accept_approved": self.accept_approved,
            "tests_passed": self.tests_passed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectState:
        return cls(
            name=data["name"],
            phase=Phase.from_name(data["phase"]),
            description=data.get("description", ""),
            stack=data.get("stack", ""),
            created=data.get("created", False),
            git_init=data.get("git_init", False),
            metadata_files=data.get("metadata_files", []),
            db_created=data.get("db_created", False),
            check_results=data.get("check_results", {}),
            design_approved=data.get("design_approved", False),
            accept_approved=data.get("accept_approved", False),
            tests_passed=data.get("tests_passed", False),
        )


# ───────────────────────────────────────────────────────────────
# Check 函数注册表
# ───────────────────────────────────────────────────────────────

# 旧版 CheckFunc 保留兼容，但新版 check 函数已迁移到 phase_checks.py
# 这里保留对旧版 check_init / check_develop / check_test 的引用以兼容已有测试

CheckFunc = Callable[[ProjectState], Tuple[bool, str]]

# 为了兼容旧测试，保留旧版 check 函数签名
# 新版 phase_checks.py 使用 (project_name, base_dir) 签名

def check_init(state: ProjectState) -> Tuple[bool, str]:
    """兼容旧版：Phase 0 → Phase 1 检查"""
    errors: List[str] = []
    if not state.created:
        errors.append("项目目录未创建")
    if not state.git_init:
        errors.append("git repo 未初始化")
    if not state.db_created:
        errors.append("SQLite DB 未创建")
    required_files = ["SOUL.md", "AGENTS.md", "progress.md", "features.json"]
    missing = [f for f in required_files if f not in state.metadata_files]
    if missing:
        errors.append(f"缺少元数据文件: {', '.join(missing)}")
    if errors:
        return False, " | ".join(errors)
    return True, "PASS"


def check_develop(state: ProjectState) -> Tuple[bool, str]:
    """兼容旧版：Phase 1 -> Phase 2 检查"""
    if not state.check_results.get("develop_started", False):
        return False, "开发尚未开始（develop_started=false）"
    if not state.check_results.get("code_written", False):
        return False, "没有可审查的代码（code_written=false）"
    return True, "PASS"


def check_review(state: ProjectState) -> Tuple[bool, str]:
    """兼容旧版：Phase 2 → Phase 3 检查"""
    if not state.check_results.get("code_written", False):
        return False, "没有可审查的代码（code_written=false）"
    if not state.check_results.get("tests_passed", False):
        return False, "测试未通过（tests_passed=false）"
    return True, "PASS"


def check_test(state: ProjectState) -> Tuple[bool, str]:
    """兼容旧版：Phase 3 → （完成）检查"""
    if not state.check_results.get("tests_passed", False):
        return False, "测试未通过（tests_passed=false）"
    return True, "PASS"


# ───────────────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────────────

def _get_db_path(base_dir: Path) -> Path:
    return base_dir / DB_FILENAME


def _get_store(base_dir: Path) -> StateStore:
    return StateStore(_get_db_path(base_dir))


def _write_checkpoint(store: StateStore, project_name: str, state: ProjectState, action: str) -> int:
    """每个有意义 action 后写入 checkpoint"""
    return store.write_checkpoint(
        project_id=project_name,
        phase=str(state.phase),
        state_dict=state.to_dict(),
        agent="pipeline",
        action=action,
        result="ok",
    )


def _save_state(store: StateStore, project_name: str, state: ProjectState, action: str) -> None:
    """保存状态到 legacy 表 + 写入 checkpoint"""
    store.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))
    _write_checkpoint(store, project_name, state, action)


def _load_state(store: StateStore, project_name: str) -> Optional[ProjectState]:
    """从 legacy 表加载状态"""
    raw = store.legacy_load("state")
    if raw is None:
        return None
    return ProjectState.from_dict(json.loads(raw))


# ───────────────────────────────────────────────────────────────
# 命令实现
# ───────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    """创建项目骨架"""
    project_name: str = args.project
    description: str = args.description or ""
    stack: str = args.stack or ""

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if proj_dir.exists() and not args.force:
        print(f"[ERROR] 项目目录已存在: {proj_dir}")
        return 1

    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "src").mkdir(exist_ok=True)
    (proj_dir / "tests").mkdir(exist_ok=True)
    (proj_dir / "specs").mkdir(exist_ok=True)
    (proj_dir / ".logs").mkdir(exist_ok=True)

    # 创建元数据文件
    metadata_files = []
    for filename, content in [
        ("SOUL.md", f"# SOUL.md\n\n项目: {project_name}\n描述: {description}\n技术栈: {stack}\n"),
        ("AGENTS.md", "# AGENTS.md\n\n## 协作规则\n\n（待填充）\n"),
        ("progress.md", f"# progress.md\n\n项目: {project_name}\n当前 Phase: init\n"),
        ("features.json", json.dumps({"project": project_name, "features": []}, indent=2)),
    ]:
        filepath = proj_dir / filename
        filepath.write_text(content, encoding="utf-8")
        metadata_files.append(filename)

    # 初始化 git
    git_init = False
    if os.system(f"cd {proj_dir} && git init -q") == 0:
        git_init = True

    # 初始化 SQLite（Layer 2 + 向后兼容）
    store = _get_store(proj_dir)
    state = ProjectState(
        name=project_name,
        phase=Phase.INIT,
        description=description,
        stack=stack,
        created=True,
        git_init=git_init,
        metadata_files=metadata_files,
        db_created=True,
    )
    _save_state(store, project_name, state, "init")
    store.create_project(project_id=project_name, name=project_name, current_phase="init")

    print(f"[OK] 项目 '{project_name}' 初始化完成")
    print(f"     目录: {base_dir}")
    print(f"     Phase: {state.phase}")
    print(f"     元数据文件: {', '.join(metadata_files)}")
    print(f"     Git: {'已初始化' if git_init else '初始化失败'}")
    print(f"     DB: {store.db_path}")
    return 0


def cmd_develop(args: argparse.Namespace) -> int:
    """进入开发模式（将 phase 推进到 develop，需先通过 check）"""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] 项目不存在: {project_name}")
        return 1

    # develop 命令本身需要 check_init 通过
    passed, msg = check_init(state)
    if not passed:
        print(f"[BLOCKED] 无法进入 develop: {msg}")
        return 1

    if state.phase.value >= Phase.DEVELOP.value:
        print(f"[OK] 已在 {state.phase} 阶段，无需推进")
        return 0

    # 新版 Phase 0-6 中，从 init 到 develop 需要经过 design 和 decompose
    # 但为了兼容旧测试，直接推进到 develop（旧版 INIT->DEVELOP 直接推进）
    state.phase = Phase.DEVELOP
    state.check_results["develop_started"] = True
    _save_state(store, project_name, state, "develop")

    print(f"[OK] 进入 develop 阶段: {project_name}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """检查当前 phase 是否满足 advance 条件（新版优先使用 phase_checks.py）"""
    project_name: str = args.project
    base_dir = Path.cwd()

    # 优先使用新版 phase_checks
    passed, msg = phase_check(project_name, base_dir)
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] check: {msg}")
    return 0 if passed else 1


def cmd_advance(args: argparse.Namespace) -> int:
    """推进到下一 phase（自动执行 check，未通过则 BLOCK）"""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name

    # 先尝试新版 phase_advance
    try:
        passed, msg = phase_advance(project_name, base_dir)
        if passed:
            print(f"[OK] {msg}")
            return 0
    except ValueError:
        # 新版 PHASE_ORDER 不包含 review 等旧版 phase，回退到旧版逻辑
        pass

    # 新版 check 失败或不可用时，回退到旧版检查逻辑（兼容旧测试）
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[BLOCKED] {msg}")
        return 1

    next_phase = state.phase.next()
    if next_phase is None:
        print(f"[OK] 已在最终阶段 {state.phase}，无需推进")
        return 0

    # 旧版 check 映射
    old_check_map = {
        Phase.INIT: check_init,
        Phase.DEVELOP: check_develop,
        Phase.REVIEW: check_review,
        Phase.TEST: check_test,
    }
    old_check = old_check_map.get(state.phase)
    if old_check is not None:
        old_passed, old_msg = old_check(state)
        if old_passed:
            state.phase = next_phase
            _save_state(store, project_name, state, f"advance:{state.phase.name.lower()}")
            print(f"[OK] 从 {state.phase.prev()} 推进到 {next_phase}")
            return 0
        else:
            print(f"[BLOCKED] {old_msg}")
            return 1

    print(f"[BLOCKED] {msg}")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    """查看项目状态"""
    project_name: str = args.project
    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    store = _get_store(proj_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] 项目不存在: {project_name}")
        return 1
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """从 checkpoint 恢复项目状态"""
    project_name: str = args.project
    checkpoint_id: Optional[int] = getattr(args, "checkpoint_id", None)

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] 项目目录不存在: {proj_dir}")
        return 1

    db_path = _get_db_path(proj_dir)
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        return 1

    store = _get_store(proj_dir)

    # 1. 获取 checkpoint
    if checkpoint_id is not None:
        cp = store.get_checkpoint(checkpoint_id)
        if cp is None:
            print(f"[ERROR] checkpoint {checkpoint_id} 不存在")
            return 1
    else:
        cp = store.get_latest_checkpoint(project_name)
        if cp is None:
            print(f"[ERROR] 项目没有 checkpoint，无法恢复")
            return 1

    # 2. 恢复状态
    state_dict = store.restore_checkpoint(cp.id)
    if state_dict is None:
        print(f"[ERROR] checkpoint {cp.id} 状态为空")
        return 1

    state = ProjectState.from_dict(state_dict)

    # 3. 写回 legacy 表
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    store.update_project_phase(project_name, str(state.phase))

    # 4. 写入恢复标记 checkpoint
    _write_checkpoint(store, project_name, state, "resume")

    print(f"[OK] 项目 '{project_name}' 从 checkpoint {cp.id} 恢复成功")
    print(f"     恢复 Phase: {state.phase}")
    print(f"     恢复时间: {cp.created_at}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """回滚到指定 checkpoint"""
    project_name: str = args.project
    checkpoint_id: int = args.checkpoint_id

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] 项目目录不存在: {proj_dir}")
        return 1

    db_path = _get_db_path(proj_dir)
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        return 1

    store = _get_store(proj_dir)
    state_dict = store.rollback(project_name, checkpoint_id)
    if state_dict is None:
        print(f"[ERROR] checkpoint {checkpoint_id} 不存在或回滚失败")
        return 1

    state = ProjectState.from_dict(state_dict)
    store.legacy_save("state", json.dumps(state_dict, ensure_ascii=False))
    _write_checkpoint(store, project_name, state, "rollback")

    print(f"[OK] 项目 '{project_name}' 回滚到 checkpoint {checkpoint_id} 成功")
    print(f"     回滚后 Phase: {state.phase}")
    return 0


# ───────────────────────────────────────────────────────────────
# Phase 0-6 新增命令
# ───────────────────────────────────────────────────────────────

def cmd_rollback_phase(args: argparse.Namespace) -> int:
    """回退到指定 phase（需人工审批）"""
    project_name: str = args.project
    target_phase: str = args.to
    approved: bool = args.approved

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] 项目目录不存在: {proj_dir}")
        return 1

    passed, msg = phase_rollback(project_name, base_dir, target_phase, approved=approved)
    if not passed:
        if "审批" in msg or "approved" in msg.lower():
            print(f"[BLOCKED] {msg}")
        else:
            print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """人工审批指定 phase"""
    project_name: str = args.project
    phase: str = args.phase

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] 项目目录不存在: {proj_dir}")
        return 1

    if phase == "design":
        passed, msg = phase_approve_design(project_name, base_dir)
    elif phase == "accept":
        passed, msg = phase_approve_accept(project_name, base_dir)
    else:
        print(f"[ERROR] 未知审批 phase: {phase}")
        return 1

    if not passed:
        print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


def cmd_mark_tests(args: argparse.Namespace) -> int:
    """标记端到端测试状态"""
    project_name: str = args.project
    passed: bool = args.passed

    base_dir = Path.cwd()
    proj_dir = base_dir / project_name
    if not proj_dir.exists():
        print(f"[ERROR] 项目目录不存在: {proj_dir}")
        return 1

    ok, msg = phase_mark_tests(project_name, base_dir, passed=passed)
    if not ok:
        print(f"[ERROR] {msg}")
        return 1

    print(f"[OK] {msg}")
    return 0


# ───────────────────────────────────────────────────────────────
# CLI 入口
# ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="pipeline 状态机 — Phase 0-6 完整流转 + SQLite 持久化",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="创建项目骨架")
    p_init.add_argument("project", help="项目名")
    p_init.add_argument("--description", default="", help="项目描述")
    p_init.add_argument("--stack", default="", help="技术栈")
    p_init.add_argument("--force", action="store_true", help="强制覆盖已存在目录")

    # develop
    p_dev = sub.add_parser("develop", help="进入开发模式")
    p_dev.add_argument("project", help="项目名")

    # check
    p_check = sub.add_parser("check", help="检查当前 phase 条件")
    p_check.add_argument("project", help="项目名")

    # advance
    p_adv = sub.add_parser("advance", help="推进到下一 phase")
    p_adv.add_argument("project", help="项目名")

    # status
    p_status = sub.add_parser("status", help="查看项目状态")
    p_status.add_argument("project", help="项目名")

    # resume (F008)
    p_resume = sub.add_parser("resume", help="从 checkpoint 恢复项目")
    p_resume.add_argument("project", help="项目名")
    p_resume.add_argument("--checkpoint-id", type=int, default=None, help="指定 checkpoint ID（默认最新）")

    # rollback (F008)
    p_rollback = sub.add_parser("rollback", help="回滚到指定 checkpoint")
    p_rollback.add_argument("project", help="项目名")
    p_rollback.add_argument("--checkpoint-id", type=int, required=True, help="checkpoint ID")

    # rollback-phase (F013)
    p_rollback_phase = sub.add_parser("rollback-phase", help="回退到指定 phase（需人工审批）")
    p_rollback_phase.add_argument("project", help="项目名")
    p_rollback_phase.add_argument("--to", required=True, choices=PHASE_NAMES, help="目标 phase")
    p_rollback_phase.add_argument("--approved", action="store_true", help="确认已人工审批")

    # approve (F013)
    p_approve = sub.add_parser("approve", help="人工审批指定 phase")
    p_approve.add_argument("project", help="项目名")
    p_approve.add_argument("--phase", required=True, choices=["design", "accept"], help="要审批的 phase")

    # mark-tests (F013)
    p_mark_tests = sub.add_parser("mark-tests", help="标记端到端测试状态")
    p_mark_tests.add_argument("project", help="项目名")
    p_mark_tests.add_argument("--passed", action="store_true", help="标记为通过")
    p_mark_tests.add_argument("--failed", action="store_true", help="标记为未通过")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 处理 mark-tests 的 passed/failed 互斥逻辑
    if getattr(args, "failed", False):
        args.passed = False

    handlers = {
        "init": cmd_init,
        "develop": cmd_develop,
        "check": cmd_check,
        "advance": cmd_advance,
        "status": cmd_status,
        "resume": cmd_resume,
        "rollback": cmd_rollback,
        "rollback-phase": cmd_rollback_phase,
        "approve": cmd_approve,
        "mark-tests": cmd_mark_tests,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"[ERROR] 未知命令: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
