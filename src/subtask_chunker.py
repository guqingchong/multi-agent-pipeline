"""src/subtask_chunker.py — Long-task chunker for multi-agent pipeline.

W2-A04: Splits large tasks into independently executable subtasks,
tracks dependencies, and supports checkpoint-based resume.

Key capabilities:
  - Subtask dataclass: id, parent_task_id, order, goal, expected_files, timeout, depends_on
  - SubtaskChunker: chunk() splits a task at module boundaries (≤300 lines each)
  - resume_from_checkpoint() uses Checkpointer to skip completed subtasks
  - Topological sort ensures dependency ordering
  - Each subtask independently timeout-able
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from checkpointer import Checkpointer
except ImportError:
    from src.checkpointer import Checkpointer


# ───────────────────────────────────────────────────────────────
# Data Models
# ───────────────────────────────────────────────────────────────

@dataclass
class Subtask:
    """A single independently-executable unit of work within a larger task.

    Attributes:
        id: Unique subtask identifier (e.g. 'task-1-st-3').
        parent_task_id: The parent task this subtask belongs to.
        order: Execution order index (0-based, after topological sort).
        goal: Human-readable description of what this subtask should achieve.
        expected_files: List of file paths this subtask is expected to produce.
        timeout: Maximum execution time in seconds (0 = no limit).
        depends_on: IDs of subtasks that must complete before this one.
        estimated_lines: Estimated lines of code this subtask will produce.
        module_name: Logical module/feature name this subtask belongs to.
        context: Arbitrary extra data (imports, shared state, etc.).
    """

    id: str = ""
    parent_task_id: str = ""
    order: int = 0
    goal: str = ""
    expected_files: List[str] = field(default_factory=list)
    timeout: float = 0.0
    depends_on: List[str] = field(default_factory=list)
    estimated_lines: int = 0
    module_name: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "parent_task_id": self.parent_task_id,
            "order": self.order,
            "goal": self.goal,
            "expected_files": self.expected_files,
            "timeout": self.timeout,
            "depends_on": self.depends_on,
            "estimated_lines": self.estimated_lines,
            "module_name": self.module_name,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Subtask:
        """Deserialize from a dictionary."""
        return cls(
            id=data.get("id", ""),
            parent_task_id=data.get("parent_task_id", ""),
            order=data.get("order", 0),
            goal=data.get("goal", ""),
            expected_files=data.get("expected_files", []),
            timeout=data.get("timeout", 0.0),
            depends_on=data.get("depends_on", []),
            estimated_lines=data.get("estimated_lines", 0),
            module_name=data.get("module_name", ""),
            context=data.get("context", {}),
        )


# ───────────────────────────────────────────────────────────────
# SubtaskChunker
# ───────────────────────────────────────────────────────────────

class SubtaskChunker:
    """Splits large tasks into independently executable subtasks.

    The chunker analyses a task description, identifies functional module
    boundaries, estimates line counts, detects inter-module dependencies,
    and produces a topologically sorted list of ``Subtask`` objects.

    Each subtask is sized to stay within ``max_subtask_lines`` (default 300)
    to keep individual agent sessions manageable.

    Usage::

        chunker = SubtaskChunker(checkpointer=cp)
        subtasks = chunker.chunk(task_dict, max_subtask_lines=300)
        remaining = chunker.resume_from_checkpoint("task-1")
    """

    # ── Line estimation heuristics ──────────────────────────────

    # Average lines per "module" mentioned in a task description.
    _DEFAULT_LINES_PER_MODULE = 80

    # Patterns that indicate module / file boundaries.
    _MODULE_BOUNDARY_PATTERNS: List[str] = [
        r"\b(?:file|module|component|class|service|endpoint|handler|route)\s*[:=]?\s*[\"'`]?(\w+(?:[./]\w+)*\.?\w*)",
        r"\b(?:create|implement|build|write|add|modify|update)\s+(?:a\s+)?(?:new\s+)?[\"'`]?(\w+(?:[./]\w+)*\.?\w*)",
        r"\b(?:src|lib|app|tests?|docs?)/\S+",
        r"\bFeature\s*\d*\s*:\s*(.+)",
        r"\b(?:Task|Step|Part)\s*\d+\s*:\s*(.+)",
    ]

    # Patterns for detecting dependency relationships.
    _DEPENDENCY_PATTERNS: List[Tuple[str, str]] = [
        # (<dependency>, <depender>)
        (r"(?:depends\s+on|requires|imports?\s+from|uses)\s+[\"'`]?(\w+)", r"(\w+)"),
        (r"(\w+)\s+(?:must|should|needs?\s+to)\s+be?\s+(?:done|completed|finished|ready)\s+before\s+(\w+)", r"(\w+)"),
    ]

    def __init__(
        self,
        checkpointer: Optional[Checkpointer] = None,
    ) -> None:
        """Initialize the chunker.

        Args:
            checkpointer: Optional ``Checkpointer`` instance for
                resume-from-checkpoint support.  Required for
                ``resume_from_checkpoint()``.
        """
        self._checkpointer = checkpointer

    # ── Public API ──────────────────────────────────────────────

    def chunk(
        self,
        task: Union[Dict[str, Any], str],
        max_subtask_lines: int = 300,
    ) -> List[Subtask]:
        """Split a task into topologically sorted subtasks.

        The task is analysed to identify functional modules, estimate
        line counts, detect dependencies, and produce a sorted list
        where each subtask is ≤ *max_subtask_lines* lines.

        Args:
            task: A task description.  Either a string (free-form
                description) or a dictionary with keys such as
                ``id``, ``goal``, ``files``, ``modules``, ``description``.
            max_subtask_lines: Maximum estimated lines per subtask
                (default 300).  Modules exceeding this are further split.

        Returns:
            List of ``Subtask`` objects, topologically sorted by
            dependency order.  Subtasks with no inter-dependencies
            are ordered by their position in the original description.
        """
        # Normalize task input
        task_dict = self._normalize_task(task)

        task_id = task_dict.get("id", "task")
        goal = task_dict.get("goal", task_dict.get("description", ""))
        modules = self._extract_modules(task_dict)
        dependencies = self._detect_dependencies(modules, task_dict)
        estimated_total = self._estimate_total_lines(task_dict, modules)

        # Split large modules into sub-modules if necessary
        all_modules = self._split_large_modules(
            modules, max_subtask_lines, estimated_total
        )

        # Apply detected dependencies to each module
        for mod in all_modules:
            mod_name = mod.get("name", "")
            if mod_name in dependencies and "depends_on_modules" not in mod:
                mod["depends_on_modules"] = dependencies[mod_name]

        # Build subtasks
        subtasks: List[Subtask] = []
        for idx, mod in enumerate(all_modules):
            st = Subtask(
                id=f"{task_id}-st-{idx + 1}",
                parent_task_id=task_id,
                order=idx,
                goal=mod.get("goal", ""),
                expected_files=mod.get("files", []),
                timeout=mod.get("timeout", 0.0),
                depends_on=self._resolve_dep_ids(
                    mod.get("depends_on_modules", []), all_modules, task_id
                ),
                estimated_lines=mod.get("lines", self._DEFAULT_LINES_PER_MODULE),
                module_name=mod.get("name", f"module-{idx + 1}"),
                context=mod.get("context", {}),
            )
            subtasks.append(st)

        # Topological sort
        subtasks = self._topological_sort(subtasks)

        # Re-assign order based on sorted positions
        for i, st in enumerate(subtasks):
            st.order = i

        return subtasks

    def resume_from_checkpoint(self, task_id: str) -> List[Subtask]:
        """Return the remaining subtasks after the last successful checkpoint.

        Requires a ``Checkpointer`` to have been provided during init.

        Args:
            task_id: The parent task identifier.

        Returns:
            List of ``Subtask`` IDs still remaining.  Returns an empty
            list if all subtasks completed successfully.

        Raises:
            RuntimeError: If no checkpointer was configured.
            ValueError: If the task has no subtask records.
        """
        if self._checkpointer is None:
            raise RuntimeError(
                "resume_from_checkpoint requires a Checkpointer. "
                "Pass one to SubtaskChunker(checkpointer=...)."
            )

        # Retrieve all known subtask IDs for this task from checkpoints
        records = self._checkpointer.list_checkpoints(task_id, limit=200)
        if not records:
            return []

        # Collect all unique subtask IDs in insertion order (oldest first)
        seen: set = set()
        all_ids: List[str] = []
        for rec in reversed(records):  # oldest first
            if rec.subtask_id not in seen:
                seen.add(rec.subtask_id)
                all_ids.append(rec.subtask_id)

        # Use the checkpointer's resume logic
        remaining_ids = self._checkpointer.resume(task_id, all_ids)

        # Build Subtask stubs for remaining
        remaining: List[Subtask] = []
        for idx, st_id in enumerate(remaining_ids):
            # Try to reconstruct from last checkpoint if available
            last_rec = self._checkpointer.get_last_success(task_id)
            st = Subtask(
                id=st_id,
                parent_task_id=task_id,
                order=idx,
                goal=last_rec.state_dict().get("goal", "") if last_rec else "",
                expected_files=last_rec.state_dict().get("expected_files", [])
                if last_rec
                else [],
            )
            remaining.append(st)

        return remaining

    # ── Normalization ───────────────────────────────────────────

    @staticmethod
    def _normalize_task(task: Union[Dict[str, Any], str]) -> Dict[str, Any]:
        """Normalize task input to a dictionary."""
        if isinstance(task, dict):
            return dict(task)
        return {"description": task, "id": "task"}

    # ── Module extraction ───────────────────────────────────────

    def _extract_modules(self, task_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract functional modules from the task description.

        Looks for:
        1. Explicit ``modules`` key in the task dict.
        2. ``files`` key treated as module list.
        3. Module boundary patterns in the description text.
        """
        # Case 1: Explicit modules key
        if "modules" in task_dict:
            return self._normalize_module_list(task_dict["modules"])

        # Case 2: Files as modules
        if "files" in task_dict:
            files = task_dict["files"]
            if isinstance(files, list):
                return [
                    {"name": f, "files": [f], "goal": f"Implement {f}"}
                    for f in files
                ]

        # Case 3: Pattern-based extraction from description
        description = task_dict.get(
            "description", task_dict.get("goal", "")
        )
        if not description:
            return []

        modules: List[Dict[str, Any]] = []
        seen_names: set = set()

        for pattern in self._MODULE_BOUNDARY_PATTERNS:
            for match in re.finditer(pattern, description, re.IGNORECASE):
                name = match.group(1).strip().rstrip(".,;:")
                if name and name not in seen_names:
                    seen_names.add(name)
                    modules.append({
                        "name": name,
                        "files": [name],
                        "goal": f"Implement {name}",
                    })

        # If no modules found, treat whole task as one module
        if not modules:
            modules.append({
                "name": "main",
                "files": [],
                "goal": description,
            })

        return modules

    @staticmethod
    def _normalize_module_list(
        raw_modules: List[Any],
    ) -> List[Dict[str, Any]]:
        """Normalize a list of module entries to dicts."""
        result: List[Dict[str, Any]] = []
        for m in raw_modules:
            if isinstance(m, dict):
                result.append(dict(m))
            elif isinstance(m, str):
                result.append({
                    "name": m,
                    "files": [m],
                    "goal": f"Implement {m}",
                })
        return result

    # ── Line estimation ─────────────────────────────────────────

    @staticmethod
    def _estimate_total_lines(
        task_dict: Dict[str, Any],
        modules: List[Dict[str, Any]],
    ) -> int:
        """Estimate total lines for the entire task."""
        # If the task provides an explicit estimate, use it
        if "estimated_lines" in task_dict:
            return int(task_dict["estimated_lines"])

        if "total_lines" in task_dict:
            return int(task_dict["total_lines"])

        # Sum module-level estimates or use default
        total = 0
        for mod in modules:
            total += mod.get("lines", SubtaskChunker._DEFAULT_LINES_PER_MODULE)

        return total if total > 0 else SubtaskChunker._DEFAULT_LINES_PER_MODULE

    # ── Large-module splitting ──────────────────────────────────

    def _split_large_modules(
        self,
        modules: List[Dict[str, Any]],
        max_lines: int,
        total_estimated: int,
    ) -> List[Dict[str, Any]]:
        """Split any module exceeding *max_lines* into sub-modules."""
        result: List[Dict[str, Any]] = []
        for mod in modules:
            lines = mod.get("lines", self._DEFAULT_LINES_PER_MODULE)
            if lines <= max_lines:
                result.append(mod)
                continue

            # Split into chunks
            num_chunks = (lines + max_lines - 1) // max_lines
            base_name = mod.get("name", "module")
            base_files = mod.get("files", [])
            base_goal = mod.get("goal", "")

            for i in range(num_chunks):
                chunk_name = f"{base_name}-part-{i + 1}"
                # Distribute files: each chunk gets a subset
                chunk_files = (
                    [f"{f}#part{i + 1}" for f in base_files]
                    if base_files
                    else []
                )
                chunk_goal = (
                    f"{base_goal} (part {i + 1}/{num_chunks})"
                    if base_goal
                    else f"Implement {chunk_name}"
                )
                result.append({
                    "name": chunk_name,
                    "files": chunk_files,
                    "goal": chunk_goal,
                    "lines": min(max_lines, lines - i * max_lines),
                    "depends_on_modules": (
                        [f"{base_name}-part-{i}"] if i > 0 else []
                    ),
                    "context": mod.get("context", {}),
                })

        return result

    # ── Dependency detection ────────────────────────────────────

    def _detect_dependencies(
        self,
        modules: List[Dict[str, Any]],
        task_dict: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """Detect inter-module dependencies.

        Returns a mapping: module_name → list of module_names it depends on.
        """
        # Explicit dependencies from the task dict take priority
        if "dependencies" in task_dict:
            return self._parse_explicit_deps(task_dict["dependencies"])

        deps: Dict[str, List[str]] = defaultdict(list)
        description = task_dict.get(
            "description", task_dict.get("goal", "")
        )

        # Use pattern-based detection on the description
        for dep_pattern, target_pattern in self._DEPENDENCY_PATTERNS:
            for match in re.finditer(dep_pattern, description, re.IGNORECASE):
                dep_name = match.group(1)
                # Find the target (depender) by looking nearby
                remaining_text = description[match.end():]
                target_match = re.search(target_pattern, remaining_text)
                if target_match:
                    target_name = target_match.group(1)
                    if target_name != dep_name:
                        deps[target_name].append(dep_name)

        return dict(deps)

    @staticmethod
    def _parse_explicit_deps(
        raw_deps: Any,
    ) -> Dict[str, List[str]]:
        """Parse explicit dependency declarations."""
        if isinstance(raw_deps, dict):
            return {str(k): [str(d) for d in (v if isinstance(v, list) else [v])]
                    for k, v in raw_deps.items()}
        if isinstance(raw_deps, list):
            result: Dict[str, List[str]] = {}
            for item in raw_deps:
                if isinstance(item, dict):
                    result.update(item)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    target, dep = item[0], item[1]
                    result.setdefault(str(target), []).append(str(dep))
            return result
        return {}

    # ── Dependency ID resolution ────────────────────────────────

    @staticmethod
    def _resolve_dep_ids(
        dep_module_names: List[str],
        all_modules: List[Dict[str, Any]],
        task_id: str,
    ) -> List[str]:
        """Convert module-name dependencies to subtask ID references."""
        name_to_st_id: Dict[str, str] = {}
        for idx, mod in enumerate(all_modules):
            name_to_st_id[mod.get("name", "")] = f"{task_id}-st-{idx + 1}"

        resolved: List[str] = []
        for name in dep_module_names:
            st_id = name_to_st_id.get(name)
            if st_id:
                resolved.append(st_id)
            else:
                # Partial match: find modules whose names contain this string
                for mod_name, sid in name_to_st_id.items():
                    if name in mod_name and sid not in resolved:
                        resolved.append(sid)
        return resolved

    # ── Topological sort ────────────────────────────────────────

    @staticmethod
    def _topological_sort(subtasks: List[Subtask]) -> List[Subtask]:
        """Sort subtasks so that dependencies come before dependents.

        Uses Kahn's algorithm.  Subtasks with no dependencies come first;
        circular dependencies are broken by falling back to original order.
        """
        if not subtasks:
            return []

        id_to_st: Dict[str, Subtask] = {st.id: st for st in subtasks}

        # Build in-degree map
        in_degree: Dict[str, int] = {st.id: 0 for st in subtasks}
        adj: Dict[str, List[str]] = {st.id: [] for st in subtasks}

        for st in subtasks:
            for dep_id in st.depends_on:
                if dep_id in id_to_st:
                    adj[dep_id].append(st.id)
                    in_degree[st.id] += 1

        # Kahn's algorithm
        queue: deque = deque(
            st_id for st_id, deg in in_degree.items() if deg == 0
        )
        sorted_ids: List[str] = []

        while queue:
            current = queue.popleft()
            sorted_ids.append(current)
            for neighbor in adj.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If we have a cycle, fall back to original order for remaining
        if len(sorted_ids) < len(subtasks):
            original_order = {st.id: st.order for st in subtasks}
            remaining = [
                st_id for st_id in id_to_st if st_id not in sorted_ids
            ]
            remaining.sort(key=lambda sid: original_order.get(sid, 9999))
            sorted_ids.extend(remaining)

        # Map back to Subtask objects
        return [id_to_st[sid] for sid in sorted_ids]

    # ── Properties ──────────────────────────────────────────────

    @property
    def checkpointer(self) -> Optional[Checkpointer]:
        """Access the configured ``Checkpointer``, if any."""
        return self._checkpointer
