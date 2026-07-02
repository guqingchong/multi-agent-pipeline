#!/usr/bin/env python3
"""worktree.py — 并行 feature 开发 Worktree 管理器

功能：
- 在外部目录创建 git worktree（避免 Windows 260 字符路径限制）
- 文件重叠检测（claimed_files 级别）
- 自动清理机制（feature passed 后移除 worktree）
- 列出活跃 worktree

设计要点：
- 外部目录默认: C:/agent-worktrees/<project>-<feature>
- 使用 git worktree 命令操作，不依赖第三方库
- 支持 Windows 长路径（通过 pathlib + git config）
- 记录 claimed_files 用于重叠检测
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DEFAULT_WORKTREE_ROOT = Path("C:/agent-worktrees")
WORKTREE_DB_FILENAME = "worktree_registry.json"


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class WorktreeEntry:
    """单个 worktree 的注册信息。"""
    project: str
    feature: str
    path: str
    branch: str
    claimed_files: List[str] = field(default_factory=list)
    status: str = "active"  # active | merged | abandoned

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "feature": self.feature,
            "path": self.path,
            "branch": self.branch,
            "claimed_files": self.claimed_files,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorktreeEntry":
        return cls(
            project=d["project"],
            feature=d["feature"],
            path=d["path"],
            branch=d["branch"],
            claimed_files=d.get("claimed_files", []),
            status=d.get("status", "active"),
        )


# ───────────────────────────────────────────────────────────────
# 工具函数
# ───────────────────────────────────────────────────────────────


def _run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """在指定目录运行 git 命令。"""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def _worktree_registry_path(worktree_root: Path) -> Path:
    """返回注册表文件路径。"""
    return worktree_root / WORKTREE_DB_FILENAME


def _load_registry(worktree_root: Path) -> Dict[str, WorktreeEntry]:
    """加载 worktree 注册表。"""
    reg_path = _worktree_registry_path(worktree_root)
    if not reg_path.exists():
        return {}
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        return {k: WorktreeEntry.from_dict(v) for k, v in data.items()}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _save_registry(worktree_root: Path, registry: Dict[str, WorktreeEntry]) -> None:
    """保存 worktree 注册表。"""
    reg_path = _worktree_registry_path(worktree_root)
    _ensure_dir(worktree_root)
    data = {k: v.to_dict() for k, v in registry.items()}
    reg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _make_key(project: str, feature: str) -> str:
    """生成注册表键。"""
    return f"{project}:{feature}"


def _make_branch_name(feature: str, agent: str = "claude") -> str:
    """生成分支名: agent/f001-claude。"""
    return f"agent/{feature.lower()}-{agent}"


def _normalize_claimed_files(files: Optional[List[str]]) -> List[str]:
    """标准化 claimed_files 路径（统一正斜杠、去重）。"""
    if not files:
        return []
    seen: Set[str] = set()
    result: List[str] = []
    for f in files:
        norm = f.replace("\\", "/").lstrip("/")
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


# ───────────────────────────────────────────────────────────────
# 核心类
# ───────────────────────────────────────────────────────────────

class WorktreeManager:
    """管理外部目录 git worktree，支持并行 feature 开发。

    主要方法:
        create(project, feature, claimed_files=None) -> str
        detect_overlap(feature_a, feature_b) -> bool
        auto_cleanup(feature, project=None) -> bool
        list_active(project=None) -> List[WorktreeEntry]
        remove(project, feature) -> bool
        register_claimed_files(project, feature, files) -> None
    """

    def __init__(
        self,
        worktree_root: Optional[Path] = None,
    ):
        self.worktree_root = Path(worktree_root) if worktree_root else DEFAULT_WORKTREE_ROOT
        self._registry: Dict[str, WorktreeEntry] = {}
        self._load()

    # ── 内部 ──

    def _load(self) -> None:
        """从磁盘加载注册表。"""
        self._registry = _load_registry(self.worktree_root)

    def _persist(self) -> None:
        """持久化注册表到磁盘。"""
        _save_registry(self.worktree_root, self._registry)

    def _project_path(self, project: str) -> Path:
        """项目源码路径（假设在当前工作目录或给定绝对路径）。"""
        # 如果 project 是绝对路径或已存在，直接使用
        p = Path(project)
        if p.is_absolute() and p.exists():
            return p
        # 否则尝试在当前工作目录下查找
        cwd = Path.cwd()
        candidate = cwd / project
        if candidate.exists():
            return candidate
        #  fallback: 返回原路径，让 git 报错
        return p

    def _worktree_dir(self, project: str, feature: str) -> Path:
        """计算外部 worktree 目录路径。"""
        # C:\agent-worktrees\<project>-<feature>
        return self.worktree_root / f"{project}-{feature}"

    def _git_dir(self, project_path: Path) -> Path:
        """返回项目 .git 目录（支持 worktree 的 .git 文件指向）。"""
        git_path = project_path / ".git"
        if git_path.is_file():
            # .git 文件包含 gitdir: <path>
            content = git_path.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir: "):
                return Path(content[8:].strip())
        return git_path

    # ── 公共 API ──

    def create(
        self,
        project: str,
        feature: str,
        claimed_files: Optional[List[str]] = None,
        agent: str = "claude",
    ) -> str:
        """在外部目录为指定 feature 创建 git worktree。

        Args:
            project: 项目名称或源码路径
            feature: feature ID（如 F018）
            claimed_files: 该 feature 计划修改的文件列表（用于重叠检测）
            agent: 负责该 feature 的 agent 标识

        Returns:
            创建的 worktree 目录路径（字符串）

        Raises:
            RuntimeError: git 命令失败
        """
        project_path = self._project_path(project)
        if not (project_path / ".git").exists():
            raise RuntimeError(f"Project '{project}' is not a git repository (no .git found)")

        worktree_dir = self._worktree_dir(project, feature)
        branch_name = _make_branch_name(feature, agent)
        key = _make_key(project, feature)

        # 如果已存在同名 worktree，先移除
        if key in self._registry or worktree_dir.exists():
            self.remove(project, feature)

        # 确保分支存在（基于当前 HEAD 创建）
        # 先检查分支是否已存在
        branch_check = _run_git(project_path, "branch", "--list", branch_name, check=False)
        if branch_name not in branch_check.stdout:
            # 创建新分支
            _run_git(project_path, "branch", branch_name)

        # 创建 worktree
        _ensure_dir(worktree_dir.parent)
        result = _run_git(
            project_path,
            "worktree",
            "add",
            "-B", branch_name,  # -B: 如果存在则重置
            str(worktree_dir),
            check=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr}")

        # 注册
        entry = WorktreeEntry(
            project=str(project_path),
            feature=feature,
            path=str(worktree_dir),
            branch=branch_name,
            claimed_files=_normalize_claimed_files(claimed_files),
            status="active",
        )
        self._registry[key] = entry
        self._persist()

        return str(worktree_dir)

    def detect_overlap(
        self,
        feature_a: str,
        feature_b: str,
        project: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """检测两个 feature 的 claimed_files 是否有重叠。

        Args:
            feature_a: 第一个 feature ID
            feature_b: 第二个 feature ID
            project: 项目名称（默认从注册表推断，若只有一个项目则省略）

        Returns:
            (has_overlap, overlapping_files)
        """
        # 如果未指定 project，尝试从注册表查找
        if project is None:
            keys_a = [k for k in self._registry if k.endswith(f":{feature_a}")]
            keys_b = [k for k in self._registry if k.endswith(f":{feature_b}")]
            if not keys_a or not keys_b:
                return False, []
            # 使用第一个匹配的项目
            key_a = keys_a[0]
            key_b = keys_b[0]
        else:
            key_a = _make_key(project, feature_a)
            key_b = _make_key(project, feature_b)

        entry_a = self._registry.get(key_a)
        entry_b = self._registry.get(key_b)
        if not entry_a or not entry_b:
            return False, []

        set_a = set(entry_a.claimed_files)
        set_b = set(entry_b.claimed_files)
        overlap = sorted(set_a & set_b)
        return bool(overlap), overlap

    def auto_cleanup(
        self,
        feature: str,
        project: Optional[str] = None,
    ) -> bool:
        """自动清理指定 feature 的 worktree（feature passed 后调用）。

        Args:
            feature: feature ID
            project: 项目名称（可选）

        Returns:
            是否成功清理
        """
        return self.remove(project, feature) if project else self.remove_by_feature(feature)

    def remove(self, project: str, feature: str) -> bool:
        """移除指定 project + feature 的 worktree。

        Args:
            project: 项目名称或路径
            feature: feature ID

        Returns:
            是否成功移除
        """
        key = _make_key(project, feature)
        entry = self._registry.pop(key, None)

        if entry is None:
            # 尝试按路径查找
            worktree_dir = self._worktree_dir(project, feature)
            if not worktree_dir.exists():
                return False
            entry = WorktreeEntry(
                project=project,
                feature=feature,
                path=str(worktree_dir),
                branch=_make_branch_name(feature),
            )

        worktree_path = Path(entry.path)
        project_path = self._project_path(entry.project)

        # 1. git worktree remove
        if worktree_path.exists():
            result = _run_git(
                project_path,
                "worktree",
                "remove",
                "--force",
                str(worktree_path),
                check=False,
            )
            # 如果 git worktree remove 失败（如已损坏），强制删除目录
            if result.returncode != 0 and worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
        else:
            # 目录已不存在，清理 git 内部记录
            _run_git(project_path, "worktree", "prune", check=False)

        # 2. 删除分支（可选，如果已合并则删除）
        _run_git(project_path, "branch", "-D", entry.branch, check=False)

        self._persist()
        return True

    def remove_by_feature(self, feature: str) -> bool:
        """按 feature ID 移除所有匹配的 worktree（跨项目）。"""
        keys_to_remove = [k for k in self._registry if k.endswith(f":{feature}")]
        if not keys_to_remove:
            return False
        for key in keys_to_remove:
            parts = key.split(":", 1)
            if len(parts) == 2:
                self.remove(parts[0], parts[1])
        return True

    def list_active(self, project: Optional[str] = None) -> List[WorktreeEntry]:
        """列出活跃（status=active）的 worktree。

        Args:
            project: 若指定，只返回该项目的 worktree

        Returns:
            WorktreeEntry 列表
        """
        results: List[WorktreeEntry] = []
        for entry in self._registry.values():
            if entry.status != "active":
                continue
            if project is not None and entry.project != project:
                # 也支持按项目名匹配（非绝对路径时）
                if not Path(entry.project).name == project and not entry.project.endswith(project):
                    continue
            # 验证目录是否真实存在
            if Path(entry.path).exists():
                results.append(entry)
        return results

    def register_claimed_files(
        self,
        project: str,
        feature: str,
        files: List[str],
    ) -> None:
        """为已存在的 worktree 注册/更新 claimed_files。

        Args:
            project: 项目名称
            feature: feature ID
            files: 文件路径列表
        """
        key = _make_key(project, feature)
        entry = self._registry.get(key)
        if entry is None:
            raise KeyError(f"Worktree for {project}/{feature} not found")
        entry.claimed_files = _normalize_claimed_files(files)
        self._persist()

    def get_entry(self, project: str, feature: str) -> Optional[WorktreeEntry]:
        """获取指定 worktree 的注册信息。"""
        return self._registry.get(_make_key(project, feature))

    def mark_merged(self, project: str, feature: str) -> None:
        """标记 feature 已合并（触发后续清理）。"""
        key = _make_key(project, feature)
        entry = self._registry.get(key)
        if entry:
            entry.status = "merged"
            self._persist()

    def mark_abandoned(self, project: str, feature: str) -> None:
        """标记 feature 已废弃（触发后续清理）。"""
        key = _make_key(project, feature)
        entry = self._registry.get(key)
        if entry:
            entry.status = "abandoned"
            self._persist()

    def sync_with_git(self, project: str) -> List[WorktreeEntry]:
        """同步 git worktree 列表与注册表，清理孤儿记录。

        Args:
            project: 项目名称或路径

        Returns:
            清理后的活跃 worktree 列表
        """
        project_path = self._project_path(project)
        result = _run_git(project_path, "worktree", "list", "--porcelain", check=False)
        if result.returncode != 0:
            return self.list_active(project)

        # 解析 git worktree list --porcelain 输出
        active_paths: Set[str] = set()
        lines = result.stdout.strip().splitlines()
        current_path: Optional[str] = None
        for line in lines:
            if line.startswith("worktree "):
                current_path = line[9:].strip()
            elif line == "" and current_path:
                active_paths.add(current_path)
                current_path = None
        if current_path:
            active_paths.add(current_path)

        # 清理注册表中已不存在的 worktree
        keys_to_remove: List[str] = []
        for key, entry in self._registry.items():
            if entry.project == str(project_path) or Path(entry.project).name == project:
                if entry.path not in active_paths and not Path(entry.path).exists():
                    keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._registry[key]

        if keys_to_remove:
            self._persist()

        return self.list_active(project)
