#!/usr/bin/env python3
"""
测试 adapters.py 中的集成功能
主要验证 CodeWhaleAdapter 和 QwenCodeAdapter 的 build_input 方法
是否正确生成包含结构化知识上下文的 prompt
"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from adapters import CodeWhaleAdapter, QwenCodeAdapter


def test_codewhale_build_input():
    """测试 CodeWhaleAdapter.build_input() 生成的 prompt"""
    print("Testing CodeWhaleAdapter.build_input()...")
    
    results = []
    
    # 测试 review 类型任务
    adapter = CodeWhaleAdapter()
    task = "Review the authentication module"
    context = {"task_type": "review", "diff": "sample diff"}
    
    prompt = adapter.build_input(task, context)
    
    # 检查是否包含技能加载指令
    has_skill_loading = "[SKILL LOADING" in prompt
    # 检查是否包含结构化知识上下文
    has_structured_context = "专业技能上下文" in prompt or "Phase:" in prompt
    
    results.append({
        "name": "CodeWhaleAdapter.review_build_input_has_skill_loading",
        "status": "PASS" if has_skill_loading else "FAIL",
        "message": f"Prompt contains skill loading: {has_skill_loading}"
    })
    
    results.append({
        "name": "CodeWhaleAdapter.review_build_input_has_structured_context",
        "status": "PASS" if has_structured_context else "FAIL",
        "message": f"Prompt contains structured context: {has_structured_context}"
    })
    
    # 测试 audit 类型任务
    context_audit = {"task_type": "audit"}
    prompt_audit = adapter.build_input("Audit security", context_audit)
    
    has_audit_context = "独立审计" in prompt_audit or "审计" in prompt_audit
    results.append({
        "name": "CodeWhaleAdapter.audit_build_input_has_audit_context",
        "status": "PASS" if has_audit_context else "FAIL",
        "message": f"Audit prompt contains audit context: {has_audit_context}"
    })
    
    return results


def test_qwen_build_input():
    """测试 QwenCodeAdapter.build_input() 生成的 prompt"""
    print("Testing QwenCodeAdapter.build_input()...")
    
    results = []
    
    # 测试不同任务类型
    adapter = QwenCodeAdapter()
    
    # 测试 test 类型任务
    context_test = {"task_type": "test", "test_type": "unit"}
    prompt_test = adapter.build_input("Write unit tests", context_test)
    
    has_test_context = "[SKILL LOADING" in prompt_test and "测试专业技能" in prompt_test
    results.append({
        "name": "QwenCodeAdapter.test_build_input_has_test_context",
        "status": "PASS" if has_test_context else "FAIL",
        "message": f"Test prompt contains test context: {has_test_context}"
    })
    
    # 测试 e2e 类型任务
    context_e2e = {"task_type": "e2e"}
    prompt_e2e = adapter.build_input("Write E2E tests", context_e2e)
    
    has_e2e_context = "[SKILL LOADING" in prompt_e2e and "E2E" in prompt_e2e
    results.append({
        "name": "QwenCodeAdapter.e2e_build_input_has_e2e_context",
        "status": "PASS" if has_e2e_context else "FAIL",
        "message": f"E2E prompt contains E2E context: {has_e2e_context}"
    })
    
    # 测试 review 类型任务
    context_review = {"task_type": "review"}
    prompt_review = adapter.build_input("Review code", context_review)
    
    has_review_context = "[SKILL LOADING" in prompt_review and "审查专业技能" in prompt_review
    results.append({
        "name": "QwenCodeAdapter.review_build_input_has_review_context",
        "status": "PASS" if has_review_context else "FAIL",
        "message": f"Review prompt contains review context: {has_review_context}"
    })
    
    # 测试 doc 类型任务
    context_doc = {"task_type": "doc"}
    prompt_doc = adapter.build_input("Write documentation", context_doc)
    
    has_doc_context = "[SKILL LOADING" in prompt_doc and "技术文档专业技能" in prompt_doc
    results.append({
        "name": "QwenCodeAdapter.doc_build_input_has_doc_context",
        "status": "PASS" if has_doc_context else "FAIL",
        "message": f"Doc prompt contains doc context: {has_doc_context}"
    })
    
    return results


def test_task_type_mapping():
    """测试 task_type 是否正确映射到 phase"""
    print("Testing task_type to phase mapping...")
    
    results = []
    
    # 这里我们主要是验证映射逻辑是否按预期工作
    # 由于映射在内部实现中，我们将通过检查输出来间接验证
    
    adapter = CodeWhaleAdapter()
    prompt = adapter.build_input("Review code", {"task_type": "review"})
    
    # 检查是否包含 decompose phase 的内容（因为 review 映射到 decompose）
    has_decompose_context = "decompose" in prompt.lower()
    results.append({
        "name": "CodeWhaleAdapter.task_type_review_maps_to_decompose",
        "status": "PASS" if has_decompose_context else "FAIL",
        "message": f"Review task includes decompose context: {has_decompose_context}"
    })
    
    adapter_qwen = QwenCodeAdapter()
    prompt_qwen = adapter_qwen.build_input("Run tests", {"task_type": "test"})
    
    # 检查是否包含 test phase 的内容
    has_test_phase_context = "test" in prompt_qwen.lower()
    results.append({
        "name": "QwenCodeAdapter.task_type_test_maps_to_test",
        "status": "PASS" if has_test_phase_context else "FAIL",
        "message": f"Test task includes test context: {has_test_phase_context}"
    })
    
    return results


def main():
    """运行所有集成测试"""
    print("="*50)
    print("Running adapters integration tests...")
    print("="*50)
    
    all_results = []
    all_results.extend(test_codewhale_build_input())
    all_results.extend(test_qwen_build_input())
    all_results.extend(test_task_type_mapping())
    
    # 统计结果
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    failed = sum(1 for r in all_results if r["status"] == "FAIL")
    
    print("\n" + "="*50)
    print("INTEGRATION TEST RESULTS SUMMARY:")
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