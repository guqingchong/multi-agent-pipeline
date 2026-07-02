#!/usr/bin/env python3
"""
测试 skill_injector.py 的功能
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from skill_injector import PHASE_SKILL_MAP, SkillInjector


def test_phase_skill_map():
    """测试 PHASE_SKILL_MAP 的映射关系"""
    print("Testing PHASE_SKILL_MAP...")
    
    expected_mappings = {
        "prd": ["product-manager"],
        "design": ["domain-driven-design"],
        "decompose": ["product-manager", "domain-driven-design"],
        "develop": ["domain-driven-design"],
        "integrate": ["domain-driven-design"],
        "evaluate": ["product-manager", "domain-driven-design"],
        "accept": ["product-manager", "domain-driven-design"],
        "audit": ["product-manager", "domain-driven-design"],
        "adversarial_review": ["product-manager", "domain-driven-design"],
        "inspector": ["product-manager", "domain-driven-design"],
        "journey": ["product-manager"],
        "research": ["product-manager"],
        "test": [],  # 空列表，由adapters注入
    }
    
    results = []
    
    for phase, expected_skills in expected_mappings.items():
        actual_skills = PHASE_SKILL_MAP.get(phase, [])
        success = actual_skills == expected_skills
        results.append({
            "name": f"PHASE_SKILL_MAP[{phase}]",
            "status": "PASS" if success else "FAIL",
            "message": f"Expected {expected_skills}, got {actual_skills}" if not success else "Correct mapping"
        })
        
    return results


def test_skill_injector():
    """测试 SkillInjector.inject() 返回的 SkillContext"""
    print("Testing SkillInjector.inject()...")
    
    results = []
    
    # 测试非空技能列表的阶段
    non_empty_phases = ["prd", "design", "decompose", "develop", "evaluate"]
    for phase in non_empty_phases:
        context = SkillInjector.inject(phase)
        frameworks_ok = len(context.frameworks) >= 4
        quality_gates_ok = len(context.quality_gates) >= 2
        
        success = frameworks_ok and quality_gates_ok
        results.append({
            "name": f"SkillContext[{phase}]",
            "status": "PASS" if success else "FAIL",
            "message": f"frameworks={len(context.frameworks)}({'>=4' if frameworks_ok else '<4'}), "
                       f"quality_gates={len(context.quality_gates)}({'>=2' if quality_gates_ok else '<2'})"
        })
    
    # 测试空技能列表的阶段 (test)
    context = SkillInjector.inject("test")
    success = len(context.frameworks) == 0 and len(context.quality_gates) == 0
    results.append({
        "name": f"SkillContext[test]",
        "status": "PASS" if success else "FAIL",
        "message": f"Empty context for test phase: frameworks={len(context.frameworks)}, "
                   f"quality_gates={len(context.quality_gates)}"
    })
    
    return results


def main():
    """运行所有测试"""
    print("="*50)
    print("Running skill_injector unit tests...")
    print("="*50)
    
    all_results = []
    all_results.extend(test_phase_skill_map())
    all_results.extend(test_skill_injector())
    
    # 统计结果
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    failed = sum(1 for r in all_results if r["status"] == "FAIL")
    
    print("\n" + "="*50)
    print("TEST RESULTS SUMMARY:")
    print(f"Total: {len(all_results)}, Passed: {passed}, Failed: {failed}")
    print("="*50)
    
    for result in all_results:
        print(f"{result['name']}: {result['status']} - {result['message']}")
    
    return {
        "success": failed == 0,
        "tests_run": len(all_results),
        "passed": passed,
        "failed": failed,
        "details": all_results
    }


if __name__ == "__main__":
    results = main()
    print(f"\nFinal result: {'SUCCESS' if results['success'] else 'FAILURE'}")
    sys.exit(0 if results['success'] else 1)