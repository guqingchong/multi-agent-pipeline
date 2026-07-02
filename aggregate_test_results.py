#!/usr/bin/env python3
"""
汇总所有测试结果为JSON格式
"""

import json
import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from test_skill_injector import main as run_skill_injector_tests
from test_adapters_integration import main as run_adapters_integration_tests
from test_boundary_conditions import main as run_boundary_tests


def aggregate_test_results():
    """运行所有测试并汇总结果"""
    print("Aggregating all test results...")
    
    # 运行所有测试套件
    skill_injector_results = run_skill_injector_tests()
    adapters_integration_results = run_adapters_integration_tests()
    boundary_condition_results = run_boundary_tests()
    
    # 合并所有测试详情
    all_details = []
    all_details.extend(skill_injector_results["details"])
    all_details.extend(adapters_integration_results["details"])
    all_details.extend(boundary_condition_results["details"])
    
    # 计算总体结果
    total_tests = (
        skill_injector_results["tests_run"] +
        adapters_integration_results["tests_run"] +
        boundary_condition_results["tests_run"]
    )
    total_passed = (
        skill_injector_results["passed"] +
        adapters_integration_results["passed"] +
        boundary_condition_results["passed"]
    )
    total_failed = (
        skill_injector_results["failed"] +
        adapters_integration_results["failed"] +
        boundary_condition_results["failed"]
    )
    
    final_result = {
        "success": total_failed == 0,
        "tests_run": total_tests,
        "passed": total_passed,
        "failed": total_failed,
        "details": all_details
    }
    
    return final_result


def main():
    """主函数：汇总并输出JSON结果"""
    results = aggregate_test_results()
    
    # 输出JSON格式的结果
    print("\n" + "="*50)
    print("FINAL AGGREGATED TEST RESULTS (JSON):")
    print("="*50)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    
    return results


if __name__ == "__main__":
    results = main()
    print(f"\nOverall result: {'SUCCESS' if results['success'] else 'FAILURE'}")
    sys.exit(0 if results['success'] else 1)