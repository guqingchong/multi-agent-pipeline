"""src/inspector.py — Unified Inspector review role with global memory + user perspective.

The Inspector is the unified review role invoked at key Phase check points.
It loads global context (knowledge graph, architecture, journey, review logs)
and runs a 4-dimension review:

  1. user_perspective        — Can the user complete their task?
  2. knowledge_completeness  — Is the knowledge graph fully covered?
  3. journey_fidelity        — Does actual behavior match the journey design?
  4. cross_phase_coherence   — Are decisions consistent across phases?

The Inspector uses an independent LLM (different from the project's coding model)
to avoid self-preference bias.

Key design (from pipeline-v3 plan):
  Inspector = independent LLM call + global context injection + user-perspective review template

Dependencies: W4-K01 (knowledge_graph.py) — used for knowledge completeness review.

Usage::

    from inspector import Inspector, InspectorReport

    insp = Inspector(model="deepseek-v4-pro")
    report = insp.review("research", project_dir=Path("/path/to/project"))
    print(report.overall_pass, report.summary)

    # Access global memory across reviews
    print(insp.global_memory)  # accumulated context from all reviews

    # User perspective configuration
    insp.set_user_perspective(
        user_goals=["Order product with one click", "Track shipment in real time"],
        user_concerns=["Mobile responsiveness", "Offline capability"],
    )
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from thresholds_loader import get_threshold
except (ModuleNotFoundError, ImportError):
    from src.thresholds_loader import get_threshold

logger = logging.getLogger(__name__)

__all__ = [
    "DimensionFinding",
    "InspectorReport",
    "Inspector",
    "ReviewDimension",
    "SEVERITY_LEVELS",
]

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

SEVERITY_LEVELS = ("pass", "warning", "fail")

DEFAULT_MODEL = get_threshold("inspector.defaults.model", "claude-sonnet-4-20250514")


class ReviewDimension:
    """Named constants for the four review dimensions."""

    USER_PERSPECTIVE = "user_perspective"
    KNOWLEDGE_COMPLETENESS = "knowledge_completeness"
    JOURNEY_FIDELITY = "journey_fidelity"
    CROSS_PHASE_COHERENCE = "cross_phase_coherence"

    ALL = [USER_PERSPECTIVE, KNOWLEDGE_COMPLETENESS, JOURNEY_FIDELITY, CROSS_PHASE_COHERENCE]


# ═══════════════════════════════════════════════════════════════
# Review dimension prompt templates
# ═══════════════════════════════════════════════════════════════

_USER_PERSPECTIVE_PROMPT = (
    "You are an independent Inspector reviewing this project from the USER's perspective.\n"
    "Your job: assess whether a real user can complete their tasks with the current outputs.\n\n"
    "=== USER GOALS ===\n"
    "{user_goals}\n\n"
    "=== USER CONCERNS ===\n"
    "{user_concerns}\n\n"
    "=== CURRENT PHASE ===\n"
    "Phase: {phase}\n\n"
    "=== PHASE ARTIFACTS ===\n"
    "{phase_artifacts}\n\n"
    "=== QUESTIONS TO ANSWER ===\n"
    "1. If I were a user, could I complete my tasks after this phase? Why or why not?\n"
    "2. What edge cases would I encounter as a user?\n"
    "3. Is there any over-engineering — things the user does NOT need?\n"
    "4. Overall: PASS / WARNING / FAIL — with specific evidence.\n\n"
    "Respond in strict JSON format:\n"
    '{{"overall": "pass"|"warning"|"fail", "score": 0.0-1.0, '
    '"answers": [{{"question": "...", "answer": "...", "evidence": [...], "recommendation": "..."}}]}}'
)

_KNOWLEDGE_COMPLETENESS_PROMPT = (
    "You are an independent Inspector reviewing KNOWLEDGE COMPLETENESS.\n\n"
    "=== KNOWLEDGE GRAPH ===\n"
    "{knowledge_graph}\n\n"
    "=== CURRENT PHASE ===\n"
    "Phase: {phase}\n\n"
    "=== PHASE ARTIFACTS ===\n"
    "{phase_artifacts}\n\n"
    "=== QUESTIONS TO ANSWER ===\n"
    "1. Are all core concepts from the knowledge graph mapped into the current design/outputs?\n"
    "2. Are there any concepts marked as critical that are NOT covered?\n"
    "3. Are there knowledge gaps where confidence is low but the design depends on it?\n"
    "4. Overall: PASS / WARNING / FAIL — with specific evidence.\n\n"
    "Respond in strict JSON format:\n"
    '{{"overall": "pass"|"warning"|"fail", "score": 0.0-1.0, '
    '"answers": [{{"question": "...", "answer": "...", "evidence": [...], "recommendation": "..."}}]}}'
)

_JOURNEY_FIDELITY_PROMPT = (
    "You are an independent Inspector reviewing JOURNEY FIDELITY.\n\n"
    "=== USER JOURNEY ===\n"
    "{journey}\n\n"
    "=== CURRENT PHASE ===\n"
    "Phase: {phase}\n\n"
    "=== PHASE ARTIFACTS ===\n"
    "{phase_artifacts}\n\n"
    "=== QUESTIONS TO ANSWER ===\n"
    "1. Can each step in the journey be executed by the current system?\n"
    "2. Are there deviations between actual behavior and the journey design?\n"
    "3. Are there missing steps or gaps in the journey that should exist?\n"
    "4. Overall: PASS / WARNING / FAIL — with specific evidence.\n\n"
    "Respond in strict JSON format:\n"
    '{{"overall": "pass"|"warning"|"fail", "score": 0.0-1.0, '
    '"answers": [{{"question": "...", "answer": "...", "evidence": [...], "recommendation": "..."}}]}}'
)

_CROSS_PHASE_COHERENCE_PROMPT = (
    "You are an independent Inspector reviewing CROSS-PHASE COHERENCE.\n\n"
    "=== ARCHITECTURE ===\n"
    "{architecture}\n\n"
    "=== REVIEW LOGS (previous reviews) ===\n"
    "{review_logs}\n\n"
    "=== CURRENT PHASE ===\n"
    "Phase: {phase}\n\n"
    "=== PHASE ARTIFACTS ===\n"
    "{phase_artifacts}\n\n"
    "=== QUESTIONS TO ANSWER ===\n"
    "1. Are decisions from previous phases correctly inherited in this phase?\n"
    "2. Are there any design decisions silently overturned by later phases?\n"
    "3. Is there consistency between what was promised and what was delivered?\n"
    "4. Overall: PASS / WARNING / FAIL — with specific evidence.\n\n"
    "Respond in strict JSON format:\n"
    '{{"overall": "pass"|"warning"|"fail", "score": 0.0-1.0, '
    '"answers": [{{"question": "...", "answer": "...", "evidence": [...], "recommendation": "..."}}]}}'
)

DIMENSION_PROMPTS: Dict[str, str] = {
    ReviewDimension.USER_PERSPECTIVE: _USER_PERSPECTIVE_PROMPT,
    ReviewDimension.KNOWLEDGE_COMPLETENESS: _KNOWLEDGE_COMPLETENESS_PROMPT,
    ReviewDimension.JOURNEY_FIDELITY: _JOURNEY_FIDELITY_PROMPT,
    ReviewDimension.CROSS_PHASE_COHERENCE: _CROSS_PHASE_COHERENCE_PROMPT,
}


# ═══════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════

@dataclass
class DimensionFinding:
    """A single finding from one review dimension.

    Attributes:
        dimension: Which of the four review dimensions this belongs to.
        severity: One of "pass", "warning", "fail".
        question: The review question being answered.
        answer: The inspector's answer/finding.
        evidence: Supporting evidence items (file paths, specific observations, etc.).
        recommendation: Recommended action to address the finding.
    """

    dimension: str = ""
    severity: str = "pass"
    question: str = ""
    answer: str = ""
    evidence: List[str] = field(default_factory=list)
    recommendation: str = ""

    def __post_init__(self):
        if self.severity not in SEVERITY_LEVELS:
            self.severity = "warning"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "severity": self.severity,
            "question": self.question,
            "answer": self.answer,
            "evidence": list(self.evidence),
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DimensionFinding:
        return cls(
            dimension=data.get("dimension", ""),
            severity=data.get("severity", "pass"),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            evidence=data.get("evidence", []),
            recommendation=data.get("recommendation", ""),
        )


@dataclass
class InspectorReport:
    """Complete Inspector review report after a single review(phase) call.

    Contains findings from all four dimensions, scores per dimension,
    and an overall pass/fail verdict.

    Attributes:
        report_id: Unique identifier for this report.
        phase: The pipeline phase being reviewed (e.g., "research", "design").
        project_dir: The project directory path (as string).
        findings: All dimension findings from this review.
        user_perspective_score: 0.0-1.0 score for user perspective dimension.
        knowledge_completeness_score: 0.0-1.0 score for knowledge completeness.
        journey_fidelity_score: 0.0-1.0 score for journey fidelity.
        cross_phase_coherence_score: 0.0-1.0 score for cross-phase coherence.
        overall_pass: True if all dimensions pass (no fails), False otherwise.
        global_memory_summary: Summary of accumulated global memory before this review.
        independent_model: The independent LLM model used for this review.
        started_at: Unix timestamp when review started.
        completed_at: Unix timestamp when review completed (None if in progress).
        summary: Human-readable summary of the review.
        metadata: Additional metadata (e.g., loaded context file paths).
    """

    report_id: str = ""
    phase: str = ""
    project_dir: str = ""
    findings: List[DimensionFinding] = field(default_factory=list)
    user_perspective_score: float = 0.0
    knowledge_completeness_score: float = 0.0
    journey_fidelity_score: float = 0.0
    cross_phase_coherence_score: float = 0.0
    overall_pass: bool = False
    global_memory_summary: str = ""
    independent_model: str = ""
    started_at: float = 0.0
    completed_at: Optional[float] = None
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "fail")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def pass_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "pass")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "phase": self.phase,
            "project_dir": self.project_dir,
            "findings": [f.to_dict() for f in self.findings],
            "scores": {
                "user_perspective": self.user_perspective_score,
                "knowledge_completeness": self.knowledge_completeness_score,
                "journey_fidelity": self.journey_fidelity_score,
                "cross_phase_coherence": self.cross_phase_coherence_score,
            },
            "overall_pass": self.overall_pass,
            "global_memory_summary": self.global_memory_summary,
            "independent_model": self.independent_model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "summary": self.summary,
            "statistics": {
                "total_findings": len(self.findings),
                "pass": self.pass_count,
                "warning": self.warning_count,
                "fail": self.fail_count,
            },
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InspectorReport:
        scores = data.get("scores", {})
        return cls(
            report_id=data.get("report_id", ""),
            phase=data.get("phase", ""),
            project_dir=data.get("project_dir", ""),
            findings=[DimensionFinding.from_dict(f) for f in data.get("findings", [])],
            user_perspective_score=scores.get("user_perspective", 0.0),
            knowledge_completeness_score=scores.get("knowledge_completeness", 0.0),
            journey_fidelity_score=scores.get("journey_fidelity", 0.0),
            cross_phase_coherence_score=scores.get("cross_phase_coherence", 0.0),
            overall_pass=data.get("overall_pass", False),
            global_memory_summary=data.get("global_memory_summary", ""),
            independent_model=data.get("independent_model", ""),
            started_at=data.get("started_at", 0.0),
            completed_at=data.get("completed_at"),
            summary=data.get("summary", ""),
            metadata=data.get("metadata", {}),
        )


# ═══════════════════════════════════════════════════════════════
# Inspector — main class
# ═══════════════════════════════════════════════════════════════

class Inspector:
    """Unified review role with global memory and user perspective.

    The Inspector is invoked at key Phase check points to perform a structured
    4-dimension review using an independent LLM (different model from the project's
    coding model, to avoid self-preference bias).

    Global memory accumulates context across all review calls, so later reviews
    can build on findings from earlier phases.

    User perspective is configurable via set_user_perspective() — this defines
    what the "user tasks" are that the Inspector should evaluate against.

    Workflow::

        insp = Inspector(model="deepseek-v4-pro")
        insp.set_user_perspective(
            user_goals=["User can order items", "User can track orders"],
            user_concerns=["Mobile experience", "Payment security"],
        )

        # Review the research phase
        report = insp.review("research", project_dir=Path("/project"))
        print(report.overall_pass)  # → True/False

        # Later phases build on global memory
        report2 = insp.review("design", project_dir=Path("/project"))

    Attributes:
        model: The independent LLM model name (must differ from project coding model).
        global_memory: Accumulated context from all reviews (dict of phase → summary).
        user_goals: User goals to evaluate against (from user perspective).
        user_concerns: User concerns to evaluate against.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        """Initialize the Inspector with an independent LLM model.

        Args:
            model: LLM model name for independent review.
                   Must be different from the project's coding model.
        """
        self.model: str = model
        self.global_memory: Dict[str, Any] = {}
        self.user_goals: List[str] = []
        self.user_concerns: List[str] = []

        # Paths relative to project_dir that we try to load
        self._specs_dir_name: str = "specs"
        self._logs_dir_name: str = ".logs"

    # ── Configuration ───────────────────────────────────────────

    def set_user_perspective(
        self,
        user_goals: List[str],
        user_concerns: Optional[List[str]] = None,
    ) -> None:
        """Configure the user perspective for review.

        Args:
            user_goals: What tasks the user wants to accomplish.
                        Example: ["Order product with one click", "Track shipment in real time"]
            user_concerns: What the user is worried about.
                           Example: ["Mobile responsiveness", "Offline capability"]
        """
        self.user_goals = list(user_goals) if user_goals else []
        self.user_concerns = list(user_concerns) if user_concerns else []

    def clear_memory(self) -> None:
        """Clear accumulated global memory."""
        self.global_memory.clear()

    # ── Public API ──────────────────────────────────────────────

    def review(self, phase: str, project_dir: Path) -> InspectorReport:
        """Execute a full 4-dimension review for a given phase.

        Loads global context (knowledge graph, architecture, journey, review logs),
        then runs all four review dimensions.

        Args:
            phase: Pipeline phase name (e.g., "research", "design", "develop").
            project_dir: Root directory of the project being reviewed.

        Returns:
            InspectorReport with all findings, scores, and overall verdict.
        """
        report_id = str(uuid.uuid4())[:8]
        started_at = time.time()

        logger.info("Inspector[%s]: Starting review for phase=%s project=%s",
                     report_id, phase, project_dir)

        # ── Step 1: Load all context ──
        context = self._load_context(project_dir=project_dir, phase=phase)

        # ── Step 2: Build phase artifacts summary ──
        phase_artifacts = self._collect_phase_artifacts(
            project_dir=project_dir, phase=phase, context=context
        )

        # ── Step 3: Run all four dimensions ──
        all_findings: List[DimensionFinding] = []

        scores: Dict[str, float] = {}

        for dim in ReviewDimension.ALL:
            dim_findings, dim_score = self._review_dimension(
                dimension=dim,
                phase=phase,
                context=context,
                phase_artifacts=phase_artifacts,
            )
            all_findings.extend(dim_findings)
            scores[dim] = dim_score

        # ── Step 4: Build report ──
        overall_pass = all(
            f.severity != "fail" for f in all_findings
        )

        completed_at = time.time()

        report = InspectorReport(
            report_id=report_id,
            phase=phase,
            project_dir=str(project_dir),
            findings=all_findings,
            user_perspective_score=scores.get(ReviewDimension.USER_PERSPECTIVE, 0.0),
            knowledge_completeness_score=scores.get(ReviewDimension.KNOWLEDGE_COMPLETENESS, 0.0),
            journey_fidelity_score=scores.get(ReviewDimension.JOURNEY_FIDELITY, 0.0),
            cross_phase_coherence_score=scores.get(ReviewDimension.CROSS_PHASE_COHERENCE, 0.0),
            overall_pass=overall_pass,
            global_memory_summary=self._summarize_memory(),
            independent_model=self.model,
            started_at=started_at,
            completed_at=completed_at,
            summary=self._build_summary(all_findings, overall_pass, phase),
            metadata={
                "context_files": context.get("_loaded_files", []),
                "memory_phases": list(self.global_memory.keys()),
            },
        )

        # ── Step 5: Update global memory ──
        self.global_memory[phase] = {
            "report_id": report_id,
            "overall_pass": overall_pass,
            "scores": scores,
            "findings_count": len(all_findings),
            "summary": report.summary,
            "completed_at": completed_at,
        }

        logger.info("Inspector[%s]: Review complete — overall_pass=%s scores=%s",
                     report_id, overall_pass, scores)

        return report

    def review_dimensions(
        self,
        phase: str,
        project_dir: Path,
        dimensions: Optional[List[str]] = None,
    ) -> InspectorReport:
        """Run review on a subset of dimensions.

        Args:
            phase: Pipeline phase name.
            project_dir: Root directory of the project.
            dimensions: Dimensions to review (default: all four).

        Returns:
            InspectorReport with findings only for the requested dimensions.
        """
        if dimensions is None:
            dimensions = ReviewDimension.ALL
        else:
            dimensions = [d for d in dimensions if d in ReviewDimension.ALL]

        report_id = str(uuid.uuid4())[:8]
        started_at = time.time()

        context = self._load_context(project_dir=project_dir, phase=phase)
        phase_artifacts = self._collect_phase_artifacts(
            project_dir=project_dir, phase=phase, context=context
        )

        all_findings: List[DimensionFinding] = []
        scores: Dict[str, float] = {}

        for dim in dimensions:
            dim_findings, dim_score = self._review_dimension(
                dimension=dim,
                phase=phase,
                context=context,
                phase_artifacts=phase_artifacts,
            )
            all_findings.extend(dim_findings)
            scores[dim] = dim_score

        overall_pass = all(f.severity != "fail" for f in all_findings)

        return InspectorReport(
            report_id=report_id,
            phase=phase,
            project_dir=str(project_dir),
            findings=all_findings,
            user_perspective_score=scores.get(ReviewDimension.USER_PERSPECTIVE, 0.0),
            knowledge_completeness_score=scores.get(ReviewDimension.KNOWLEDGE_COMPLETENESS, 0.0),
            journey_fidelity_score=scores.get(ReviewDimension.JOURNEY_FIDELITY, 0.0),
            cross_phase_coherence_score=scores.get(ReviewDimension.CROSS_PHASE_COHERENCE, 0.0),
            overall_pass=overall_pass,
            global_memory_summary=self._summarize_memory(),
            independent_model=self.model,
            started_at=started_at,
            completed_at=time.time(),
            summary=self._build_summary(all_findings, overall_pass, phase),
            metadata={"dimensions_requested": dimensions},
        )

    # ── Context loading ─────────────────────────────────────────

    def _load_context(
        self, project_dir: Path, phase: str
    ) -> Dict[str, Any]:
        """Load all global context files for the review.

        Loads:
          1. specs/knowledge_graph.json or .yaml (knowledge graph)
          2. specs/architecture.md (architecture design)
          3. specs/journey.md (user journey)
          4. specs/*review_log*.md (historical review logs)
          5. features.json (project feature status)

        Returns a dict with keys: knowledge_graph, architecture, journey,
        review_logs, features_json, and _loaded_files tracking.
        """
        context: Dict[str, Any] = {
            "knowledge_graph": "",
            "architecture": "",
            "journey": "",
            "review_logs": "",
            "features_json": "",
            "_loaded_files": [],
        }

        specs_dir = project_dir / self._specs_dir_name

        # 1. Knowledge graph (multiple possible formats)
        for kg_name in ("knowledge_graph.json", "knowledge_graph.yaml", "knowledge_graph.yml"):
            kg_path = specs_dir / kg_name
            if kg_path.exists():
                try:
                    context["knowledge_graph"] = kg_path.read_text(encoding="utf-8")
                    context["_loaded_files"].append(str(kg_path))
                    break
                except (IOError, OSError) as e:
                    logger.warning("Inspector: Could not read %s: %s", kg_path, e)

        # If no file found, try loading from KnowledgeGraph class
        if not context["knowledge_graph"]:
            context["knowledge_graph"] = self._load_knowledge_graph_class(project_dir)

        # 2. Architecture
        arch_paths = [
            specs_dir / "architecture.md",
            specs_dir / "architecture.txt",
            project_dir / "architecture.md",
        ]
        for arch_path in arch_paths:
            if arch_path.exists():
                try:
                    context["architecture"] = arch_path.read_text(encoding="utf-8")
                    context["_loaded_files"].append(str(arch_path))
                    break
                except (IOError, OSError) as e:
                    logger.warning("Inspector: Could not read %s: %s", arch_path, e)

        # 3. Journey
        journey_paths = [
            specs_dir / "journey.md",
            specs_dir / "journey.txt",
            specs_dir / "user_journey.md",
        ]
        for jp in journey_paths:
            if jp.exists():
                try:
                    context["journey"] = jp.read_text(encoding="utf-8")
                    context["_loaded_files"].append(str(jp))
                    break
                except (IOError, OSError) as e:
                    logger.warning("Inspector: Could not read %s: %s", jp, e)

        # 4. Review logs
        if specs_dir.exists():
            for rl_pattern in ("*review_log*", "*review*log*"):
                for rl_path in sorted(specs_dir.glob(rl_pattern)):
                    if rl_path.is_file():
                        try:
                            text = rl_path.read_text(encoding="utf-8")
                            if context["review_logs"]:
                                context["review_logs"] += f"\n\n--- {rl_path.name} ---\n{text}"
                            else:
                                context["review_logs"] = f"--- {rl_path.name} ---\n{text}"
                            context["_loaded_files"].append(str(rl_path))
                        except (IOError, OSError) as e:
                            logger.warning("Inspector: Could not read %s: %s", rl_path, e)

        # Also check .logs directory
        logs_dir = project_dir / self._logs_dir_name
        if logs_dir.exists():
            for rl_path in sorted(logs_dir.glob("*review*")):
                if rl_path.is_file():
                    try:
                        text = rl_path.read_text(encoding="utf-8")
                        if context["review_logs"]:
                            context["review_logs"] += f"\n\n--- {rl_path.name} ---\n{text}"
                        else:
                            context["review_logs"] = f"--- {rl_path.name} ---\n{text}"
                        context["_loaded_files"].append(str(rl_path))
                    except (IOError, OSError) as e:
                        logger.warning("Inspector: Could not read %s: %s", rl_path, e)

        # 5. Features.json
        features_path = project_dir / "features.json"
        if features_path.exists():
            try:
                context["features_json"] = features_path.read_text(encoding="utf-8")
                context["_loaded_files"].append(str(features_path))
            except (IOError, OSError) as e:
                logger.warning("Inspector: Could not read features.json: %s", e)

        return context

    def _load_knowledge_graph_class(self, project_dir: Path) -> str:
        """Try to load the knowledge graph via the KnowledgeGraph class and serialize it."""
        try:
            # Try dual import (package / flat)
            try:
                from knowledge_graph import KnowledgeGraph
            except ImportError:
                from src.knowledge_graph import KnowledgeGraph

            # Look for serialized file in project specs
            kg_path = project_dir / self._specs_dir_name / "knowledge_graph.json"
            if kg_path.exists():
                kg = KnowledgeGraph.from_json(kg_path)
                return json.dumps(kg.to_dict(), indent=2, ensure_ascii=False)
        except ImportError:
            logger.debug("Inspector: KnowledgeGraph class not available for auto-load")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Inspector: Could not load KnowledgeGraph class: %s", e)

        return json.dumps({"error": "Knowledge graph not available", "stats": {}})

    def _collect_phase_artifacts(
        self,
        project_dir: Path,
        phase: str,
        context: Dict[str, Any],
    ) -> str:
        """Collect all phase-specific artifacts into a text summary.

        Looks for:
          - specs/<phase>_*.md files
          - specs/<phase>* files
          - General output files in the specs directory
        """
        parts: List[str] = []

        specs_dir = project_dir / self._specs_dir_name

        phase_artifact_max_chars = get_threshold("inspector.artifacts.phase_artifact_max_chars", 5000)
        features_json_max_chars = get_threshold("inspector.artifacts.features_json_max_chars", 3000)

        if specs_dir.exists():
            # Look for phase-specific files
            for pattern in (f"{phase}*.md", f"{phase}*.txt", f"{phase}*.json"):
                for fpath in sorted(specs_dir.glob(pattern)):
                    if fpath.is_file():
                        try:
                            text = fpath.read_text(encoding="utf-8")
                            parts.append(f"--- {fpath.name} ---\n{text[:phase_artifact_max_chars]}")
                        except (IOError, OSError):
                            pass

        # Also note what we have from features.json
        if context.get("features_json"):
            try:
                features = json.loads(context["features_json"])
                phase_features = [
                    f for f in features.get("features", [])
                    if f.get("phase", "").lower() == phase.lower()
                    or phase.lower() in f.get("id", "").lower()
                ]
                if phase_features:
                    parts.append(
                        f"--- features.json (phase-related) ---\n"
                        f"{json.dumps(phase_features, indent=2, ensure_ascii=False)[:features_json_max_chars]}"
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        if not parts:
            return f"(No phase-specific artifacts found for phase '{phase}' in {specs_dir})"

        return "\n\n".join(parts)

    # ── Dimension review ────────────────────────────────────────

    def _review_dimension(
        self,
        dimension: str,
        phase: str,
        context: Dict[str, Any],
        phase_artifacts: str,
    ) -> Tuple[List[DimensionFinding], float]:
        """Run review for a single dimension.

        Constructs a prompt from the dimension template, fills in context,
        parses the LLM response (structured JSON), and returns findings + score.

        In practice, the LLM call would go through the pipeline's adapter layer.
        Here we provide the structured prompt building and response parsing;
        the actual LLM invocation is done via a pluggable callable or adapter.

        Args:
            dimension: One of ReviewDimension.ALL.
            phase: Current pipeline phase.
            context: Loaded global context dict.
            phase_artifacts: Text summary of phase-specific artifacts.

        Returns:
            (list of DimensionFinding, dimension_score: float)
        """
        prompt_template = DIMENSION_PROMPTS.get(dimension)
        if not prompt_template:
            logger.error("Inspector: Unknown review dimension: %s", dimension)
            return [], 0.0

        # Build prompt substitutions
        subs = {
            "phase": phase,
            "phase_artifacts": phase_artifacts,
            "user_goals": "\n".join(
                f"- {g}" for g in self.user_goals
            ) if self.user_goals else "(No user goals configured — use set_user_perspective())",
            "user_concerns": "\n".join(
                f"- {c}" for c in self.user_concerns
            ) if self.user_concerns else "(No user concerns configured)",
            "knowledge_graph": context.get("knowledge_graph", "(Not available)"),
            "journey": context.get("journey", "(Not available)"),
            "architecture": context.get("architecture", "(Not available)"),
            "review_logs": context.get("review_logs", "(No previous review logs)"),
        }

        prompt = prompt_template.format(**subs)

        # ── LLM call (pluggable) ──
        raw_response = self._call_llm(prompt, dimension=dimension)

        # ── Parse response ──
        findings, score = self._parse_dimension_response(
            raw_response=raw_response,
            dimension=dimension,
            prompt=prompt,
        )

        return findings, score

    def _call_llm(self, prompt: str, dimension: str) -> str:
        """Call the independent LLM with the review prompt.

        This is a pluggable method. In production, the pipeline adapter layer
        handles the actual LLM invocation. The default implementation returns
        a placeholder structured response for testing.

        Override this method or inject a callable via set_llm_caller() for
        real LLM integration.

        Args:
            prompt: The fully formatted review prompt.
            dimension: Which dimension is being reviewed.

        Returns:
            Raw text response from the LLM (expected to be JSON).
        """
        # Check if a custom LLM caller is set
        if hasattr(self, "_llm_caller") and self._llm_caller is not None:
            try:
                return self._llm_caller(prompt, self.model, dimension)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
                logger.error("Inspector: LLM caller failed for dimension=%s: %s", dimension, e)
                return json.dumps({
                    "error": str(e),
                    "overall": "warning",
                    "score": 0.5,
                })

        # Default placeholder: returns a neutral pass response
        logger.info(
            "Inspector: Using default _call_llm (no LLM caller configured). "
            "Set via inspector.set_llm_caller(callable)."
        )
        return json.dumps({
            "overall": "pass",
            "score": 0.7,
            "answers": [
                {
                    "question": f"{dimension} review — automated check",
                    "answer": f"No issues detected in {dimension} for this phase. "
                             f"Set up an LLM caller for real review.",
                    "evidence": ["Default inspector response"],
                    "recommendation": "Configure an LLM caller via set_llm_caller() for real reviews.",
                }
            ],
        })

    def set_llm_caller(self, caller) -> None:
        """Set a custom LLM caller function.

        The caller should have signature:
            caller(prompt: str, model: str, dimension: str) -> str

        Args:
            caller: A callable that takes (prompt, model, dimension) and returns a JSON string.
        """
        self._llm_caller = caller

    def _parse_dimension_response(
        self,
        raw_response: str,
        dimension: str,
        prompt: str,
    ) -> Tuple[List[DimensionFinding], float]:
        """Parse the LLM's JSON response into findings and an overall score.

        Args:
            raw_response: Raw text from the LLM (expected JSON).
            dimension: The review dimension name.
            prompt: The prompt that was sent (for logging fallback).

        Returns:
            (list of DimensionFinding, dimension_score: float)
        """
        findings: List[DimensionFinding] = []
        default_neutral = get_threshold("inspector.defaults.neutral_score", 0.5)
        score: float = default_neutral

        score_min = get_threshold("inspector.defaults.score_min", 0.0)
        score_max = get_threshold("inspector.defaults.score_max", 1.0)

        try:
            # Try to extract JSON from response (may have markdown fences)
            text = raw_response.strip()
            if text.startswith("```"):
                # Strip markdown fences
                lines = text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)

            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "Inspector: Could not parse LLM response as JSON for dimension=%s. "
                "Raw: %.200s...", dimension, raw_response
            )
            # Create a fallback finding
            findings.append(DimensionFinding(
                dimension=dimension,
                severity="warning",
                question=f"{dimension} review failed to parse",
                answer=f"Could not parse LLM response as JSON. Raw: {raw_response[:300]}",
                evidence=["Parse error"],
                recommendation="Check LLM response format and retry.",
            ))
            return findings, score

        # Handle error in response
        if "error" in data:
            findings.append(DimensionFinding(
                dimension=dimension,
                severity="warning",
                question=f"{dimension} LLM call error",
                answer=data.get("error", "Unknown error"),
                evidence=["LLM call failed"],
                recommendation="Retry the review or configure a different LLM.",
            ))
            score = data.get("score", default_neutral)
            return findings, score

        # Extract overall score
        score = float(data.get("score", default_neutral))
        score = max(score_min, min(score_max, score))

        # Extract answers as findings
        answers = data.get("answers", [])
        if not answers:
            # The overall field might itself be the answer
            overall = data.get("overall", "pass")
            findings.append(DimensionFinding(
                dimension=dimension,
                severity=overall if overall in SEVERITY_LEVELS else "pass",
                question=f"{dimension} overall assessment",
                answer=str(data),
                evidence=[],
                recommendation="",
            ))
            return findings, score

        for entry in answers:
            if not isinstance(entry, dict):
                continue
            findings.append(DimensionFinding(
                dimension=dimension,
                severity=(
                    entry.get("severity", "pass")
                    if entry.get("severity") in SEVERITY_LEVELS
                    else "pass"
                ),
                question=entry.get("question", ""),
                answer=entry.get("answer", ""),
                evidence=entry.get("evidence", []),
                recommendation=entry.get("recommendation", ""),
            ))

        return findings, score

    # ── Helpers ─────────────────────────────────────────────────

    def _summarize_memory(self) -> str:
        """Produce a human-readable summary of accumulated global memory."""
        if not self.global_memory:
            return "No prior reviews in global memory."

        lines = [f"Global memory contains {len(self.global_memory)} phase review(s):"]
        for phase_name, mem in self.global_memory.items():
            status = "PASS" if mem.get("overall_pass") else "FAIL"
            lines.append(
                f"  - {phase_name}: {status} "
                f"(scores={mem.get('scores', {})}, "
                f"findings={mem.get('findings_count', 0)})"
            )
        return "\n".join(lines)

    def _build_summary(
        self,
        findings: List[DimensionFinding],
        overall_pass: bool,
        phase: str,
    ) -> str:
        """Build a human-readable summary of the review."""
        fails = [f for f in findings if f.severity == "fail"]
        warnings = [f for f in findings if f.severity == "warning"]
        passes = [f for f in findings if f.severity == "pass"]

        verdict = "PASS" if overall_pass else "FAIL"
        lines = [
            f"Inspector review for phase '{phase}': {verdict}",
            f"  Total findings: {len(findings)} "
            f"(pass={len(passes)}, warning={len(warnings)}, fail={len(fails)})",
            f"  Model: {self.model}",
        ]

        if fails:
            lines.append("  Failures:")
            for f in fails[:5]:
                lines.append(f"    - [{f.dimension}] {f.question}: {f.answer[:120]}")

        if warnings:
            lines.append("  Warnings:")
            for w in warnings[:3]:
                lines.append(f"    - [{w.dimension}] {w.question}")

        return "\n".join(lines)

    # ── Serialization ───────────────────────────────────────────

    def save_report(self, report: InspectorReport, path: Path) -> None:
        """Save an InspectorReport to a JSON file.

        Args:
            report: The report to save.
            path: File path to write to.
        """
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_report(self, path: Path) -> InspectorReport:
        """Load an InspectorReport from a JSON file.

        Args:
            path: File path to read from.

        Returns:
            Parsed InspectorReport.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        return InspectorReport.from_dict(data)

    def __repr__(self) -> str:
        return (
            f"Inspector(model={self.model!r}, "
            f"memory_phases={list(self.global_memory.keys())}, "
            f"user_goals={len(self.user_goals)})"
        )
