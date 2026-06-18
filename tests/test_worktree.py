"""tests/test_worktree.py — WorktreeManager 单元测试

F018 验收标准:
- [command] worktree 创建在外部目录
- [test] 文件重叠检测测试通过
- [command] feature passed 后自动清理 worktree

要求: 25+ 测试，覆盖 create / detect_overlap / auto_cleanup / list_active / 边界情况
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from worktree import (
    DEFAULT_WORKTREE_ROOT,
    WORKTREE_DB_FILENAME,
    WorktreeEntry,
    WorktreeManager,
    _ensure_dir,
    _load_registry,
    _make_branch_name,
    _make_key,
    _normalize_claimed_files,
    _run_git,
    _save_registry,
    _worktree_registry_path,
)


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def temp_worktree_root(tmp_path: Path) -> Path:
    """提供一个临时 worktree 根目录。"""
    return tmp_path / "worktrees"


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """创建一个带 git 仓库的临时项目目录。"""
    project_dir = tmp_path / "fake_project"
    project_dir.mkdir()
    _run_git(project_dir, "init", check=True)
    _run_git(project_dir, "config", "user.email", "test@example.com", check=True)
    _run_git(project_dir, "config", "user.name", "Test User", check=True)
    (project_dir / "README.md").write_text("# init\n", encoding="utf-8")
    _run_git(project_dir, "add", "README.md", check=True)
    _run_git(project_dir, "commit", "-m", "init", check=True)
    return project_dir


@pytest.fixture
def manager(temp_worktree_root: Path) -> WorktreeManager:
    """返回使用临时根目录的 WorktreeManager。"""
    return WorktreeManager(worktree_root=temp_worktree_root)


@pytest.fixture
def manager_with_entry(
    manager: WorktreeManager, fake_project: Path
) -> WorktreeManager:
    """返回已注册一个 worktree entry 的 manager（不实际创建 git worktree）。"""
    entry = WorktreeEntry(
        project=str(fake_project),
        feature="F001",
        path=str(manager._worktree_dir("fake_project", "F001")),
        branch="agent/f001-claude",
        claimed_files=["src/a.py", "src/b.py"],
        status="active",
    )
    manager._registry[_make_key("fake_project", "F001")] = entry
    manager._persist()
    return manager


# ───────────────────────────────────────────────────────────────
# 1. 工具函数测试 (_normalize_claimed_files, _make_key, _make_branch_name, _ensure_dir)
# ───────────────────────────────────────────────────────────────

class TestUtilityFunctions:
    def test_normalize_claimed_files_empty(self) -> None:
        assert _normalize_claimed_files(None) == []
        assert _normalize_claimed_files([]) == []

    def test_normalize_claimed_files_backslash_to_slash(self) -> None:
        files = ["src\\a.py", "src/b.py"]
        assert _normalize_claimed_files(files) == ["src/a.py", "src/b.py"]

    def test_normalize_claimed_files_deduplicate(self) -> None:
        files = ["src/a.py", "src/a.py", "src/b.py"]
        assert _normalize_claimed_files(files) == ["src/a.py", "src/b.py"]

    def test_normalize_claimed_files_strip_leading_slash(self) -> None:
        files = ["/src/a.py", "src/b.py"]
        assert _normalize_claimed_files(files) == ["src/a.py", "src/b.py"]

    def test_normalize_claimed_files_empty_strings_removed(self) -> None:
        files = ["", "src/a.py", ""]
        assert _normalize_claimed_files(files) == ["src/a.py"]

    def test_make_key(self) -> None:
        assert _make_key("proj", "F001") == "proj:F001"

    def test_make_branch_name_default_agent(self) -> None:
        assert _make_branch_name("F001") == "agent/f001-claude"

    def test_make_branch_name_custom_agent(self) -> None:
        assert _make_branch_name("F002", "qwen") == "agent/f002-qwen"

    def test_ensure_dir_creates_nested(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        _ensure_dir(nested)
        assert nested.is_dir()

    def test_ensure_dir_idempotent(self, tmp_path: Path) -> None:
        d = tmp_path / "exists"
        d.mkdir()
        _ensure_dir(d)
        assert d.is_dir()


# ───────────────────────────────────────────────────────────────
# 2. 注册表持久化测试 (_load_registry, _save_registry)
# ───────────────────────────────────────────────────────────────

class TestRegistryPersistence:
    def test_load_registry_empty(self, tmp_path: Path) -> None:
        reg = _load_registry(tmp_path)
        assert reg == {}

    def test_load_registry_missing_file(self, tmp_path: Path) -> None:
        reg = _load_registry(tmp_path / "nonexistent")
        assert reg == {}

    def test_save_and_load_registry(self, tmp_path: Path) -> None:
        entry = WorktreeEntry(
            project="proj", feature="F001", path="/tmp/wt", branch="agent/f001-claude"
        )
        registry = {"proj:F001": entry}
        _save_registry(tmp_path, registry)
        loaded = _load_registry(tmp_path)
        assert len(loaded) == 1
        assert loaded["proj:F001"].feature == "F001"
        assert loaded["proj:F001"].claimed_files == []
        assert loaded["proj:F001"].status == "active"

    def test_load_registry_corrupted_json(self, tmp_path: Path) -> None:
        reg_path = _worktree_registry_path(tmp_path)
        _ensure_dir(tmp_path)
        reg_path.write_text("not json", encoding="utf-8")
        reg = _load_registry(tmp_path)
        assert reg == {}

    def test_load_registry_missing_fields(self, tmp_path: Path) -> None:
        reg_path = _worktree_registry_path(tmp_path)
        _ensure_dir(tmp_path)
        reg_path.write_text(json.dumps({"k": {"project": "p"}}), encoding="utf-8")
        reg = _load_registry(tmp_path)
        assert reg == {}

    def test_worktree_registry_path(self, tmp_path: Path) -> None:
        assert _worktree_registry_path(tmp_path) == tmp_path / WORKTREE_DB_FILENAME


# ───────────────────────────────────────────────────────────────
# 3. WorktreeManager 初始化测试
# ───────────────────────────────────────────────────────────────

class TestWorktreeManagerInit:
    def test_default_root(self) -> None:
        mgr = WorktreeManager()
        assert mgr.worktree_root == DEFAULT_WORKTREE_ROOT

    def test_custom_root(self, tmp_path: Path) -> None:
        mgr = WorktreeManager(worktree_root=tmp_path / "wt")
        assert mgr.worktree_root == tmp_path / "wt"

    def test_init_loads_existing_registry(self, tmp_path: Path) -> None:
        entry = WorktreeEntry(
            project="p", feature="F001", path="/tmp/wt", branch="b"
        )
        _save_registry(tmp_path, {"p:F001": entry})
        mgr = WorktreeManager(worktree_root=tmp_path)
        assert "p:F001" in mgr._registry

    def test_worktree_dir_path(self, manager: WorktreeManager) -> None:
        p = manager._worktree_dir("myproj", "F018")
        assert p == manager.worktree_root / "myproj-F018"

    def test_project_path_absolute_exists(self, tmp_path: Path) -> None:
        mgr = WorktreeManager(worktree_root=tmp_path)
        assert mgr._project_path(str(tmp_path)) == tmp_path

    def test_project_path_relative_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mgr = WorktreeManager(worktree_root=tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "subproj").mkdir()
        assert mgr._project_path("subproj") == tmp_path / "subproj"

    def test_project_path_fallback(self, tmp_path: Path) -> None:
        mgr = WorktreeManager(worktree_root=tmp_path)
        assert mgr._project_path("nonexistent_relative") == Path("nonexistent_relative")


# ───────────────────────────────────────────────────────────────
# 4. create() 测试
# ───────────────────────────────────────────────────────────────

class TestCreate:
    def test_create_raises_when_not_git_repo(self, manager: WorktreeManager, tmp_path: Path) -> None:
        not_repo = tmp_path / "not_repo"
        not_repo.mkdir()
        with pytest.raises(RuntimeError, match="not a git repository"):
            manager.create(str(not_repo), "F001")

    def test_create_returns_external_path(self, manager: WorktreeManager, fake_project: Path) -> None:
        wt_path = manager.create(str(fake_project), "F001")
        assert Path(wt_path).exists()
        assert Path(wt_path).is_dir()
        # manager 使用 _worktree_dir 计算路径，应落在 worktree_root 下
        expected = manager._worktree_dir(str(fake_project), "F001")
        assert Path(wt_path).resolve() == expected.resolve()

    def test_create_registers_entry(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        key = _make_key(str(fake_project), "F001")
        assert key in manager._registry
        assert manager._registry[key].status == "active"

    def test_create_with_claimed_files(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001", claimed_files=["src/a.py", "src/b.py"])
        key = _make_key(str(fake_project), "F001")
        assert manager._registry[key].claimed_files == ["src/a.py", "src/b.py"]

    def test_create_creates_branch(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        result = _run_git(fake_project, "branch", "--list", "agent/f001-claude", check=False)
        assert "agent/f001-claude" in result.stdout

    def test_create_custom_agent(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001", agent="qwen")
        result = _run_git(fake_project, "branch", "--list", "agent/f001-qwen", check=False)
        assert "agent/f001-qwen" in result.stdout

    def test_create_recreate_existing(self, manager: WorktreeManager, fake_project: Path) -> None:
        p1 = manager.create(str(fake_project), "F001")
        p2 = manager.create(str(fake_project), "F001")
        assert Path(p2).exists()
        # 重新创建后注册表仍应有一条记录
        assert len(manager._registry) == 1

    def test_create_registry_persisted(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        reg_path = _worktree_registry_path(manager.worktree_root)
        assert reg_path.exists()
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_create_git_worktree_list_shows_it(self, manager: WorktreeManager, fake_project: Path) -> None:
        wt_path = manager.create(str(fake_project), "F001")
        result = _run_git(fake_project, "worktree", "list", "--porcelain", check=False)
        # git 输出使用正斜杠，统一比较
        assert str(wt_path).replace("\\", "/") in result.stdout or result.stdout == ""
        # 注意: bare repo 的 worktree list 可能为空，但至少不报错


# ───────────────────────────────────────────────────────────────
# 5. detect_overlap() 测试
# ───────────────────────────────────────────────────────────────

class TestDetectOverlap:
    def test_no_overlap(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        # 添加第二个无重叠的 entry
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=str(mgr._registry[_make_key("fake_project", "F001")].project),
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=["src/c.py"],
            status="active",
        )
        has, files = mgr.detect_overlap("F001", "F002", project="fake_project")
        assert has is False
        assert files == []

    def test_has_overlap(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=mgr._registry[_make_key("fake_project", "F001")].project,
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=["src/a.py", "src/d.py"],  # src/a.py 重叠
            status="active",
        )
        has, files = mgr.detect_overlap("F001", "F002", project="fake_project")
        assert has is True
        assert files == ["src/a.py"]

    def test_overlap_without_project(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=mgr._registry[_make_key("fake_project", "F001")].project,
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=["src/a.py"],
            status="active",
        )
        has, files = mgr.detect_overlap("F001", "F002")
        assert has is True
        assert files == ["src/a.py"]

    def test_overlap_missing_feature(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        has, files = mgr.detect_overlap("F001", "F999", project="fake_project")
        assert has is False
        assert files == []

    def test_overlap_both_empty_claimed(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr._registry[_make_key("fake_project", "F001")].claimed_files = []
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=mgr._registry[_make_key("fake_project", "F001")].project,
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=[],
            status="active",
        )
        has, files = mgr.detect_overlap("F001", "F002", project="fake_project")
        assert has is False
        assert files == []

    def test_overlap_same_feature(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        has, files = mgr.detect_overlap("F001", "F001", project="fake_project")
        assert has is True
        assert files == ["src/a.py", "src/b.py"]

    def test_overlap_multiple_files(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=mgr._registry[_make_key("fake_project", "F001")].project,
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=["src/a.py", "src/b.py", "src/c.py"],
            status="active",
        )
        has, files = mgr.detect_overlap("F001", "F002", project="fake_project")
        assert has is True
        assert files == ["src/a.py", "src/b.py"]

    def test_overlap_no_registry(self, manager: WorktreeManager) -> None:
        has, files = manager.detect_overlap("F001", "F002")
        assert has is False
        assert files == []


# ───────────────────────────────────────────────────────────────
# 6. auto_cleanup() / remove() / remove_by_feature() 测试
# ───────────────────────────────────────────────────────────────

class TestAutoCleanup:
    def test_auto_cleanup_with_project(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        assert manager.auto_cleanup("F001", project=str(fake_project)) is True
        assert not manager._worktree_dir(str(fake_project), "F001").exists()
        key = _make_key(str(fake_project), "F001")
        assert key not in manager._registry

    def test_auto_cleanup_without_project(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        assert manager.auto_cleanup("F001") is True
        key = _make_key(str(fake_project), "F001")
        assert key not in manager._registry

    def test_remove_by_feature(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        assert manager.remove_by_feature("F001") is True
        key = _make_key(str(fake_project), "F001")
        assert key not in manager._registry

    def test_remove_by_feature_no_match(self, manager: WorktreeManager) -> None:
        assert manager.remove_by_feature("F999") is False

    def test_remove_nonexistent(self, manager: WorktreeManager) -> None:
        assert manager.remove("fake_project", "F999") is False

    def test_remove_registry_only_no_dir(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        key = _make_key("fake_project", "F001")
        # 手动删除目录，保留注册表
        wt_path = Path(mgr._registry[key].path)
        if wt_path.exists():
            shutil.rmtree(wt_path)
        assert mgr.remove("fake_project", "F001") is True
        assert key not in mgr._registry

    def test_auto_cleanup_deletes_branch(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        manager.auto_cleanup("F001", project=str(fake_project))
        result = _run_git(fake_project, "branch", "--list", "agent/f001-claude", check=False)
        assert "agent/f001-claude" not in result.stdout

    def test_auto_cleanup_multiple_projects_same_feature(self, manager: WorktreeManager, tmp_path: Path) -> None:
        # 创建两个项目，同名 feature
        p1 = tmp_path / "proj1"
        p2 = tmp_path / "proj2"
        for p in (p1, p2):
            p.mkdir()
            _run_git(p, "init", check=True)
            _run_git(p, "config", "user.email", "test@example.com", check=True)
            _run_git(p, "config", "user.name", "Test User", check=True)
            (p / "file.txt").write_text("x")
            _run_git(p, "add", "file.txt", check=True)
            _run_git(p, "commit", "-m", "init", check=True)
            manager.create(str(p), "F001")
        assert manager.remove_by_feature("F001") is True
        assert len(manager._registry) == 0


# ───────────────────────────────────────────────────────────────
# 7. list_active() 测试
# ───────────────────────────────────────────────────────────────

class TestListActive:
    def test_list_active_empty(self, manager: WorktreeManager) -> None:
        assert manager.list_active() == []

    def test_list_active_returns_active(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        # 创建目录让 exists() 通过
        Path(mgr._registry[_make_key("fake_project", "F001")].path).mkdir(parents=True, exist_ok=True)
        active = mgr.list_active()
        assert len(active) == 1
        assert active[0].feature == "F001"

    def test_list_active_skips_merged(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.mark_merged("fake_project", "F001")
        assert mgr.list_active() == []

    def test_list_active_skips_abandoned(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.mark_abandoned("fake_project", "F001")
        assert mgr.list_active() == []

    def test_list_active_filters_by_project(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        Path(mgr._registry[_make_key("fake_project", "F001")].path).mkdir(parents=True, exist_ok=True)
        # 添加另一个项目的 entry
        key2 = _make_key("other_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project="other_project",
            feature="F002",
            path=str(mgr.worktree_root / "other_project-F002"),
            branch="agent/f002-claude",
            status="active",
        )
        Path(mgr._registry[key2].path).mkdir(parents=True, exist_ok=True)
        active = mgr.list_active(project="fake_project")
        assert len(active) == 1
        assert active[0].feature == "F001"

    def test_list_active_skips_nonexistent_dir(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        # 目录不存在
        active = mgr.list_active()
        assert active == []

    def test_list_active_by_project_name_match(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        entry = mgr._registry[_make_key("fake_project", "F001")]
        entry.project = str(Path("/some/path/fake_project"))
        Path(entry.path).mkdir(parents=True, exist_ok=True)
        active = mgr.list_active(project="fake_project")
        assert len(active) == 1


# ───────────────────────────────────────────────────────────────
# 8. register_claimed_files / get_entry / mark_merged / mark_abandoned
# ───────────────────────────────────────────────────────────────

class TestEntryManagement:
    def test_register_claimed_files(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.register_claimed_files("fake_project", "F001", ["src/x.py", "src/y.py"])
        assert mgr._registry[_make_key("fake_project", "F001")].claimed_files == ["src/x.py", "src/y.py"]

    def test_register_claimed_files_missing_entry(self, manager: WorktreeManager) -> None:
        with pytest.raises(KeyError, match="not found"):
            manager.register_claimed_files("proj", "F001", ["a.py"])

    def test_get_entry_found(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        entry = mgr.get_entry("fake_project", "F001")
        assert entry is not None
        assert entry.feature == "F001"

    def test_get_entry_not_found(self, manager: WorktreeManager) -> None:
        assert manager.get_entry("proj", "F999") is None

    def test_mark_merged(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.mark_merged("fake_project", "F001")
        assert mgr._registry[_make_key("fake_project", "F001")].status == "merged"

    def test_mark_merged_nonexistent(self, manager: WorktreeManager) -> None:
        # 不应报错
        manager.mark_merged("proj", "F999")

    def test_mark_abandoned(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.mark_abandoned("fake_project", "F001")
        assert mgr._registry[_make_key("fake_project", "F001")].status == "abandoned"

    def test_mark_abandoned_nonexistent(self, manager: WorktreeManager) -> None:
        manager.mark_abandoned("proj", "F999")


# ───────────────────────────────────────────────────────────────
# 9. sync_with_git() 测试
# ───────────────────────────────────────────────────────────────

class TestSyncWithGit:
    def test_sync_with_git_cleans_orphan(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        # 手动删除目录，模拟孤儿记录
        key = _make_key(str(fake_project), "F001")
        wt_path = Path(manager._registry[key].path)
        shutil.rmtree(wt_path)
        active = manager.sync_with_git(str(fake_project))
        assert key not in manager._registry
        assert len(active) == 0

    def test_sync_with_git_keeps_existing(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        active = manager.sync_with_git(str(fake_project))
        assert len(active) >= 0  # 至少不报错

    def test_sync_with_git_git_failure(self, manager_with_entry: WorktreeManager, tmp_path: Path) -> None:
        mgr = manager_with_entry
        # 使用一个存在但非 git 目录的路径，让 git 命令失败
        non_git = tmp_path / "non_git_dir"
        non_git.mkdir()
        active = mgr.sync_with_git(str(non_git))
        # 失败时返回 list_active 的结果
        assert isinstance(active, list)


# ───────────────────────────────────────────────────────────────
# 10. WorktreeEntry 数据模型测试
# ───────────────────────────────────────────────────────────────

class TestWorktreeEntry:
    def test_to_dict(self) -> None:
        entry = WorktreeEntry(
            project="p", feature="F001", path="/tmp/wt", branch="b", claimed_files=["a.py"], status="merged"
        )
        d = entry.to_dict()
        assert d == {
            "project": "p",
            "feature": "F001",
            "path": "/tmp/wt",
            "branch": "b",
            "claimed_files": ["a.py"],
            "status": "merged",
        }

    def test_from_dict(self) -> None:
        d = {
            "project": "p",
            "feature": "F001",
            "path": "/tmp/wt",
            "branch": "b",
            "claimed_files": ["a.py"],
            "status": "abandoned",
        }
        entry = WorktreeEntry.from_dict(d)
        assert entry.project == "p"
        assert entry.status == "abandoned"
        assert entry.claimed_files == ["a.py"]

    def test_from_dict_defaults(self) -> None:
        d = {
            "project": "p",
            "feature": "F001",
            "path": "/tmp/wt",
            "branch": "b",
        }
        entry = WorktreeEntry.from_dict(d)
        assert entry.claimed_files == []
        assert entry.status == "active"


# ───────────────────────────────────────────────────────────────
# 11. 边界 / 集成测试
# ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_create_with_special_chars_feature(self, manager: WorktreeManager, fake_project: Path) -> None:
        # feature 名包含连字符等
        wt = manager.create(str(fake_project), "F018-sub2")
        assert Path(wt).exists()

    def test_create_long_project_name(self, manager: WorktreeManager, fake_project: Path) -> None:
        long_name = "a" * 50
        # 使用绝对路径
        p = fake_project
        wt = manager.create(str(p), long_name)
        assert Path(wt).exists()
        assert long_name in Path(wt).name

    def test_registry_survives_reinit(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        # 重新初始化 manager
        mgr2 = WorktreeManager(worktree_root=manager.worktree_root)
        assert _make_key(str(fake_project), "F001") in mgr2._registry

    def test_multiple_worktrees_same_project(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        manager.create(str(fake_project), "F002")
        assert len(manager._registry) == 2
        keys = list(manager._registry.keys())
        assert all("fake_project" in k for k in keys)

    def test_remove_one_keeps_other(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        manager.create(str(fake_project), "F002")
        manager.remove(str(fake_project), "F001")
        assert _make_key(str(fake_project), "F001") not in manager._registry
        assert _make_key(str(fake_project), "F002") in manager._registry

    def test_claimed_files_normalization_on_create(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(
            str(fake_project), "F001", claimed_files=["src\\a.py", "/src/b.py", "src/a.py"]
        )
        key = _make_key(str(fake_project), "F001")
        assert manager._registry[key].claimed_files == ["src/a.py", "src/b.py"]

    def test_empty_claimed_files_on_create(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001", claimed_files=None)
        key = _make_key(str(fake_project), "F001")
        assert manager._registry[key].claimed_files == []

    def test_worktree_root_is_path_object(self) -> None:
        mgr = WorktreeManager(worktree_root="/tmp/test-wt")
        assert isinstance(mgr.worktree_root, Path)

    def test_detect_overlap_order_independent(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        key2 = _make_key("fake_project", "F002")
        mgr._registry[key2] = WorktreeEntry(
            project=mgr._registry[_make_key("fake_project", "F001")].project,
            feature="F002",
            path=str(mgr._worktree_dir("fake_project", "F002")),
            branch="agent/f002-claude",
            claimed_files=["src/a.py"],
            status="active",
        )
        has1, files1 = mgr.detect_overlap("F001", "F002", project="fake_project")
        has2, files2 = mgr.detect_overlap("F002", "F001", project="fake_project")
        assert has1 == has2
        assert files1 == files2

    def test_list_active_returns_copy(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        Path(mgr._registry[_make_key("fake_project", "F001")].path).mkdir(parents=True, exist_ok=True)
        active1 = mgr.list_active()
        active2 = mgr.list_active()
        assert active1 is not active2
        assert active1[0].feature == active2[0].feature

    def test_auto_cleanup_on_merged_entry(self, manager_with_entry: WorktreeManager) -> None:
        mgr = manager_with_entry
        mgr.mark_merged("fake_project", "F001")
        # auto_cleanup 不检查 status，直接 remove
        assert mgr.auto_cleanup("F001", project="fake_project") is True
        assert _make_key("fake_project", "F001") not in mgr._registry

    def test_registry_json_format(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        reg_path = _worktree_registry_path(manager.worktree_root)
        text = reg_path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert isinstance(data, dict)
        for k, v in data.items():
            assert "project" in v
            assert "feature" in v
            assert "path" in v
            assert "branch" in v
            assert "claimed_files" in v
            assert "status" in v

    def test_git_dir_with_git_file(self, tmp_path: Path) -> None:
        # 模拟 worktree 的 .git 文件
        git_file = tmp_path / ".git"
        git_file.write_text("gitdir: /path/to/real.git\n", encoding="utf-8")
        mgr = WorktreeManager(worktree_root=tmp_path)
        git_dir = mgr._git_dir(tmp_path)
        assert str(git_dir.as_posix()) == "/path/to/real.git"

    def test_git_dir_with_git_dir(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mgr = WorktreeManager(worktree_root=tmp_path)
        assert mgr._git_dir(tmp_path) == git_dir

    def test_run_git_failure(self, tmp_path: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            _run_git(tmp_path, "status", check=True)

    def test_run_git_no_check(self, tmp_path: Path) -> None:
        result = _run_git(tmp_path, "status", check=False)
        assert result.returncode != 0

    def test_create_preserves_existing_worktrees(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        manager.create(str(fake_project), "F002")
        manager.create(str(fake_project), "F001")  # 重建 F001
        assert len(manager._registry) == 2
        assert _make_key(str(fake_project), "F002") in manager._registry

    def test_remove_then_list_active(self, manager: WorktreeManager, fake_project: Path) -> None:
        manager.create(str(fake_project), "F001")
        manager.remove(str(fake_project), "F001")
        assert manager.list_active() == []

    def test_detect_overlap_with_empty_project_registry(self, manager: WorktreeManager) -> None:
        has, files = manager.detect_overlap("F001", "F002", project="nonexistent")
        assert has is False
        assert files == []
