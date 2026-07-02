"""src/repo_map.py — Aider-style repository map generator.

W3-E05: Scans a project directory, builds a file dependency graph using
AST-based import parsing, and identifies files affected by a given change
scope. Returns structured file lists for Agent context injection.

Core API:
    generate_map(project_dir) → RepoMap
        RepoMap.files[]          — list of file metadata dicts
        RepoMap.dependencies{}    — path → list of direct imports
        RepoMap.affected_by(change_files) → list of affected file paths

Design references:
    - Aider's RepoMap (https://aider.chat)
    - PRD §3.3 / §6 Agentic Search / context injection
"""

from __future__ import annotations

import ast
import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ───────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────


def _norm(path: str) -> str:
    """Normalize a path to use forward slashes (cross-platform safe key)."""
    return path.replace("\\", "/")


# ───────────────────────────────────────────────────────────────
# Constants / defaults
# ───────────────────────────────────────────────────────────────

# Default ignore patterns (gitignore-style shell globs)
DEFAULT_IGNORE_PATTERNS: List[str] = [
    ".git",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    "*.egg-info",
    ".eggs",
    ".logs",
]

# Extensions recognised as parseable source files
PYTHON_EXTENSIONS: Set[str] = {".py", ".pyi", ".pyx"}


# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────


@dataclass
class FileInfo:
    """Metadata about a single file in the repository."""

    path: str  # relative path from project root
    language: str = "unknown"
    symbols: List[str] = field(default_factory=list)  # top-level classes / functions
    imports: List[str] = field(default_factory=list)  # modules imported
    exports: List[str] = field(default_factory=list)  # __all__ entries or public symbols
    size_bytes: int = 0
    lines: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "symbols": self.symbols,
            "imports": self.imports,
            "exports": self.exports,
            "size_bytes": self.size_bytes,
            "lines": self.lines,
        }


@dataclass
class RepoMap:
    """Repository map containing file metadata and dependency graph.

    Attributes:
        project_dir: absolute path to the scanned project root.
        files: list of FileInfo for every source file discovered.
        dependencies: dict mapping file path → list of *direct* import
                      targets (resolved to repo-relative paths where possible).
        reverse_deps: dict mapping file path → list of files that import it.
    """

    project_dir: str = ""
    files: List[FileInfo] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    reverse_deps: Dict[str, List[str]] = field(default_factory=dict)

    # ── Public helpers ─────────────────────────────────────────

    def get_file(self, path: str) -> Optional[FileInfo]:
        """Return FileInfo for *path* (repo-relative), or None."""
        for f in self.files:
            if f.path == path:
                return f
        return None

    def affected_by(
        self,
        change_files: List[str],
        transitive: bool = True,
        max_depth: int = 10,
    ) -> List[str]:
        """Return files affected when *change_files* are modified.

        By default computes the transitive closure (all downstream files
        that directly or indirectly depend on any of *change_files*).

        Args:
            change_files: repo-relative paths that changed.
            transitive: if True, follow the dependency graph transitively.
            max_depth: safety cap on graph depth for cyclic graphs.

        Returns:
            Sorted list of repo-relative file paths.
        """
        if not change_files:
            return []

        root = {_norm(cf) for cf in change_files}
        visited: Set[str] = set()
        queue: List[str] = list(root)

        for _depth in range(max_depth):
            if not queue:
                break
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if transitive:
                for dependent in self.reverse_deps.get(current, []):
                    if dependent not in visited:
                        queue.append(dependent)

        # Only return downstream *affected* files (exclude the change_files
        # themselves, though callers may re-add them if desired).
        affected = visited - root
        return sorted(affected)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict (for context injection)."""
        return {
            "project_dir": self.project_dir,
            "files": [f.to_dict() for f in self.files],
            "dependencies": self.dependencies,
            "reverse_deps": self.reverse_deps,
        }

    def summary(self) -> str:
        """One-line summary for fast logging."""
        return (
            f"RepoMap(project={self.project_dir!r}, "
            f"files={len(self.files)}, "
            f"deps={len(self.dependencies)})"
        )


# ───────────────────────────────────────────────────────────────
# AST helper — extract imports & top-level symbols
# ───────────────────────────────────────────────────────────────


def _resolve_relative_import(
    base_module: str,
    import_name: str,
    level: int,
) -> Optional[str]:
    """Resolve a relative import like ``from .foo import bar``.

    Returns the absolute dotted module name, or None on failure.
    """
    if level == 0:
        return import_name
    parts = base_module.split(".")
    if level > len(parts):
        return None  # relative beyond top-level
    prefix = ".".join(parts[: len(parts) - (level - 1)]) if level > 1 else ".".join(parts)
    if import_name:
        return f"{prefix}.{import_name}"
    return prefix or ""


def _module_to_repo_path(module: str) -> Optional[str]:
    """Convert a dotted module name to a repo-relative file path.

    E.g. 'foo.bar.baz' → 'foo/bar/baz.py' or 'foo/bar/baz/__init__.py'.
    Returns the first variant that is common (we prefer .py over __init__.py).
    """
    parts = module.split(".")
    # module file
    return os.path.join(*parts) + ".py"


def _parse_python_source(
    file_path: str,
    content: str,
    repo_root: str,
) -> FileInfo:
    """Parse a single Python source file and return FileInfo."""

    info = FileInfo(
        path=file_path,
        language="python",
        size_bytes=len(content.encode("utf-8")),
        lines=content.count("\n") + 1,
    )

    # Derive a "module name" from the path (repo-relative, without extension)
    rel = Path(file_path).with_suffix("")
    base_module = str(rel).replace(os.sep, ".").replace("/", ".")

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return info  # unparseable file — return what we have

    imports: List[str] = []
    symbols: List[str] = []
    exports: List[str] = []

    for node in ast.walk(tree):
        # ── imports ─────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            resolved = _resolve_relative_import(base_module, module, level)
            if resolved:
                imports.append(resolved)

        # ── top-level symbols ────────────────────────────────
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            symbols.append(node.name)

        # ── __all__ exports ──────────────────────────────────
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                exports.append(elt.value)

    # De-duplicate while preserving order
    info.imports = list(dict.fromkeys(imports))
    info.symbols = list(dict.fromkeys(symbols))
    info.exports = list(dict.fromkeys(exports))
    return info


# ───────────────────────────────────────────────────────────────
# File-system helpers
# ───────────────────────────────────────────────────────────────


def _detect_language(file_path: str) -> str:
    """Return a language tag based on file extension."""
    ext = Path(file_path).suffix.lower()
    _lang_map: Dict[str, str] = {
        ".py": "python",
        ".pyi": "python",
        ".pyx": "cython",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".rst": "restructuredtext",
        ".cfg": "ini",
        ".ini": "ini",
        ".sh": "shell",
        ".bash": "shell",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
    }
    return _lang_map.get(ext, "unknown")


def _should_ignore(rel_path: str, ignore_patterns: List[str]) -> bool:
    """Return True if *rel_path* matches any ignore pattern."""
    parts = rel_path.replace("\\", "/").split("/")
    for pattern in ignore_patterns:
        # Check both against the full path and each path component
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def _collect_files(
    project_dir: str,
    ignore_patterns: List[str],
) -> List[str]:
    """Walk *project_dir* and return repo-relative file paths (sorted)."""
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        # Filter directories in-place so we don't descend into ignored dirs
        filtered: List[str] = []
        for d in dirnames:
            try:
                rel = os.path.relpath(os.path.join(dirpath, d), project_dir)
            except ValueError:
                continue
            if not _should_ignore(rel, ignore_patterns):
                filtered.append(d)
        dirnames[:] = filtered

        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            try:
                rel_path = os.path.relpath(abs_path, project_dir)
            except ValueError:
                continue  # skip files on different mounts / special devices
            if _should_ignore(rel_path, ignore_patterns):
                continue
            files.append(_norm(rel_path))
    return sorted(files)


# ───────────────────────────────────────────────────────────────
# Dependency resolver
# ───────────────────────────────────────────────────────────────


def _match_import_to_repo_path(
    module_candidate: str,
    all_paths: Set[str],
) -> Optional[str]:
    """Match a resolved module path to a known repo-relative path.

    Strategy:
      1. Exact match (e.g. ``src/models.py`` → ``src/models.py``).
      2. Suffix match — any path ending with the candidate
         (e.g. ``models.py`` → ``src/models.py``).
      3. ``__init__.py`` package match
         (e.g. ``src/foo/bar/__init__.py``).
      4. Prefix-based package fallback.
    """
    if not module_candidate:
        return None

    module_candidate = _norm(module_candidate)

    # 1. Exact match
    if module_candidate in all_paths:
        return module_candidate

    # 2. Suffix match (handles subdirectory imports)
    suffix = module_candidate  # already normalized
    for path in all_paths:
        norm = _norm(path)
        if norm.endswith("/" + suffix) or norm == suffix:
            return path

    # 3. __init__.py variant
    parts = module_candidate.replace(".py", "").split("/")
    init_candidate = "/".join(parts + ["__init__.py"])
    if init_candidate in all_paths:
        return init_candidate
    # suffix-init
    for path in all_paths:
        norm = _norm(path)
        if norm.endswith("/" + init_candidate) or norm == init_candidate:
            return path

    # 4. Prefix package fallback
    for p in range(len(parts), 0, -1):
        prefix = "/".join(parts[:p])
        init_file = prefix + "/__init__.py"
        if init_file in all_paths:
            return init_file
        py_file = prefix + ".py"
        if py_file in all_paths:
            return py_file
        # suffix variants
        for path in all_paths:
            norm = _norm(path)
            if norm.endswith("/" + init_file) or norm.endswith("/" + py_file):
                return path

    return None


def _build_dependency_graph(
    files: List[FileInfo],
    all_paths: Set[str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Build forward and reverse dependency maps.

    For each Python file, resolve its imports to repo-relative paths
    by matching against the set of known file paths.
    """
    dependencies: Dict[str, List[str]] = {}
    reverse_deps: Dict[str, List[str]] = {}

    for finfo in files:
        deps: List[str] = []
        for imp in finfo.imports:
            candidate = _module_to_repo_path(imp)
            matched = _match_import_to_repo_path(candidate or "", all_paths)
            if matched and matched != finfo.path:
                deps.append(matched)

        fpath = _norm(finfo.path)
        dependencies[fpath] = [_norm(d) for d in dict.fromkeys(deps)]

        # Build reverse deps
        for dep in deps:
            ndep = _norm(dep)
            rd_list = reverse_deps.setdefault(ndep, [])
            if fpath not in rd_list:
                rd_list.append(fpath)

    return dependencies, reverse_deps


# ───────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────


def generate_map(
    project_dir: str,
    *,
    ignore_patterns: Optional[List[str]] = None,
    include_non_python: bool = True,
) -> RepoMap:
    """Generate a RepoMap for *project_dir*.

    Scans the directory tree, parses Python source files with ``ast``,
    and builds a dependency graph.

    Args:
        project_dir: absolute or relative path to the project root.
        ignore_patterns: optional override for default ignore globs.
        include_non_python: if True, include non-Python files as
                            metadata-only entries.

    Returns:
        A fully populated ``RepoMap`` instance.
    """
    project_dir = os.path.abspath(project_dir)

    if ignore_patterns is None:
        ignore_patterns = list(DEFAULT_IGNORE_PATTERNS)

    # 1. Collect file paths
    rel_paths = _collect_files(project_dir, ignore_patterns)
    all_path_set = set(rel_paths)

    # 2. Parse files and build FileInfo
    files: List[FileInfo] = []
    for rel_path in rel_paths:
        ext = Path(rel_path).suffix.lower()
        language = _detect_language(rel_path)

        if ext in PYTHON_EXTENSIONS:
            abs_path = os.path.join(project_dir, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except (OSError, PermissionError):
                content = ""
            finfo = _parse_python_source(rel_path, content, project_dir)
            files.append(finfo)
        elif include_non_python:
            abs_path = os.path.join(project_dir, rel_path)
            size = 0
            lines = 0
            try:
                st = os.stat(abs_path)
                size = st.st_size
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = sum(1 for _ in fh)
            except (OSError, PermissionError):
                pass
            files.append(
                FileInfo(
                    path=rel_path,
                    language=language,
                    size_bytes=size,
                    lines=lines,
                )
            )

    # 3. Build dependency graph
    dependencies, reverse_deps = _build_dependency_graph(files, all_path_set)

    return RepoMap(
        project_dir=project_dir,
        files=files,
        dependencies=dependencies,
        reverse_deps=reverse_deps,
    )


# ───────────────────────────────────────────────────────────────
# Convenience functions for context injection
# ───────────────────────────────────────────────────────────────


def map_to_context_payload(
    repo_map: RepoMap,
    *,
    max_files: int = 200,
    include_deps: bool = True,
) -> Dict[str, Any]:
    """Convert a RepoMap into a compact context-injection payload.

    This is the primary interface used by ContextManager / Agentic Search
    to inject repository awareness into agent prompts.

    Args:
        repo_map: populated RepoMap.
        max_files: cap on the number of files included.
        include_deps: if True, also include the dependency graph.

    Returns:
        Dict suitable for inclusion in a context layer.
    """
    files_slice = [
        f.to_dict()
        for f in repo_map.files[:max_files]
    ]
    payload: Dict[str, Any] = {
        "project_dir": repo_map.project_dir,
        "total_files": len(repo_map.files),
        "files": files_slice,
    }
    if include_deps:
        payload["dependencies"] = repo_map.dependencies
    return payload


def find_importers(
    repo_map: RepoMap,
    target_path: str,
) -> List[str]:
    """Return all files that import *target_path*.

    Convenience wrapper around reverse_deps lookup.
    """
    return sorted(repo_map.reverse_deps.get(_norm(target_path), []))
