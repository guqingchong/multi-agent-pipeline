"""src/adversarial_review.py — Per-point 3-round adversarial debate engine with arbitration.

Core mechanism:
    For each disputed point in PRD / DESIGN / JOURNEY phases:
        1. Reviewer agent independently researches (own knowledge graph)
        2. Round 1: Author agent argues position A, Reviewer agent argues position B
        3. Round 2: Each supplements evidence, refines positions
        4. Round 3: Final statements — if still no consensus:
        5. Third-party arbiter (independent LLM, different model from A and B) decides:
           A wins / B wins / COMPROMISE

All debate records are preserved for full traceability.

Dependencies: W4-K01 (knowledge_graph.py) — used for independent research context.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# Enums
# ───────────────────────────────────────────────────────────────

class ArbiterDecision(Enum):
    """Arbiter's final decision after 3 rounds without consensus."""
    A_WINS = "A"
    B_WINS = "B"
    COMPROMISE = "COMPROMISE"

    def __str__(self) -> str:
        return self.value


class DebatePhase(Enum):
    """Which pipeline phase this debate is part of."""
    PRD = "prd"
    DESIGN = "design"
    JOURNEY = "journey"

    def __str__(self) -> str:
        return self.value


# ───────────────────────────────────────────────────────────────
# Dataclasses
# ───────────────────────────────────────────────────────────────

@dataclass
class DebateRound:
    """A single round of debate between two agents on one disputed point.

    Each round captures both sides' arguments, evidence, and whether
    convergence was reached during this round.
    """
    round_number: int                              # 1, 2, or 3
    argument_a: str                                # Agent A's argument this round
    argument_b: str                                # Agent B's argument this round
    evidence_a: List[str] = field(default_factory=list)  # Agent A's evidence
    evidence_b: List[str] = field(default_factory=list)  # Agent B's evidence
    convergence: bool = False                      # Did they reach agreement?
    converged_position: str = ""                   # The agreed-upon position if convergence=True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_number": self.round_number,
            "argument_a": self.argument_a,
            "evidence_a": self.evidence_a,
            "argument_b": self.argument_b,
            "evidence_b": self.evidence_b,
            "convergence": self.convergence,
            "converged_position": self.converged_position,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DebateRound:
        return cls(
            round_number=data["round_number"],
            argument_a=data.get("argument_a", ""),
            evidence_a=data.get("evidence_a", []),
            argument_b=data.get("argument_b", ""),
            evidence_b=data.get("evidence_b", []),
            convergence=data.get("convergence", False),
            converged_position=data.get("converged_position", ""),
        )


@dataclass
class DebatePoint:
    """A single disputed point extracted from a document under review.

    Each point represents one specific location/claim where the reviewer
    agent disagrees with the original author. Multiple disputed points
    can exist within a single paragraph or section.
    """
    id: str                                        # Unique point ID (e.g., "P1", "P2")
    topic: str                                     # Short topic / title of the dispute
    position_a: str                                # Author agent's core position
    position_b: str                                # Reviewer agent's core position
    source_location: str = ""                      # Where in the document (section/line ref)
    context_before: str = ""                       # Original text before the disputed point
    context_after: str = ""                        # Original text after the disputed point
    rounds: List[DebateRound] = field(default_factory=list)
    phase: str = ""                                # Which pipeline phase (prd/design/journey)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def round_count(self) -> int:
        """Number of debate rounds completed for this point."""
        return len(self.rounds)

    @property
    def is_converged(self) -> bool:
        """Whether this point reached consensus during debate."""
        return any(r.convergence for r in self.rounds)

    @property
    def latest_round(self) -> Optional[DebateRound]:
        """Most recent debate round, or None if no rounds yet."""
        return self.rounds[-1] if self.rounds else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "position_a": self.position_a,
            "position_b": self.position_b,
            "source_location": self.source_location,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "rounds": [r.to_dict() for r in self.rounds],
            "phase": self.phase,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DebatePoint:
        return cls(
            id=data["id"],
            topic=data.get("topic", ""),
            position_a=data.get("position_a", ""),
            position_b=data.get("position_b", ""),
            source_location=data.get("source_location", ""),
            context_before=data.get("context_before", ""),
            context_after=data.get("context_after", ""),
            rounds=[DebateRound.from_dict(r) for r in data.get("rounds", [])],
            phase=data.get("phase", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ArbitrationResult:
    """Result of third-party arbitration after 3 inconclusive debate rounds.

    The arbiter is an independent LLM (different model from both A and B)
    that reviews all debate records and evidence, then rules:
        - A wins (author's position is better)
        - B wins (reviewer's position is better)
        - COMPROMISE (a middle-ground solution is proposed)
    """
    point_id: str                                  # Which DebatePoint this is for
    winner: str                                    # "A" / "B" / "COMPROMISE"
    final_decision: str                            # The final ruling / resolution text
    rationale: str                                 # Why the arbiter decided this way
    arbiter_model: str                             # Which model served as arbiter
    timestamp: float = field(default_factory=time.time)
    confidence: float = 1.0                        # Arbiter's confidence (0.0-1.0)
    all_rounds_summary: str = ""                   # Brief summary of all 3 rounds
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_compromise(self) -> bool:
        return self.winner == "COMPROMISE"

    @property
    def author_wins(self) -> bool:
        return self.winner == "A"

    @property
    def reviewer_wins(self) -> bool:
        return self.winner == "B"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "point_id": self.point_id,
            "winner": self.winner,
            "final_decision": self.final_decision,
            "rationale": self.rationale,
            "arbiter_model": self.arbiter_model,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "all_rounds_summary": self.all_rounds_summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArbitrationResult:
        return cls(
            point_id=data["point_id"],
            winner=data.get("winner", "COMPROMISE"),
            final_decision=data.get("final_decision", ""),
            rationale=data.get("rationale", ""),
            arbiter_model=data.get("arbiter_model", ""),
            timestamp=data.get("timestamp", time.time()),
            confidence=data.get("confidence", 1.0),
            all_rounds_summary=data.get("all_rounds_summary", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ReviewReport:
    """Complete report of an adversarial review session.

    Contains all debate points, all rounds, all arbitration results,
    plus summary statistics for the entire review.
    """
    document_path: str                             # Path to the reviewed document
    phase: str                                     # PRD / DESIGN / JOURNEY
    author_agent: str                              # Who wrote the document
    reviewer_agent: str                            # Who reviewed it
    reviewer_context: str = ""                     # Independent research context
    debate_points: List[DebatePoint] = field(default_factory=list)
    arbitration_results: List[ArbitrationResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    summary: str = ""                              # Human-readable summary

    @property
    def total_points(self) -> int:
        return len(self.debate_points)

    @property
    def converged_count(self) -> int:
        """Number of points that converged without arbitration."""
        return sum(1 for p in self.debate_points if p.is_converged)

    @property
    def arbitrated_count(self) -> int:
        """Number of points that required third-party arbitration."""
        return len(self.arbitration_results)

    @property
    def author_win_count(self) -> int:
        return sum(1 for r in self.arbitration_results if r.author_wins)

    @property
    def reviewer_win_count(self) -> int:
        return sum(1 for r in self.arbitration_results if r.reviewer_wins)

    @property
    def compromise_count(self) -> int:
        return sum(1 for r in self.arbitration_results if r.is_compromise)

    @property
    def duration_seconds(self) -> float:
        end = self.completed_at or time.time()
        return end - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_path": self.document_path,
            "phase": self.phase,
            "author_agent": self.author_agent,
            "reviewer_agent": self.reviewer_agent,
            "reviewer_context": self.reviewer_context,
            "debate_points": [p.to_dict() for p in self.debate_points],
            "arbitration_results": [r.to_dict() for r in self.arbitration_results],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": self.summary,
            "statistics": {
                "total_points": self.total_points,
                "converged": self.converged_count,
                "arbitrated": self.arbitrated_count,
                "author_wins": self.author_win_count,
                "reviewer_wins": self.reviewer_win_count,
                "compromises": self.compromise_count,
                "duration_seconds": self.duration_seconds,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewReport:
        return cls(
            document_path=data.get("document_path", ""),
            phase=data.get("phase", ""),
            author_agent=data.get("author_agent", ""),
            reviewer_agent=data.get("reviewer_agent", ""),
            reviewer_context=data.get("reviewer_context", ""),
            debate_points=[DebatePoint.from_dict(p) for p in data.get("debate_points", [])],
            arbitration_results=[ArbitrationResult.from_dict(r) for r in data.get("arbitration_results", [])],
            started_at=data.get("started_at", time.time()),
            completed_at=data.get("completed_at"),
            summary=data.get("summary", ""),
        )


# ───────────────────────────────────────────────────────────────
# AdversarialReview — Main engine
# ───────────────────────────────────────────────────────────────

MAX_DEBATE_ROUNDS = 3

# Debate prompt templates
ROUND_PROMPTS: Dict[int, str] = {
    1: (
        "Round 1 of 3 — Opening Arguments.\n\n"
        "Disputed Point: {topic}\n"
        "Original Text (Agent A / Author): {position_a}\n"
        "Reviewer's Objection (Agent B): {position_b}\n\n"
        "Agent A: Defend your original position with evidence and reasoning.\n"
        "Agent B: Strengthen your objection with independent research findings.\n\n"
        "If you find common ground, state 'CONVERGENCE: <agreed position>'."
    ),
    2: (
        "Round 2 of 3 — Evidence & Refinement.\n\n"
        "Disputed Point: {topic}\n"
        "Round 1 Summary:\n"
        "  Agent A: {prev_arg_a}\n"
        "  Agent B: {prev_arg_b}\n\n"
        "Agent A: Address Agent B's concerns. Provide additional evidence.\n"
        "Agent B: Address Agent A's counterpoints. Provide additional evidence.\n\n"
        "If you find common ground, state 'CONVERGENCE: <agreed position>'."
    ),
    3: (
        "Round 3 of 3 — Final Statements.\n\n"
        "Disputed Point: {topic}\n"
        "Round 2 Summary:\n"
        "  Agent A: {prev_arg_a}\n"
        "  Agent B: {prev_arg_b}\n\n"
        "Agent A: Make your final case. What is your bottom line?\n"
        "Agent B: Make your final case. What is your bottom line?\n\n"
        "If you find common ground, state 'CONVERGENCE: <agreed position>'.\n"
        "Otherwise this will go to third-party arbitration."
    ),
}

ARBITER_PROMPT = (
    "You are an independent third-party arbiter reviewing a 3-round debate that "
    "did not reach consensus.\n\n"
    "=== DISPUTED POINT ===\n"
    "Topic: {topic}\n"
    "Agent A (Author) Position: {position_a}\n"
    "Agent B (Reviewer) Position: {position_b}\n\n"
    "=== DEBATE RECORDS ===\n"
    "{rounds_summary}\n\n"
    "=== YOUR TASK ===\n"
    "Review all arguments and evidence from both sides. You must issue ONE of:\n"
    "  1. 'A' — Agent A's position is superior\n"
    "  2. 'B' — Agent B's position is superior\n"
    "  3. 'COMPROMISE' — Propose a middle-ground solution\n\n"
    "Output JSON format:\n"
    '{{"winner": "A"|"B"|"COMPROMISE", "final_decision": "...", '
    '"rationale": "...", "confidence": 0.0-1.0}}'
)

EXTRACT_POINTS_PROMPT = (
    "You are a document reviewer. Read the following document and identify every "
    "point of disagreement / ambiguity / risk / gap.\n\n"
    "For each disputed point, output:\n"
    '  - id: "P1", "P2", ...\n'
    '  - topic: Short description\n'
    '  - position_a: What the document currently says/assumes\n'
    '  - position_b: What you (the reviewer) believe is wrong/missing/risky\n'
    '  - source_location: Section or line reference\n\n'
    "Output as JSON array.\n\n"
    "=== DOCUMENT ===\n{document}\n\n"
    "=== DISPUTED POINTS ==="
)


class AdversarialReview:
    """Per-point 3-round adversarial debate + third-party arbitration engine.

    Used for PRD / DESIGN / JOURNEY phases where documents need rigorous
    adversarial review before advancement.

    Workflow:
        1. Reviewer agent independently researches the domain (own knowledge graph)
        2. Reviewer marks every disputed point in the document
        3. For each point: up to 3 rounds of debate
        4. If no consensus after 3 rounds: independent arbiter decides
        5. All debate records + arbitration results are preserved

    Example:
        >>> engine = AdversarialReview(arbiter_model="deepseek-v4-pro")
        >>> report = engine.review_document(
        ...     document_path=Path("specs/prd.md"),
        ...     author_agent="hermes",
        ...     reviewer_agent="codewhale",
        ...     reviewer_context="Independent research on heat supply domain"
        ... )
        >>> print(f"Points: {report.total_points}, "
        ...       f"Converged: {report.converged_count}, "
        ...       f"Arbitrated: {report.arbitrated_count}")
    """

    def __init__(self, arbiter_model: str = "deepseek-v4-pro") -> None:
        """Initialize the adversarial review engine.

        Args:
            arbiter_model: The LLM model to use for third-party arbitration.
                           Must be different from both the author and reviewer agents.
        """
        self.arbiter_model = arbiter_model

    # ── Public API ──────────────────────────────────────────

    def review_document(
        self,
        document_path: Path,
        author_agent: str,
        reviewer_agent: str,
        reviewer_context: str = "",
        phase: str = "",
    ) -> ReviewReport:
        """Complete adversarial review of a document.

        Full pipeline:
            1. Load document
            2. Extract disputed points (reviewer's objections)
            3. Debate each point (up to 3 rounds)
            4. Arbitrate any points that didn't converge
            5. Return complete ReviewReport

        Args:
            document_path: Path to the document being reviewed.
            author_agent: Name of the agent who authored the document.
            reviewer_agent: Name of the agent performing the review.
            reviewer_context: Independent research findings from the reviewer.
            phase: Pipeline phase (prd/design/journey).

        Returns:
            Complete ReviewReport with all debate records and arbitration results.
        """
        # Load document
        if not document_path.exists():
            raise FileNotFoundError(f"Document not found: {document_path}")
        document_text = document_path.read_text(encoding="utf-8")

        # Auto-detect phase from path if not provided
        if not phase:
            phase = self._detect_phase(document_path)

        report = ReviewReport(
            document_path=str(document_path),
            phase=phase,
            author_agent=author_agent,
            reviewer_agent=reviewer_agent,
            reviewer_context=reviewer_context,
        )

        # Step 1: Extract disputed points
        points = self._extract_debate_points(document_text, phase=phase)
        report.debate_points = points

        # Step 2 & 3: Debate each point + arbitrate if needed
        for point in points:
            # Run up to 3 rounds of debate
            debate_result = self.debate_point(
                point=point,
                agent_a=author_agent,
                agent_b=reviewer_agent,
                reviewer_context=reviewer_context,
            )

            if debate_result.winner != "CONVERGED":
                # Point did not converge — add arbitration result
                report.arbitration_results.append(debate_result)

        report.completed_at = time.time()
        report.summary = self._build_summary(report)

        return report

    def debate_point(
        self,
        point: DebatePoint,
        agent_a: str,
        agent_b: str,
        reviewer_context: str = "",
    ) -> ArbitrationResult:
        """Run up to 3 debate rounds on a single disputed point.

        Flow:
            Round 1: Both agents present opening arguments.
            Round 2: Both agents supplement evidence and refine positions.
            Round 3: Final statements.
            If no convergence → third-party arbiter decides.

        Args:
            point: The DebatePoint to debate.
            agent_a: The author agent (defends original position).
            agent_b: The reviewer agent (defends objection).
            reviewer_context: Independent research context for the reviewer.

        Returns:
            ArbitrationResult. If converged, winner="CONVERGED".
            If arbitrated, winner is "A", "B", or "COMPROMISE".
        """
        for round_num in range(1, MAX_DEBATE_ROUNDS + 1):
            dr = self._run_single_round(
                point=point,
                round_number=round_num,
                agent_a=agent_a,
                agent_b=agent_b,
                reviewer_context=reviewer_context,
            )
            point.rounds.append(dr)

            if dr.convergence:
                # Consensus reached — no arbitration needed
                return ArbitrationResult(
                    point_id=point.id,
                    winner="CONVERGED",
                    final_decision=dr.converged_position,
                    rationale=f"Both agents reached consensus in round {round_num}.",
                    arbiter_model="N/A (converged before arbitration)",
                    all_rounds_summary=self._summarize_rounds(point.rounds),
                )

        # All 3 rounds exhausted without convergence → arbitrate
        return self._arbitrate(point, point.rounds)

    def debate_all_points(
        self,
        document: str,
        author_agent: str,
        reviewer_agent: str,
        arbiter_agent: str = "",
        phase: str = "",
    ) -> List[ArbitrationResult]:
        """Debate all disputed points in a document.

        Convenience wrapper that extracts points, debates each one, and
        returns all results.

        Args:
            document: Full document text to review.
            author_agent: Author agent name.
            reviewer_agent: Reviewer agent name.
            arbiter_agent: Arbiter model (overrides self.arbiter_model if provided).
            phase: Pipeline phase.

        Returns:
            List of ArbitrationResult for every disputed point.
        """
        if arbiter_agent:
            self.arbiter_model = arbiter_agent

        points = self._extract_debate_points(document, phase=phase)
        results: List[ArbitrationResult] = []

        for point in points:
            result = self.debate_point(
                point=point,
                agent_a=author_agent,
                agent_b=reviewer_agent,
            )
            results.append(result)

        return results

    # ── Internal: Round execution ───────────────────────────

    def _run_single_round(
        self,
        point: DebatePoint,
        round_number: int,
        agent_a: str,
        agent_b: str,
        reviewer_context: str = "",
    ) -> DebateRound:
        """Simulate a single round of debate between two agents.

        In a real implementation, this would call the actual LLM agents.
        Here we provide the framework: the prompt template is constructed,
        convergence is detected, and the round record is created.

        Subclasses or external callers can override `_agent_argue` to
        integrate with actual LLM backends.
        """
        # Build argument A (author defending original position)
        argument_a, evidence_a = self._agent_argue(
            agent_name=agent_a,
            role="author",
            point=point,
            round_number=round_number,
            opponent_agent=agent_b,
            context="",
        )

        # Build argument B (reviewer defending objection)
        argument_b, evidence_b = self._agent_argue(
            agent_name=agent_b,
            role="reviewer",
            point=point,
            round_number=round_number,
            opponent_agent=agent_a,
            context=reviewer_context,
        )

        # Check for convergence signals
        convergence = False
        converged_position = ""

        # Detect explicit CONVERGENCE marker
        for text in (argument_a, argument_b):
            if "CONVERGENCE:" in text:
                convergence = True
                converged_position = text.split("CONVERGENCE:", 1)[1].strip()
                break

        return DebateRound(
            round_number=round_number,
            argument_a=argument_a,
            evidence_a=evidence_a,
            argument_b=argument_b,
            evidence_b=evidence_b,
            convergence=convergence,
            converged_position=converged_position,
        )

    def _agent_argue(
        self,
        agent_name: str,
        role: str,
        point: DebatePoint,
        round_number: int,
        opponent_agent: str,
        context: str = "",
    ) -> Tuple[str, List[str]]:
        """Generate an agent's argument for a given debate round.

        This is the pluggable hook for LLM integration. The default
        implementation returns placeholder arguments based on the round
        template. Override this method to connect to real LLM agents.

        Args:
            agent_name: Name of the arguing agent.
            role: "author" or "reviewer".
            point: The DebatePoint being debated.
            round_number: Current round (1-3).
            opponent_agent: Name of the opposing agent.
            context: Additional context (e.g., independent research).

        Returns:
            Tuple of (argument_text, list_of_evidence_items).
        """
        # Build the prompt that would be sent to the LLM
        prompt_template = ROUND_PROMPTS.get(round_number, ROUND_PROMPTS[3])

        prev_rounds = point.rounds
        prev_arg_a = prev_rounds[-1].argument_a if prev_rounds else "(none)"
        prev_arg_b = prev_rounds[-1].argument_b if prev_rounds else "(none)"

        prompt = prompt_template.format(
            topic=point.topic,
            position_a=point.position_a,
            position_b=point.position_b,
            prev_arg_a=prev_arg_a,
            prev_arg_b=prev_arg_b,
        )

        # Placeholder: in a real system, this would call the LLM
        if role == "author":
            argument = (
                f"[{agent_name} Round {round_number}] "
                f"Defending position on: {point.topic}. "
                f"Prompt: {prompt[:200]}..."
            )
            position = point.position_a
        else:
            context_note = f" [Context: {context}]" if context else ""
            argument = (
                f"[{agent_name} Round {round_number}] "
                f"Challenging position on: {point.topic}.{context_note} "
                f"Prompt: {prompt[:200]}..."
            )
            position = point.position_b

        evidence = [f"Source: {agent_name} knowledge graph", f"Position: {position}"]

        return argument, evidence

    # ── Internal: Arbitration ───────────────────────────────

    def _arbitrate(
        self,
        point: DebatePoint,
        rounds: List[DebateRound],
    ) -> ArbitrationResult:
        """Third-party arbitration after 3 inconclusive rounds.

        The arbiter is an independent LLM (different model from both A and B)
        that reviews all debate records and evidence, then decides:
            - A wins (author's position is better)
            - B wins (reviewer's position is better)
            - COMPROMISE (middle-ground solution)

        Args:
            point: The DebatePoint that could not be resolved.
            rounds: All 3 debate rounds (guaranteed non-empty).

        Returns:
            ArbitrationResult with the arbiter's decision.
        """
        rounds_summary = self._summarize_rounds(rounds)

        arbiter_prompt = ARBITER_PROMPT.format(
            topic=point.topic,
            position_a=point.position_a,
            position_b=point.position_b,
            rounds_summary=rounds_summary,
        )

        # Placeholder arbitration: in a real system, this calls the arbiter LLM.
        # The default heuristic: if the reviewer (B) provided substantial evidence
        # in later rounds, they win. Otherwise, compromise.
        decision = self._default_arbitration_heuristic(point, rounds)

        return ArbitrationResult(
            point_id=point.id,
            winner=decision["winner"],
            final_decision=decision["final_decision"],
            rationale=decision["rationale"],
            arbiter_model=self.arbiter_model,
            confidence=decision.get("confidence", 0.7),
            all_rounds_summary=rounds_summary,
            metadata={
                "arbiter_prompt": arbiter_prompt[:500],
                "total_rounds": len(rounds),
            },
        )

    def _default_arbitration_heuristic(
        self,
        point: DebatePoint,
        rounds: List[DebateRound],
    ) -> Dict[str, Any]:
        """Default heuristic for arbitration when no LLM is connected.

        Analyzes debate records to make a reasoned default decision.
        Override with actual LLM integration for production use.

        Strategy:
            - If agent B provided more evidence items overall → B wins
            - If roughly equal evidence → COMPROMISE
            - If agent A's final argument is substantially longer → A wins
        """
        total_evidence_a = sum(len(r.evidence_a) for r in rounds)
        total_evidence_b = sum(len(r.evidence_b) for r in rounds)

        if total_evidence_b > total_evidence_a + 2:
            return {
                "winner": "B",
                "final_decision": (
                    f"Reviewer ({point.position_b[:200]}) position accepted. "
                    f"Reviewer provided {total_evidence_b} evidence items vs "
                    f"author's {total_evidence_a}."
                ),
                "rationale": "Reviewer provided substantially more evidence.",
                "confidence": 0.75,
            }
        elif total_evidence_a > total_evidence_b + 2:
            return {
                "winner": "A",
                "final_decision": (
                    f"Author ({point.position_a[:200]}) position accepted. "
                    f"Author provided {total_evidence_a} evidence items vs "
                    f"reviewer's {total_evidence_b}."
                ),
                "rationale": "Author provided substantially more evidence.",
                "confidence": 0.75,
            }
        else:
            # Compromise: blend both positions
            return {
                "winner": "COMPROMISE",
                "final_decision": (
                    f"Compromise: both positions have merit. "
                    f"Author position: {point.position_a[:150]}... "
                    f"Reviewer concern: {point.position_b[:150]}... "
                    f"Recommendation: adopt author's core intent but address "
                    f"reviewer's key concerns."
                ),
                "rationale": (
                    "Both sides presented comparable evidence. "
                    "A middle-ground solution preserves the author's intent "
                    "while addressing the reviewer's valid concerns."
                ),
                "confidence": 0.65,
            }

    # ── Internal: Point extraction ──────────────────────────

    def _extract_debate_points(
        self,
        document: str,
        phase: str = "",
    ) -> List[DebatePoint]:
        """Extract disputed points from a document.

        In a real implementation, the reviewer agent would parse the document
        and identify every specific disagreement. The default implementation
        performs a structural scan to identify potential dispute areas.

        Args:
            document: Full document text.
            phase: Pipeline phase (for tagging points).

        Returns:
            List of DebatePoint objects extracted from the document.
        """
        points: List[DebatePoint] = []

        # Split document into sections for structural analysis
        sections = self._split_into_sections(document)
        point_counter = 0

        for section_title, section_body in sections:
            # Look for markers of debatable content
            debate_candidates = self._find_debatable_statements(section_body)

            for candidate_text, reason in debate_candidates:
                point_counter += 1
                point_id = f"P{point_counter}"

                points.append(DebatePoint(
                    id=point_id,
                    topic=f"[{section_title}] {reason[:80]}",
                    position_a=candidate_text,
                    position_b=f"REVIEWER OBJECTION: {reason}",
                    source_location=section_title,
                    context_before=self._get_context_before(section_body, candidate_text),
                    context_after=self._get_context_after(section_body, candidate_text),
                    phase=phase,
                ))

        return points

    def _split_into_sections(self, document: str) -> List[Tuple[str, str]]:
        """Split a markdown document into titled sections.

        Returns list of (section_title, section_body) tuples.
        """
        sections: List[Tuple[str, str]] = []
        lines = document.split("\n")

        current_title = "(preamble)"
        current_body: List[str] = []

        for line in lines:
            stripped = line.strip()
            # Heuristic: lines starting with # or ## are section headers
            if stripped.startswith("#") and not stripped.startswith("####"):
                if current_body:
                    sections.append((current_title, "\n".join(current_body)))
                current_title = stripped.lstrip("#").strip()
                current_body = []
            else:
                current_body.append(line)

        if current_body or current_title != "(preamble)":
            sections.append((current_title, "\n".join(current_body)))

        return sections

    def _find_debatable_statements(
        self,
        section_body: str,
    ) -> List[Tuple[str, str]]:
        """Find potentially debatable statements in a section.

        Looks for markers of strong claims, assumptions, or gaps:
            - "must", "always", "never", "guaranteed" (absolute claims)
            - "obviously", "clearly" (unqualified assertions)
            - "TODO", "FIXME", "XXX" (known gaps)
            - Sections referencing missing components

        Returns list of (statement_text, reason_for_dispute) tuples.
        """
        candidates: List[Tuple[str, str]] = []
        lines = section_body.split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            lower = stripped.lower()

            # Absolute claims
            if any(word in lower for word in ("must always", "never fail", "guaranteed", "100%")):
                candidates.append((stripped, "Absolute claim requires verification"))

            # Unqualified assertions
            if lower.startswith(("obviously", "clearly", "of course")):
                candidates.append((stripped, "Unqualified assertion needs evidence"))

            # Known gaps
            if any(marker in lower for marker in ("todo", "fixme", "xxx", "tbd")):
                candidates.append((stripped, "Known gap / incomplete item"))

            # Risky assumptions
            if "assume" in lower or "assuming" in lower:
                candidates.append((stripped, "Assumption should be validated"))

            # Missing components referenced
            if "should be implemented" in lower or "to be added" in lower:
                candidates.append((stripped, "Deferred implementation — timeline unclear"))

            # Single-line strong opinions
            if lower.startswith("the best") or lower.startswith("the only"):
                candidates.append((stripped, "Overly narrow claim — alternatives may exist"))

        return candidates

    def _get_context_before(self, section_body: str, target: str) -> str:
        """Get the text immediately before a target statement."""
        lines = section_body.split("\n")
        for i, line in enumerate(lines):
            if target in line:
                start = max(0, i - 2)
                return "\n".join(lines[start:i])
        return ""

    def _get_context_after(self, section_body: str, target: str) -> str:
        """Get the text immediately after a target statement."""
        lines = section_body.split("\n")
        for i, line in enumerate(lines):
            if target in line:
                end = min(len(lines), i + 3)
                return "\n".join(lines[i + 1 : end])
        return ""

    # ── Internal: Helpers ───────────────────────────────────

    def _detect_phase(self, document_path: Path) -> str:
        """Auto-detect pipeline phase from document path."""
        path_str = str(document_path).lower()
        if "prd" in path_str:
            return "prd"
        elif "design" in path_str or "arch" in path_str:
            return "design"
        elif "journey" in path_str:
            return "journey"
        return "unknown"

    def _summarize_rounds(self, rounds: List[DebateRound]) -> str:
        """Generate a compact summary of all debate rounds."""
        lines: List[str] = []
        for r in rounds:
            lines.append(
                f"Round {r.round_number}: "
                f"A: {r.argument_a[:120]}... | "
                f"B: {r.argument_b[:120]}..."
            )
            if r.convergence:
                lines.append(f"  → CONVERGED: {r.converged_position}")
        return "\n".join(lines)

    def _build_summary(self, report: ReviewReport) -> str:
        """Build a human-readable summary of the review."""
        lines = [
            f"Adversarial Review Report",
            f"{'=' * 40}",
            f"Document: {report.document_path}",
            f"Phase: {report.phase}",
            f"Author: {report.author_agent} | Reviewer: {report.reviewer_agent}",
            f"",
            f"Results:",
            f"  Total disputed points: {report.total_points}",
            f"  Converged (no arbitration needed): {report.converged_count}",
            f"  Arbitrated: {report.arbitrated_count}",
        ]

        if report.arbitrated_count > 0:
            lines.append(f"    - Author wins: {report.author_win_count}")
            lines.append(f"    - Reviewer wins: {report.reviewer_win_count}")
            lines.append(f"    - Compromises: {report.compromise_count}")

        lines.append(f"  Duration: {report.duration_seconds:.1f}s")
        lines.append(f"")
        lines.append(f"All debate records and arbitration decisions are preserved.")

        return "\n".join(lines)

    # ── Serialization ───────────────────────────────────────

    def save_report(self, report: ReviewReport, output_path: Path) -> None:
        """Save a ReviewReport to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_report(self, path: Path) -> ReviewReport:
        """Load a ReviewReport from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return ReviewReport.from_dict(data)


# ───────────────────────────────────────────────────────────────
# Module-level convenience functions
# ───────────────────────────────────────────────────────────────

def create_debate_point(
    topic: str,
    position_a: str,
    position_b: str,
    source_location: str = "",
    phase: str = "",
) -> DebatePoint:
    """Factory for creating a DebatePoint with auto-generated ID."""
    point_id = f"P{uuid.uuid4().hex[:8]}"
    return DebatePoint(
        id=point_id,
        topic=topic,
        position_a=position_a,
        position_b=position_b,
        source_location=source_location,
        phase=phase,
    )


def debate_single_point(
    topic: str,
    position_a: str,
    position_b: str,
    author_agent: str = "hermes",
    reviewer_agent: str = "codewhale",
    arbiter_model: str = "deepseek-v4-pro",
) -> ArbitrationResult:
    """Quick single-point debate without loading a full document.

    Convenience function for testing or simple one-off debates.

    Args:
        topic: The debate topic.
        position_a: Author's position.
        position_b: Reviewer's position.
        author_agent: Name of the author agent.
        reviewer_agent: Name of the reviewer agent.
        arbiter_model: Model to use for arbitration.

    Returns:
        ArbitrationResult.
    """
    engine = AdversarialReview(arbiter_model=arbiter_model)
    point = create_debate_point(
        topic=topic,
        position_a=position_a,
        position_b=position_b,
    )
    return engine.debate_point(
        point=point,
        agent_a=author_agent,
        agent_b=reviewer_agent,
    )


# ───────────────────────────────────────────────────────────────
# __main__ — Quick smoke test
# ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Adversarial Review Engine — Smoke Test ===\n")

    # Test 1: Single point debate
    print("1. Single point debate (convergence test):")
    result = debate_single_point(
        topic="MVP scope for PRD",
        position_a="MVP should include 10 core features for completeness",
        position_b="MVP should include only 6 features to reduce risk",
    )
    print(f"   Point: {result.point_id}")
    print(f"   Winner: {result.winner}")
    print(f"   Decision: {result.final_decision[:100]}...")
    print(f"   Arbiter: {result.arbiter_model}")
    print(f"   Confidence: {result.confidence}")
    print()

    # Test 2: Complete review of a mock document
    print("2. Full document review:")
    mock_doc = Path("_test_adversarial_review.md")
    mock_doc.write_text(
        "# Test PRD\n\n"
        "## MVP Scope\n"
        "The MVP must always include all 10 features.\n"
        "Obviously, the upload flow should auto-trigger parsing.\n\n"
        "## Assumptions\n"
        "We assume users have stable internet. TODO: offline mode.\n",
        encoding="utf-8",
    )

    try:
        engine = AdversarialReview(arbiter_model="deepseek-v4-pro")
        report = engine.review_document(
            document_path=mock_doc,
            author_agent="hermes",
            reviewer_agent="codewhale",
            reviewer_context="Independent research on PRD methodology",
        )
        print(f"   Total points: {report.total_points}")
        print(f"   Converged: {report.converged_count}")
        print(f"   Arbitrated: {report.arbitrated_count}")
        print(f"   Author wins: {report.author_win_count}")
        print(f"   Reviewer wins: {report.reviewer_win_count}")
        print(f"   Compromises: {report.compromise_count}")
        print(f"   Duration: {report.duration_seconds:.2f}s")
        print()

        # Show detailed results
        for i, point in enumerate(report.debate_points, 1):
            print(f"   Point {i}: {point.topic}")
            print(f"     Position A: {point.position_a[:80]}...")
            print(f"     Position B: {point.position_b[:80]}...")
            print(f"     Rounds: {point.round_count}")
            print(f"     Converged: {point.is_converged}")
            if point.is_converged:
                converging_round = next(r for r in point.rounds if r.convergence)
                print(f"     Converged at round {converging_round.round_number}")
            print()

        # Serialization round-trip test
        print("3. Serialization round-trip:")
        data = report.to_dict()
        loaded = ReviewReport.from_dict(data)
        assert loaded.total_points == report.total_points
        assert loaded.arbitrated_count == report.arbitrated_count
        print("   ✓ Round-trip OK")
        print()

        # Save to JSON
        output_path = Path("_test_adversarial_report.json")
        engine.save_report(report, output_path)
        print(f"4. Report saved to: {output_path}")
        loaded_report = engine.load_report(output_path)
        print(f"   ✓ Loaded: {loaded_report.total_points} points, {loaded_report.arbitrated_count} arbitrated")

    finally:
        # Cleanup
        if mock_doc.exists():
            mock_doc.unlink()
        output_json = Path("_test_adversarial_report.json")
        if output_json.exists():
            output_json.unlink()

    print("\n=== Smoke test complete ===")
