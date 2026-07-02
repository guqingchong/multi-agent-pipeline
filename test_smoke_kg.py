"""Smoke test for knowledge_graph.py — W4-K01 review."""

import sys
import json
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from knowledge_graph import (
    KnowledgeGraph,
    DomainConcept,
    ConstraintRule,
    ComponentMapping,
    CodeGenRule,
    Confidence,
    L1ValidationResult,
    L2ValidationResult,
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

# ── 1. Confidence ────────────────────────────────────────────
print("\n=== Confidence ===")
check("parse VERIFIED", Confidence.parse("VERIFIED") == 1.0)
check("parse HIGH", Confidence.parse("HIGH") == 0.8)
check("parse MEDIUM", Confidence.parse("MEDIUM") == 0.6)
check("parse LOW", Confidence.parse("LOW") == 0.4)
check("parse UNKNOWN", Confidence.parse("UNKNOWN") == 0.2)
check("parse 0.75 float", Confidence.parse(0.75) == 0.75)
check("parse bogus string", Confidence.parse("BOGUS") == 0.5)
check("parse int 1", Confidence.parse(1) == 1.0)
check("parse out-of-range 1.5", Confidence.parse(1.5) == 1.0)
check("parse out-of-range -0.5", Confidence.parse(-0.5) == 0.0)

# ── 2. DomainConcept (L1) ───────────────────────────────────
print("\n=== L1 DomainConcept ===")
dc = DomainConcept(
    name="Order",
    definition="A customer purchase order",
    source_url="https://example.com/order",
    confidence="HIGH",
    related_concepts=["Customer", "Payment"],
    category="core_entity",
    notes="Aggregate root"
)
check("DomainConcept name", dc.name == "Order")
check("DomainConcept confidence parsed", dc.confidence == 0.8)
check("DomainConcept to_dict level=1", dc.to_dict()["level"] == 1)
dc2 = DomainConcept.from_dict(dc.to_dict())
check("DomainConcept roundtrip", dc2.name == "Order" and dc2.confidence == 0.8)

# ── 3. ConstraintRule (L2) ──────────────────────────────────
print("\n=== L2 ConstraintRule ===")
cr = ConstraintRule(
    name="OrderTotalPositive",
    rule="Order total must be > 0",
    severity="error",
    auto_check=True,
    expected_pattern=r"assert.*total\s*>\s*0|if\s+total\s*<=\s*0",
    file_glob="**/*.py",
    source_url="https://example.com/rules",
    confidence="HIGH",
    related_concepts=["Order"]
)
check("ConstraintRule name", cr.name == "OrderTotalPositive")
check("ConstraintRule severity error", cr.severity == "error")
check("ConstraintRule bad severity", ConstraintRule(name="X", rule="x", severity="BOGUS").severity == "warning")
check("ConstraintRule to_dict level=2", cr.to_dict()["level"] == 2)
cr2 = ConstraintRule.from_dict(cr.to_dict())
check("ConstraintRule roundtrip", cr2.name == "OrderTotalPositive" and cr2.auto_check)

# ── 4. ComponentMapping (L3) ─────────────────────────────────
print("\n=== L3 ComponentMapping ===")
cm = ComponentMapping(
    concept_name="Order",
    target_module="src.services.order_service",
    target_file="src/services/order_service.py",
    interface="class OrderService: create, get, cancel",
    status="implemented",
    confidence="MEDIUM"
)
check("ComponentMapping concept_name", cm.concept_name == "Order")
check("ComponentMapping bad status", ComponentMapping(concept_name="X", status="BOGUS").status == "planned")
check("ComponentMapping to_dict level=3", cm.to_dict()["level"] == 3)
cm2 = ComponentMapping.from_dict(cm.to_dict())
check("ComponentMapping roundtrip", cm2.concept_name == "Order" and cm2.status == "implemented")

# ── 5. CodeGenRule (L4) ─────────────────────────────────────
print("\n=== L4 CodeGenRule ===")
cgr = CodeGenRule(
    mapping_id="Order",
    template="class {{ class_name }}:\n    def __init__(self): ...",
    constraints=["OrderTotalPositive"],
    language="python",
    output_pattern="src/services/*.py",
    confidence="HIGH"
)
check("CodeGenRule mapping_id", cgr.mapping_id == "Order")
check("CodeGenRule to_dict level=4", cgr.to_dict()["level"] == 4)
cgr2 = CodeGenRule.from_dict(cgr.to_dict())
check("CodeGenRule roundtrip", cgr2.mapping_id == "Order" and cgr2.language == "python")

# ── 6. KnowledgeGraph CRUD ──────────────────────────────────
print("\n=== KnowledgeGraph CRUD ===")
kg = KnowledgeGraph()
kg.add_concept(dc)
kg.add_rule(cr)
kg.add_mapping(cm)
kg.add_codegen_rule(cgr)

check("stats l1", kg.stats["l1_concepts"] == 1)
check("stats l2", kg.stats["l2_rules"] == 1)
check("stats l3", kg.stats["l3_mappings"] == 1)
check("stats l4", kg.stats["l4_codegen_rules"] == 1)
check("len", len(kg) == 4)

check("get_concept", kg.get_concept("Order") is not None)
check("get_rule", kg.get_rule("OrderTotalPositive") is not None)
check("get_mapping", kg.get_mapping("Order") is not None)
check("get_codegen_rule", kg.get_codegen_rule("Order") is not None)

check("remove_concept False", kg.remove_concept("NotFound") == False)
check("remove_concept True", kg.remove_concept("Order") == True)
check("get_concept after remove", kg.get_concept("Order") is None)
kg.add_concept(dc)  # add back

check("concepts property list", len(kg.concepts) == 1)
check("rules property list", len(kg.rules) == 1)
check("mappings property list", len(kg.mappings) == 1)
check("codegen_rules property list", len(kg.codegen_rules) == 1)

# ── 7. Serialization ────────────────────────────────────────
print("\n=== Serialization ===")
d = kg.to_dict()
check("to_dict meta version", d["meta"]["version"] == "3.0")
check("to_dict l1 size", len(d["l1_concepts"]) == 1)
check("to_dict l2 size", len(d["l2_rules"]) == 1)
check("to_dict l3 size", len(d["l3_mappings"]) == 1)
check("to_dict l4 size", len(d["l4_codegen_rules"]) == 1)

kg2 = KnowledgeGraph.from_dict(d)
check("from_dict roundtrip stats", kg2.stats == kg.stats)

# JSON roundtrip
json_str = kg.to_json()
check("to_json returns str", isinstance(json_str, str))
kg3 = KnowledgeGraph.from_json(json_str)
check("from_json roundtrip", kg3.stats == kg.stats)

# JSON file roundtrip
with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
    f.write(json_str)
    json_path = f.name
kg.to_json(json_path)
kg4 = KnowledgeGraph.from_json(json_path)
check("to_json file roundtrip", kg4.stats == kg.stats)
os.unlink(json_path)

# YAML roundtrip
yaml_str = kg.to_yaml()
check("to_yaml returns str", isinstance(yaml_str, str))
kg5 = KnowledgeGraph.from_yaml(yaml_str)
check("from_yaml roundtrip", kg5.stats == kg.stats)

# YAML file roundtrip
with tempfile.NamedTemporaryFile(suffix=".yml", delete=False, mode="w") as f:
    yaml_path = f.name
kg.to_yaml(yaml_path)
kg6 = KnowledgeGraph.from_yaml(yaml_path)
check("to_yaml file roundtrip", kg6.stats == kg.stats)
os.unlink(yaml_path)

# ── 8. Merge ────────────────────────────────────────────────
print("\n=== Merge ===")
kg_a = KnowledgeGraph()
kg_a.add_concept(DomainConcept(name="A", definition="Concept A"))
kg_a.add_rule(ConstraintRule(name="RuleA", rule="Rule A"))

kg_b = KnowledgeGraph()
kg_b.add_concept(DomainConcept(name="B", definition="Concept B"))
kg_b.add_rule(ConstraintRule(name="RuleB", rule="Rule B"))

kg_a.merge(kg_b)
check("merge has both concepts", kg_a.get_concept("A") is not None and kg_a.get_concept("B") is not None)
check("merge has both rules", kg_a.get_rule("RuleA") is not None and kg_a.get_rule("RuleB") is not None)

# Merge override
kg_c = KnowledgeGraph()
kg_c.add_concept(DomainConcept(name="A", definition="Overridden"))
kg_a.merge(kg_c)
check("merge override", kg_a.get_concept("A").definition == "Overridden")

# ── 9. trace_concept_to_code ────────────────────────────────
print("\n=== trace_concept_to_code ===")
trace = kg.trace_concept_to_code("Order")
check("trace has concept", trace["concept"] is not None and trace["concept"]["name"] == "Order")
check("trace has constraint_rules", len(trace["constraint_rules"]) == 1)
check("trace has component_mappings", len(trace["component_mappings"]) == 1)
check("trace has codegen_rules", len(trace["codegen_rules"]) == 1)

check("trace non-existent concept", kg.trace_concept_to_code("NotFound")["concept"] is None)

# ── 10. validate_l2_rules ───────────────────────────────────
print("\n=== validate_l2_rules ===")

# Create a temp codebase with a file that matches the pattern
with tempfile.TemporaryDirectory() as tmpdir:
    codebase = Path(tmpdir)
    (codebase / "order.py").write_text("assert total > 0\n")
    
    # Test pattern-based validation
    results = kg.validate_l2_rules(codebase)
    check("validate_l2_rules returns list", isinstance(results, list))
    check("validate_l2_rules has 1 result", len(results) == 1)
    r = results[0]
    check("validate_l2 pattern passed", r.passed == True)
    check("validate_l2 method pattern", r.method == "pattern")
    
    # Test rule with auto_check=False
    kg.add_rule(ConstraintRule(name="ManualRule", rule="Manual", auto_check=False))
    results2 = kg.validate_l2_rules(codebase)
    # Should now have 2 results (pattern rule + manual rule)
    manual_results = [r for r in results2 if r.method == "manual"]
    check("validate_l2 manual rule present", len(manual_results) >= 1)
    manual = manual_results[0]
    check("validate_l2 manual passed", manual.passed == True)
    
    # Test invalid regex pattern
    kg.add_rule(ConstraintRule(
        name="BadRegex",
        rule="Bad regex",
        auto_check=True,
        expected_pattern="[invalid",
        file_glob="**/*.py"
    ))
    results3 = kg.validate_l2_rules(codebase)
    bad_results = [r for r in results3 if r.rule_name == "BadRegex"]
    check("validate_l2 bad regex fails", len(bad_results) == 1 and bad_results[0].passed == False)

# ── 11. Average confidence ──────────────────────────────────
print("\n=== Average confidence ===")
avg = kg.average_confidence
check("average_confidence has keys", all(k in avg for k in ["l1", "l2", "l3", "l4"]))
check("average_confidence l1 > 0", avg["l1"] > 0)

# ── 12. Clear ──────────────────────────────────────────────
print("\n=== Clear ===")
kg.clear()
check("clear zeroes everything", kg.stats == {"l1_concepts": 0, "l2_rules": 0, "l3_mappings": 0, "l4_codegen_rules": 0})

# ── 13. __repr__ ────────────────────────────────────────────
print("\n=== __repr__ ===")
r = repr(kg)
check("repr contains class name", "KnowledgeGraph" in r)

# ── Summary ────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
if FAIL == 0:
    print("ALL TESTS PASSED ✅")
else:
    print(f"SOME TESTS FAILED ❌ ({FAIL} failures)")
