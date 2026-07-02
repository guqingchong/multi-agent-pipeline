---
name: refactoring-old-constant-references
description: Methodology for identifying and replacing old constant references with modern registry-based equivalents in multi-agent systems
source: auto-skill
extracted_at: '2026-07-02T10:53:58.519Z'
---

# Refactoring Old Constant References in Multi-Agent Systems

## Overview
This methodology describes the systematic approach for identifying and replacing old hard-coded constants with dynamic references from a centralized registry in a multi-agent pipeline system.

## Context
Modern multi-agent systems require centralized configuration management through registries (like REGISTRY) rather than scattered constants. This refactoring ensures consistency, maintainability, and easier updates across the system.

## Process

### 1. Identify Target Files and Constants
- Scan the codebase for commonly misused constants like:
  - PHASE_NAMES (should reference REGISTRY.phases)
  - DEFAULT_ENDPOINTS (should be dynamically generated from REGISTRY)
  - TASK_ADAPTER_MAP (should use REGISTRY for mapping)
  - Phase lists like _GREENFIELD_PHASES (should align with REGISTRY)
  - Other hardcoded configuration values

### 2. Examine Current Implementation
- Read each file containing the old constants to understand how they're currently used
- Verify the new registry implementation exists and provides equivalent functionality
- Understand any dependencies between modules

### 3. Update References
- Replace direct constant usage with registry-based alternatives
- For example, replace `PHASE_NAMES` with `REGISTRY.phases` or `_get_core_phase_names()` function that accesses the registry
- Maintain backward compatibility where needed by updating helper functions

### 4. Handle Complex Mappings
- For complex mappings like TASK_ADAPTER_MAP, ensure the new implementation preserves all functionality
- Update all internal references to the old constants to use the new mapping
- Keep backward compatibility aliases where necessary

### 5. Clean Up Duplicate Definitions
- Remove redundant constants that are now accessible through the registry
- Consolidate similar definitions across files
- Ensure consistent naming conventions

### 6. Maintain Compatibility
- Keep backward compatibility functions/aliases during transition period
- Update all internal references to use new approach
- Consider deprecation warnings for public APIs

### 7. Verification
- Run comprehensive tests to ensure functionality remains intact
- Specifically test the areas where changes were made
- Validate that new registry-based approach works as expected

## Best Practices
- Always maintain backward compatibility during refactoring
- Update all related references when changing a constant
- Use helper functions to encapsulate registry access logic
- Keep detailed notes on what changed for future maintenance
- Verify that the registry contains all necessary values before removing constants