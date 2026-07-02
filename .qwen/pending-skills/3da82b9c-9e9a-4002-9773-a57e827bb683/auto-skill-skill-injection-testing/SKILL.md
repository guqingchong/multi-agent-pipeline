---
name: skill-injection-testing
description: Testing methodology for skill injection and integration in multi-agent pipeline
source: auto-skill
extracted_at: '2026-07-01T05:20:26.733Z'
---

# Skill Injection and Integration Testing Methodology

## Overview
Comprehensive testing approach for verifying skill injection and integration functionality in the multi-agent pipeline v3.0, focusing on the `skill_injector.py` and `adapters.py` components.

## Test Categories

### 1. Unit Tests for skill_injector.py
- Verify PHASE_SKILL_MAP contains all 14 correct mappings:
  - prd→product-manager
  - design→domain-driven-design
  - decompose→[product-manager, domain-driven-design]
  - develop→[domain-driven-design]
  - integrate→[domain-driven-design]
  - evaluate→[product-manager, domain-driven-design]
  - accept→[product-manager, domain-driven-design]
  - audit→[product-manager, domain-driven-design]
  - adversarial_review→[product-manager, domain-driven-design]
  - inspector→[product-manager, domain-driven-design]
  - journey/research→[product-manager]
  - test→[] (empty, injected via adapters)
- Validate SkillInjector.inject() returns SkillContext with frameworks≥4 and quality_gates≥2

### 2. Integration Tests for adapters.py
- Verify CodeWhaleAdapter/QwenCodeAdapter.build_input() generates prompts containing:
  - Structured knowledge context from SkillInjector
  - Hardcoded fallback instructions
  - task_type correctly mapped to phase

### 3. Boundary Condition Tests
- Empty skill lists (test→[])
- Non-existent skill names
- Non-existent phase names
- Disk file loading vs embedded knowledge fallback

## Implementation Approach

### Test Structure
```python
# Example test structure
def test_phase_skill_map():
    expected_mappings = {
        "prd": ["product-manager"],
        "design": ["domain-driven-design"],
        # ... additional mappings
    }
    
    for phase, expected_skills in expected_mappings.items():
        actual_skills = PHASE_SKILL_MAP.get(phase, [])
        assert actual_skills == expected_skills
```

### Validation Points
1. **Correctness**: All mappings in PHASE_SKILL_MAP are accurate
2. **Completeness**: SkillContext contains minimum required frameworks and quality gates
3. **Integration**: Adapters properly inject structured knowledge into prompts
4. **Robustness**: Boundary conditions handled appropriately

## Key Validation Checks
- `SkillInjector.build_context_prompt()` generates appropriate context strings
- Empty phases return empty context without errors
- Non-existent phases map to empty skill lists
- Embedded knowledge serves as fallback when disk loading fails
- Task types in adapters map to correct phases

## Success Criteria
- All unit tests pass (PHASE_SKILL_MAP accuracy)
- All integration tests pass (adapter prompt generation)
- All boundary tests pass (robustness)
- Overall test suite has 0 failures out of total tests