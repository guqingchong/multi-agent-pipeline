"""Smoke test for research_agent.py — W4-K02 verification."""

import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from research_agent import (
    ResearchAgentType,
    ResearchQuery,
    ResearchResult,
    ResearchSource,
    PlatformRouting,
    ResearchAgent,
    ResearchAgentDispatcher,
    Deduplicator,
    deep_research_integration,
    quick_research,
)
from knowledge_graph import (
    KnowledgeGraph,
    DomainConcept,
    ConstraintRule,
    ComponentMapping,
)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  — {detail}")

# ── 1. ResearchAgentType enum ─────────────────────────────────
print("\n=== ResearchAgentType ===")
check("DOMAIN exists", hasattr(ResearchAgentType, "DOMAIN"))
check("TECH exists", hasattr(ResearchAgentType, "TECH"))
check("COMPETITOR exists", hasattr(ResearchAgentType, "COMPETITOR"))
check("three values", len(ResearchAgentType) == 3)

# ── 2. ResearchQuery dataclass ────────────────────────────────
print("\n=== ResearchQuery ===")
q = ResearchQuery(topic="E-commerce system")
check("default language", q.language == "en")
check("default depth", q.depth == 2)
check("default max_sources", q.max_sources == 10)
check("default agent_types has all 3", len(q.agent_types) == 3)
check("depth clamp min", ResearchQuery(topic="t", depth=0).depth == 1)
check("depth clamp max", ResearchQuery(topic="t", depth=10).depth == 5)
check("max_sources clamp min", ResearchQuery(topic="t", max_sources=0).max_sources == 1)

q_custom = ResearchQuery(
    topic="微服务架构",
    language="zh",
    depth=3,
    max_sources=20,
    agent_types=[ResearchAgentType.TECH, ResearchAgentType.DOMAIN],
    domain_hint="software",
)
check("custom language zh", q_custom.language == "zh")
check("custom depth 3", q_custom.depth == 3)
check("custom agent_types", len(q_custom.agent_types) == 2)

# ── 3. ResearchSource dataclass ───────────────────────────────
print("\n=== ResearchSource ===")
rs = ResearchSource(
    url="https://github.com/example/repo",
    title="Example Repo",
    snippet="A great example",
    platform="github.com",
    relevance_score=0.9,
    date="2024-01-15",
)
check("ResearchSource url", rs.url == "https://github.com/example/repo")
check("ResearchSource to_dict", rs.to_dict()["url"] == "https://github.com/example/repo")
rs2 = ResearchSource.from_dict(rs.to_dict())
check("ResearchSource roundtrip", rs2.title == "Example Repo" and rs2.platform == "github.com")

# ── 4. PlatformRouting ────────────────────────────────────────
print("\n=== PlatformRouting ===")
router = PlatformRouting()

# TECH routing
tech_platforms = router.route("OAuth2 implementation", ResearchAgentType.TECH)
check("TECH has github", "github.com" in tech_platforms)
check("TECH has stackoverflow", "stackoverflow.com" in tech_platforms)
check("TECH respects max", len(tech_platforms) <= 8)

# DOMAIN routing
domain_platforms = router.route("Quantum computing basics", ResearchAgentType.DOMAIN)
check("DOMAIN has arxiv", "arxiv.org" in domain_platforms)
check("DOMAIN has scholar", "scholar.google.com" in domain_platforms)

# COMPETITOR routing
comp_platforms = router.route("CRM software market", ResearchAgentType.COMPETITOR)
check("COMPETITOR has g2", "g2.com" in comp_platforms)
check("COMPETITOR has producthunt", "producthunt.com" in comp_platforms)

# Chinese language routing
zh_platforms = router.route("Python 异步编程", ResearchAgentType.TECH, language="zh")
check("ZH has zhihu", "zhihu.com" in zh_platforms)
check("ZH has wechat", "weixin.qq.com" in zh_platforms)

# Keyword prioritization
gh_platforms = router.route("github actions CI", ResearchAgentType.TECH)
check("github keyword promotes github to front", gh_platforms[0] == "github.com")

paper_platforms = router.route("academic paper on ML", ResearchAgentType.DOMAIN)
check("academic keyword promotes arxiv", paper_platforms[0] in ("arxiv.org", "scholar.google.com", "semanticscholar.org"))

# get_search_urls
search_urls = router.get_search_urls("OAuth2", ResearchAgentType.TECH)
check("search_urls returns list", isinstance(search_urls, list))
check("search_urls has github query", any("github.com" in u for u in search_urls))

# ── 5. Deduplicator ──────────────────────────────────────────
print("\n=== Deduplicator ===")
dedup = Deduplicator()

# Concept dedup
c1 = DomainConcept(name="Order", definition="Customer order", confidence=0.8)
c2 = DomainConcept(name="Order", definition="Customer purchase order", confidence=0.6, source_url="http://example.com")
deduped = dedup.deduplicate_concepts([c1, c2])
check("dedup concepts count", len(deduped) == 1)
check("dedup keeps highest confidence", deduped[0].confidence == 0.8)
check("dedup merges source_url", "http://example.com" in deduped[0].source_url)

# Different concepts
c3 = DomainConcept(name="Payment", definition="Payment processing")
deduped2 = dedup.deduplicate_concepts([c1, c3])
check("different concepts stay separate", len(deduped2) == 2)

# Rule dedup
r1 = ConstraintRule(name="TotalPositive", rule="Total > 0", confidence=0.8, auto_check=True)
r2 = ConstraintRule(name="TotalPositive", rule="Total must be > 0", confidence=0.5)
deduped_rules = dedup.deduplicate_rules([r1, r2])
check("dedup rules count", len(deduped_rules) == 1)
check("dedup rules keeps auto_check", deduped_rules[0].auto_check == True)

# Source dedup
s1 = ResearchSource(url="http://a.com", relevance_score=0.5)
s2 = ResearchSource(url="http://a.com", relevance_score=0.9)
s3 = ResearchSource(url="http://b.com", relevance_score=0.7)
deduped_srcs = dedup.deduplicate_sources([s1, s2, s3])
check("dedup sources count", len(deduped_srcs) == 2)
check("dedup sources keeps highest score", deduped_srcs[0].relevance_score == 0.9)

# Mapping dedup
m1 = ComponentMapping(concept_name="Order", target_module="src.order", confidence=0.7, status="planned")
m2 = ComponentMapping(concept_name="Order", target_module="src.orders", confidence=0.5, status="implemented")
deduped_maps = dedup.deduplicate_mappings([m1, m2])
check("dedup mappings count", len(deduped_maps) == 1)
check("dedup mappings keeps implemented status", deduped_maps[0].status == "implemented")

# _normalize_key
check("normalize spaces", dedup._normalize_key("Hello World") == "hello world")
check("normalize dashes", dedup._normalize_key("hello-world") == "hello world")
check("normalize underscores", dedup._normalize_key("hello_world") == "hello world")

# ── 6. ResearchAgent (heuristic mode) ─────────────────────────
print("\n=== ResearchAgent (heuristic) ===")
agent = ResearchAgent(ResearchAgentType.DOMAIN)
result = agent.research(ResearchQuery(topic="E-commerce order management", depth=2))
check("DOMAIN research success", result.success == True)
check("DOMAIN has concepts", len(result.concepts) >= 3)
check("DOMAIN has rules", len(result.rules) >= 2)
check("DOMAIN has mappings", len(result.mappings) >= 1)
check("DOMAIN has sources", len(result.sources) >= 1)
check("DOMAIN elapsed_ms > 0", result.elapsed_ms > 0)

tech_agent = ResearchAgent(ResearchAgentType.TECH)
tech_result = tech_agent.research(ResearchQuery(topic="Microservices API design"))
check("TECH research success", tech_result.success == True)
check("TECH has concepts", len(tech_result.concepts) >= 3)
check("TECH has rules", len(tech_result.rules) >= 2)

comp_agent = ResearchAgent(ResearchAgentType.COMPETITOR)
comp_result = comp_agent.research(ResearchQuery(topic="Project management tools"))
check("COMPETITOR research success", comp_result.success == True)
check("COMPETITOR has concepts", len(comp_result.concepts) >= 3)

# to_dict / from_dict roundtrip
rd = result.to_dict()
check("ResearchResult to_dict has agent_type", rd["agent_type"] == "DOMAIN")
result2 = ResearchResult.from_dict(rd)
check("ResearchResult roundtrip", result2.agent_type == ResearchAgentType.DOMAIN and result2.success)

# ── 7. ResearchAgentDispatcher (parallel) ─────────────────────
print("\n=== ResearchAgentDispatcher ===")
dispatcher = ResearchAgentDispatcher(max_workers=3)
query = ResearchQuery(topic="Online payment processing system", language="en", depth=2)
kg = dispatcher.dispatch(query)

check("dispatcher returns KnowledgeGraph", isinstance(kg, KnowledgeGraph))
check("kg has concepts", kg.stats["l1_concepts"] > 0)
check("kg has rules", kg.stats["l2_rules"] > 0)
check("kg has mappings", kg.stats["l3_mappings"] > 0)
check("kg len > 0", len(kg) > 0)

summary = dispatcher.get_summary(kg)
check("summary has stats", "stats" in summary)
check("summary has average_confidence", "average_confidence" in summary)
check("summary has concepts list", isinstance(summary["concepts"], list))
check("summary has rules list", isinstance(summary["rules"], list))

# ── 8. dispatch_sequential ────────────────────────────────────
print("\n=== dispatch_sequential ===")
kg2, all_results = dispatcher.dispatch_sequential(
    ResearchQuery(topic="REST API best practices", depth=1)
)
check("sequential returns KnowledgeGraph", isinstance(kg2, KnowledgeGraph))
check("sequential returns results list", len(all_results) == 3)
check("sequential kg has concepts", kg2.stats["l1_concepts"] > 0)

# ── 9. quick_research convenience ─────────────────────────────
print("\n=== quick_research ===")
kg3 = quick_research("GraphQL vs REST", depth=1)
check("quick_research returns KnowledgeGraph", isinstance(kg3, KnowledgeGraph))
check("quick_research has content", len(kg3) > 0)

# ── 10. Deep research callback integration ────────────────────
print("\n=== Deep research callback ===")

def mock_deep_research(prompt: str, context: dict) -> str:
    """Mock deep-research callback that returns structured output."""
    agent_type = context.get("agent_type", "UNKNOWN")
    return (
        f"# Research Results for {agent_type}\n"
        f"## Concepts\n"
        f"**CallbackConcept**: A concept from deep research callback\n"
        f"**AnotherConcept**: Another discovered concept https://example.com/cb\n"
        f"## Rules\n"
        f"**CallbackRule**: A rule from deep research\n"
        f"## Sources\n"
        f"https://deep-research.example.com/finding/1\n"
        f"https://deep-research.example.com/finding/2\n"
    )

dispatcher_cb = ResearchAgentDispatcher(
    max_workers=3,
    deep_research_callback=mock_deep_research,
)
query_cb = ResearchQuery(topic="Deep research test", depth=2)
kg_cb = dispatcher_cb.dispatch(query_cb)
check("callback kg has concepts", kg_cb.stats["l1_concepts"] > 0)
check("callback kg has rules", kg_cb.stats["l2_rules"] > 0)
check("callback kg has mappings", kg_cb.stats["l3_mappings"] > 0)
check("callback kg len > 0", len(kg_cb) > 0)

# Integration function
kg_int = deep_research_integration(query_cb, mock_deep_research)
check("deep_research_integration works", isinstance(kg_int, KnowledgeGraph))
check("integration has results", len(kg_int) > 0)

# ── 11. Chinese research ──────────────────────────────────────
print("\n=== Chinese research ===")
zh_query = ResearchQuery(topic="微服务架构最佳实践", language="zh", depth=1)
kg_zh = quick_research("微服务架构最佳实践", language="zh", depth=1)
check("Chinese research works", isinstance(kg_zh, KnowledgeGraph))
check("Chinese kg has concepts", kg_zh.stats["l1_concepts"] > 0)

# ── 12. Edge cases ────────────────────────────────────────────
print("\n=== Edge cases ===")

# Empty query
eq = ResearchQuery(topic="")
eq_kg = quick_research("", depth=1)
check("empty topic works", isinstance(eq_kg, KnowledgeGraph))

# Single agent type
single_query = ResearchQuery(
    topic="Something",
    agent_types=[ResearchAgentType.TECH],
)
single_kg = dispatcher.dispatch(single_query)
check("single agent type dispatch", isinstance(single_kg, KnowledgeGraph))

# ResearchResult from_dict with bad agent_type
bad_result = ResearchResult.from_dict({"agent_type": "NONEXISTENT", "success": False})
check("ResearchResult bad agent_type defaults to DOMAIN", bad_result.agent_type == ResearchAgentType.DOMAIN)

# ── Summary ───────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
if FAIL == 0:
    print("ALL TESTS PASSED ✅")
else:
    print(f"SOME TESTS FAILED ❌ ({FAIL} failures)")
