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

from state_store import StateStore, CheckpointRecord


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

PHASE_NAMES = ["init", "develop", "review", "test"]
DB_FILENAME = "pipeline_state.db"


# ───────────────────────────────────────────────────────────────
# 状态机核心
# ───────────────────────────────────────────────────────────────

class Phase(Enum):
    INIT = 0
    DEVELOP = 1
    REVIEW = 2
    TEST = 3

    def __str__(self) -> str:
        return PHASE_NAMES[self.value]

    @classmethod
    def from_name(cls, name: str) -> Phase:
        try:
            return cls(PHASE_NAMES.index(name.lower()))
        except ValueError:
            raise ValueError(f"Unknown phase: {name}")

    def next(self) -> Optional[Phase]:
        if self.value < len(PHASE_NAMES) - 1:
            return Phase(self.value + 1)
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
        )


# ───────────────────────────────────────────────────────────────
# Check 函数注册表
# ───────────────────────────────────────────────────────────────

CheckFunc = Callable[[ProjectState], Tuple[bool, str]]

CHECK_REGISTRY: Dict[str, CheckFunc] = {}


def register_check(phase_name: str) -> Callable[[CheckFunc], CheckFunc]:
    def decorator(func: CheckFunc) -> CheckFunc:
        CHECK_REGISTRY[phase_name] = func
        return func
    return decorator


@register_check("init")
def check_init(state: ProjectState) -> Tuple[bool, str]:
    """Phase 0 → Phase 1 检查"""
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


@register_check("develop")
def check_develop(state: ProjectState) -> Tuple[bool, str]:
    """Phase 1 -> Phase 2 检查（简化版：至少有一个 feature 在开发中）"""
    # 最简版：只要进入 develop 就算满足
    # 实际项目中应检查是否有 feature 正在开发
    if not state.check_results.get("develop_started", False):
        return False, "开发尚未开始（develop_started=false）"
    # 还需要检查是否有代码可审查（code_written）
    if not state.check_results.get("code_written", False):
        return False, "没有可审查的代码（code_written=false）"
    return True, "PASS"


@register_check("review")
def check_review(state: ProjectState) -> Tuple[bool, str]:
    """Phase 2 → Phase 3 检查（简化版：检查是否有代码可审查）"""
    if not state.check_results.get("code_written", False):
        return False, "没有可审查的代码（code_written=false）"
    # 还需要检查测试是否通过
    if not state.check_results.get("tests_passed", False):
        return False, "测试未通过（tests_passed=false）"
    return True, "PASS"


@register_check("test")
def check_test(state: ProjectState) -> Tuple[bool, str]:
    """Phase 3 → （完成）检查"""
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

    base_dir = Path.cwd() / project_name
    if base_dir.exists() and not args.force:
        print(f"[ERROR] 项目目录已存在: {base_dir}")
        return 1

    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "src").mkdir(exist_ok=True)
    (base_dir / "tests").mkdir(exist_ok=True)
    (base_dir / "specs").mkdir(exist_ok=True)
    (base_dir / ".logs").mkdir(exist_ok=True)

    # 创建元数据文件
    metadata_files = []
    for filename, content in [
        ("SOUL.md", f"# SOUL.md\n\n项目: {project_name}\n描述: {description}\n技术栈: {stack}\n"),
        ("AGENTS.md", "# AGENTS.md\n\n## 协作规则\n\n（待填充）\n"),
        ("progress.md", f"# progress.md\n\n项目: {project_name}\n当前 Phase: init\n"),
        ("features.json", json.dumps({"project": project_name, "features": []}, indent=2)),
    ]:
        filepath = base_dir / filename
        filepath.write_text(content, encoding="utf-8")
        metadata_files.append(filename)

    # 初始化 git
    git_init = False
    if os.system(f"cd {base_dir} && git init -q") == 0:
        git_init = True

    # 初始化 SQLite（Layer 2 + 向后兼容）
    store = _get_store(base_dir)
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
    base_dir = Path.cwd() / project_name
    store = _get_store(base_dir)
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

    state.phase = Phase.DEVELOP
    state.check_results["develop_started"] = True
    _save_state(store, project_name, state, "develop")

    print(f"[OK] 进入 develop 阶段: {project_name}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """检查当前 phase 是否满足 advance 条件"""
    project_name: str = args.project
    base_dir = Path.cwd() / project_name
    store = _get_store(base_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] 项目不存在: {project_name}")
        return 1

    phase_name = str(state.phase)
    check_fn = CHECK_REGISTRY.get(phase_name)
    if check_fn is None:
        print(f"[ERROR] 未注册 check 函数: {phase_name}")
        return 1

    passed, msg = check_fn(state)
    state.check_results[f"check_{phase_name}"] = passed
    _save_state(store, project_name, state, f"check_{phase_name}")

    status = "PASS" if passed else "FAIL"
    print(f"[{status}] check {phase_name}: {msg}")
    return 0 if passed else 1


def cmd_advance(args: argparse.Namespace) -> int:
    """推进到下一 phase（自动执行 check，未通过则 BLOCK）"""
    project_name: str = args.project
    base_dir = Path.cwd() / project_name
    store = _get_store(base_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] 项目不存在: {project_name}")
        return 1

    current_phase = state.phase
    phase_name = str(current_phase)

    # 1. 执行 check
    check_fn = CHECK_REGISTRY.get(phase_name)
    if check_fn is None:
        print(f"[ERROR] 未注册 check 函数: {phase_name}")
        return 1

    passed, msg = check_fn(state)
    state.check_results[f"check_{phase_name}"] = passed
    _save_state(store, project_name, state, f"check_{phase_name}")

    if not passed:
        print(f"[BLOCKED] advance blocked: check '{phase_name}' not passed — {msg}")
        return 1

    # 2. 推进
    next_phase = current_phase.next()
    if next_phase is None:
        print(f"[OK] 已在最终阶段 ({current_phase})，无法继续推进")
        return 0

    state.phase = next_phase
    _save_state(store, project_name, state, f"advance_to_{next_phase}")
    print(f"[OK] 从 {current_phase} 推进到 {next_phase}: {project_name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """查看项目状态"""
    project_name: str = args.project
    base_dir = Path.cwd() / project_name
    store = _get_store(base_dir)
    state = _load_state(store, project_name)
    if state is None:
        print(f"[ERROR] 项目不存在: {project_name}")
        return 1

    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """从最新 checkpoint 恢复项目状态（F008）"""
    project_name: str = args.project
    checkpoint_id: Optional[int] = getattr(args, "checkpoint_id", None)

    base_dir = Path.cwd() / project_name
    if not base_dir.exists():
        print(f"[ERROR] 项目目录不存在: {base_dir}")
        return 1

    db_path = _get_db_path(base_dir)
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        return 1

    store = _get_store(base_dir)

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
    """回滚到指定 checkpoint（F008）"""
    project_name: str = args.project
    checkpoint_id: int = args.checkpoint_id

    base_dir = Path.cwd() / project_name
    if not base_dir.exists():
        print(f"[ERROR] 项目目录不存在: {base_dir}")
        return 1

    db_path = _get_db_path(base_dir)
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        return 1

    store = _get_store(base_dir)
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
# CLI 入口
# ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="最简版 pipeline 状态机 — Phase 0-3 流转 + SQLite 持久化",
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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "init": cmd_init,
        "develop": cmd_develop,
        "check": cmd_check,
        "advance": cmd_advance,
        "status": cmd_status,
        "resume": cmd_resume,
        "rollback": cmd_rollback,
    }

    handler = handlers.get(args.command)
    if handler is None:
        print(f"[ERROR] 未知命令: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
