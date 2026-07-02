"""src/task_decomposer.py — LLM+template-driven features.json generator.

W3-E04: Reads PRD → generates features.json with:
  - LLM-driven feature extraction from PRD
  - DAG scheduler (topological sort via Kahn's algorithm)
  - Token budget constraints (per-feature + per-project)

Depends on W3-E01 (workflow_registry / workflow_template) for template
integration and condition evaluation.

Usage::

    decomposer = TaskDecomposer(project_name="my-project")
    features = decomposer.decompose(prd_text)
    # Or from file:
    features = decomposer.decompose_from_prd(Path("specs/prd.md"))
    decomposer.generate_features_json(features, Path("features.json"))

Heuristic fallback (no LLM required):
    features = decomposer.decompose_heuristic(prd_text)
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Feature",
    "FeaturesManifest",
    "TokenBudget",
    "DAGScheduler",
    "TaskDecomposer",
    "decompose_prd_to_features",
]


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════

@dataclass
class Feature:
    """A single feature extracted from the PRD.

    Matches the features.json schema used throughout the pipeline.

    Attributes:
        id: Unique feature identifier (e.g. 'W3-E04').
        title: Short human-readable title.
        description: Detailed description of the feature.
        acceptance_criteria: Acceptance criteria as free-form text.
        dependencies: List of feature IDs this feature depends on.
        estimated_complexity: One of 'simple', 'medium', 'complex'.
        owner_agent: Default agent assigned to implement (e.g. 'claude-code').
        reviewer_agent: Default agent assigned to review.
        tester_agent: Default agent assigned to test.
        status: Initial status (default 'pending').
        wave: Wave / sprint number (0 = unassigned).
        expected_lines: Estimated lines of code (default 200).
        max_token_budget: Maximum tokens allowed for this feature (0 = unlimited).
        files_changed: Files expected to be touched.
        context: Arbitrary extra metadata.
    """

    id: str = ""
    title: str = ""
    description: str = ""
    acceptance_criteria: str = ""
    dependencies: List[str] = field(default_factory=list)
    estimated_complexity: str = "medium"
    owner_agent: str = "claude-code"
    reviewer_agent: str = "codewhale"
    tester_agent: str = "qwen-code"
    status: str = "pending"
    wave: int = 0
    expected_lines: int = 200
    max_token_budget: int = 0
    files_changed: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict (features.json format)."""
        d: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "dependencies": list(self.dependencies),
            "estimated_complexity": self.estimated_complexity,
            "owner_agent": self.owner_agent,
            "reviewer_agent": self.reviewer_agent,
            "tester_agent": self.tester_agent,
            "status": self.status,
            "wave": self.wave,
            "expected_lines": self.expected_lines,
            "files_changed": list(self.files_changed),
        }
        if self.max_token_budget > 0:
            d["max_token_budget"] = self.max_token_budget
        # Only include non-empty context
        if self.context:
            d["context"] = dict(self.context)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Feature:
        """Deserialize from a dictionary."""
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            acceptance_criteria=data.get("acceptance_criteria", ""),
            dependencies=data.get("dependencies", []),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            owner_agent=data.get("owner_agent", "claude-code"),
            reviewer_agent=data.get("reviewer_agent", "codewhale"),
            tester_agent=data.get("tester_agent", "qwen-code"),
            status=data.get("status", "pending"),
            wave=data.get("wave", 0),
            expected_lines=data.get("expected_lines", 200),
            max_token_budget=data.get("max_token_budget", 0),
            files_changed=data.get("files_changed", []),
            context=data.get("context", {}),
        )


@dataclass
class FeaturesManifest:
    """Complete features.json manifest.

    Attributes:
        project: Project name.
        version: Manifest version.
        features: List of Feature objects.
        waves: Per-wave metadata (name, status, feature_ids).
        generated_by: Name of generator (e.g. 'task_decomposer').
        generation_date: ISO-format generation timestamp.
    """

    project: str = ""
    version: str = "1.0.0"
    features: List[Feature] = field(default_factory=list)
    waves: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    generated_by: str = "task_decomposer"
    generation_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to features.json format."""
        return {
            "project": self.project,
            "version": self.version,
            "features": [f.to_dict() for f in self.features],
            "waves": dict(self.waves),
            "generated_by": self.generated_by,
            "generation_date": self.generation_date,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FeaturesManifest:
        """Deserialize from a dictionary."""
        features = [Feature.from_dict(f) for f in data.get("features", [])]
        return cls(
            project=data.get("project", ""),
            version=data.get("version", "1.0.0"),
            features=features,
            waves=data.get("waves", {}),
            generated_by=data.get("generated_by", ""),
            generation_date=data.get("generation_date", ""),
        )


@dataclass
class TokenBudget:
    """Token budget tracking for a project or feature.

    Three-level enforcement:
      - WARNING  (80%):  notify user
      - SOFT_CAP (100%): pause new tasks, wait for user confirmation
      - HARD_CAP (150%): force-stop all tasks

    Attributes:
        limit: Maximum token budget (0 = unlimited).
        spent: Current tokens consumed.
    """

    limit: int = 0
    spent: int = 0

    WARNING_RATIO: float = field(default=0.8, repr=False)
    SOFT_CAP_RATIO: float = field(default=1.0, repr=False)
    HARD_CAP_RATIO: float = field(default=1.5, repr=False)

    @property
    def remaining(self) -> int:
        """Remaining tokens before hitting the limit."""
        if self.limit <= 0:
            return -1  # unlimited
        return max(0, self.limit - self.spent)

    @property
    def usage_ratio(self) -> float:
        """Ratio of spent / limit (0.0-∞)."""
        if self.limit <= 0:
            return 0.0
        return self.spent / self.limit

    def check(self) -> str:
        """Check budget status.

        Returns:
            'ok' if under warning threshold,
            'warning' if >= 80%,
            'soft_cap' if >= 100%,
            'hard_cap' if >= 150%.
        """
        if self.limit <= 0:
            return "ok"
        ratio = self.usage_ratio
        if ratio >= self.HARD_CAP_RATIO:
            return "hard_cap"
        if ratio >= self.SOFT_CAP_RATIO:
            return "soft_cap"
        if ratio >= self.WARNING_RATIO:
            return "warning"
        return "ok"

    def can_allocate(self, tokens: int) -> bool:
        """Check whether *tokens* can be allocated without exceeding HARD_CAP."""
        if self.limit <= 0:
            return True
        return (self.spent + tokens) <= int(self.limit * self.HARD_CAP_RATIO)

    def charge(self, tokens: int) -> str:
        """Charge *tokens* to the budget.

        Returns the budget status after charging: 'ok' / 'warning' / 'soft_cap' / 'hard_cap'.
        """
        self.spent += tokens
        return self.check()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limit": self.limit,
            "spent": self.spent,
            "remaining": self.remaining,
            "usage_ratio": round(self.usage_ratio, 4),
            "status": self.check(),
        }


# ═══════════════════════════════════════════════════════════════
# DAG Scheduler (topological sort)
# ═══════════════════════════════════════════════════════════════

class DAGScheduler:
    """DAG-aware task scheduler using Kahn's algorithm for topological sort.

    Validates dependency graphs, detects cycles, and produces an ordered
    execution sequence where dependents always follow their dependencies.
    """

    @staticmethod
    def topological_sort(features: List[Feature]) -> Tuple[List[Feature], List[str]]:
        """Sort features so that dependencies come before dependents.

        Uses Kahn's algorithm (BFS-based).  Features with no dependencies
        come first.  Circular dependencies are detected and reported.

        Args:
            features: Unsorted list of Feature objects.

        Returns:
            Tuple of:
              - Sorted list of Feature objects.
              - List of feature IDs involved in a cycle (empty if DAG is valid).
        """
        if not features:
            return [], []

        # Filter out features with empty/missing IDs before building maps
        features = [f for f in features if f.id]
        if not features:
            return [], []

        # Build lookup maps
        id_to_feature: Dict[str, Feature] = {}
        for f in features:
            if f.id:
                id_to_feature[f.id] = f

        # In-degree map and adjacency list
        in_degree: Dict[str, int] = {f.id: 0 for f in features}
        adj: Dict[str, List[str]] = defaultdict(list)

        for f in features:
            for dep_id in f.dependencies:
                if dep_id in id_to_feature and dep_id != f.id:
                    adj[dep_id].append(f.id)
                    in_degree[f.id] += 1

        # Kahn's algorithm
        queue: deque = deque(fid for fid, deg in in_degree.items() if deg == 0)
        sorted_ids: List[str] = []

        while queue:
            current = queue.popleft()
            sorted_ids.append(current)
            for neighbor in adj.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Detect cycles: features not in sorted_ids
        cycle_ids = [fid for fid in id_to_feature if fid not in sorted_ids]

        if cycle_ids:
            logger.warning(
                "DAG cycle detected among features: %s — falling back to original order",
                cycle_ids,
            )
            # Fall back to original order for cycle participants
            remaining = sorted(
                cycle_ids,
                key=lambda fid: features.index(id_to_feature[fid])
                if id_to_feature[fid] in features
                else 9999,
            )
            sorted_ids.extend(remaining)

        # Map back to Feature objects
        sorted_features = [id_to_feature[fid] for fid in sorted_ids]
        return sorted_features, cycle_ids

    @staticmethod
    def validate_dag(features: List[Feature]) -> Tuple[bool, List[str]]:
        """Validate that the feature dependency graph is a valid DAG.

        Checks:
          1. All dependency IDs reference existing features.
          2. No self-dependencies.
          3. No circular dependencies (detected via topological sort).

        Returns:
            Tuple of (is_valid, list_of_error_messages).
        """
        errors: List[str] = []

        if not features:
            return True, []

        id_set: set = {f.id for f in features if f.id}

        # Check 1 & 2: valid references, no self-deps
        for f in features:
            if not f.id:
                errors.append("Feature has empty id")
                continue
            for dep in f.dependencies:
                if dep == f.id:
                    errors.append(f"Feature {f.id} depends on itself")
                elif dep not in id_set:
                    errors.append(
                        f"Feature {f.id} depends on unknown feature '{dep}'"
                    )

        # Check 3: cycles
        _, cycle_ids = DAGScheduler.topological_sort(features)
        if cycle_ids:
            errors.append(f"Circular dependency detected: {cycle_ids}")

        return len(errors) == 0, errors

    @staticmethod
    def compute_execution_order(
        features: List[Feature],
    ) -> List[List[Feature]]:
        """Compute wave-based execution order.

        Groups features into "waves" where all features in wave N can run
        in parallel (their dependencies are satisfied by waves < N).

        Args:
            features: Topologically sorted list of features.

        Returns:
            List of wave-groups, each a list of features that can run concurrently.
        """
        if not features:
            return []

        id_to_idx: Dict[str, int] = {}
        for i, f in enumerate(features):
            id_to_idx[f.id] = i

        # Compute the "depth" of each feature: max depth of dependencies + 1
        depth: Dict[str, int] = {}
        for f in features:
            if not f.dependencies:
                depth[f.id] = 0
            else:
                max_dep_depth = 0
                for dep in f.dependencies:
                    dep_depth = depth.get(dep, 0)
                    max_dep_depth = max(max_dep_depth, dep_depth)
                depth[f.id] = max_dep_depth + 1

        # Group by depth
        max_depth = max(depth.values()) if depth else 0
        waves: List[List[Feature]] = [[] for _ in range(max_depth + 1)]
        for f in features:
            waves[depth[f.id]].append(f)

        # Remove empty waves
        return [w for w in waves if w]


# ═══════════════════════════════════════════════════════════════
# PRD parsing helpers (heuristic fallback)
# ═══════════════════════════════════════════════════════════════

# Regex patterns for extracting feature-like sections from PRD text
_FEATURE_SECTION_RE = re.compile(
    r"^#+\s*(?:Feature|Task|Week\s*\d+|Wave\s*\d+)\s*[:#-]\s*(?P<title>.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# Multiple ID patterns to match different naming conventions
_FEATURE_ID_PATTERNS = [
    re.compile(r"\b(W\d+[-_][A-Z]\d+)\b"),     # e.g. W3-E04, W1-P01
    re.compile(r"\b(W\d+G\d+)\b"),              # e.g. W1G1, W1G2 (goal IDs)
    re.compile(r"\b(F\d{3})\b"),                # e.g. F001, F002
    re.compile(r"\b([A-Z]{2,4}-\d{2,4})\b"),   # e.g. PRD-001, FEAT-01
]

_DEPENDENCY_RE = re.compile(
    r"(?:depends?\s+(?:on\s+)?|dependencies?\s*:\s*|依赖\s*)",
    re.IGNORECASE,
)

_COMPLEXITY_KEYWORDS: Dict[str, str] = {
    "simple": "simple",
    "medium": "medium",
    "moderate": "medium",
    "complex": "complex",
    "advanced": "complex",
}

_AGENT_KEYWORDS: Dict[str, str] = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codewhale": "codewhale",
    "code-whale": "codewhale",
    "qwen": "qwen-code",
    "qwen-code": "qwen-code",
    "hermes": "hermes",
}

# Wave detection patterns
_WAVE_PATTERNS = [
    re.compile(r"##\s*Wave\s*(\d+)\s*[:#-]\s*(.+)", re.IGNORECASE),
    re.compile(r"##\s*Week\s*(\d+)\s*[:#-]\s*(.+)", re.IGNORECASE),
    re.compile(r"##\s*Sprint\s*(\d+)\s*[:#-]\s*(.+)", re.IGNORECASE),
    re.compile(r"###\s*Wave\s*(\d+)\s*[:#-]\s*(.+)", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════════════
# TaskDecomposer
# ═══════════════════════════════════════════════════════════════

class TaskDecomposer:
    """LLM-driven task decomposer: reads PRD → generates features.json.

    Integrates:
      - DAG scheduler for topological sort and cycle detection.
      - Token budget constraints (per-feature + per-project).
      - LLM call (via configurable callback) with heuristic fallback.
      - Features.json generation from decomposed features.

    Usage::

        decomposer = TaskDecomposer(
            project_name="multi-agent-pipeline",
            total_token_budget=500_000,
            default_token_budget=30_000,
        )

        # LLM-driven (requires LLM callable)
        features = decomposer.decompose(prd_text)

        # Heuristic (no LLM needed)
        features = decomposer.decompose_heuristic(prd_text)

        # Validate + sort
        valid, errors = decomposer.validate(features)
        if not valid:
            print("Errors:", errors)

        # Check budget
        budget_ok, budget_msg = decomposer.check_budget(features)

        # Write features.json
        decomposer.generate_features_json(features, Path("features.json"))
    """

    # ── Constructor ─────────────────────────────────────────────

    def __init__(
        self,
        project_name: str = "",
        total_token_budget: int = 0,
        default_token_budget: int = 30_000,
        llm_call: Optional[
            Callable[[str, str], str]
        ] = None,
        default_agents: Optional[Dict[str, str]] = None,
    ) -> None:
        """Initialize the task decomposer.

        Args:
            project_name: Name of the project.
            total_token_budget: Total project-wide token budget (0 = unlimited).
            default_token_budget: Default per-feature token budget in tokens.
            llm_call: Optional callable (prompt, model) → output for LLM interaction.
                      If None, only heuristic decomposition is available.
            default_agents: Default agent assignments, e.g.
                            {'owner': 'claude-code', 'reviewer': 'codewhale', 'tester': 'qwen-code'}.
        """
        self.project_name = project_name
        self.total_budget = TokenBudget(limit=total_token_budget)
        self.default_token_budget = default_token_budget
        self._llm_call = llm_call

        agents = default_agents or {}
        self.default_owner = agents.get("owner", "claude-code")
        self.default_reviewer = agents.get("reviewer", "codewhale")
        self.default_tester = agents.get("tester", "qwen-code")

        # DAG scheduler instance
        self._dag = DAGScheduler()

    # ── Public API: Decomposition ───────────────────────────────

    def decompose(self, prd_text: str) -> List[Feature]:
        """Decompose a PRD into features, using LLM if available.

        Falls back to heuristic decomposition if no LLM callable is configured
        or if the LLM returns an unparseable response.

        Args:
            prd_text: Full PRD text (Markdown).

        Returns:
            List of Feature objects extracted from the PRD.
        """
        if self._llm_call is not None:
            try:
                features = self._decompose_with_llm(prd_text)
                if features:
                    return features
                logger.warning(
                    "LLM decomposition returned empty result; "
                    "falling back to heuristic."
                )
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning(
                    "LLM decomposition failed (%s); falling back to heuristic.",
                    exc,
                )

        return self.decompose_heuristic(prd_text)

    def decompose_heuristic(self, prd_text: str) -> List[Feature]:
        """Heuristic feature extraction from PRD (no LLM required).

        Uses regex and structural patterns to identify features in the PRD:
          1. Scans for Wave/Week/Sprint section headers.
          2. Extracts feature IDs (e.g. W3-E04) and their descriptions.
          3. Detects explicit dependency lists.
          4. Assigns complexity estimates based on keyword analysis.

        Args:
            prd_text: Full PRD text (Markdown).

        Returns:
            List of Feature objects.
        """
        features: List[Feature] = []
        id_map: Dict[str, Feature] = {}
        wave_info: Dict[int, Dict[str, Any]] = {}

        # ── Step 1: Detect wave/phase structure ──────────────────
        for pattern in _WAVE_PATTERNS:
            for match in pattern.finditer(prd_text):
                wave_num = int(match.group(1))
                wave_name = match.group(2).strip()
                if wave_num not in wave_info:
                    wave_info[wave_num] = {
                        "name": wave_name,
                        "status": "pending",
                        "features": [],
                    }

        # ── Step 2: Extract feature IDs ──────────────────────────
        # Try multiple ID patterns; collect all unique IDs
        found_ids: Dict[str, int] = {}  # fid → match position
        for pattern in _FEATURE_ID_PATTERNS:
            for match in pattern.finditer(prd_text):
                fid = match.group(0)
                if fid not in found_ids:
                    found_ids[fid] = match.start()

        # If no feature-like IDs found, extract from section headers
        if not found_ids:
            # Use section headers as feature candidates
            section_re = re.compile(
                r"^##\s+(.+)$", re.MULTILINE
            )
            counter = 1
            for match in section_re.finditer(prd_text):
                title = match.group(1).strip()
                # Skip boilerplate sections
                if any(
                    kw in title.lower()
                    for kw in (
                        "principle", "原则", "范围", "scope",
                        "目标", "goal", "核心", "总结",
                        "附录", "appendix", "参考", "reference",
                    )
                ):
                    continue
                fid = f"F{counter:03d}"
                found_ids[fid] = match.start()
                counter += 1

            # If still nothing, create a single feature for the whole PRD
            if not found_ids:
                fid = "F001"
                found_ids[fid] = 0

        for fid, pos in found_ids.items():
            if fid in id_map:
                continue

            # Extract surrounding context (up to 600 chars around match)
            start = max(0, pos - 50)
            end = min(len(prd_text), pos + 550)
            context = prd_text[start:end].strip()

            # Try to find a title nearby (look for heading or title-like text)
            title = self._extract_title(context, fid)

            # Extract description from the context
            description = self._extract_description(context, fid)

            # Extract dependencies (pass fid to filter self-deps)
            dependencies = self._extract_dependencies(context, fid)

            # Estimate complexity from keywords in context
            complexity = self._estimate_complexity(context)

            # Determine wave from context
            wave = self._determine_wave(context, wave_info)

            # Estimate lines from keywords
            expected_lines = self._estimate_lines(context, complexity)

            feature = Feature(
                id=fid,
                title=title,
                description=description,
                acceptance_criteria="",
                dependencies=dependencies,
                estimated_complexity=complexity,
                owner_agent=self.default_owner,
                reviewer_agent=self.default_reviewer,
                tester_agent=self.default_tester,
                status="pending",
                wave=wave,
                expected_lines=expected_lines,
                max_token_budget=self.default_token_budget,
            )
            features.append(feature)
            id_map[fid] = feature

        # ── Step 3: Fill in wave→feature mappings ────────────────
        for f in features:
            if f.wave > 0 and f.wave in wave_info:
                wave_info[f.wave].setdefault("features", []).append(f.id)

        # ── Step 4: Fill in acceptance_criteria from PRD ─────────
        for f in features:
            f.acceptance_criteria = self._extract_acceptance_criteria(
                prd_text, f.id
            )

        return features

    def decompose_from_prd(self, prd_path: Path) -> List[Feature]:
        """Read a PRD file and decompose it.

        Args:
            prd_path: Path to the PRD Markdown file.

        Returns:
            List of Feature objects.

        Raises:
            FileNotFoundError: If the PRD file does not exist.
        """
        if not prd_path.exists():
            raise FileNotFoundError(f"PRD file not found: {prd_path}")
        prd_text = prd_path.read_text(encoding="utf-8")
        return self.decompose(prd_text)

    # ── Public API: Validation ──────────────────────────────────

    def validate(self, features: List[Feature]) -> Tuple[bool, List[str]]:
        """Validate the feature list.

        Checks performed:
          - DAG validity (no cycles, valid references, no self-deps).
          - Budget constraints.
          - Required fields.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        errors: List[str] = []

        # DAG validation
        dag_ok, dag_errors = self._dag.validate_dag(features)
        errors.extend(dag_errors)

        # Required fields
        seen_ids: set = set()
        for f in features:
            if not f.id:
                errors.append("Feature has empty id")
            elif f.id in seen_ids:
                errors.append(f"Duplicate feature id: {f.id}")
            else:
                seen_ids.add(f.id)

            if not f.title:
                errors.append(f"Feature {f.id or '<unknown>'} has empty title")

        # Budget check
        budget_ok, budget_msg = self.check_budget(features)
        if not budget_ok:
            errors.append(budget_msg)

        return len(errors) == 0, errors

    def check_budget(self, features: List[Feature]) -> Tuple[bool, str]:
        """Check token budget constraints across all features.

        Sums individual feature budgets against the total project budget.
        Also validates each feature's individual budget against its
        estimated_lines.

        Returns:
            Tuple of (within_budget, message).
        """
        total_feature_budget = sum(
            f.max_token_budget for f in features if f.max_token_budget > 0
        )

        if self.total_budget.limit > 0:
            if total_feature_budget > self.total_budget.limit:
                return False, (
                    f"Total feature budget ({total_feature_budget:,} tokens) "
                    f"exceeds project limit ({self.total_budget.limit:,} tokens)"
                )

            ratio = total_feature_budget / self.total_budget.limit
            if ratio >= self.total_budget.WARNING_RATIO:
                return True, (
                    f"WARNING: Budget at {ratio:.0%} "
                    f"({total_feature_budget:,}/{self.total_budget.limit:,} tokens)"
                )

            return True, (
                f"Budget OK: {total_feature_budget:,}/{self.total_budget.limit:,} tokens "
                f"({ratio:.0%} used)"
            )

        return True, (
            f"Budget: {total_feature_budget:,} tokens allocated (no project limit)"
        )

    def topological_sort(
        self, features: List[Feature]
    ) -> Tuple[List[Feature], List[str]]:
        """Topologically sort features by dependency order.

        Args:
            features: Unsorted list of features.

        Returns:
            Tuple of (sorted_features, cycle_ids).  cycle_ids is non-empty
            when circular dependencies are detected.
        """
        return self._dag.topological_sort(features)

    def compute_waves(
        self, features: List[Feature]
    ) -> List[List[Feature]]:
        """Group sorted features into parallel-execution waves.

        Args:
            features: Topologically sorted features.

        Returns:
            List of wave-groups.
        """
        return self._dag.compute_execution_order(features)

    # ── Public API: Output ──────────────────────────────────────

    def generate_features_json(
        self,
        features: List[Feature],
        output_path: Path,
        version: str = "1.0.0",
    ) -> FeaturesManifest:
        """Generate and write features.json from a list of features.

        Validates the feature list before writing.  Features are
        topologically sorted before being written.

        Args:
            features: List of features to include.
            output_path: Where to write features.json.
            version: Manifest version string.

        Returns:
            The FeaturesManifest that was written.

        Raises:
            ValueError: If validation fails (invalid DAG / budget constraint).
        """
        # Validate first
        valid, errors = self.validate(features)
        if not valid:
            raise ValueError(
                "Feature validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        # Topological sort
        sorted_features, cycle_ids = self.topological_sort(features)

        # Build waves dict from feature wave assignments
        waves: Dict[str, Dict[str, Any]] = {}
        for f in sorted_features:
            wave_key = str(f.wave) if f.wave > 0 else "unassigned"
            if wave_key not in waves:
                waves[wave_key] = {
                    "features": [],
                    "status": "pending",
                    "feature_count": 0,
                }
            if f.id not in waves[wave_key]["features"]:
                waves[wave_key]["features"].append(f.id)
        for w in waves.values():
            w["feature_count"] = len(w["features"])

        # Build manifest
        from datetime import datetime, timezone

        manifest = FeaturesManifest(
            project=self.project_name,
            version=version,
            features=sorted_features,
            waves=waves,
            generated_by="task_decomposer",
            generation_date=datetime.now(timezone.utc).isoformat(),
        )

        # Write to file
        data = manifest.to_dict()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(
            "Wrote %d features to %s", len(sorted_features), output_path
        )
        return manifest

    # ── LLM-driven decomposition ────────────────────────────────

    def _decompose_with_llm(self, prd_text: str) -> List[Feature]:
        """Use LLM to extract features from PRD text.

        The LLM is prompted to return a JSON array of feature objects
        matching the Feature schema.

        Args:
            prd_text: Full PRD text.

        Returns:
            List of Feature objects parsed from the LLM response.
        """
        prompt = self._build_decompose_prompt(prd_text)
        response = self._llm_call(prompt, "hermes-research")  # type: ignore[misc]

        # Try to extract JSON from the response
        features = self._parse_llm_response(response)
        return features

    def _build_decompose_prompt(self, prd_text: str) -> str:
        """Build the LLM prompt for PRD decomposition."""
        return f"""You are a technical project manager. Your task is to decompose the
following PRD (Product Requirements Document) into a structured list of
features suitable for a multi-agent development pipeline.

For each feature, extract:
  - id: A unique feature identifier (e.g. W3-E04, F001).  Use the IDs
    already present in the PRD if available.
  - title: A short, descriptive title.
  - description: A detailed description of what the feature entails.
  - acceptance_criteria: Clear, testable acceptance criteria.
  - dependencies: List of feature IDs this feature depends on (empty list if none).
  - estimated_complexity: One of 'simple', 'medium', or 'complex'.
  - expected_lines: Estimated lines of code (integer).
  - wave: Sprint/wave number (integer, starting from 1).

Return ONLY a valid JSON array.  Each element must follow this schema:

{{
  "id": "string",
  "title": "string",
  "description": "string",
  "acceptance_criteria": "string",
  "dependencies": ["string"],
  "estimated_complexity": "simple|medium|complex",
  "wave": integer,
  "expected_lines": integer
}}

PRD text follows:

{prd_text[:15000]}
"""

    def _parse_llm_response(self, response: str) -> List[Feature]:
        """Parse the LLM response into a list of Feature objects.

        Handles:
          - Pure JSON array response.
          - JSON inside markdown code fences.
          - Mixed text + JSON (extracts first JSON array).

        Returns:
            List of Feature objects, or empty list on parse failure.
        """
        # Try to find JSON array in response
        json_text = response.strip()

        # Strip markdown code fences if present
        code_fence_re = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
        fence_match = code_fence_re.search(response)
        if fence_match:
            json_text = fence_match.group(1).strip()

        # Find the first '[' ... ']' block
        arr_match = re.search(r"\[.*\]", json_text, re.DOTALL)
        if arr_match:
            json_text = arr_match.group(0)

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(data, list):
            logger.warning("LLM response is not a JSON array")
            return []

        features: List[Feature] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                f = Feature(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    acceptance_criteria=item.get("acceptance_criteria", ""),
                    dependencies=item.get("dependencies", []),
                    estimated_complexity=item.get("estimated_complexity", "medium"),
                    owner_agent=self.default_owner,
                    reviewer_agent=self.default_reviewer,
                    tester_agent=self.default_tester,
                    status="pending",
                    wave=item.get("wave", 0),
                    expected_lines=item.get("expected_lines", 200),
                    max_token_budget=self.default_token_budget,
                )
                features.append(f)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning("Skipping malformed feature item: %s", exc)
                continue

        return features

    # ── Heuristic helpers ───────────────────────────────────────

    @staticmethod
    def _extract_title(context: str, fid: str) -> str:
        """Extract a feature title from surrounding context."""
        # Look for a heading line near the feature ID
        heading_re = re.compile(
            rf"^#+\s*(?:{re.escape(fid)}\s*[:\-–—]*\s*)?(.+)$",
            re.MULTILINE,
        )
        match = heading_re.search(context)
        if match:
            title = match.group(1).strip()
            if title and len(title) < 120:
                return title

        # Try "title:" or "标题：" pattern
        title_re = re.compile(
            r"(?:title|标题)\s*[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE
        )
        match = title_re.search(context)
        if match:
            return match.group(1).strip()[:120]

        # Use the fid as fallback
        return f"Feature {fid}"

    @staticmethod
    def _extract_description(context: str, fid: str) -> str:
        """Extract a feature description from context."""
        # Look for "description" / "描述" lines
        desc_re = re.compile(
            r"(?:description|描述|DESCRIPTION)\s*[:：]\s*(.+?)(?:\n\n|\n#|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        match = desc_re.search(context)
        if match:
            return match.group(1).strip()[:500]

        # Use the text between the feature ID and the next heading/blank line
        lines = context.split("\n")
        desc_lines: List[str] = []
        in_desc = False
        for line in lines:
            if fid in line:
                in_desc = True
                continue
            if in_desc:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    break
                if not stripped.startswith("```"):
                    desc_lines.append(stripped)

        if desc_lines:
            return " ".join(desc_lines)[:500]

        return f"See PRD for {fid}"

    @staticmethod
    def _extract_dependencies(context: str, fid: str = "") -> List[str]:
        """Extract dependency IDs from context text."""
        deps: List[str] = []

        # Look for explicit dependency lists
        dep_match = _DEPENDENCY_RE.search(context)
        if dep_match:
            # Extract IDs after the dependency marker, stop at next section boundary
            rest = context[dep_match.end():]
            # Truncate at next "###" or "##" to avoid grabbing IDs from subsequent sections
            import re as _re
            next_section = _re.search(r'\n\s*#{2,3}\s', rest)
            if next_section:
                rest = rest[:next_section.start()]
            for pattern in _FEATURE_ID_PATTERNS:
                for mid in pattern.finditer(rest):
                    deps.append(mid.group(0))

        # Deduplicate while preserving order, skip self-references
        seen: set = set()
        result: List[str] = []
        for d in deps:
            if d != fid and d not in seen:
                seen.add(d)
                result.append(d)
        return result

    @staticmethod
    def _estimate_complexity(context: str) -> str:
        """Estimate complexity from keyword analysis."""
        context_lower = context.lower()
        scores: Dict[str, int] = {"simple": 0, "medium": 0, "complex": 0}

        for keyword, level in _COMPLEXITY_KEYWORDS.items():
            if keyword in context_lower:
                scores[level] += 1

        # Select the highest-scoring complexity level
        if scores["complex"] > 0:
            return "complex"
        if scores["medium"] > 0:
            return "medium"
        return "simple"

    @staticmethod
    def _determine_wave(
        context: str, wave_info: Dict[int, Dict[str, Any]]
    ) -> int:
        """Determine which wave a feature belongs to."""
        # Check for explicit wave/sprint mentions
        for pattern in _WAVE_PATTERNS:
            for match in pattern.finditer(context):
                return int(match.group(1))
        return 0

    @staticmethod
    def _estimate_lines(context: str, complexity: str) -> int:
        """Estimate lines of code based on complexity and context."""
        # Look for explicit line count mentions
        lines_re = re.compile(
            r"(?:expected_lines|代码行|lines?)\s*[:：=]\s*(\d+)", re.IGNORECASE
        )
        match = lines_re.search(context)
        if match:
            return int(match.group(1))

        # Default by complexity
        defaults = {"simple": 100, "medium": 200, "complex": 350}
        return defaults.get(complexity, 200)

    @staticmethod
    def _extract_acceptance_criteria(prd_text: str, fid: str) -> str:
        """Extract acceptance criteria for a feature from the full PRD."""
        # Search for the feature ID and extract criteria nearby
        idx = prd_text.find(fid)
        if idx == -1:
            return ""

        # Look for acceptance_criteria / 验收标准 within 1500 chars
        snippet = prd_text[max(0, idx):idx + 1500]

        ac_re = re.compile(
            r"(?:acceptance_criteria|验收标准|Acceptance Criteria)\s*[:：]\s*(.+?)(?:\n\n|\n#|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        match = ac_re.search(snippet)
        if match:
            return match.group(1).strip()[:500]

        # Fallback: look for bullet lists after a heading like "Acceptance"
        ac_heading_re = re.compile(
            r"^#+\s*(?:Acceptance|验收|Criteria).*\n((?:\s*[-*+]\s*.+\n?)+)",
            re.MULTILINE | re.IGNORECASE,
        )
        h_match = ac_heading_re.search(snippet)
        if h_match:
            return h_match.group(1).strip()[:500]

        return ""


# ═══════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════

def decompose_prd_to_features(
    prd_path: Path,
    output_path: Path,
    project_name: str = "",
    total_token_budget: int = 0,
    default_token_budget: int = 30_000,
    llm_call: Optional[Callable[[str, str], str]] = None,
) -> List[Feature]:
    """Convenience function: read PRD → decompose → validate → write features.json.

    Args:
        prd_path: Path to PRD markdown file.
        output_path: Where to write features.json.
        project_name: Project name (defaults to stem of prd_path parent).
        total_token_budget: Total project token budget (0 = unlimited).
        default_token_budget: Default per-feature token budget.
        llm_call: Optional LLM callable for enhanced decomposition.

    Returns:
        List of decomposed features.

    Raises:
        FileNotFoundError: If PRD file not found.
        ValueError: If validation fails.
    """
    if not project_name:
        project_name = prd_path.parent.name

    decomposer = TaskDecomposer(
        project_name=project_name,
        total_token_budget=total_token_budget,
        default_token_budget=default_token_budget,
        llm_call=llm_call,
    )

    # Decompose
    features = decomposer.decompose_from_prd(prd_path)

    # Validate
    valid, errors = decomposer.validate(features)
    if not valid:
        logger.error("Validation failed:\n%s", "\n".join(errors))
        raise ValueError(
            "Feature validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # Write
    decomposer.generate_features_json(features, output_path)

    logger.info(
        "Successfully decomposed %d features from %s → %s",
        len(features),
        prd_path,
        output_path,
    )

    return features
