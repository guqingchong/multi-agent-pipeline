#!/usr/bin/env python3
"""
测试 ProjectProfile 功能
"""

import os
import tempfile
from pathlib import Path
from src.project_profile import ProjectProfile, get_project_profile

def test_project_profile():
    """测试 ProjectProfile 功能"""
    print("开始测试 ProjectProfile...")
    
    # 创建临时目录用于测试
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # 创建一些测试文件
        (temp_path / "main.py").write_text("# Main Python file")
        (temp_path / "utils.js").write_text("// JS utility file")
        (temp_path / "test_main.py").write_text("# Test file")
        (temp_path / "spec_helper.rb").write_text("# Ruby spec helper")
        (temp_path / "README.md").write_text("# README")
        
        # 创建子目录和更多文件
        subdir = temp_path / "subdir"
        subdir.mkdir()
        (subdir / "module.cpp").write_text("// C++ module")
        (subdir / "test_module.py").write_text("# Another test file")
        
        # 创建项目配置文件
        features_json = {
            "source_extensions": [".py", ".js", ".cpp", ".rb"],
            "test_patterns": ["*test*", "spec*"]
        }
        
        import json
        with open(temp_path / "features.json", 'w', encoding='utf-8') as f:
            json.dump(features_json, f)
        
        # 测试 ProjectProfile
        profile = ProjectProfile(str(temp_path))
        
        print(f"源代码扩展名: {profile.source_extensions}")
        print(f"测试文件模式: {profile.test_patterns}")
        
        source_files = profile.get_source_files()
        print(f"发现源代码文件: {len(source_files)} 个")
        for f in source_files:
            print(f"  - {f}")
        
        test_files = profile.get_test_files()
        print(f"发现测试文件: {len(test_files)} 个")
        for f in test_files:
            print(f"  - {f}")
        
        # 验证结果 - 我们期望找到所有带有所列扩展名的文件作为源代码文件
        # test_main.py, spec_helper.rb, main.py, utils.js, module.cpp - 总共5个（test文件也是源码）
        # 另外subdir/test_module.py也是一个源码文件，所以总共6个
        assert len(source_files) == 6, f"期望找到6个源码文件，实际找到{len(source_files)}"
        
        # 对于测试文件，我们期望找到任何匹配 *test* 或 spec* 模式的文件
        assert len(test_files) == 3, f"期望找到3个测试文件，实际找到{len(test_files)}"
        
        print("ProjectProfile 测试通过!")
    
    print("所有测试通过!")


def test_check_functions():
    """测试 check_develop 和 check_test 函数"""
    print("\n开始测试 check 函数...")
    
    # 创建一个模拟项目环境
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # 创建必要的项目结构
        (temp_path / "src").mkdir()
        (temp_path / "tests").mkdir()
        (temp_path / "src" / "main.py").write_text("# Main Python file")
        (temp_path / "tests" / "test_main.py").write_text("# Test file")
        (temp_path / "features.json").write_text('{"project": "test", "features": []}')
        (temp_path / "progress.md").write_text("# Progress\nDevelop phase completed")
        
        # 初始化 git
        os.system(f'cd "{temp_path}" && git init')
        os.system(f'cd "{temp_path}" && git config user.name "Test User"')
        os.system(f'cd "{temp_path}" && git config user.email "test@example.com"')
        os.system(f'cd "{temp_path}" && git add .')
        os.system(f'cd "{temp_path}" && git commit -m "Initial commit"')
        
        # 导入并测试 check 函数
        from src.phase_checks import check_develop, check_test
        
        project_name = "test_project"
        base_dir = temp_path.parent
        project_path = temp_path
        
        # 将临时目录重命名为项目名称
        project_path.rename(base_dir / project_name)
        project_path = base_dir / project_name
        
        # 运行 check_develop
        result_dev = check_develop(project_name, base_dir)
        print(f"check_develop 结果: {result_dev['passed']}")
        if not result_dev['passed']:
            print(f"  错误: {result_dev['reason']}")
        else:
            print("  check_develop 通过")
        
        # 运行 check_test
        result_test = check_test(project_name, base_dir)
        print(f"check_test 结果: {result_test['passed']}")
        if not result_test['passed']:
            print(f"  错误: {result_test['reason']}")
        else:
            print("  check_test 通过")


if __name__ == "__main__":
    test_project_profile()
    test_check_functions()