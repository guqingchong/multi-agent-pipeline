"""src/knowledge_graph.py — 4-level knowledge graph data structures for v3.0 pipeline.

The knowledge graph drives the RESEARCH → DESIGN → DEVELOP pipeline by capturing
domain knowledge at four levels of increasing specificity:

  L1 — DomainConcept:      What domain concepts exist and how they relate
  L2 — ConstraintRule:     What rules/constraints must be satisfied (auto-checkable)
  L3 — ComponentMapping:   How concepts map to code modules/files
  L4 — CodeGenRule:        Templates and constraints for code generation

Each node is annotated with source URL + confidence for provenance tracking.

Usage:
    kg = KnowledgeGraph()
    kg.add_concept(DomainConcept(name="Order", ...))
    kg.add_rule(ConstraintRule(name="OrderTotalPositive", ...))
    kg.add_mapping(ComponentMapping(concept_name="Order", ...))
    kg.add_codegen_rule(CodeGenRule(mapping_id="...", ...))

    # Validate L2 rules against a codebase
    results = kg.validate_l2_rules(Path("/path/to/codebase"))

    # Serialization
    d = kg.to_dict()
    kg2 = KnowledgeGraph.from_dict(d)
    kg.to_yaml("/path/to/output.yml")
    kg3 = KnowledgeGraph.from_yaml("/path/to/output.yml")
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "DomainConcept",
    "ConstraintRule",
    "ComponentMapping",
    "CodeGenRule",
    "L1ValidationResult",
    "L2ValidationResult",
    "KnowledgeGraph",
    "Confidence",
]


# ───────────────────────────────────────────────────────────────
# Confidence enum
# ───────────────────────────────────────────────────────────────

class Confidence:
    """Named confidence levels with numeric values for comparison."""

    VERIFIED = 1.0       # Verified by multiple authoritative sources
    HIGH = 0.8           # Single authoritative source or multiple corroborating
    MEDIUM = 0.6         # Plausible, single non-authoritative source
    LOW = 0.4            # Speculative or single low-quality source
    UNKNOWN = 0.2        # No source, purely inferred

    _VALUES = {
        "VERIFIED": 1.0,
        "HIGH": 0.8,
        "MEDIUM": 0.6,
        "LOW": 0.4,
        "UNKNOWN": 0.2,
    }

    @classmethod
    def parse(cls, value: Union[str, float, int]) -> float:
        """Parse a confidence value from string or numeric.

        Accepts string names ("VERIFIED", "HIGH", etc.) or float values 0.0-1.0.
        """
        if isinstance(value, (float, int)):
            return max(0.0, min(1.0, float(value)))
        upper = value.upper().strip()
        if upper in cls._VALUES:
            return cls._VALUES[upper]
        try:
            return max(0.0, min(1.0, float(value)))
        except (ValueError, TypeError):
            return 0.5


# ───────────────────────────────────────────────────────────────
# L1: DomainConcept
# ───────────────────────────────────────────────────────────────

@dataclass
class DomainConcept:
    """L1 node: a domain concept with definition, source provenance, and relationships.

    Attributes:
        name: Unique concept name (e.g., "Order", "Inventory", "PaymentGateway").
        definition: Human-readable definition of the concept.
        source_url: URL or reference where this concept was discovered.
        confidence: Numeric 0.0-1.0 or Confidence level string indicating reliability.
        related_concepts: List of related concept names (L1 relationships).
        category: Optional category tag for grouping (e.g., "core_entity", "process").
        notes: Free-form notes or caveats about the concept.
    """

    name: str
    definition: str
    source_url: str = ""
    confidence: float = 0.5
    related_concepts: List[str] = field(default_factory=list)
    category: str = ""
    notes: str = ""

    def __post_init__(self):
        self.confidence = Confidence.parse(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": 1,
            "name": self.name,
            "definition": self.definition,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "related_concepts": list(self.related_concepts),
            "category": self.category,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DomainConcept:
        return cls(
            name=data.get("name", ""),
            definition=data.get("definition", ""),
            source_url=data.get("source_url", ""),
            confidence=data.get("confidence", 0.5),
            related_concepts=data.get("related_concepts", []),
            category=data.get("category", ""),
            notes=data.get("notes", ""),
        )


# ───────────────────────────────────────────────────────────────
# L2: ConstraintRule
# ───────────────────────────────────────────────────────────────

@dataclass
class ConstraintRule:
    """L2 node: a constraint or business rule that can be auto-validated.

    Attributes:
        name: Unique rule name (e.g., "OrderTotalPositive").
        rule: Human-readable description of the rule/constraint.
        severity: "error", "warning", or "info".
        auto_check: If True, this rule can be validated automatically.
        check_sql: Optional SQL query to validate the rule against a database.
            When present, execute this SQL and check that the result confirms the rule.
            Use a convention: query returns 0 rows → rule passes; returns rows → violations found.
        source_url: URL or reference where this rule comes from.
        confidence: Numeric 0.0-1.0 or Confidence level string.
        category: Optional category tag (e.g., "data_integrity", "business_logic").
        related_concepts: L1 concept names this rule constrains.
        expected_pattern: Optional regex pattern to grep for in source files
            (used by auto_check when check_sql is not applicable).
        file_glob: Glob pattern for files to scan with expected_pattern.
    """

    name: str
    rule: str
    severity: str = "error"
    auto_check: bool = False
    check_sql: str = ""
    source_url: str = ""
    confidence: float = 0.5
    category: str = ""
    related_concepts: List[str] = field(default_factory=list)
    expected_pattern: str = ""
    file_glob: str = "**/*.py"

    def __post_init__(self):
        self.confidence = Confidence.parse(self.confidence)
        if self.severity not in ("error", "warning", "info"):
            self.severity = "warning"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": 2,
            "name": self.name,
            "rule": self.rule,
            "severity": self.severity,
            "auto_check": self.auto_check,
            "check_sql": self.check_sql,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "category": self.category,
            "related_concepts": list(self.related_concepts),
            "expected_pattern": self.expected_pattern,
            "file_glob": self.file_glob,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConstraintRule:
        return cls(
            name=data.get("name", ""),
            rule=data.get("rule", ""),
            severity=data.get("severity", "error"),
            auto_check=data.get("auto_check", False),
            check_sql=data.get("check_sql", ""),
            source_url=data.get("source_url", ""),
            confidence=data.get("confidence", 0.5),
            category=data.get("category", ""),
            related_concepts=data.get("related_concepts", []),
            expected_pattern=data.get("expected_pattern", ""),
            file_glob=data.get("file_glob", "**/*.py"),
        )


# ───────────────────────────────────────────────────────────────
# L3: ComponentMapping
# ───────────────────────────────────────────────────────────────

@dataclass
class ComponentMapping:
    """L3 node: maps a domain concept (L1) to a code target (module/file/interface).

    Attributes:
        concept_name: Name of the L1 DomainConcept this maps from.
        target_module: Python module path (e.g., "src.services.order_service").
        target_file: Relative file path (e.g., "src/services/order_service.py").
        interface: Description of the public interface (class/method signatures).
        source_url: URL or reference for this mapping decision.
        confidence: Numeric 0.0-1.0 or Confidence level string.
        status: Implementation status: "planned", "in_progress", "implemented", "verified".
        notes: Additional design notes or rationale.
    """

    concept_name: str
    target_module: str = ""
    target_file: str = ""
    interface: str = ""
    source_url: str = ""
    confidence: float = 0.5
    status: str = "planned"
    notes: str = ""

    def __post_init__(self):
        self.confidence = Confidence.parse(self.confidence)
        valid_statuses = ("planned", "in_progress", "implemented", "verified")
        if self.status not in valid_statuses:
            self.status = "planned"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": 3,
            "concept_name": self.concept_name,
            "target_module": self.target_module,
            "target_file": self.target_file,
            "interface": self.interface,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "status": self.status,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ComponentMapping:
        return cls(
            concept_name=data.get("concept_name", ""),
            target_module=data.get("target_module", ""),
            target_file=data.get("target_file", ""),
            interface=data.get("interface", ""),
            source_url=data.get("source_url", ""),
            confidence=data.get("confidence", 0.5),
            status=data.get("status", "planned"),
            notes=data.get("notes", ""),
        )


# ───────────────────────────────────────────────────────────────
# L4: CodeGenRule
# ───────────────────────────────────────────────────────────────

@dataclass
class CodeGenRule:
    """L4 node: code generation template tied to a mapping with constraints.

    Attributes:
        mapping_id: References an L3 ComponentMapping (by concept_name or index).
        template: Jinja2-style template string or template file path for code generation.
        constraints: List of L2 ConstraintRule names that constrain this generated code.
        source_url: URL or reference for this template/constraint combination.
        confidence: Numeric 0.0-1.0 or Confidence level string.
        language: Target programming language (e.g., "python", "typescript").
        output_pattern: File path pattern for generated output.
    """

    mapping_id: str
    template: str = ""
    constraints: List[str] = field(default_factory=list)
    source_url: str = ""
    confidence: float = 0.5
    language: str = "python"
    output_pattern: str = ""

    def __post_init__(self):
        self.confidence = Confidence.parse(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": 4,
            "mapping_id": self.mapping_id,
            "template": self.template,
            "constraints": list(self.constraints),
            "source_url": self.source_url,
            "confidence": self.confidence,
            "language": self.language,
            "output_pattern": self.output_pattern,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CodeGenRule:
        return cls(
            mapping_id=data.get("mapping_id", ""),
            template=data.get("template", ""),
            constraints=data.get("constraints", []),
            source_url=data.get("source_url", ""),
            confidence=data.get("confidence", 0.5),
            language=data.get("language", "python"),
            output_pattern=data.get("output_pattern", ""),
        )


# ───────────────────────────────────────────────────────────────
# Validation result dataclasses
# ───────────────────────────────────────────────────────────────

@dataclass
class L1ValidationResult:
    """Result of validating an L1 DomainConcept against a codebase."""

    concept_name: str
    found_in_code: bool
    matched_files: List[str] = field(default_factory=list)
    missing_related: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class L2ValidationResult:
    """Result of validating a single L2 ConstraintRule."""

    rule_name: str
    passed: bool
    severity: str
    method: str  # "sql", "pattern", "manual"
    details: str = ""
    violations: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


# ───────────────────────────────────────────────────────────────
# KnowledgeGraph — main class
# ───────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """4-level knowledge graph for the v3.0 pipeline.

    Holds L1-L4 nodes and provides validation + serialization.

    Each node level is stored as a dict keyed by a unique identifier:
      - L1 concepts: keyed by name
      - L2 rules:    keyed by name
      - L3 mappings: keyed by concept_name (one mapping per concept)
      - L4 codegen:  keyed by mapping_id

    Serialization format:
      {
        "meta": {"version": "3.0", "pipeline": "multi-agent-pipeline"},
        "l1_concepts": [...],
        "l2_rules": [...],
        "l3_mappings": [...],
        "l4_codegen_rules": [...]
      }
    """

    def __init__(self):
        self._concepts: Dict[str, DomainConcept] = {}
        self._rules: Dict[str, ConstraintRule] = {}
        self._mappings: Dict[str, ComponentMapping] = {}
        self._codegen_rules: Dict[str, CodeGenRule] = {}

    # ── L1: DomainConcept ──────────────────────────────────────

    def add_concept(self, concept: DomainConcept) -> None:
        """Add or replace an L1 domain concept."""
        self._concepts[concept.name] = concept

    def get_concept(self, name: str) -> Optional[DomainConcept]:
        """Get an L1 concept by name."""
        return self._concepts.get(name)

    def remove_concept(self, name: str) -> bool:
        """Remove an L1 concept. Returns True if it existed."""
        if name in self._concepts:
            del self._concepts[name]
            return True
        return False

    @property
    def concepts(self) -> List[DomainConcept]:
        return list(self._concepts.values())

    # ── L2: ConstraintRule ─────────────────────────────────────

    def add_rule(self, rule: ConstraintRule) -> None:
        """Add or replace an L2 constraint rule."""
        self._rules[rule.name] = rule

    def get_rule(self, name: str) -> Optional[ConstraintRule]:
        """Get an L2 rule by name."""
        return self._rules.get(name)

    def remove_rule(self, name: str) -> bool:
        """Remove an L2 rule. Returns True if it existed."""
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    @property
    def rules(self) -> List[ConstraintRule]:
        return list(self._rules.values())

    # ── L3: ComponentMapping ───────────────────────────────────

    def add_mapping(self, mapping: ComponentMapping) -> None:
        """Add or replace an L3 component mapping."""
        self._mappings[mapping.concept_name] = mapping

    def get_mapping(self, concept_name: str) -> Optional[ComponentMapping]:
        """Get an L3 mapping by concept name."""
        return self._mappings.get(concept_name)

    def remove_mapping(self, concept_name: str) -> bool:
        """Remove an L3 mapping. Returns True if it existed."""
        if concept_name in self._mappings:
            del self._mappings[concept_name]
            return True
        return False

    @property
    def mappings(self) -> List[ComponentMapping]:
        return list(self._mappings.values())

    # ── L4: CodeGenRule ────────────────────────────────────────

    def add_codegen_rule(self, cg_rule: CodeGenRule) -> None:
        """Add or replace an L4 code generation rule."""
        self._codegen_rules[cg_rule.mapping_id] = cg_rule

    def get_codegen_rule(self, mapping_id: str) -> Optional[CodeGenRule]:
        """Get an L4 codegen rule by mapping ID."""
        return self._codegen_rules.get(mapping_id)

    def remove_codegen_rule(self, mapping_id: str) -> bool:
        """Remove an L4 codegen rule. Returns True if it existed."""
        if mapping_id in self._codegen_rules:
            del self._codegen_rules[mapping_id]
            return True
        return False

    @property
    def codegen_rules(self) -> List[CodeGenRule]:
        return list(self._codegen_rules.values())

    # ── Statistics ─────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, int]:
        """Return count stats for each level."""
        return {
            "l1_concepts": len(self._concepts),
            "l2_rules": len(self._rules),
            "l3_mappings": len(self._mappings),
            "l4_codegen_rules": len(self._codegen_rules),
        }

    @property
    def average_confidence(self) -> Dict[str, float]:
        """Average confidence per level."""
        def _avg(items, attr="confidence"):
            if not items:
                return 0.0
            return sum(getattr(i, attr, 0.0) for i in items) / len(items)

        return {
            "l1": _avg(self._concepts.values()),
            "l2": _avg(self._rules.values()),
            "l3": _avg(self._mappings.values()),
            "l4": _avg(self._codegen_rules.values()),
        }

    # ── Validation ─────────────────────────────────────────────

    def validate_l1_concepts(self, codebase_path: Union[str, Path]) -> List[L1ValidationResult]:
        """Validate L1 concepts against a codebase by checking if concept names appear in source.

        Searches for concept names (and their synonyms from related_concepts) in source files.
        Returns a list of L1ValidationResult, one per concept.
        """
        codebase = Path(codebase_path)
        results: List[L1ValidationResult] = []

        # Gather all source file paths
        source_exts = {".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h", ".yaml", ".yml", ".json", ".md"}
        source_files = [
            str(f) for f in codebase.rglob("*")
            if f.is_file() and f.suffix in source_exts
        ]

        for concept in self._concepts.values():
            matched_files: List[str] = []
            name_lower = concept.name.lower()

            for fpath in source_files:
                try:
                    content = Path(fpath).read_text(encoding="utf-8", errors="ignore").lower()
                except (IOError, OSError):
                    continue
                if name_lower in content:
                    matched_files.append(fpath)

            # Check if related concepts are also found
            missing_related: List[str] = []
            if concept.related_concepts:
                for rel_name in concept.related_concepts:
                    rel_lower = rel_name.lower()
                    found = False
                    for fpath in source_files:
                        if fpath in matched_files:
                            continue  # already scanned, would have caught it
                        try:
                            content = Path(fpath).read_text(encoding="utf-8", errors="ignore").lower()
                        except (IOError, OSError):
                            continue
                        if rel_lower in content:
                            found = True
                            break
                    if not found:
                        # Also check matched files more carefully
                        for fpath in matched_files:
                            try:
                                content = Path(fpath).read_text(encoding="utf-8", errors="ignore").lower()
                            except (IOError, OSError):
                                continue
                            if rel_lower in content:
                                found = True
                                break
                        if not found:
                            missing_related.append(rel_name)

            results.append(L1ValidationResult(
                concept_name=concept.name,
                found_in_code=len(matched_files) > 0,
                matched_files=matched_files,
                missing_related=missing_related,
                notes="OK" if matched_files else f"Concept '{concept.name}' not found in codebase",
            ))

        return results

    def validate_l2_rules(self, codebase_path: Union[str, Path]) -> List[L2ValidationResult]:
        """Validate all L2 constraint rules against a codebase.

        For each rule:
          - If check_sql is provided and a SQLite DB is found at codebase_path, run the SQL.
          - If expected_pattern is provided, grep source files matching file_glob.
          - Otherwise, mark as manual validation needed.

        Returns a list of L2ValidationResult, one per rule.
        """
        import time as _time

        codebase = Path(codebase_path)
        results: List[L2ValidationResult] = []

        for rule in self._rules.values():
            t0 = _time.monotonic()

            if not rule.auto_check:
                results.append(L2ValidationResult(
                    rule_name=rule.name,
                    passed=True,  # Not auto-checkable, assume manual pass
                    severity=rule.severity,
                    method="manual",
                    details="Auto-check disabled; requires manual validation.",
                ))
                continue

            # Strategy 1: SQL-based validation
            if rule.check_sql:
                sql_result = self._validate_rule_via_sql(codebase, rule)
                sql_result.duration_ms = (_time.monotonic() - t0) * 1000
                results.append(sql_result)
                continue

            # Strategy 2: Pattern-based validation
            if rule.expected_pattern:
                pattern_result = self._validate_rule_via_pattern(codebase, rule)
                pattern_result.duration_ms = (_time.monotonic() - t0) * 1000
                results.append(pattern_result)
                continue

            # No auto-check mechanism configured
            results.append(L2ValidationResult(
                rule_name=rule.name,
                passed=True,
                severity=rule.severity,
                method="manual",
                details="No auto_check mechanism configured (no check_sql or expected_pattern).",
            ))

        return results

    def _validate_rule_via_sql(self, codebase: Path, rule: ConstraintRule) -> L2ValidationResult:
        """Validate a rule by executing its check_sql against a SQLite database.

        Convention: The SQL query should return rows that represent *violations*.
        If the query returns 0 rows, the rule passes. If rows are returned, the rule fails.
        """
        # Find SQLite database files
        db_files = list(codebase.rglob("*.db")) + list(codebase.rglob("*.sqlite")) + list(codebase.rglob("*.sqlite3"))

        if not db_files:
            return L2ValidationResult(
                rule_name=rule.name,
                passed=False,
                severity=rule.severity,
                method="sql",
                details="No SQLite database (.db/.sqlite/.sqlite3) found in codebase.",
            )

        # Try each database
        violations: List[str] = []
        for db_path in db_files:
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(rule.check_sql)
                rows = cursor.fetchall()
                conn.close()

                if rows:
                    for row in rows[:20]:  # Cap at 20 violations per DB
                        violations.append(
                            f"[{db_path.name}] {dict(row)}"
                        )
            except sqlite3.Error as e:
                return L2ValidationResult(
                    rule_name=rule.name,
                    passed=False,
                    severity=rule.severity,
                    method="sql",
                    details=f"SQL execution error on {db_path.name}: {e}",
                )

        if violations:
            return L2ValidationResult(
                rule_name=rule.name,
                passed=False,
                severity=rule.severity,
                method="sql",
                details=f"Found {len(violations)} violation(s)",
                violations=violations,
            )

        return L2ValidationResult(
            rule_name=rule.name,
            passed=True,
            severity=rule.severity,
            method="sql",
            details=f"SQL check passed (checked {len(db_files)} database(s)).",
        )

    def _validate_rule_via_pattern(self, codebase: Path, rule: ConstraintRule) -> L2ValidationResult:
        """Validate a rule by grepping its expected_pattern in source files.

        The expected_pattern is a regex. If matches are found, the rule passes
        (the pattern exists in code). If no matches are found, the rule fails.
        """
        try:
            pattern = re.compile(rule.expected_pattern, re.IGNORECASE)
        except re.error as e:
            return L2ValidationResult(
                rule_name=rule.name,
                passed=False,
                severity=rule.severity,
                method="pattern",
                details=f"Invalid regex pattern: {e}",
            )

        glob_pattern = rule.file_glob or "**/*.py"
        matched_files: List[str] = []

        for fpath in codebase.glob(glob_pattern):
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except (IOError, OSError):
                continue
            if pattern.search(content):
                matched_files.append(str(fpath))

        if matched_files:
            return L2ValidationResult(
                rule_name=rule.name,
                passed=True,
                severity=rule.severity,
                method="pattern",
                details=f"Pattern found in {len(matched_files)} file(s).",
                violations=list(matched_files),  # reuse field for matched files
            )

        return L2ValidationResult(
            rule_name=rule.name,
            passed=False,
            severity=rule.severity,
            method="pattern",
            details=f"Pattern '{rule.expected_pattern}' not found in any file matching '{glob_pattern}'.",
        )

    # ── Serialization ──────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the entire knowledge graph to a plain dict."""
        return {
            "meta": {
                "version": "3.0",
                "pipeline": "multi-agent-pipeline",
            },
            "l1_concepts": [c.to_dict() for c in self._concepts.values()],
            "l2_rules": [r.to_dict() for r in self._rules.values()],
            "l3_mappings": [m.to_dict() for m in self._mappings.values()],
            "l4_codegen_rules": [cgr.to_dict() for cgr in self._codegen_rules.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgeGraph:
        """Deserialize a knowledge graph from a dict."""
        kg = cls()

        for item in data.get("l1_concepts", []):
            kg.add_concept(DomainConcept.from_dict(item))

        for item in data.get("l2_rules", []):
            kg.add_rule(ConstraintRule.from_dict(item))

        for item in data.get("l3_mappings", []):
            kg.add_mapping(ComponentMapping.from_dict(item))

        for item in data.get("l4_codegen_rules", []):
            kg.add_codegen_rule(CodeGenRule.from_dict(item))

        return kg

    def to_json(self, path: Optional[Union[str, Path]] = None, indent: int = 2) -> Optional[str]:
        """Serialize to JSON. If path is given, writes to file. Otherwise returns string."""
        json_str = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        if path:
            Path(path).write_text(json_str, encoding="utf-8")
            return None
        return json_str

    @classmethod
    def from_json(cls, source: Union[str, Path]) -> KnowledgeGraph:
        """Deserialize from a JSON file path or JSON string."""
        path = Path(source)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(str(source))
        return cls.from_dict(data)

    def to_yaml(self, path: Optional[Union[str, Path]] = None) -> Optional[str]:
        """Serialize to YAML. If path is given, writes to file. Otherwise returns string."""
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML serialization. "
                "Install it with: pip install pyyaml"
            )

        # Use safe_dump with custom representer for better formatting
        yaml_str = yaml.safe_dump(
            self.to_dict(),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
        )
        if path:
            Path(path).write_text(yaml_str, encoding="utf-8")
            return None
        return yaml_str

    @classmethod
    def from_yaml(cls, source: Union[str, Path]) -> KnowledgeGraph:
        """Deserialize from a YAML file path or YAML string."""
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML serialization. "
                "Install it with: pip install pyyaml"
            )

        path = Path(source)
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            data = yaml.safe_load(str(source)) or {}

        return cls.from_dict(data)

    # ── Graph traversal helpers ─────────────────────────────────

    def get_rules_for_concept(self, concept_name: str) -> List[ConstraintRule]:
        """Get all L2 rules that reference a given L1 concept."""
        return [
            r for r in self._rules.values()
            if concept_name in r.related_concepts
        ]

    def get_mappings_for_concept(self, concept_name: str) -> List[ComponentMapping]:
        """Get all L3 mappings for a given concept name."""
        mapping = self._mappings.get(concept_name)
        return [mapping] if mapping else []

    def get_codegen_rules_for_mapping(self, mapping_id: str) -> List[CodeGenRule]:
        """Get all L4 codegen rules for a given mapping ID."""
        cgr = self._codegen_rules.get(mapping_id)
        return [cgr] if cgr else []

    def trace_concept_to_code(self, concept_name: str) -> Dict[str, Any]:
        """Full trace: L1 concept → L3 mapping(s) → L4 codegen rule(s).

        Returns a dict with the full trace from concept to code generation rules.
        """
        concept = self._concepts.get(concept_name)
        rules = self.get_rules_for_concept(concept_name)
        mappings = self.get_mappings_for_concept(concept_name)
        codegen = []
        for m in mappings:
            codegen.extend(self.get_codegen_rules_for_mapping(m.concept_name))

        return {
            "concept": concept.to_dict() if concept else None,
            "constraint_rules": [r.to_dict() for r in rules],
            "component_mappings": [m.to_dict() for m in mappings],
            "codegen_rules": [c.to_dict() for c in codegen],
        }

    # ── Bulk operations ─────────────────────────────────────────

    def clear(self) -> None:
        """Remove all nodes from all levels."""
        self._concepts.clear()
        self._rules.clear()
        self._mappings.clear()
        self._codegen_rules.clear()

    def merge(self, other: KnowledgeGraph) -> None:
        """Merge another KnowledgeGraph into this one (other's nodes override on conflict)."""
        for c in other._concepts.values():
            self._concepts[c.name] = c
        for r in other._rules.values():
            self._rules[r.name] = r
        for m in other._mappings.values():
            self._mappings[m.concept_name] = m
        for cgr in other._codegen_rules.values():
            self._codegen_rules[cgr.mapping_id] = cgr

    def __len__(self) -> int:
        return (
            len(self._concepts)
            + len(self._rules)
            + len(self._mappings)
            + len(self._codegen_rules)
        )

    def __repr__(self) -> str:
        return (
            f"KnowledgeGraph(concepts={len(self._concepts)}, "
            f"rules={len(self._rules)}, "
            f"mappings={len(self._mappings)}, "
            f"codegen_rules={len(self._codegen_rules)})"
        )
