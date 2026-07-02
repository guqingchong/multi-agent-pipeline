"""src/inspector.py — Independent Inspector with veto power.

The Inspector is an independent auditor that checks phase outputs against
PRD, architecture, journey, and acceptance criteria. It does not execute
tasks; it only reads project documents and decides whether the current phase
may advance.

Public API:
    AuditVerdict   — pass / veto / needs_clarification
    AuditReport    — structured audit result
    Inspector.audit(phase, evidence) -> AuditReport
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditVerdict(str, Enum):
    PASS = "pass"
    VETO = "veto"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass
class AuditReport:
    phase: str
    verdict: AuditVerdict = AuditVerdict.PASS
    evidence_files: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    human_can_override: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "verdict": self.verdict.value,
            "evidence_files": list(self.evidence_files),
            "findings": list(self.findings),
            "risks": list(self.risks),
            "suggestions": list(self.suggestions),
            "human_can_override": self.human_can_override,
        }


class Inspector:
    """Independent auditor that checks phase outputs against PRD, architecture,
    journey, and acceptance criteria. Can veto advance if inconsistencies found.
    """

    CORE_DOCS = ("prd.md", "architecture.md", "journey.md", "acceptance.md")

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.docs_dir = self.project_dir / "docs"

    def _read_doc(self, name: str) -> str:
        path = self.docs_dir / name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def audit(self, phase: str, evidence: Optional[Dict[str, Any]] = None) -> AuditReport:
        """Audit the given phase before it is allowed to advance."""
        evidence = evidence or {}
        phase = (phase or "").lower()

        prd = self._read_doc("prd.md")
        architecture = self._read_doc("architecture.md")
        journey = self._read_doc("journey.md")
        acceptance = self._read_doc("acceptance.md")

        report = AuditReport(phase=phase)

        # Collect evidence files actually present
        for doc_name in self.CORE_DOCS:
            if (self.docs_dir / doc_name).exists():
                report.evidence_files.append(doc_name)

        # Example: plan phase must reference PRD goals
        if phase == "plan":
            plan_doc = self._read_doc("plan.md")
            if not plan_doc:
                report.verdict = AuditVerdict.VETO
                report.findings.append("plan.md 缺失，无法判断优化方案是否基于目标制定。")
            elif prd and "响应时间" in prd and "响应时间" not in plan_doc:
                report.verdict = AuditVerdict.VETO
                report.findings.append("PRD 明确要求优化响应时间，但 plan.md 未提及该目标。")

        # Example: execute phase must not violate architecture boundaries
        if phase == "execute":
            changed_summary = list(evidence.get("changed_files") or [])
            if architecture and "外部 API 接口" in architecture:
                if any("api" in f.lower() for f in changed_summary):
                    report.risks.append("检测到 api 相关文件改动，请确认未修改外部接口契约。")

        # Default pass if no veto findings
        if report.verdict != AuditVerdict.VETO:
            report.verdict = AuditVerdict.PASS

        return report
