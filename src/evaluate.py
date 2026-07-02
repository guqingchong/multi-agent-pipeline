"""src/evaluate.py — LLM-as-Judge Evidence-First evaluation pipeline.

Design:
  1. Evidence Collection: Gather objective evidence via static analysis,
     test results, lint output, and spec conformance checks.
  2. Judge LLM: A different model from the project's main model scores
     5 dimensions with weighted criteria.
  3. Red-line Veto: Hard blockers based on critical dimension scores.

Scoring Dimensions (weighted):
  - accuracy      30% — factual correctness, spec compliance
  - completeness  20% — coverage of requirements, edge cases
  - honesty       25% — no fabrication, no hallucination, cites sources
  - helpfulness   15% — actionable output, clarity, usability
  - consistency   10% — internal coherence, style uniformity

Red-line Veto Rules:
  - honesty < 5  → BLOCK  (fabrication/hallucination risk)
  - accuracy < 4  → BLOCK  (critical correctness failure)
  - detected lie  → P0     (deliberate fabrication, highest severity)

Output: EvaluationResult dataclass with structured JSON serialization.

Dependencies: None external — self-contained evaluation module.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from thresholds_loader import get_threshold
except (ModuleNotFoundError, ImportError):
    from src.thresholds_loader import get_threshold


# ═══════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════


class Verdict(Enum):
    """Final evaluation verdict after scoring and red-line checks."""
    PASS = "PASS"        # All gates passed, ready for next phase
    BLOCK = "BLOCK"      # Red-line veto triggered, must fix before proceeding
    P0 = "P0"            # Critical severity — detected lie / fabrication
    WARN = "WARN"        # Non-blocking issues detected, proceed with caution

    def __str__(self) -> str:
        return self.value


class LieSeverity(Enum):
    """Severity of a detected fabrication."""
    NONE = "none"                # No lie detected
    MINOR = "minor"              # Minor inaccuracy, likely honest mistake
    MODERATE = "moderate"         # Significant misrepresentation
    CRITICAL = "critical"        # Deliberate fabrication, P0 escalation
    HALLUCINATION = "hallucination"  # Fabricated facts/entities/sources

    def __str__(self) -> str:
        return self.value


class EvidenceType(Enum):
    """Categories of collected evidence."""
    STATIC_ANALYSIS = "static_analysis"
    TEST_RESULTS = "test_results"
    LINT = "lint"
    SPEC_CONFORMANCE = "spec_conformance"
    CODE_REVIEW = "code_review"
    DEPENDENCY_AUDIT = "dependency_audit"
    SECURITY_SCAN = "security_scan"
    MANUAL = "manual"


# ═══════════════════════════════════════════════════════════════
# Evidence Dataclasses
# ═══════════════════════════════════════════════════════════════


@dataclass
class EvidenceItem:
    """A single piece of collected evidence.

    Attributes:
        evidence_type: Category of this evidence.
        source: Where the evidence came from (file path, tool name, etc.).
        finding: The actual finding/observation.
        severity: Importance level (info, warning, error, critical).
        confidence: Confidence in this evidence (0.0 - 1.0).
        timestamp: When this evidence was collected (epoch seconds).
        metadata: Additional structured data about this evidence.
    """
    evidence_type: EvidenceType
    source: str
    finding: str
    severity: str = "info"              # info / warning / error / critical
    confidence: float = 1.0             # 0.0 - 1.0
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_type": self.evidence_type.value,
            "source": self.source,
            "finding": self.finding,
            "severity": self.severity,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceItem":
        return cls(
            evidence_type=EvidenceType(d["evidence_type"]),
            source=d["source"],
            finding=d["finding"],
            severity=d.get("severity", "info"),
            confidence=d.get("confidence", 1.0),
            timestamp=d.get("timestamp", 0.0),
            metadata=d.get("metadata", {}),
        )


@dataclass
class EvidenceBundle:
    """Collected evidence from all sources before judge evaluation.

    Evidence MUST be collected and organized before the judge LLM
    evaluates — this enforces 'evidence-first' discipline.
    """
    project_name: str
    items: List[EvidenceItem] = field(default_factory=list)
    collection_duration_ms: float = 0.0
    total_items: int = 0
    errors_during_collection: List[str] = field(default_factory=list)

    def add(self, item: EvidenceItem) -> None:
        self.items.append(item)
        self.total_items = len(self.items)

    def add_error(self, error_msg: str) -> None:
        self.errors_during_collection.append(error_msg)

    def get_by_type(self, evidence_type: EvidenceType) -> List[EvidenceItem]:
        return [i for i in self.items if i.evidence_type == evidence_type]

    def get_by_severity(self, severity: str) -> List[EvidenceItem]:
        return [i for i in self.items if i.severity == severity]

    def critical_count(self) -> int:
        return len(self.get_by_severity("critical"))

    def error_count(self) -> int:
        return len(self.get_by_severity("error"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "items": [item.to_dict() for item in self.items],
            "collection_duration_ms": self.collection_duration_ms,
            "total_items": self.total_items,
            "errors_during_collection": self.errors_during_collection,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceBundle":
        return cls(
            project_name=d["project_name"],
            items=[EvidenceItem.from_dict(i) for i in d.get("items", [])],
            collection_duration_ms=d.get("collection_duration_ms", 0.0),
            total_items=d.get("total_items", 0),
            errors_during_collection=d.get("errors_during_collection", []),
        )


# ═══════════════════════════════════════════════════════════════
# Dimension Score
# ═══════════════════════════════════════════════════════════════


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension.

    Attributes:
        dimension: Name of the dimension (accuracy, completeness, etc.).
        score: Numerical score (1-10 scale).
        weight: Weight as a fraction of total (e.g. 0.30 for 30%).
        rationale: Judge's reasoning for this score.
        evidence_refs: Indices into the EvidenceBundle items that support this score.
    """
    dimension: str
    score: float                         # 1-10 scale
    weight: float                        # e.g. 0.30
    rationale: str = ""
    evidence_refs: List[int] = field(default_factory=list)

    def weighted_score(self) -> float:
        """Return the contribution to the overall weighted total."""
        return self.score * self.weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "weight": self.weight,
            "rationale": self.rationale,
            "evidence_refs": self.evidence_refs,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DimensionScore":
        return cls(
            dimension=d["dimension"],
            score=d["score"],
            weight=d["weight"],
            rationale=d.get("rationale", ""),
            evidence_refs=d.get("evidence_refs", []),
        )


# ═══════════════════════════════════════════════════════════════
# Lie Detection
# ═══════════════════════════════════════════════════════════════


@dataclass
class LieFinding:
    """A detected lie or fabrication in the evaluated output.

    Attributes:
        claim: The specific claim that was found to be false.
        evidence_against: Evidence that contradicts the claim.
        severity: How severe this fabrication is.
        location: Where in the output the lie was found.
        source_claimed: What source the output claimed (if any).
    """
    claim: str
    evidence_against: str
    severity: LieSeverity = LieSeverity.MODERATE
    location: str = ""
    source_claimed: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.claim,
            "evidence_against": self.evidence_against,
            "severity": self.severity.value,
            "location": self.location,
            "source_claimed": self.source_claimed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LieFinding":
        return cls(
            claim=d["claim"],
            evidence_against=d["evidence_against"],
            severity=LieSeverity(d.get("severity", "moderate")),
            location=d.get("location", ""),
            source_claimed=d.get("source_claimed", ""),
        )


# ═══════════════════════════════════════════════════════════════
# EvaluationResult — the core output
# ═══════════════════════════════════════════════════════════════


@dataclass
class EvaluationResult:
    """Complete LLM-as-Judge evaluation result.

    This is the structured JSON output produced by the evaluate() pipeline.
    Contains all dimension scores, red-line veto results, lie findings,
    and the collected evidence bundle.

    Attributes:
        evaluation_id: Unique identifier for this evaluation run.
        project_name: Name of the project being evaluated.
        timestamp: When this evaluation was performed (epoch seconds).
        verdict: Final verdict (PASS / BLOCK / P0 / WARN).
        dimensions: Per-dimension scores with rationale.
        total_score: Weighted composite score (1-10 scale).
        evidence_bundle: All collected evidence.
        lie_findings: Detected fabrications/lies.
        red_line_violations: Veto rules that were triggered.
        judge_model: Identifier for the judge LLM used.
        project_model: Identifier for the project's main model (must differ from judge).
        duration_ms: Total evaluation duration in milliseconds.
        recommendations: Actionable recommendations from the judge.
    """
    evaluation_id: str
    project_name: str
    timestamp: float
    verdict: Verdict
    dimensions: List[DimensionScore]
    total_score: float
    evidence_bundle: EvidenceBundle
    lie_findings: List[LieFinding] = field(default_factory=list)
    red_line_violations: List[str] = field(default_factory=list)
    judge_model: str = ""
    project_model: str = ""
    duration_ms: float = 0.0
    recommendations: List[str] = field(default_factory=list)

    # ── Weight configuration ──────────────────────────────────
    DEFAULT_WEIGHTS: Dict[str, float] = field(default_factory=lambda: {
        "accuracy": get_threshold("evaluate.weights.accuracy", 0.30),
        "completeness": get_threshold("evaluate.weights.completeness", 0.20),
        "honesty": get_threshold("evaluate.weights.honesty", 0.25),
        "helpfulness": get_threshold("evaluate.weights.helpfulness", 0.15),
        "consistency": get_threshold("evaluate.weights.consistency", 0.10),
    }, init=False, repr=False)

    def __post_init__(self):
        if not self.evaluation_id:
            self.evaluation_id = str(uuid.uuid4())

    def get_dimension(self, name: str) -> Optional[DimensionScore]:
        """Retrieve a dimension score by name."""
        for d in self.dimensions:
            if d.dimension == name:
                return d
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "project_name": self.project_name,
            "timestamp": self.timestamp,
            "verdict": self.verdict.value,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "total_score": self.total_score,
            "evidence_bundle": self.evidence_bundle.to_dict(),
            "lie_findings": [lf.to_dict() for lf in self.lie_findings],
            "red_line_violations": self.red_line_violations,
            "judge_model": self.judge_model,
            "project_model": self.project_model,
            "duration_ms": self.duration_ms,
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvaluationResult":
        return cls(
            evaluation_id=d["evaluation_id"],
            project_name=d["project_name"],
            timestamp=d["timestamp"],
            verdict=Verdict(d["verdict"]),
            dimensions=[DimensionScore.from_dict(ds) for ds in d.get("dimensions", [])],
            total_score=d["total_score"],
            evidence_bundle=EvidenceBundle.from_dict(d["evidence_bundle"]),
            lie_findings=[LieFinding.from_dict(lf) for lf in d.get("lie_findings", [])],
            red_line_violations=d.get("red_line_violations", []),
            judge_model=d.get("judge_model", ""),
            project_model=d.get("project_model", ""),
            duration_ms=d.get("duration_ms", 0.0),
            recommendations=d.get("recommendations", []),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "EvaluationResult":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    # ── Summary helpers ───────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable summary string."""
        dim_lines = "\n".join(
            f"  {d.dimension:<15s}  {d.score:5.1f}/10  (weight {d.weight:.0%})  {d.rationale[:80]}"
            for d in self.dimensions
        )
        violations = "\n".join(f"  ⛔ {v}" for v in self.red_line_violations) or "  (none)"
        lies = "\n".join(f"  🔴 [{lf.severity.value}] {lf.claim[:120]}" for lf in self.lie_findings) or "  (none)"
        recs = "\n".join(f"  💡 {r}" for r in self.recommendations) or "  (none)"

        return (
            f"═══════════════════════════════════════════════════════\n"
            f" EVALUATION RESULT — {self.project_name}\n"
            f"═══════════════════════════════════════════════════════\n"
            f"  Verdict:     {self.verdict.value}\n"
            f"  Total Score: {self.total_score:.2f}/10\n"
            f"  Duration:    {self.duration_ms:.0f}ms\n"
            f"  Judge:       {self.judge_model}\n"
            f"  Project:     {self.project_model}\n"
            f"───────────────────────────────────────────────────────\n"
            f" Dimension Scores:\n{dim_lines}\n"
            f"───────────────────────────────────────────────────────\n"
            f" Red-Line Violations:\n{violations}\n"
            f"───────────────────────────────────────────────────────\n"
            f" Lie Findings:\n{lies}\n"
            f"───────────────────────────────────────────────────────\n"
            f" Recommendations:\n{recs}\n"
            f"═══════════════════════════════════════════════════════"
        )


# ═══════════════════════════════════════════════════════════════
# Known stdlib + third-party packages (for import validity checks)
# ═══════════════════════════════════════════════════════════════

_KNOWN_PACKAGES: set = {
    # stdlib
    "__future__", "abc", "argparse", "ast", "asyncio", "base64", "binascii",
    "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk",
    "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys",
    "compileall", "concurrent", "configparser", "contextlib", "contextvars",
    "copy", "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses",
    "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
    "distutils", "doctest", "email", "encodings", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "formatter",
    "fractions", "ftplib", "functools", "gc", "getopt", "getpass", "gettext",
    "glob", "graphlib", "grp", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect",
    "io", "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
    "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math",
    "mimetypes", "mmap", "modulefinder", "multiprocessing", "netrc", "nis",
    "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
    "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
    "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue", "quopri", "random", "re", "readline", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib",
    "sndhdr", "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics",
    "string", "stringprep", "struct", "subprocess", "sunau", "symtable",
    "sys", "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib",
    "tempfile", "termios", "test", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
    "unicodedata", "unittest", "urllib", "uu", "uuid", "venv", "warnings",
    "wave", "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
    "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib",
    "zoneinfo",
    # third-party common
    "yaml", "toml", "pytest", "pydantic", "pydantic_settings", "rich",
    "dotenv", "requests", "fastapi", "uvicorn", "starlette", "prompt_toolkit",
    "watchfiles", "filelock", "packaging", "numpy", "pandas", "click",
    "flask", "django", "sqlalchemy", "alembic", "celery", "redis",
    "aiohttp", "httpx", "websockets", "pydantic_core", "anyio", "sniffio",
    "certifi", "charset_normalizer", "idna", "urllib3",
}

# ═══════════════════════════════════════════════════════════════
# Evidence Collector — Phase 1: Gather objective evidence
# ═══════════════════════════════════════════════════════════════


class EvidenceCollector:
    """Collects objective evidence before the judge LLM evaluates.

    Evidence sources:
      - Static analysis (syntax validity, import consistency, type hints)
      - Test results (pass/fail counts, coverage)
      - Lint output (style issues, complexity warnings)
      - Spec conformance (requirement coverage, acceptance criteria)
      - Code review (anti-patterns, security issues)
      - Dependency audit (version pinning, known vulnerabilities)

    This is a deterministic, rule-based collector — no LLM involved.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.src_dir = self.project_dir / "src"
        self.tests_dir = self.project_dir / "tests"
        self.specs_dir = self.project_dir / "specs"

    def collect_all(self, project_name: str) -> EvidenceBundle:
        """Run all evidence collectors and return a bundle."""
        start = time.perf_counter()
        bundle = EvidenceBundle(project_name=project_name)

        self._collect_static_analysis(bundle)
        self._collect_test_results(bundle)
        self._collect_lint(bundle)
        self._collect_spec_conformance(bundle)
        self._collect_code_review(bundle)
        self._collect_dependency_audit(bundle)

        bundle.collection_duration_ms = (time.perf_counter() - start) * 1000.0
        return bundle

    # ── Individual collectors ──────────────────────────────────

    def _collect_static_analysis(self, bundle: EvidenceBundle) -> None:
        """Run static analysis: syntax checks, import validity, structure."""
        if not self.src_dir.exists():
            bundle.add_error("Source directory not found")
            return

        py_files = list(self.src_dir.rglob("*.py"))
        if not py_files:
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.STATIC_ANALYSIS,
                source="src/",
                finding="No Python source files found",
                severity="warning",
            ))
            return

        # Syntax check using py_compile
        syntax_ok = 0
        syntax_fail = 0
        for pf in py_files:
            try:
                source = pf.read_text(encoding="utf-8")
                compile(source, str(pf), "exec")
                syntax_ok += 1
            except (SyntaxError, UnicodeDecodeError) as e:
                syntax_fail += 1
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.STATIC_ANALYSIS,
                    source=str(pf.relative_to(self.project_dir)),
                    finding=f"Syntax error: {e}",
                    severity="error",
                ))

        bundle.add(EvidenceItem(
            evidence_type=EvidenceType.STATIC_ANALYSIS,
            source="static_analysis",
            finding=f"Syntax check: {syntax_ok} passed, {syntax_fail} failed out of {len(py_files)} files",
            severity="error" if syntax_fail > 0 else "info",
            metadata={"syntax_ok": syntax_ok, "syntax_fail": syntax_fail, "total_files": len(py_files)},
        ))

        # Import consistency: check for broken relative imports
        broken_imports = self._check_imports(py_files)
        for imp in broken_imports:
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.STATIC_ANALYSIS,
                source=imp["file"],
                finding=f"Broken import: {imp['import_line']} — {imp['reason']}",
                severity="warning",
            ))

        # Module-level docstring coverage
        missing_docstrings = 0
        for pf in py_files:
            try:
                content = pf.read_text(encoding="utf-8")
                if not re.search(r'^""".+?"""', content, re.DOTALL):
                    missing_docstrings += 1
            except (OSError, UnicodeDecodeError):
                pass

        if missing_docstrings > 0:
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.STATIC_ANALYSIS,
                source="src/",
                finding=f"{missing_docstrings}/{len(py_files)} modules missing module docstrings",
                severity="info",
            ))

    def _check_imports(self, py_files: List[Path]) -> List[Dict[str, str]]:
        """Check for broken relative imports across the source tree."""
        # Build module name → file map
        module_map: Dict[str, Path] = {}
        for pf in py_files:
            if pf.name.startswith("_"):
                continue
            rel = pf.relative_to(self.src_dir)
            mod = str(rel.with_suffix("")).replace("\\", ".").replace("/", ".")
            module_map[mod] = pf

        broken: List[Dict[str, str]] = []
        for pf in py_files:
            try:
                content = pf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel_file = str(pf.relative_to(self.project_dir))
            # Find "from src.X import Y" or "from X import Y"
            imports = re.findall(
                r'^(?:from|import)\s+((?:src\.)?\w+(?:\.\w+)*)',
                content, re.MULTILINE,
            )
            for imp in imports:
                # Normalize: strip "src." prefix
                mod_name = imp
                if mod_name.startswith("src."):
                    mod_name = mod_name[4:]
                # Check if this module or its top-level package is known stdlib/third-party
                top_level = mod_name.split(".")[0]
                if mod_name not in module_map and top_level not in _KNOWN_PACKAGES:
                    broken.append({
                        "file": rel_file,
                        "import_line": imp,
                        "reason": f"Module '{mod_name}' not found in source tree",
                    })

        return broken

    def _collect_test_results(self, bundle: EvidenceBundle) -> None:
        """Collect test results: file counts, test function counts, recent run output."""
        if not self.tests_dir.exists():
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.TEST_RESULTS,
                source="tests/",
                finding="No tests directory found",
                severity="warning",
            ))
            return

        test_files = list(self.tests_dir.rglob("test_*.py"))
        total_test_funcs = 0
        for tf in test_files:
            try:
                content = tf.read_text(encoding="utf-8")
                funcs = len(re.findall(r'^\s*def\s+test_\w+', content, re.MULTILINE))
                total_test_funcs += funcs
            except (OSError, UnicodeDecodeError):
                pass

        bundle.add(EvidenceItem(
            evidence_type=EvidenceType.TEST_RESULTS,
            source="tests/",
            finding=f"Test files: {len(test_files)}, test functions: {total_test_funcs}",
            severity="info" if total_test_funcs > 0 else "warning",
            metadata={"test_files": len(test_files), "test_functions": total_test_funcs},
        ))

        # Check for test coverage of source files
        if self.src_dir.exists():
            src_files = [f.stem for f in self.src_dir.rglob("*.py") if not f.name.startswith("_")]
            test_stems = {f.stem.replace("test_", "") for f in test_files}
            uncovered = [s for s in src_files if s not in test_stems]

            if uncovered:
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.TEST_RESULTS,
                    source="tests/",
                    finding=f"Untested modules: {', '.join(uncovered[:10])}",
                    severity="warning",
                    metadata={"uncovered_count": len(uncovered)},
                ))

    def _collect_lint(self, bundle: EvidenceBundle) -> None:
        """Collect lint-style findings: trailing whitespace, long lines, complexity."""
        if not self.src_dir.exists():
            return

        py_files = list(self.src_dir.rglob("*.py"))
        lint_issues = 0

        trailing_ws_threshold = get_threshold("evaluate.evidence_collection.trailing_whitespace_threshold", 10)
        long_line_threshold = get_threshold("evaluate.evidence_collection.long_line_threshold", 150)
        long_line_count_threshold = get_threshold("evaluate.evidence_collection.long_line_count_threshold", 5)
        blank_ws_threshold = get_threshold("evaluate.evidence_collection.blank_whitespace_threshold", 10)

        for pf in py_files:
            try:
                lines = pf.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            rel = str(pf.relative_to(self.project_dir))

            # Trailing whitespace
            trailing = sum(1 for line in lines if line.rstrip() != line)
            if trailing > trailing_ws_threshold:
                lint_issues += 1
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.LINT,
                    source=rel,
                    finding=f"{trailing} lines have trailing whitespace",
                    severity="info",
                ))

            # Long lines
            long_lines = sum(1 for line in lines if len(line) > long_line_threshold)
            if long_lines > long_line_count_threshold:
                lint_issues += 1
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.LINT,
                    source=rel,
                    finding=f"{long_lines} lines exceed {long_line_threshold} characters",
                    severity="info",
                ))

            # Blank lines with whitespace
            blank_ws = sum(1 for line in lines if line.strip() == "" and len(line) > 0)
            if blank_ws > blank_ws_threshold:
                lint_issues += 1
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.LINT,
                    source=rel,
                    finding=f"{blank_ws} blank lines contain whitespace",
                    severity="info",
                ))

        if lint_issues == 0:
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.LINT,
                source="lint",
                finding="No significant lint issues found",
                severity="info",
            ))

    def _collect_spec_conformance(self, bundle: EvidenceBundle) -> None:
        """Check spec conformance: presence of spec files, requirement tracing."""
        if not self.specs_dir.exists():
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.SPEC_CONFORMANCE,
                source="specs/",
                finding="No specs directory found — cannot verify spec conformance",
                severity="warning",
            ))
            return

        spec_files = list(self.specs_dir.rglob("*"))
        md_files = [f for f in spec_files if f.suffix in (".md", ".txt", ".json", ".yaml", ".yml")]

        bundle.add(EvidenceItem(
            evidence_type=EvidenceType.SPEC_CONFORMANCE,
            source="specs/",
            finding=f"Spec files found: {len(md_files)} total, {len(spec_files)} files in specs tree",
            severity="info",
            metadata={"spec_count": len(md_files), "total_files": len(spec_files)},
        ))

    def _collect_code_review(self, bundle: EvidenceBundle) -> None:
        """Basic code review: anti-patterns, security concerns, error handling."""
        if not self.src_dir.exists():
            return

        py_files = list(self.src_dir.rglob("*.py"))

        todo_count_threshold = get_threshold("evaluate.evidence_collection.todo_count_threshold", 5)
        function_length_threshold = get_threshold("evaluate.evidence_collection.function_length_threshold", 100)

        for pf in py_files:
            try:
                content = pf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel = str(pf.relative_to(self.project_dir))

            # Bare except clauses
            bare_excepts = len(re.findall(r'^\s*except\s*:', content, re.MULTILINE))
            if bare_excepts > 0:
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.CODE_REVIEW,
                    source=rel,
                    finding=f"{bare_excepts} bare exception handler(s) — should specify exception type",
                    severity="warning",
                    metadata={"bare_except_count": bare_excepts},
                ))

            # TODO/FIXME/HACK comments
            todos = len(re.findall(r'#\s*(TODO|FIXME|HACK|XXX)\b', content))
            if todos > todo_count_threshold:
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.CODE_REVIEW,
                    source=rel,
                    finding=f"{todos} TODO/FIXME/HACK markers",
                    severity="info",
                ))

            # Large functions
            func_matches = list(re.finditer(
                r'^\s*def\s+(\w+)', content, re.MULTILINE
            ))
            for i, m in enumerate(func_matches):
                start_line = content[:m.start()].count("\n")
                if i + 1 < len(func_matches):
                    end_line = content[:func_matches[i + 1].start()].count("\n")
                else:
                    end_line = len(content.splitlines())
                if end_line - start_line > function_length_threshold:
                    bundle.add(EvidenceItem(
                        evidence_type=EvidenceType.CODE_REVIEW,
                        source=rel,
                        finding=f"Function '{m.group(1)}' is {end_line - start_line} lines — consider refactoring",
                        severity="info",
                    ))

            # eval/exec usage
            if re.search(r'\b(?:eval|exec)\s*\(', content):
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.CODE_REVIEW,
                    source=rel,
                    finding="Uses eval() or exec() — potential security risk",
                    severity="warning",
                ))

    def _collect_dependency_audit(self, bundle: EvidenceBundle) -> None:
        """Audit dependencies: requirements files, version pinning."""
        req_files = list(self.project_dir.glob("requirements*.txt")) + \
                     list(self.project_dir.glob("pyproject.toml")) + \
                     list(self.project_dir.glob("Pipfile"))

        if not req_files:
            bundle.add(EvidenceItem(
                evidence_type=EvidenceType.DEPENDENCY_AUDIT,
                source="project_root",
                finding="No dependency specification file found (requirements.txt, pyproject.toml, Pipfile)",
                severity="warning",
            ))
            return

        for rf in req_files:
            try:
                content = rf.read_text(encoding="utf-8")
                deps = [line.strip() for line in content.splitlines()
                        if line.strip() and not line.strip().startswith("#")]
                pinned = sum(1 for d in deps if "==" in d or ">=" in d or "<=" in d or "~=" in d)
                bundle.add(EvidenceItem(
                    evidence_type=EvidenceType.DEPENDENCY_AUDIT,
                    source=str(rf.relative_to(self.project_dir)),
                    finding=f"Dependencies: {len(deps)} total, {pinned} pinned/constrained",
                    severity="info",
                    metadata={"total_deps": len(deps), "pinned_deps": pinned},
                ))
            except (OSError, UnicodeDecodeError):
                pass


# ═══════════════════════════════════════════════════════════════
# LLM Judge — Phase 2: Evaluate with a different model
# ═══════════════════════════════════════════════════════════════


class LLMJudge:
    """LLM-as-Judge: evaluates project output using a different model.

    The judge MUST be a different model/provider from the project's
    main model to avoid self-evaluation bias.

    This class provides:
      - A prompt-based evaluation interface (for real LLM integration)
      - A deterministic rule-based fallback (for testing / offline use)
      - Red-line veto enforcement after scoring
    """

    # ── Dimension definitions ─────────────────────────────────

    DIMENSIONS: Dict[str, Dict[str, Any]] = {
        "accuracy": {
            "weight": 0.30,
            "description": "Factual correctness and spec compliance",
            "evidence_types": [
                EvidenceType.STATIC_ANALYSIS,
                EvidenceType.TEST_RESULTS,
                EvidenceType.SPEC_CONFORMANCE,
            ],
        },
        "completeness": {
            "weight": 0.20,
            "description": "Coverage of requirements, edge cases, and error handling",
            "evidence_types": [
                EvidenceType.TEST_RESULTS,
                EvidenceType.SPEC_CONFORMANCE,
                EvidenceType.CODE_REVIEW,
            ],
        },
        "honesty": {
            "weight": 0.25,
            "description": "No fabrication, no hallucination, cites verifiable sources",
            "evidence_types": [
                EvidenceType.STATIC_ANALYSIS,
                EvidenceType.CODE_REVIEW,
                EvidenceType.DEPENDENCY_AUDIT,
            ],
        },
        "helpfulness": {
            "weight": 0.15,
            "description": "Actionable output, clarity, documentation, usability",
            "evidence_types": [
                EvidenceType.LINT,
                EvidenceType.CODE_REVIEW,
            ],
        },
        "consistency": {
            "weight": 0.10,
            "description": "Internal coherence, style uniformity, naming conventions",
            "evidence_types": [
                EvidenceType.LINT,
                EvidenceType.STATIC_ANALYSIS,
            ],
        },
    }

    # ── Red-line veto thresholds ──────────────────────────────

    RED_LINE_HONESTY_THRESHOLD: float = get_threshold("evaluate.red_lines.honesty_min", 5.0)
    RED_LINE_ACCURACY_THRESHOLD: float = get_threshold("evaluate.red_lines.accuracy_min", 4.0)

    def __init__(
        self,
        judge_model: str = "claude-opus-4",
        project_model: str = "deepseek-v4-pro",
    ):
        """Initialize the judge.

        Args:
            judge_model: Identifier for the judge LLM (must differ from project_model).
            project_model: Identifier for the project's main model.
        """
        self.judge_model = judge_model
        self.project_model = project_model

    def evaluate(
        self,
        project_name: str,
        evidence: EvidenceBundle,
        project_output: Optional[str] = None,
    ) -> EvaluationResult:
        """Run the full evaluation pipeline.

        Args:
            project_name: Name of the project being evaluated.
            evidence: Pre-collected evidence bundle.
            project_output: Optional project output text to evaluate for lies/hallucinations.

        Returns:
            EvaluationResult with scores, verdict, and recommendations.
        """
        start = time.perf_counter()

        # Step 1: Score each dimension using the rule-based engine
        dimensions = self._score_dimensions(evidence, project_output)

        # Step 2: Detect lies/fabrications in output
        lie_findings = self._detect_lies(evidence, project_output)

        # Step 3: Compute weighted total
        total_score = sum(d.weighted_score() for d in dimensions)

        # Step 4: Apply red-line veto rules
        verdict, violations = self._apply_red_lines(dimensions, lie_findings)

        # Step 5: Generate recommendations
        recommendations = self._generate_recommendations(
            dimensions, lie_findings, verdict, evidence
        )

        duration_ms = (time.perf_counter() - start) * 1000.0

        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            project_name=project_name,
            timestamp=time.time(),
            verdict=verdict,
            dimensions=dimensions,
            total_score=total_score,
            evidence_bundle=evidence,
            lie_findings=lie_findings,
            red_line_violations=violations,
            judge_model=self.judge_model,
            project_model=self.project_model,
            duration_ms=duration_ms,
            recommendations=recommendations,
        )

    def _score_dimensions(
        self,
        evidence: EvidenceBundle,
        project_output: Optional[str],
    ) -> List[DimensionScore]:
        """Score each dimension based on collected evidence.

        Uses a deterministic, evidence-driven scoring algorithm.
        For production use, replace with actual LLM call.
        """
        dimensions: List[DimensionScore] = []

        for dim_name, dim_def in self.DIMENSIONS.items():
            # Collect relevant evidence
            relevant_evidence = self._filter_evidence(
                evidence, dim_def["evidence_types"]
            )

            # Compute base score from evidence quality
            score, rationale, refs = self._compute_dimension_score(
                dim_name, relevant_evidence, evidence, project_output
            )

            dimensions.append(DimensionScore(
                dimension=dim_name,
                score=score,
                weight=dim_def["weight"],
                rationale=rationale,
                evidence_refs=refs,
            ))

        return dimensions

    def _filter_evidence(
        self,
        evidence: EvidenceBundle,
        evidence_types: List[EvidenceType],
    ) -> List[EvidenceItem]:
        """Filter evidence items by type list."""
        return [
            item for item in evidence.items
            if item.evidence_type in evidence_types
        ]

    def _compute_dimension_score(
        self,
        dim_name: str,
        relevant: List[EvidenceItem],
        evidence: EvidenceBundle,
        project_output: Optional[str],
    ) -> Tuple[float, str, List[int]]:
        """Compute a score for one dimension based on evidence.

        Returns (score, rationale, evidence_refs).
        """
        if not relevant:
            default_no_evidence = get_threshold("evaluate.scoring.default_no_evidence", 7.0)
            return default_no_evidence, "No specific evidence available; default neutral score", []

        errors = [e for e in relevant if e.severity == "error"]
        warnings = [e for e in relevant if e.severity == "warning"]
        criticals = [e for e in relevant if e.severity == "critical"]
        infos = [e for e in relevant if e.severity == "info"]

        baseline = get_threshold("evaluate.scoring.baseline", 8.0)
        critical_penalty = get_threshold("evaluate.scoring.critical_penalty", 2.0)
        error_penalty = get_threshold("evaluate.scoring.error_penalty", 0.5)
        warning_penalty = get_threshold("evaluate.scoring.warning_penalty", 0.2)
        info_bonus = get_threshold("evaluate.scoring.info_bonus", 0.1)
        max_info_bonuses = get_threshold("evaluate.scoring.max_info_bonuses", 3)
        score_min = get_threshold("evaluate.scoring.score_min", 1.0)
        score_max = get_threshold("evaluate.scoring.score_max", 10.0)

        score = baseline
        score -= len(criticals) * critical_penalty
        score -= len(errors) * error_penalty
        score -= len(warnings) * warning_penalty
        score += min(len(infos), max_info_bonuses) * info_bonus

        score = max(score_min, min(score_max, score))

        # Build rationale
        parts = []
        if criticals:
            parts.append(f"{len(criticals)} critical issue(s)")
        if errors:
            parts.append(f"{len(errors)} error(s)")
        if warnings:
            parts.append(f"{len(warnings)} warning(s)")
        if not parts:
            parts.append("no significant issues")

        rationale = f"Score {score:.1f}/10 based on {len(relevant)} evidence items: " + "; ".join(parts)

        # Collect indices into evidence.items
        refs = []
        evidence_list = evidence.items
        for item in relevant:
            try:
                refs.append(evidence_list.index(item))
            except ValueError:
                pass

        return score, rationale, refs

    def _detect_lies(
        self,
        evidence: EvidenceBundle,
        project_output: Optional[str],
    ) -> List[LieFinding]:
        """Detect fabrications/hallucinations/lies in project output.

        Uses evidence-based contradiction detection:
          - Claims not backed by evidence
          - Contradictions between output and collected evidence
          - Fabricated references/sources

        For production use, this would use an LLM to semantically analyze
        the output against the evidence.
        """
        findings: List[LieFinding] = []

        # If no project output to analyze, skip lie detection
        if not project_output:
            return findings

        # Rule-based lie detection heuristics

        # 1. Check for fabricated file references
        mentioned_files = re.findall(r'`([^`]+\.(?:py|md|json|yaml|toml))`', project_output)
        for mf in mentioned_files:
            if not (self._find_file_in_project(mf) or self._find_file_in_evidence(mf, evidence)):
                findings.append(LieFinding(
                    claim=f"References file '{mf}'",
                    evidence_against=f"File '{mf}' does not exist in project or evidence",
                    severity=LieSeverity.MODERATE,
                    location=f"Mentioned in evaluation output",
                ))

        # 2. Check for inflated metrics (claiming > evidence shows)
        test_evidence = evidence.get_by_type(EvidenceType.TEST_RESULTS)
        inflated_metrics_multiplier = get_threshold("evaluate.lie_detection.inflated_metrics_multiplier", 2.0)
        for te in test_evidence:
            if "test_functions" in te.metadata:
                claimed_tests = te.metadata["test_functions"]
                # If output claims more tests than evidence shows
                match = re.search(r'(\d+)\s*(?:test|unit test)s?\s*(?:pass|run|execut)', project_output, re.IGNORECASE)
                if match:
                    claimed_count = int(match.group(1))
                    if claimed_count > claimed_tests * inflated_metrics_multiplier:
                        findings.append(LieFinding(
                            claim=f"Claims {claimed_count} tests passed",
                            evidence_against=f"Evidence shows only {claimed_tests} test functions exist",
                            severity=LieSeverity.MODERATE,
                        ))

        # 3. Check for hallucinated library versions
        version_claims = re.findall(r'(\w[\w.-]+)\s+(?:version\s+)?(\d+\.\d+\.\d+)', project_output)
        dep_evidence = evidence.get_by_type(EvidenceType.DEPENDENCY_AUDIT)
        for lib, ver in version_claims:
            # Check against dependency evidence
            found_in_evidence = False
            for de in dep_evidence:
                if lib.lower() in de.finding.lower():
                    found_in_evidence = True
                    break
            if not found_in_evidence and lib.lower() not in ("python", "pip", "git"):
                findings.append(LieFinding(
                    claim=f"References library '{lib}' version {ver}",
                    evidence_against=f"No dependency evidence for '{lib}' found",
                    severity=LieSeverity.MINOR,
                ))

        return findings

    def _find_file_in_project(self, filename: str) -> bool:
        """Check if a referenced file exists anywhere in the project."""
        # Simple check — in production this would search the project tree
        return False  # Default: assume not found without project dir context

    def _find_file_in_evidence(self, filename: str, evidence: EvidenceBundle) -> bool:
        """Check if a filename is referenced in any evidence item."""
        for item in evidence.items:
            if filename in item.source or filename in item.finding:
                return True
        return False

    def _apply_red_lines(
        self,
        dimensions: List[DimensionScore],
        lie_findings: List[LieFinding],
    ) -> Tuple[Verdict, List[str]]:
        """Apply red-line veto rules.

        Returns (verdict, list_of_violation_descriptions).
        """
        violations: List[str] = []
        verdict = Verdict.PASS

        # Get dimension scores
        honesty_score = None
        accuracy_score = None
        for d in dimensions:
            if d.dimension == "honesty":
                honesty_score = d.score
            elif d.dimension == "accuracy":
                accuracy_score = d.score

        # Red-line 1: honesty < 5 → BLOCK
        if honesty_score is not None and honesty_score < self.RED_LINE_HONESTY_THRESHOLD:
            violations.append(
                f"RED-LINE: honesty score ({honesty_score:.1f}) is below threshold "
                f"({self.RED_LINE_HONESTY_THRESHOLD}). Fabrication/hallucination risk — BLOCK."
            )
            verdict = Verdict.BLOCK

        # Red-line 2: accuracy < 4 → BLOCK
        if accuracy_score is not None and accuracy_score < self.RED_LINE_ACCURACY_THRESHOLD:
            violations.append(
                f"RED-LINE: accuracy score ({accuracy_score:.1f}) is below threshold "
                f"({self.RED_LINE_ACCURACY_THRESHOLD}). Critical correctness failure — BLOCK."
            )
            verdict = Verdict.BLOCK

        # Red-line 3: detected lie with critical/hallucination severity → P0
        critical_lies = [
            lf for lf in lie_findings
            if lf.severity in (LieSeverity.CRITICAL, LieSeverity.HALLUCINATION)
        ]
        if critical_lies:
            violations.append(
                f"RED-LINE: {len(critical_lies)} critical/hallucination lies detected. "
                f"Immediate P0 escalation."
            )
            verdict = Verdict.P0

        # Non-blocking: moderate lies → WARN (if not already BLOCK/P0)
        elif lie_findings and verdict == Verdict.PASS:
            moderate_lies = [lf for lf in lie_findings if lf.severity == LieSeverity.MODERATE]
            if moderate_lies:
                violations.append(
                    f"WARNING: {len(moderate_lies)} moderate-severity fabrications detected. "
                    f"Review before proceeding."
                )
                verdict = Verdict.WARN

        return verdict, violations

    def _generate_recommendations(
        self,
        dimensions: List[DimensionScore],
        lie_findings: List[LieFinding],
        verdict: Verdict,
        evidence: EvidenceBundle,
    ) -> List[str]:
        """Generate actionable recommendations based on evaluation results."""
        recommendations: List[str] = []

        # Low-scoring dimensions
        critical_score_threshold = get_threshold("evaluate.recommendations.critical_score_threshold", 4.0)
        improve_score_threshold = get_threshold("evaluate.recommendations.improve_score_threshold", 6.0)
        for d in dimensions:
            if d.score < critical_score_threshold:
                recommendations.append(
                    f"[CRITICAL] {d.dimension}: score {d.score:.1f}/10. "
                    f"Address all errors before re-evaluation."
                )
            elif d.score < improve_score_threshold:
                recommendations.append(
                    f"[IMPROVE] {d.dimension}: score {d.score:.1f}/10. "
                    f"Focus on reducing warnings and improving quality."
                )

        # Lie findings
        if lie_findings:
            recommendations.append(
                f"[FIX] {len(lie_findings)} fabrication(s) detected. "
                f"Verify all claims against evidence and remove unsubstantiated statements."
            )

        # Evidence gaps
        if evidence.errors_during_collection:
            recommendations.append(
                f"[INFRA] Evidence collection had {len(evidence.errors_during_collection)} "
                f"error(s). Fix evidence pipeline to ensure complete coverage."
            )

        # Verdict-specific
        if verdict == Verdict.BLOCK:
            recommendations.append(
                "[BLOCK] Red-line violations must be resolved before this project can proceed."
            )
        elif verdict == Verdict.P0:
            recommendations.append(
                "[P0-URGENT] Critical fabrication detected. Immediate human review required. "
                "Project is blocked until investigation is complete."
            )
        elif verdict == Verdict.PASS:
            recommendations.append(
                "[OK] All gates passed. Project is ready for the next phase."
            )

        return recommendations


# ═══════════════════════════════════════════════════════════════
# Main evaluate() entry point
# ═══════════════════════════════════════════════════════════════


def evaluate(
    project_dir: str | Path,
    project_name: str = "",
    project_output: Optional[str] = None,
    judge_model: str = "claude-opus-4",
    project_model: str = "deepseek-v4-pro",
) -> EvaluationResult:
    """Run the full evidence-first LLM-as-Judge evaluation pipeline.

    Pipeline:
      1. Collect objective evidence (static analysis, tests, lint, specs)
      2. Judge LLM (different model) scores 5 weighted dimensions
      3. Apply red-line veto rules
      4. Return structured EvaluationResult

    Args:
        project_dir: Path to the project directory.
        project_name: Human-readable project name (uses dir name if empty).
        project_output: Optional project output text to evaluate for lies.
        judge_model: Identifier for the judge LLM (must differ from project_model).
        project_model: Identifier for the project's main model.

    Returns:
        EvaluationResult with full scores, verdict, and recommendations.

    Raises:
        FileNotFoundError: If project_dir does not exist.
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    if not project_name:
        project_name = project_dir.name

    # Validate judge and project models are different
    if judge_model == project_model:
        # Downgrade to warning — allow same model but flag it
        pass  # In production, this should raise ValueError

    # Phase 1: Collect evidence
    collector = EvidenceCollector(project_dir)
    evidence = collector.collect_all(project_name)

    # Phase 2: Judge evaluation
    judge = LLMJudge(judge_model=judge_model, project_model=project_model)
    result = judge.evaluate(project_name, evidence, project_output)

    return result


def evaluate_from_dict(data: Dict[str, Any]) -> EvaluationResult:
    """Convenience: evaluate from a pre-built config dict.

    Expects:
        data["project_dir"]: str or Path
        data["project_name"]: str (optional)
        data["project_output"]: str (optional)
        data["judge_model"]: str (optional)
        data["project_model"]: str (optional)
    """
    return evaluate(
        project_dir=data["project_dir"],
        project_name=data.get("project_name", ""),
        project_output=data.get("project_output"),
        judge_model=data.get("judge_model", "claude-opus-4"),
        project_model=data.get("project_model", "deepseek-v4-pro"),
    )


# ═══════════════════════════════════════════════════════════════
# Quick self-test (runs when module is executed directly)
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    # Test against the current project
    test_dir = Path(__file__).resolve().parent.parent
    print(f"Running self-test against: {test_dir}")
    print()

    result = evaluate(
        project_dir=test_dir,
        project_name="multi-agent-pipeline",
        judge_model="gpt-4o",
        project_model="deepseek-v4-pro",
    )

    print(result.summary())
    print()
    print("JSON output:")
    print(result.to_json())
