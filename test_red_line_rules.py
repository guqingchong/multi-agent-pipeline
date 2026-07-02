#!/usr/bin/env python3
"""
测试 red_line_rules.py 中的 PolicyComplianceChecker 功能
"""

from decimal import Decimal
import sys
import os

# 直接从当前目录导入
from red_line_rules import PolicyComplianceChecker, NATIONAL_STANDARD

def test_province_rules():
    """测试各省份差异化规则"""
    checker = PolicyComplianceChecker()
    
    # 测试10个省份的差异化规则
    provinces = {
        "广东": {"min_capital_ratio": Decimal("20.00"), "special_bond_rate_ref": Decimal("3.20"), "coverage_multiple": Decimal("1.30")},
        "浙江": {"min_capital_ratio": Decimal("20.00"), "special_bond_rate_ref": Decimal("3.15"), "coverage_multiple": Decimal("1.30")},
        "山东": {"min_capital_ratio": Decimal("20.00"), "special_bond_rate_ref": Decimal("3.25"), "coverage_multiple": Decimal("1.25")},
        "四川": {"min_capital_ratio": Decimal("20.00"), "special_bond_rate_ref": Decimal("3.30"), "coverage_multiple": Decimal("1.25")},
        "江苏": {"min_capital_ratio": Decimal("20.00"), "special_bond_rate_ref": Decimal("3.15"), "coverage_multiple": Decimal("1.30")},
        "河南": {"min_capital_ratio": Decimal("25.00"), "special_bond_rate_ref": Decimal("3.35"), "coverage_multiple": Decimal("1.20")},
        "河北": {"min_capital_ratio": Decimal("25.00"), "special_bond_rate_ref": Decimal("3.30"), "coverage_multiple": Decimal("1.20")},
        "湖南": {"min_capital_ratio": Decimal("25.00"), "special_bond_rate_ref": Decimal("3.35"), "coverage_multiple": Decimal("1.20")},
        "湖北": {"min_capital_ratio": Decimal("25.00"), "special_bond_rate_ref": Decimal("3.30"), "coverage_multiple": Decimal("1.20")},
        "安徽": {"min_capital_ratio": Decimal("25.00"), "special_bond_rate_ref": Decimal("3.35"), "coverage_multiple": Decimal("1.20")},
    }
    
    print("测试各省份差异化规则:")
    for province, expected_rules in provinces.items():
        rules = checker.get_rules(province)
        assert rules.min_capital_ratio == expected_rules["min_capital_ratio"], f"{province} 资本金比例不匹配"
        assert rules.special_bond_rate_ref == expected_rules["special_bond_rate_ref"], f"{province} 利率参考不匹配"
        assert rules.coverage_multiple == expected_rules["coverage_multiple"], f"{province} 覆盖倍数不匹配"
        print(f"  ✓ {province}: 资本金比例={rules.min_capital_ratio}, 利率参考={rules.special_bond_rate_ref}, 覆盖倍数={rules.coverage_multiple}")
    
    print("\n测试国家标准 (非10省地区):")
    national_rules = checker.get_rules("北京")  # 北京不属于10省范围
    assert national_rules.min_capital_ratio == NATIONAL_STANDARD.min_capital_ratio
    assert national_rules.special_bond_rate_ref == NATIONAL_STANDARD.special_bond_rate_ref
    assert national_rules.coverage_multiple == NATIONAL_STANDARD.coverage_multiple
    print(f"  ✓ 北京 (国家标准): 资本金比例={national_rules.min_capital_ratio}, 利率参考={national_rules.special_bond_rate_ref}, 覆盖倍数={national_rules.coverage_multiple}")

def test_fuzzy_matching():
    """测试模糊匹配功能"""
    checker = PolicyComplianceChecker()
    
    print("\n测试模糊匹配功能:")
    
    # 测试带"省"字的输入
    assert checker.resolve_region("广东省") == "广东", "模糊匹配失败: 广东省"
    assert checker.resolve_region("浙江省") == "浙江", "模糊匹配失败: 浙江省"
    print("  ✓ 带'省'字匹配成功")
    
    # 测试简写输入
    assert checker.resolve_region("粤") is None, "模糊匹配失败: 粤 (期望不匹配)"
    # 注意：difflib的模糊匹配可能不会识别单字符缩写
    
    # 测试部分匹配
    assert checker.resolve_region("广") is not None, "模糊匹配失败: 广"
    assert checker.resolve_region("苏") is not None, "模糊匹配失败: 苏"
    print("  ✓ 部分匹配功能正常")
    
    # 测试不存在的省份
    assert checker.get_rules("不存在的省份").min_capital_ratio == NATIONAL_STANDARD.min_capital_ratio
    print("  ✓ 不存在的省份使用国家标准")

def test_check_functions():
    """测试检查功能"""
    checker = PolicyComplianceChecker()
    
    print("\n测试检查功能:")
    
    # 测试资本金比例检查 - 广东省标准为20%
    result_gd = checker.check_capital_ratio("广东", Decimal("20.00"))  # 应该通过
    result_gd_fail = checker.check_capital_ratio("广东", Decimal("19.99"))  # 应该失败
    assert result_gd, "广东资本金比例检查失败: 20.00%应该通过"
    assert not result_gd_fail, "广东资本金比例检查失败: 19.99%应该失败"
    print("  ✓ 广东资本金比例检查正常")
    
    # 测试资本金比例检查 - 河北省标准为25%
    result_heb = checker.check_capital_ratio("河北", Decimal("25.00"))  # 应该通过
    result_heb_fail = checker.check_capital_ratio("河北", Decimal("24.99"))  # 应该失败
    assert result_heb, "河北资本金比例检查失败: 25.00%应该通过"
    assert not result_heb_fail, "河北资本金比例检查失败: 24.99%应该失败"
    print("  ✓ 河北资本金比例检查正常")
    
    # 测试覆盖倍数检查 - 广东省标准为1.30
    result_gd_dscr = checker.check_coverage_multiple("广东", Decimal("1.30"))  # 应该通过
    result_gd_dscr_fail = checker.check_coverage_multiple("广东", Decimal("1.29"))  # 应该失败
    assert result_gd_dscr, "广东覆盖倍数检查失败: 1.30应该通过"
    assert not result_gd_dscr_fail, "广东覆盖倍数检查失败: 1.29应该失败"
    print("  ✓ 广东覆盖倍数检查正常")
    
    # 测试专项债利率检查 - 广东省标准为3.20%
    result_gd_rate = checker.check_special_bond_rate("广东", Decimal("3.20"))  # 应该通过
    result_gd_rate_fail = checker.check_special_bond_rate("广东", Decimal("3.21"))  # 应该失败
    assert result_gd_rate, "广东专项债利率检查失败: 3.20%应该通过"
    assert not result_gd_rate_fail, "广东专项债利率检查失败: 3.21%应该失败"
    print("  ✓ 广东专项债利率检查正常")

def test_self_review_self_issue():
    """测试自审自发省份检查"""
    checker = PolicyComplianceChecker()
    
    print("\n测试自审自发省份标记:")
    
    # 测试10省中的一个
    assert checker.is_self_review_self_issue("广东"), "广东应该是自审自发省份"
    assert checker.is_self_review_self_issue("浙江"), "浙江应该是自审自发省份"
    print("  ✓ 10省标记正常")
    
    # 测试非10省
    assert not checker.is_self_review_self_issue("北京"), "北京不应该是自审自发省份"
    print("  ✓ 非10省正确排除")

def main():
    """运行所有测试"""
    print("开始测试 PolicyComplianceChecker 功能...")
    
    try:
        test_province_rules()
        test_fuzzy_matching()
        test_check_functions()
        test_self_review_self_issue()
        
        print("\n✅ 所有测试通过！")
        print("\n总结:")
        print("- 10个省份差异化规则已实现：广东、浙江、山东、四川、江苏、河南、河北、湖南、湖北、安徽")
        print("- 资本金比例、利率参考、覆盖倍数差异化设置正确")
        print("- region参数支持模糊匹配功能")
        print("- 省份不匹配时自动使用国家标准")
        print("- 各项检查功能正常工作")
        
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n💥 测试异常: {e}")
        return 1
        
    return 0

if __name__ == "__main__":
    exit(main())