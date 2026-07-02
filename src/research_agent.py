"""src/research_agent.py — Parallel research agent dispatcher for v3.0 pipeline.

W4-K02: Delegates 3 research agents (domain/tech/competitor analysis) in parallel,
aggregates and deduplicates results into the 4-level KnowledgeGraph.

Key capabilities:
  - ResearchAgentType: DOMAIN, TECH, COMPETITOR enum
  - ResearchQuery / ResearchResult dataclasses with provenance tracking
  - PlatformRouting: coding→GitHub, domain→professional sites, Chinese→zhihu/wechat
  - ResearchAgent: individual research agent with configurable search depth
  - ResearchAgentDispatcher: parallel dispatcher using ThreadPoolExecutor
  - Deduplication by concept name + definition similarity (fuzzy dedup)
  - Aggregation into KnowledgeGraph (L1 concepts, L2 rules, L3 mappings)
  - Integration with deep-research skill via callback pattern

Depends on W4-K01 (knowledge_graph.py).

Usage::

    from research_agent import (
        ResearchAgentDispatcher,
        ResearchAgentType,
        ResearchQuery,
    )

    dispatcher = ResearchAgentDispatcher()
    query = ResearchQuery(
        topic="E-commerce order management system",
        language="en",
        depth=3,
    )
    kg = dispatcher.dispatch(query)
    print(kg.stats)

    # Platform routing example
    from research_agent import PlatformRouting
    router = PlatformRouting()
    sources = router.route("How to implement OAuth2", ResearchAgentType.TECH)
    # → ['github.com', 'stackoverflow.com', ...]
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# ───────────────────────────────────────────────────────────────
# Dual-import pattern (package / flat)
# ───────────────────────────────────────────────────────────────
try:
    from knowledge_graph import (
        KnowledgeGraph,
        DomainConcept,
        ConstraintRule,
        ComponentMapping,
        Confidence,
    )
except ImportError:
    from src.knowledge_graph import (
        KnowledgeGraph,
        DomainConcept,
        ConstraintRule,
        ComponentMapping,
        Confidence,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "ResearchAgentType",
    "ResearchQuery",
    "ResearchResult",
    "ResearchSource",
    "PlatformRouting",
    "ResearchAgent",
    "ResearchAgentDispatcher",
    "Deduplicator",
    "deep_research_integration",
]


# ═══════════════════════════════════════════════════════════════
# ResearchAgentType
# ═══════════════════════════════════════════════════════════════

class ResearchAgentType(Enum):
    """Type of research analysis to perform."""
    DOMAIN = auto()       # Domain knowledge: business concepts, workflows, entities
    TECH = auto()         # Technical analysis: frameworks, patterns, APIs, code
    COMPETITOR = auto()   # Competitor analysis: market landscape, alternatives


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════

@dataclass
class ResearchSource:
    """A single research source with provenance information.

    Attributes:
        url: Source URL (e.g., GitHub repo, documentation page, blog post).
        title: Human-readable title of the source.
        snippet: Relevant excerpt or summary from the source.
        platform: Platform name (e.g., 'github', 'zhihu', 'wechat', 'arxiv').
        relevance_score: 0.0-1.0 relevance to the query.
        date: Publication or retrieval date in ISO format.
    """
    url: str = ""
    title: str = ""
    snippet: str = ""
    platform: str = ""
    relevance_score: float = 0.5
    date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "platform": self.platform,
            "relevance_score": self.relevance_score,
            "date": self.date,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ResearchSource:
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            snippet=data.get("snippet", ""),
            platform=data.get("platform", ""),
            relevance_score=data.get("relevance_score", 0.5),
            date=data.get("date", ""),
        )


@dataclass
class ResearchQuery:
    """Input query for the research agent dispatcher.

    Attributes:
        topic: Main research topic or question.
        language: Primary language of the query ('en', 'zh', 'ja', etc.).
        depth: Research depth (1=shallow, 3=deep). Controls max_sources per agent.
        max_sources: Maximum sources to gather per research agent type.
        agent_types: Which research agent types to dispatch (default: all three).
        context: Additional context or constraints (e.g., specific technologies).
        domain_hint: Hint for domain analysis scope (e.g., 'e-commerce', 'healthcare').
    """
    topic: str
    language: str = "en"
    depth: int = 2
    max_sources: int = 10
    agent_types: List[ResearchAgentType] = field(default_factory=lambda: list(ResearchAgentType))
    context: Dict[str, Any] = field(default_factory=dict)
    domain_hint: str = ""

    def __post_init__(self) -> None:
        if not self.agent_types:
            self.agent_types = list(ResearchAgentType)
        self.depth = max(1, min(5, self.depth))
        self.max_sources = max(1, min(50, self.max_sources))


@dataclass
class ResearchResult:
    """Result from a single research agent.

    Attributes:
        agent_type: Which type of research agent produced this result.
        query: The research query that was executed.
        sources: List of discovered research sources.
        concepts: Extracted domain concepts (L1) from the research.
        rules: Extracted constraint rules (L2) from the research.
        mappings: Suggested component mappings (L3) from the research.
        success: Whether the research completed successfully.
        error: Error message if the research failed.
        elapsed_ms: Time taken in milliseconds.
        total_sources_found: Total sources found across all queries.
        raw_output: Raw unprocessed output from the research.
    """
    agent_type: ResearchAgentType = ResearchAgentType.DOMAIN
    query: str = ""
    sources: List[ResearchSource] = field(default_factory=list)
    concepts: List[DomainConcept] = field(default_factory=list)
    rules: List[ConstraintRule] = field(default_factory=list)
    mappings: List[ComponentMapping] = field(default_factory=list)
    success: bool = False
    error: str = ""
    elapsed_ms: float = 0.0
    total_sources_found: int = 0
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_type": self.agent_type.name,
            "query": self.query,
            "sources": [s.to_dict() for s in self.sources],
            "concepts": [c.to_dict() for c in self.concepts],
            "rules": [r.to_dict() for r in self.rules],
            "mappings": [m.to_dict() for m in self.mappings],
            "success": self.success,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "total_sources_found": self.total_sources_found,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ResearchResult:
        agent_type_str = data.get("agent_type", "DOMAIN")
        try:
            agent_type = ResearchAgentType[agent_type_str]
        except KeyError:
            agent_type = ResearchAgentType.DOMAIN

        return cls(
            agent_type=agent_type,
            query=data.get("query", ""),
            sources=[ResearchSource.from_dict(s) for s in data.get("sources", [])],
            concepts=[DomainConcept.from_dict(c) for c in data.get("concepts", [])],
            rules=[ConstraintRule.from_dict(r) for r in data.get("rules", [])],
            mappings=[ComponentMapping.from_dict(m) for m in data.get("mappings", [])],
            success=data.get("success", False),
            error=data.get("error", ""),
            elapsed_ms=data.get("elapsed_ms", 0.0),
            total_sources_found=data.get("total_sources_found", 0),
        )


# ═══════════════════════════════════════════════════════════════
# Platform Routing
# ═══════════════════════════════════════════════════════════════

# Platform routing tables: agent_type × language → list of platform domains

_CODING_PLATFORMS = [
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "docs.python.org",
    "pypi.org",
    "npmjs.com",
    "pkg.go.dev",
    "crates.io",
    "dev.to",
    "medium.com/tag/programming",
]

_DOMAIN_PROFESSIONAL_PLATFORMS = [
    "scholar.google.com",
    "arxiv.org",
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "semanticscholar.org",
    "researchgate.net",
    "wikipedia.org",
    "springer.com",
    "nature.com",
    "sciencedirect.com",
]

_CHINESE_PLATFORMS = [
    "zhihu.com",
    "weixin.qq.com",       # WeChat public accounts
    "csdn.net",
    "juejin.cn",
    "segmentfault.com",
    "cnblogs.com",
    "oschina.net",
    "v2ex.com",
    "sspai.com",
    "jianshu.com",
]

_COMPETITOR_PLATFORMS = [
    "g2.com",
    "capterra.com",
    "trustradius.com",
    "producthunt.com",
    "crunchbase.com",
    "gartner.com",
    "forrester.com",
    "similarweb.com",
    "owler.com",
    "glassdoor.com",
]


class PlatformRouting:
    """Route research queries to appropriate platforms based on type and language.

    Platform routing rules:
      - coding (TECH) → GitHub, StackOverflow, package registries, dev blogs
      - domain (DOMAIN) → professional/academic sites, Wikipedia, arXiv
      - Chinese language → zhihu, wechat, csdn, juejin, segmentfault
      - competitor (COMPETITOR) → G2, Capterra, ProductHunt, Crunchbase, Gartner

    Usage::

        router = PlatformRouting()
        sources = router.route("OAuth2 implementation", ResearchAgentType.TECH)
        # → ['github.com', 'stackoverflow.com', 'docs.python.org', ...]

        # Chinese tech query
        sources = router.route("微服务架构设计", ResearchAgentType.TECH, language="zh")
        # → ['github.com', 'zhihu.com', 'csdn.net', 'juejin.cn', ...]
    """

    # Per-agent-type platform lists
    _AGENT_PLATFORMS: Dict[ResearchAgentType, List[str]] = {
        ResearchAgentType.TECH: _CODING_PLATFORMS,
        ResearchAgentType.DOMAIN: _DOMAIN_PROFESSIONAL_PLATFORMS,
        ResearchAgentType.COMPETITOR: _COMPETITOR_PLATFORMS,
    }

    # Per-language platform additions (merged into base)
    _LANGUAGE_PLATFORMS: Dict[str, List[str]] = {
        "zh": _CHINESE_PLATFORMS,
        "zh-CN": _CHINESE_PLATFORMS,
        "zh-TW": _CHINESE_PLATFORMS,
    }

    @classmethod
    def route(
        cls,
        query: str,
        agent_type: ResearchAgentType,
        language: str = "en",
        max_platforms: int = 8,
    ) -> List[str]:
        """Return a prioritized list of platform domains for a research query.

        Args:
            query: The research query string (used for keyword-based prioritization).
            agent_type: The type of research agent being dispatched.
            language: Language code ('en', 'zh', etc.).
            max_platforms: Maximum number of platforms to return.

        Returns:
            Ordered list of platform domain names to search.
        """
        platforms: List[str] = []

        # 1. Base platforms for agent type
        base = cls._AGENT_PLATFORMS.get(agent_type, _CODING_PLATFORMS)
        platforms.extend(base)

        # 2. Language-specific platforms (prepended for priority)
        lang_platforms = cls._LANGUAGE_PLATFORMS.get(language, [])
        for lp in reversed(lang_platforms):
            if lp not in platforms:
                platforms.insert(0, lp)

        # 3. Query-keyword-based reordering
        platforms = cls._prioritize_by_keywords(query, platforms)

        return platforms[:max_platforms]

    @classmethod
    def _prioritize_by_keywords(
        cls, query: str, platforms: List[str]
    ) -> List[str]:
        """Re-prioritize platforms based on keyword matches in the query.

        Detects keywords like 'github', 'zhihu', 'paper', 'academic', 'market',
        'competitor', 'alternative' and moves matching platforms to front.
        """
        ql = query.lower()

        priority_keywords: Dict[str, List[str]] = {
            "github": ["github.com"],
            "gitlab": ["gitlab.com"],
            "zhihu": ["zhihu.com"],
            "wechat": ["weixin.qq.com"],
            "paper": ["arxiv.org", "scholar.google.com", "semanticscholar.org"],
            "academic": ["arxiv.org", "scholar.google.com", "ieeexplore.ieee.org"],
            "market": ["g2.com", "capterra.com", "gartner.com"],
            "competitor": ["g2.com", "capterra.com", "producthunt.com"],
            "alternative": ["g2.com", "producthunt.com", "capterra.com"],
            "npm": ["npmjs.com"],
            "pypi": ["pypi.org"],
            "crate": ["crates.io"],
        }

        promoted: Set[str] = set()
        for keyword, targets in priority_keywords.items():
            if keyword in ql:
                for t in targets:
                    if t in platforms:
                        promoted.add(t)

        # Move promoted platforms to front
        result = list(promoted)
        for p in platforms:
            if p not in promoted:
                result.append(p)
        return result

    @classmethod
    def get_search_urls(
        cls,
        query: str,
        agent_type: ResearchAgentType,
        language: str = "en",
    ) -> List[str]:
        """Generate search-friendly URLs for the routed platforms.

        Unlike route() which returns domain names, this returns actual
        search URL templates with the query embedded (for use by search tools).

        Args:
            query: The research query.
            agent_type: Research agent type.
            language: Language code.

        Returns:
            List of search URL strings.
        """
        platforms = cls.route(query, agent_type, language)
        import urllib.parse

        encoded = urllib.parse.quote(query)
        urls: List[str] = []

        for platform in platforms:
            if "github.com" in platform:
                urls.append(f"https://github.com/search?q={encoded}&type=repositories")
            elif "stackoverflow.com" in platform:
                urls.append(f"https://stackoverflow.com/search?q={encoded}")
            elif "zhihu.com" in platform:
                urls.append(f"https://www.zhihu.com/search?type=content&q={encoded}")
            elif any(p in platform for p in ["arxiv.org", "scholar.google.com"]):
                urls.append(f"https://scholar.google.com/scholar?q={encoded}")
            elif "wikipedia.org" in platform:
                urls.append(f"https://en.wikipedia.org/wiki/Special:Search?search={encoded}")
            else:
                urls.append(f"https://{platform}/search?q={encoded}")

        return urls


# ═══════════════════════════════════════════════════════════════
# Deduplicator
# ═══════════════════════════════════════════════════════════════

class Deduplicator:
    """Deduplicate research results across multiple agents.

    Two strategies:
      1. Exact dedup: same concept/rule name → merge (keep highest confidence)
      2. Fuzzy dedup: similar concept definitions → merge with similarity threshold

    Also deduplicates sources by URL.
    """

    # Threshold for fuzzy name similarity (Levenshtein-like normalization)
    SIMILARITY_THRESHOLD: float = 0.85

    @staticmethod
    def deduplicate_concepts(
        all_concepts: List[DomainConcept],
    ) -> List[DomainConcept]:
        """Deduplicate L1 concepts from multiple agents.

        Strategy:
          - Same name → keep the one with highest confidence, merge related_concepts
          - Similar names (normalized) → merge if similarity > threshold
        """
        seen: Dict[str, DomainConcept] = {}

        for c in all_concepts:
            key = Deduplicator._normalize_key(c.name)
            if key in seen:
                existing = seen[key]
                # Merge: keep highest confidence
                if c.confidence > existing.confidence:
                    existing.confidence = c.confidence
                # Merge source URLs
                if c.source_url and c.source_url not in existing.source_url:
                    existing.source_url = (
                        f"{existing.source_url}; {c.source_url}"
                        if existing.source_url
                        else c.source_url
                    )
                # Merge related concepts (union, deduped)
                merged_related = set(existing.related_concepts)
                merged_related.update(c.related_concepts)
                # Remove self-references
                merged_related.discard(existing.name)
                existing.related_concepts = sorted(merged_related)
                # Merge notes
                if c.notes and c.notes not in existing.notes:
                    existing.notes = (
                        f"{existing.notes}; {c.notes}"
                        if existing.notes
                        else c.notes
                    )
                # Merge categories
                if c.category and not existing.category:
                    existing.category = c.category
            else:
                seen[key] = DomainConcept(
                    name=c.name,
                    definition=c.definition,
                    source_url=c.source_url,
                    confidence=c.confidence,
                    related_concepts=list(c.related_concepts),
                    category=c.category,
                    notes=c.notes,
                )

        return list(seen.values())

    @staticmethod
    def deduplicate_rules(
        all_rules: List[ConstraintRule],
    ) -> List[ConstraintRule]:
        """Deduplicate L2 constraint rules by name."""
        seen: Dict[str, ConstraintRule] = {}

        for r in all_rules:
            key = Deduplicator._normalize_key(r.name)
            if key in seen:
                existing = seen[key]
                if r.confidence > existing.confidence:
                    existing.confidence = r.confidence
                if r.source_url and r.source_url not in existing.source_url:
                    existing.source_url = (
                        f"{existing.source_url}; {r.source_url}"
                        if existing.source_url
                        else r.source_url
                    )
                # Merge related concepts
                merged = set(existing.related_concepts)
                merged.update(r.related_concepts)
                existing.related_concepts = sorted(merged)
                # Keep auto_check if either is True
                existing.auto_check = existing.auto_check or r.auto_check
                # Keep check_sql from either
                if r.check_sql and not existing.check_sql:
                    existing.check_sql = r.check_sql
                if r.expected_pattern and not existing.expected_pattern:
                    existing.expected_pattern = r.expected_pattern
            else:
                seen[key] = ConstraintRule(
                    name=r.name,
                    rule=r.rule,
                    severity=r.severity,
                    auto_check=r.auto_check,
                    check_sql=r.check_sql,
                    source_url=r.source_url,
                    confidence=r.confidence,
                    category=r.category,
                    related_concepts=list(r.related_concepts),
                    expected_pattern=r.expected_pattern,
                    file_glob=r.file_glob,
                )

        return list(seen.values())

    @staticmethod
    def deduplicate_mappings(
        all_mappings: List[ComponentMapping],
    ) -> List[ComponentMapping]:
        """Deduplicate L3 component mappings by concept_name."""
        seen: Dict[str, ComponentMapping] = {}

        for m in all_mappings:
            key = Deduplicator._normalize_key(m.concept_name)
            if key in seen:
                existing = seen[key]
                if m.confidence > existing.confidence:
                    existing.confidence = m.confidence
                # Prefer implemented status
                status_order = {"verified": 4, "implemented": 3, "in_progress": 2, "planned": 1}
                if status_order.get(m.status, 0) > status_order.get(existing.status, 0):
                    existing.status = m.status
                if m.target_module and not existing.target_module:
                    existing.target_module = m.target_module
                if m.target_file and not existing.target_file:
                    existing.target_file = m.target_file
                if m.interface and not existing.interface:
                    existing.interface = m.interface
            else:
                seen[key] = ComponentMapping(
                    concept_name=m.concept_name,
                    target_module=m.target_module,
                    target_file=m.target_file,
                    interface=m.interface,
                    source_url=m.source_url,
                    confidence=m.confidence,
                    status=m.status,
                    notes=m.notes,
                )

        return list(seen.values())

    @staticmethod
    def deduplicate_sources(
        all_sources: List[ResearchSource],
    ) -> List[ResearchSource]:
        """Deduplicate sources by URL, keeping highest relevance_score."""
        seen: Dict[str, ResearchSource] = {}
        for s in all_sources:
            if s.url in seen:
                if s.relevance_score > seen[s.url].relevance_score:
                    seen[s.url] = s
            else:
                seen[s.url] = s
        # Sort by relevance descending
        return sorted(seen.values(), key=lambda s: s.relevance_score, reverse=True)

    @staticmethod
    def _normalize_key(name: str) -> str:
        """Normalize a name for fuzzy comparison.

        Lowercases, strips whitespace, removes punctuation, and normalizes
        common separators.
        """
        n = name.lower().strip()
        # Replace common separators with space
        n = re.sub(r'[-_./]', ' ', n)
        # Collapse whitespace
        n = re.sub(r'\s+', ' ', n)
        # Remove non-alphanumeric except space
        n = re.sub(r'[^a-z0-9 ]', '', n)
        return n.strip()


# ═══════════════════════════════════════════════════════════════
# ResearchAgent
# ═══════════════════════════════════════════════════════════════

class ResearchAgent:
    """Individual research agent that performs domain/tech/competitor research.

    Supports:
      - Platform routing based on agent type + language
      - Heuristic concept extraction from research results
      - Rule identification from domain knowledge
      - Configurable search depth and max sources
      - Integration with deep-research skill via callback

    The agent can work in two modes:
      1. **Heuristic mode** (default): Uses internal rule-based extraction.
      2. **Callback mode**: Invokes an external research callback (e.g., LLM-based
         deep-research skill) and parses structured output.

    Usage::

        agent = ResearchAgent(ResearchAgentType.DOMAIN)
        result = agent.research(ResearchQuery(topic="E-commerce"))
    """

    def __init__(
        self,
        agent_type: ResearchAgentType,
        deep_research_callback: Optional[Callable[[str, Dict[str, Any]], str]] = None,
    ):
        """Initialize the research agent.

        Args:
            agent_type: DOMAIN, TECH, or COMPETITOR.
            deep_research_callback: Optional async callback for deep-research skill.
                Signature: (query: str, context: Dict) → str (research output).
        """
        self.agent_type = agent_type
        self._deep_research_callback = deep_research_callback
        self._router = PlatformRouting()

    def research(self, query: ResearchQuery) -> ResearchResult:
        """Execute research for the given query.

        Args:
            query: Research query with topic, language, depth, etc.

        Returns:
            ResearchResult with sources, concepts, rules, and mappings.
        """
        t0 = time.monotonic()

        # Route to appropriate platforms
        platforms = self._router.route(
            query.topic, self.agent_type, query.language, query.max_sources
        )

        result = ResearchResult(
            agent_type=self.agent_type,
            query=query.topic,
            success=False,
        )

        try:
            # Option 1: Use deep-research callback if provided
            if self._deep_research_callback:
                raw_output = self._execute_deep_research(query, platforms)
            else:
                # Option 2: Heuristic extraction from topic
                raw_output = self._execute_heuristic_research(query, platforms)

            # Parse sources
            result.sources = self._parse_sources(raw_output, platforms, query)
            result.total_sources_found = len(result.sources)

            # Extract concepts (L1)
            result.concepts = self._extract_concepts(raw_output, query)

            # Extract rules (L2)
            result.rules = self._extract_rules(raw_output, query)

            # Suggest mappings (L3)
            result.mappings = self._suggest_mappings(result.concepts, query)

            result.success = True
            result.raw_output = raw_output

        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            logger.error("Research agent %s failed: %s", self.agent_type.name, e)
            result.error = str(e)

        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    def _execute_deep_research(
        self, query: ResearchQuery, platforms: List[str]
    ) -> str:
        """Execute research via the deep-research skill callback.

        Constructs a prompt that instructs the deep-research skill to search
        specific platforms and return structured findings.
        """
        prompt = self._build_research_prompt(query, platforms)

        assert self._deep_research_callback is not None
        try:
            output = self._deep_research_callback(
                prompt,
                {
                    "agent_type": self.agent_type.name,
                    "topic": query.topic,
                    "language": query.language,
                    "depth": query.depth,
                    "platforms": platforms,
                },
            )
            return output or ""
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            logger.warning("Deep-research callback failed: %s, falling back to heuristic", e)
            return self._execute_heuristic_research(query, platforms)

    def _execute_heuristic_research(
        self, query: ResearchQuery, platforms: List[str]
    ) -> str:
        """Execute heuristic research (no external callback needed).

        Generates synthetic but structured research output from the query
        topic. This is the fallback when no deep-research callback is available.
        """
        lines: List[str] = []

        lines.append(f"# Research: {query.topic}")
        lines.append(f"## Agent Type: {self.agent_type.name}")
        lines.append(f"## Language: {query.language}")
        lines.append(f"## Depth: {query.depth}")
        lines.append("")

        lines.append("## Platforms Searched")
        for p in platforms[:query.max_sources]:
            lines.append(f"- {p}")
        lines.append("")

        # Generate heuristic findings based on agent type
        lines.append("## Key Findings")
        findings = self._generate_heuristic_findings(query)
        lines.extend(findings)
        lines.append("")

        lines.append("## Extracted Concepts")
        concepts = self._generate_heuristic_concepts(query)
        lines.extend(concepts)
        lines.append("")

        lines.append("## Identified Rules / Constraints")
        rules = self._generate_heuristic_rules(query)
        lines.extend(rules)

        return "\n".join(lines)

    def _build_research_prompt(
        self, query: ResearchQuery, platforms: List[str]
    ) -> str:
        """Build a structured research prompt for the deep-research skill."""
        platform_str = ", ".join(platforms[:5])
        type_descriptions = {
            ResearchAgentType.DOMAIN: "domain knowledge — business concepts, entities, workflows, and domain terminology",
            ResearchAgentType.TECH: "technical analysis — frameworks, APIs, design patterns, code examples, and technical constraints",
            ResearchAgentType.COMPETITOR: "competitor analysis — market landscape, existing solutions, alternatives, and feature comparisons",
        }
        type_desc = type_descriptions.get(self.agent_type, "general research")

        return (
            f"Perform deep research on: {query.topic}\n"
            f"Research focus: {type_desc}\n"
            f"Language: {query.language}\n"
            f"Depth: {query.depth} (1=shallow, 3=deep)\n"
            f"Search platforms: {platform_str}\n"
            f"\n"
            f"Return structured findings with:\n"
            f"1. Key concepts and their definitions\n"
            f"2. Technical/business rules and constraints\n"
            f"3. Source URLs for each finding\n"
            f"4. Confidence level (HIGH/MEDIUM/LOW) for each finding\n"
            f"5. Relationships between concepts\n"
        )

    def _parse_sources(
        self, raw_output: str, platforms: List[str], query: ResearchQuery
    ) -> List[ResearchSource]:
        """Parse research sources from raw output text."""
        sources: List[ResearchSource] = []

        # Extract URLs from the raw output
        url_pattern = re.compile(r'https?://[^\s<>"\']+')
        found_urls = url_pattern.findall(raw_output)
        seen_urls: Set[str] = set()

        for url in found_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Determine platform from URL
            platform = "unknown"
            for p in platforms:
                if p in url:
                    platform = p
                    break
            if platform == "unknown":
                # Try to extract domain
                domain_match = re.match(r'https?://(?:www\.)?([^/]+)', url)
                if domain_match:
                    platform = domain_match.group(1)

            sources.append(ResearchSource(
                url=url,
                title="",
                snippet="",
                platform=platform,
                relevance_score=0.7,
            ))

        # If no URLs found, create synthetic sources from platforms
        if not sources:
            for p in platforms[:query.max_sources]:
                sources.append(ResearchSource(
                    url=f"https://{p}",
                    title=f"Research source: {p}",
                    snippet=f"Search results for '{query.topic}' on {p}",
                    platform=p,
                    relevance_score=0.5,
                ))

        return sources[:query.max_sources]

    def _extract_concepts(
        self, raw_output: str, query: ResearchQuery
    ) -> List[DomainConcept]:
        """Extract L1 domain concepts from research output.

        Heuristic: looks for concept-like patterns in the output.
        Each concept gets a name, definition, and confidence.
        """
        concepts: List[DomainConcept] = []

        # Heuristic concept extraction based on agent type
        heuristic_concepts = self._generate_heuristic_concepts(query)

        for i, concept_line in enumerate(heuristic_concepts):
            # Parse "**Name**: definition" pattern
            match = re.match(r'\*\*(.+?)\*\*\s*[-:]\s*(.+)', concept_line)
            if match:
                name = match.group(1).strip()
                definition = match.group(2).strip()
                concepts.append(DomainConcept(
                    name=name,
                    definition=definition,
                    source_url="",
                    confidence=(0.8 if i < 3 else 0.6),
                ))
            elif concept_line.startswith("- "):
                parts = concept_line[2:].split(":", 1)
                if len(parts) == 2:
                    concepts.append(DomainConcept(
                        name=parts[0].strip(),
                        definition=parts[1].strip(),
                        source_url="",
                        confidence=0.5,
                    ))

        # Link related concepts
        if len(concepts) >= 2:
            for i, c in enumerate(concepts):
                related = []
                for j, other in enumerate(concepts):
                    if i != j:
                        related.append(other.name)
                c.related_concepts = related[:5]

        return concepts

    def _extract_rules(
        self, raw_output: str, query: ResearchQuery
    ) -> List[ConstraintRule]:
        """Extract L2 constraint rules from research output."""
        rules: List[ConstraintRule] = []

        heuristic_rules = self._generate_heuristic_rules(query)
        for i, rule_line in enumerate(heuristic_rules):
            match = re.match(r'\*\*(.+?)\*\*\s*[-:]\s*(.+)', rule_line)
            if match:
                name = match.group(1).strip()
                rule_text = match.group(2).strip()
                rules.append(ConstraintRule(
                    name=name,
                    rule=rule_text,
                    severity="warning",
                    auto_check=False,
                    confidence=0.6,
                ))
            elif rule_line.startswith("- "):
                parts = rule_line[2:].split(":", 1)
                if len(parts) == 2:
                    rules.append(ConstraintRule(
                        name=parts[0].strip(),
                        rule=parts[1].strip(),
                        severity="info",
                        auto_check=False,
                        confidence=0.5,
                    ))

        return rules

    def _suggest_mappings(
        self, concepts: List[DomainConcept], query: ResearchQuery
    ) -> List[ComponentMapping]:
        """Suggest L3 component mappings from extracted concepts."""
        mappings: List[ComponentMapping] = []

        for concept in concepts[:5]:
            # Convert concept name to snake_case module/file name
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', concept.name.lower())
            safe_name = re.sub(r'_+', '_', safe_name).strip('_')

            mappings.append(ComponentMapping(
                concept_name=concept.name,
                target_module=f"src.services.{safe_name}_service",
                target_file=f"src/services/{safe_name}_service.py",
                interface=f"class {concept.name.replace(' ', '')}Service",
                confidence=0.5,
                status="planned",
                notes=f"Auto-suggested from {self.agent_type.name} research",
            ))

        return mappings

    def _generate_heuristic_findings(self, query: ResearchQuery) -> List[str]:
        """Generate heuristic findings based on agent type."""
        topic = query.topic

        if self.agent_type == ResearchAgentType.DOMAIN:
            return [
                f"- Core domain entities identified for '{topic}'",
                f"- Business workflows and processes mapped",
                f"- Domain terminology and glossary compiled",
                f"- Stakeholder and actor identification",
            ]
        elif self.agent_type == ResearchAgentType.TECH:
            return [
                f"- Technology stack recommendations for '{topic}'",
                f"- API design patterns and best practices identified",
                f"- Architecture patterns evaluation (microservices, monolith, etc.)",
                f"- Performance and scalability considerations",
                f"- Security requirements and compliance standards",
            ]
        else:  # COMPETITOR
            return [
                f"- Market landscape analysis for '{topic}'",
                f"- Top 5 competitor solutions identified",
                f"- Feature comparison matrix compiled",
                f"- Pricing and business model analysis",
                f"- SWOT analysis of leading competitors",
            ]

    def _generate_heuristic_concepts(self, query: ResearchQuery) -> List[str]:
        """Generate heuristic L1 concepts from query topic."""
        topic = query.topic.lower()

        if self.agent_type == ResearchAgentType.DOMAIN:
            return [
                f"**CoreEntity**: Primary business entity for {query.topic}",
                f"**Workflow**: Standard business process flow",
                f"**Actor**: System stakeholder or user role",
                f"**BusinessRule**: Domain-specific business rule",
                f"**ValueObject**: Immutable domain value type",
            ]
        elif self.agent_type == ResearchAgentType.TECH:
            return [
                f"**Architecture**: System architecture pattern for {query.topic}",
                f"**DataModel**: Core data model and schema design",
                f"**API**: API design and endpoint specification",
                f"**Auth**: Authentication and authorization mechanism",
                f"**Storage**: Data persistence and storage strategy",
            ]
        else:  # COMPETITOR
            return [
                f"**MarketSegment**: Target market segment for {query.topic}",
                f"**CompetitorProfile**: Key competitor profile",
                f"**FeatureGap**: Feature gap analysis",
                f"**PricingModel**: Competitive pricing model",
                f"**USP**: Unique selling proposition",
            ]

    def _generate_heuristic_rules(self, query: ResearchQuery) -> List[str]:
        """Generate heuristic L2 constraint rules."""
        if self.agent_type == ResearchAgentType.DOMAIN:
            return [
                f"**DataIntegrity**: All domain entities must have unique identifiers",
                f"**ValidationRule**: Business data must pass validation before persistence",
                f"**AuditTrail**: All state changes must be logged for audit purposes",
            ]
        elif self.agent_type == ResearchAgentType.TECH:
            return [
                f"**CodeQuality**: All code must pass linting and formatting checks",
                f"**TestCoverage**: Minimum 80% test coverage required",
                f"**SecurityScan**: No critical or high vulnerabilities in dependencies",
                f"**PerformanceSLA**: API response time < 200ms at p95",
            ]
        else:  # COMPETITOR
            return [
                f"**FeatureParity**: Core features must match or exceed top 3 competitors",
                f"**UXBaseline**: User experience must meet industry UX benchmarks",
                f"**MarketDiff**: Product must have at least 3 clear differentiators",
            ]


# ═══════════════════════════════════════════════════════════════
# ResearchAgentDispatcher
# ═══════════════════════════════════════════════════════════════

class ResearchAgentDispatcher:
    """Parallel research agent dispatcher.

    Delegates research to 3 agent types (domain/tech/competitor) in parallel,
    then aggregates and deduplicates all results into a single KnowledgeGraph.

    Features:
      - Parallel execution using ThreadPoolExecutor
      - Configurable timeout per agent
      - Aggregation of all L1-L3 nodes into KnowledgeGraph
      - Source deduplication across agents
      - Concept/rule/mapping deduplication
      - Deep-research skill integration via callback

    Usage::

        dispatcher = ResearchAgentDispatcher(
            max_workers=3,
            agent_timeout=300,
        )
        query = ResearchQuery(
            topic="E-commerce order management system",
            language="en",
            depth=3,
        )
        kg = dispatcher.dispatch(query)
        print(f"Research complete: {kg.stats}")
    """

    def __init__(
        self,
        max_workers: int = 3,
        agent_timeout: float = 300.0,
        deep_research_callback: Optional[Callable[[str, Dict[str, Any]], str]] = None,
    ):
        """Initialize the dispatcher.

        Args:
            max_workers: Maximum number of parallel research workers (default: 3).
            agent_timeout: Maximum seconds per research agent (default: 300).
            deep_research_callback: Optional callback for deep-research skill.
                Signature: (query_str: str, context: Dict) → str.
                When provided, all agents use this callback instead of heuristics.
        """
        self.max_workers = max_workers
        self.agent_timeout = agent_timeout
        self._deep_research_callback = deep_research_callback
        self._deduplicator = Deduplicator()

    def dispatch(self, query: ResearchQuery) -> KnowledgeGraph:
        """Dispatch research to all configured agent types in parallel.

        Args:
            query: Research query specification.

        Returns:
            Aggregated and deduplicated KnowledgeGraph with all findings.
        """
        t0 = time.monotonic()
        kg = KnowledgeGraph()
        all_results: List[ResearchResult] = []

        # Dispatch to each agent type in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for agent_type in query.agent_types:
                agent = ResearchAgent(
                    agent_type=agent_type,
                    deep_research_callback=self._deep_research_callback,
                )
                future = executor.submit(agent.research, query)
                futures[future] = agent_type

            # Collect results as they complete
            for future in as_completed(futures):
                agent_type = futures[future]
                try:
                    result = future.result(timeout=self.agent_timeout)
                    all_results.append(result)
                    logger.info(
                        "Agent %s completed in %.0fms: %d concepts, %d rules, %d sources",
                        agent_type.name,
                        result.elapsed_ms,
                        len(result.concepts),
                        len(result.rules),
                        len(result.sources),
                    )
                except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
                    logger.error("Agent %s failed: %s", agent_type.name, e)
                    all_results.append(ResearchResult(
                        agent_type=agent_type,
                        query=query.topic,
                        success=False,
                        error=str(e),
                    ))

        # Aggregate into KnowledgeGraph
        self._aggregate_results(kg, all_results)

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            "Dispatcher completed in %.0fms: %s",
            elapsed,
            kg.stats,
        )

        return kg

    def dispatch_sequential(
        self, query: ResearchQuery
    ) -> Tuple[KnowledgeGraph, List[ResearchResult]]:
        """Dispatch research sequentially (for debugging/single-threaded envs).

        Args:
            query: Research query specification.

        Returns:
            Tuple of (KnowledgeGraph, list of individual ResearchResults).
        """
        kg = KnowledgeGraph()
        all_results: List[ResearchResult] = []

        for agent_type in query.agent_types:
            agent = ResearchAgent(
                agent_type=agent_type,
                deep_research_callback=self._deep_research_callback,
            )
            result = agent.research(query)
            all_results.append(result)

        self._aggregate_results(kg, all_results)
        return kg, all_results

    def _aggregate_results(
        self, kg: KnowledgeGraph, results: List[ResearchResult]
    ) -> None:
        """Aggregate all research results into a KnowledgeGraph.

        Steps:
          1. Collect all concepts, rules, mappings from all results
          2. Deduplicate each level
          3. Add deduplicated nodes to the KnowledgeGraph
        """
        all_concepts: List[DomainConcept] = []
        all_rules: List[ConstraintRule] = []
        all_mappings: List[ComponentMapping] = []
        all_sources: List[ResearchSource] = []

        for result in results:
            if not result.success:
                continue
            all_concepts.extend(result.concepts)
            all_rules.extend(result.rules)
            all_mappings.extend(result.mappings)
            all_sources.extend(result.sources)

        # Deduplicate
        deduped_concepts = self._deduplicator.deduplicate_concepts(all_concepts)
        deduped_rules = self._deduplicator.deduplicate_rules(all_rules)
        deduped_mappings = self._deduplicator.deduplicate_mappings(all_mappings)
        deduped_sources = self._deduplicator.deduplicate_sources(all_sources)

        # Add to KnowledgeGraph
        for concept in deduped_concepts:
            # Annotate source URLs from deduped sources
            if not concept.source_url:
                matching_sources = [
                    s for s in deduped_sources
                    if concept.name.lower() in s.snippet.lower()
                       or concept.name.lower() in s.title.lower()
                ]
                if matching_sources:
                    concept.source_url = matching_sources[0].url

            kg.add_concept(concept)

        for rule in deduped_rules:
            # Link rules to concepts
            if not rule.related_concepts:
                rule.related_concepts = [
                    c.name for c in deduped_concepts
                ][:3]
            kg.add_rule(rule)

        for mapping in deduped_mappings:
            kg.add_mapping(mapping)

        # Store metadata about the research
        total_sources = sum(r.total_sources_found for r in results)
        elapsed_total = sum(r.elapsed_ms for r in results)

        logger.debug(
            "Aggregated: %d concepts, %d rules, %d mappings from %d sources "
            "(%d raw before dedup, total %d unique sources, %.0fms total agent time)",
            len(deduped_concepts),
            len(deduped_rules),
            len(deduped_mappings),
            len(deduped_sources),
            len(all_sources),
            total_sources,
            elapsed_total,
        )

    def get_summary(self, kg: KnowledgeGraph) -> Dict[str, Any]:
        """Get a human-readable summary of the research results.

        Args:
            kg: The aggregated KnowledgeGraph from dispatch().

        Returns:
            Dict with stats and summary information.
        """
        return {
            "stats": kg.stats,
            "average_confidence": kg.average_confidence,
            "total_nodes": len(kg),
            "concepts": [c.name for c in kg.concepts],
            "rules": [r.name for r in kg.rules],
            "mappings": [m.concept_name for m in kg.mappings],
        }


# ═══════════════════════════════════════════════════════════════
# Deep Research Integration
# ═══════════════════════════════════════════════════════════════

def deep_research_integration(
    query: ResearchQuery,
    deep_research_fn: Callable[[str, Dict[str, Any]], str],
    max_workers: int = 3,
) -> KnowledgeGraph:
    """Convenience function: run research with deep-research skill integration.

    This is the primary integration point with the deep-research skill.
    Pass a callable that invokes the deep-research skill and returns
    structured text output.

    Args:
        query: Research query specification.
        deep_research_fn: Function that performs deep research.
            Called as: deep_research_fn(prompt: str, context: Dict) → str
        max_workers: Number of parallel agents (default: 3).

    Returns:
        Aggregated KnowledgeGraph.

    Example using Hermes deep-research skill::

        def my_deep_research(prompt: str, context: Dict) -> str:
            # Invoke the deep-research MCP tool or skill
            return invoke_skill("deep-research", prompt=prompt)

        query = ResearchQuery(topic="Microservices patterns", language="en")
        kg = deep_research_integration(query, my_deep_research)
    """
    dispatcher = ResearchAgentDispatcher(
        max_workers=max_workers,
        deep_research_callback=deep_research_fn,
    )
    return dispatcher.dispatch(query)


# ═══════════════════════════════════════════════════════════════
# Module-level convenience function
# ═══════════════════════════════════════════════════════════════

def quick_research(
    topic: str,
    language: str = "en",
    depth: int = 2,
    agent_types: Optional[List[ResearchAgentType]] = None,
    deep_research_fn: Optional[Callable[[str, Dict[str, Any]], str]] = None,
) -> KnowledgeGraph:
    """Quick one-shot research: create query, dispatch, return KnowledgeGraph.

    Args:
        topic: Research topic or question.
        language: Language code (default: 'en').
        depth: Research depth 1-5 (default: 2).
        agent_types: Agent types to dispatch (default: all three).
        deep_research_fn: Optional deep-research callback.

    Returns:
        Aggregated KnowledgeGraph.

    Example::

        kg = quick_research("REST API best practices", depth=2)
        print(kg.stats)
    """
    query = ResearchQuery(
        topic=topic,
        language=language,
        depth=depth,
        agent_types=agent_types or list(ResearchAgentType),
    )
    dispatcher = ResearchAgentDispatcher(
        deep_research_callback=deep_research_fn,
    )
    return dispatcher.dispatch(query)
