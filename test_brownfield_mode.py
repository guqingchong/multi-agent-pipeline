#!/usr/bin/env python3
"""测试 pipeline brownfield 模式插件"""

import json
import os
import tempfile
from pathlib import Path

from src.config import PipelineConfig
from src.phase_checks import CHECK_REGISTRY, run_check
from src.bridge_cli import cmd_mode


def test_detect_mode_chengcetong2():
    """测试1: config.py detect_mode对chengcetong2返回brownfield"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # 创建测试项目目录
        project_dir = tmpdir_path / "chengcetong2"
        project_dir.mkdir()
        
        # 创建 features.json 并添加 passed 状态的 feature
        features_file = project_dir / "features.json"
        features_data = {
            "project": "chengcetong2",
            "features": [
                {"id": "F001", "status": "passed"},
                {"id": "F002", "status": "pending"}
            ]
        }
        features_file.write_text(json.dumps(features_data, ensure_ascii=False), encoding='utf-8')
        
        # 测试 detect_mode
        result = PipelineConfig.detect_mode(project_dir)
        assert result == "brownfield", f"期望 brownfield，实际得到 {result}"
        print("✓ 测试1通过: detect_mode 对 chengcetong2 返回 brownfield")


def test_phase_checks_functions():
    """测试2: phase_checks.py 7个check函数可被run_check调用且返回正确bool"""
    # 检查 greenfield 模式的函数
    greenfield_phases = ["init", "design", "decompose", "develop", "test", "accept", "deploy"]
    
    # 检查 brownfield 模式的函数
    brownfield_phases = ["discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"]
    
    all_phases = greenfield_phases + brownfield_phases
    
    # 验证所有函数都在注册表中
    for phase in all_phases:
        assert phase in CHECK_REGISTRY, f"Phase {phase} 不在 CHECK_REGISTRY 中"
    
    # 验证 run_check 可以调用这些函数并返回正确格式的结果
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        project_dir = tmpdir_path / "test_project"
        project_dir.mkdir()
        
        for phase in all_phases:
            result = run_check(phase, "test_project", tmpdir_path)
            
            # 验证返回结果格式
            assert isinstance(result, dict), f"Phase {phase} 的返回值不是字典"
            assert "passed" in result, f"Phase {phase} 的返回值缺少 'passed' 键"
            assert isinstance(result["passed"], bool), f"Phase {phase} 的 'passed' 值不是布尔类型"
            assert "reason" in result, f"Phase {phase} 的返回值缺少 'reason' 键"
            
    print(f"✓ 测试2通过: {len(all_phases)} 个 check 函数都可被调用且返回正确格式")


def test_bridge_cli_mode_command():
    """测试3: bridge_cli.py mode命令输出包含detected_mode和available_modes"""
    # 测试无参数的 mode 命令
    result = cmd_mode()
    assert "current_mode" in result, "mode 命令输出缺少 'current_mode'"
    assert "available_modes" in result, "mode 命令输出缺少 'available_modes'"
    assert isinstance(result["available_modes"], list), "'available_modes' 不是列表"
    
    # 测试带项目名的 mode 命令
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        project_dir = tmpdir_path / "test_project"
        project_dir.mkdir()
        
        # 创建 features.json 使检测为 brownfield
        features_file = project_dir / "features.json"
        features_data = {
            "project": "test_project",
            "features": [{"id": "F001", "status": "passed"}]
        }
        features_file.write_text(json.dumps(features_data, ensure_ascii=False), encoding='utf-8')
        
        # 保存原始环境变量
        original_env = os.environ.get("MULTI_AGENT_PIPELINE_BASE_DIR")
        
        try:
            # 设置环境变量以确保 cmd_mode 使用正确的基础目录
            os.environ["MULTI_AGENT_PIPELINE_BASE_DIR"] = str(tmpdir_path)
            
            result_with_project = cmd_mode("test_project")
            assert "detected_mode" in result_with_project, "mode 命令输出缺少 'detected_mode'"
            assert "available_modes" in result_with_project, "mode 命令输出缺少 'available_modes'"
            assert result_with_project["detected_mode"] == "brownfield", f"期望检测到 brownfield，实际得到 {result_with_project['detected_mode']}"
            assert isinstance(result_with_project["available_modes"], list), "'available_modes' 不是列表"
        finally:
            # 恢复原始环境变量
            if original_env is not None:
                os.environ["MULTI_AGENT_PIPELINE_BASE_DIR"] = original_env
            else:
                os.environ.pop("MULTI_AGENT_PIPELINE_BASE_DIR", None)
        
    print("✓ 测试3通过: mode 命令输出包含 detected_mode 和 available_modes")


def test_phase_order_isolation():
    """测试4: greenfield/brownfield双模式phase_order隔离"""
    # 获取不同模式下的 phase_order
    greenfield_config = PipelineConfig(pipeline_mode="greenfield")
    brownfield_config = PipelineConfig(pipeline_mode="brownfield")
    
    greenfield_phases = greenfield_config.phase_order
    brownfield_phases = brownfield_config.phase_order
    
    # 验证两种模式有不同的 phase_order
    assert greenfield_phases != brownfield_phases, "Greenfield 和 Brownfield 模式的 phase_order 相同"
    
    # 验证 greenfield 模式包含预期的 phases
    expected_greenfield = [
        "init", "design", "decompose", "research", "prd", "journey",
        "develop", "integrate", "test", "evaluate", "accept", "deploy"
    ]
    for phase in expected_greenfield:
        assert phase in greenfield_phases, f"Greenfield 模式缺少 phase: {phase}"
    
    # 验证 brownfield 模式包含预期的 phases
    expected_brownfield = ["discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"]
    for phase in expected_brownfield:
        assert phase in brownfield_phases, f"Brownfield 模式缺少 phase: {phase}"
    
    print("✓ 测试4通过: greenfield/brownfield 双模式 phase_order 隔离")


def main():
    """运行所有测试"""
    print("开始测试 pipeline brownfield 模式插件...")
    
    test_detect_mode_chengcetong2()
    test_phase_checks_functions()
    test_bridge_cli_mode_command()
    test_phase_order_isolation()
    
    print("\n🎉 所有测试通过！")
    print("通过的测试项数量: 4")


if __name__ == "__main__":
    main()