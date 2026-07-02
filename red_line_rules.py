"""红线规则 — 5个硬编码纯函数，便于单元测试

红线触发 = 100% 阻断输出，无一票绕过机制。

规则:
1. DSCR < 1.2                → 一票否决
2. 债券比例 > 80%            → 一票否决
3. 引用废止政策              → 调用 F002 PolicyValidator
4. 收益属性矛盾              → 纯商业项目包装为公益
5. 公式错误                  → 审计日志哈希链被篡改
"""

from dataclasses import dataclass, field
from decimal import Decimal
from difflib import get_close_matches
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine.financial_core import FinancialResult
    from src.engine.audit import AuditLog
    from src.knowledge.policy_validator import PolicyValidator

# DSCR 达标阈值
DSCR_THRESHOLD = Decimal("1.20")


@dataclass(frozen=True)
class ProvincialRules:
    """省级差异化政策规则。

    Attributes:
        min_capital_ratio: 资本金最低比例（百分比，如 20 表示 20%）
        special_bond_rate_ref: 专项债利率参考（百分比）
        coverage_multiple: 覆盖倍数要求（DSCR 最低值）
    """

    min_capital_ratio: Decimal
    special_bond_rate_ref: Decimal
    coverage_multiple: Decimal


# 国家标准（缺省省份 fallback）
NATIONAL_STANDARD = ProvincialRules(
    min_capital_ratio=Decimal("20.00"),
    special_bond_rate_ref=Decimal("3.50"),
    coverage_multiple=Decimal("1.20"),
)

# 自审自发 10 省特殊标记
SELF_REVIEW_SELF_ISSUE_PROVINCES = frozenset(
    ["广东", "浙江", "山东", "四川", "江苏", "河南", "河北", "湖南", "湖北", "安徽"]
)

# 10 省差异化规则：资本金最低比例 / 专项债利率参考 / 覆盖倍数要求
PROVINCIAL_RULES: Dict[str, ProvincialRules] = {
    "广东": ProvincialRules(
        min_capital_ratio=Decimal("20.00"),
        special_bond_rate_ref=Decimal("3.20"),
        coverage_multiple=Decimal("1.30"),
    ),
    "浙江": ProvincialRules(
        min_capital_ratio=Decimal("20.00"),
        special_bond_rate_ref=Decimal("3.15"),
        coverage_multiple=Decimal("1.30"),
    ),
    "山东": ProvincialRules(
        min_capital_ratio=Decimal("20.00"),
        special_bond_rate_ref=Decimal("3.25"),
        coverage_multiple=Decimal("1.25"),
    ),
    "四川": ProvincialRules(
        min_capital_ratio=Decimal("20.00"),
        special_bond_rate_ref=Decimal("3.30"),
        coverage_multiple=Decimal("1.25"),
    ),
    "江苏": ProvincialRules(
        min_capital_ratio=Decimal("20.00"),
        special_bond_rate_ref=Decimal("3.15"),
        coverage_multiple=Decimal("1.30"),
    ),
    "河南": ProvincialRules(
        min_capital_ratio=Decimal("25.00"),
        special_bond_rate_ref=Decimal("3.35"),
        coverage_multiple=Decimal("1.20"),
    ),
    "河北": ProvincialRules(
        min_capital_ratio=Decimal("25.00"),
        special_bond_rate_ref=Decimal("3.30"),
        coverage_multiple=Decimal("1.20"),
    ),
    "湖南": ProvincialRules(
        min_capital_ratio=Decimal("25.00"),
        special_bond_rate_ref=Decimal("3.35"),
        coverage_multiple=Decimal("1.20"),
    ),
    "湖北": ProvincialRules(
        min_capital_ratio=Decimal("25.00"),
        special_bond_rate_ref=Decimal("3.30"),
        coverage_multiple=Decimal("1.20"),
    ),
    "安徽": ProvincialRules(
        min_capital_ratio=Decimal("25.00"),
        special_bond_rate_ref=Decimal("3.35"),
        coverage_multiple=Decimal("1.20"),
    ),
}


def _normalize_region(region: Optional[str]) -> str:
    """规范化 region 输入，移除常见后缀并统一大小写。"""
    if not region:
        return ""
    normalized = region.strip()
    # 移除常见行政区划后缀
    for suffix in ("省", "市", "自治区", "特别行政区"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


class PolicyComplianceChecker:
    """政策合规检查器：支持国家级标准与 10 省差异化规则。

    特性：
    - 10 省差异化：资本金最低比例 / 专项债利率参考 / 覆盖倍数要求
    - 自审自发 10 省特殊标记
    - 缺省省份自动回退到国家标准
    - region 参数支持模糊匹配（ difflib 近似匹配）
    """

    def __init__(self, cutoff: float = 0.6):
        """初始化。

        Args:
            cutoff: 模糊匹配相似度阈值（0~1），默认 0.6
        """
        self.cutoff = cutoff
        self._provinces = tuple(PROVINCIAL_RULES.keys())

    def resolve_region(self, region: Optional[str]) -> Optional[str]:
        """解析 region，支持模糊匹配。

        匹配逻辑：
        1. 精确匹配（忽略"省/市/自治区"等后缀）
        2. 模糊匹配 difflib.get_close_matches

        Args:
            region: 地区名称，如"广东省"、"广东"、"广d"

        Returns:
            匹配到的省份名；未匹配则返回 None
        """
        normalized = _normalize_region(region)
        if not normalized:
            return None

        # 精确匹配（规范化后）
        if normalized in self._provinces:
            return normalized

        # 模糊匹配
        matches = get_close_matches(
            normalized, self._provinces, n=1, cutoff=self.cutoff
        )
        if matches:
            return matches[0]
        return None

    def get_rules(self, region: Optional[str]) -> ProvincialRules:
        """获取指定地区的规则，缺省回退到国家标准。

        Args:
            region: 地区名称

        Returns:
            对应省级规则或国家标准
        """
        province = self.resolve_region(region)
        if province is None:
            return NATIONAL_STANDARD
        return PROVINCIAL_RULES.get(province, NATIONAL_STANDARD)

    def is_self_review_self_issue(self, region: Optional[str]) -> bool:
        """判断是否为自审自发 10 省。

        Args:
            region: 地区名称

        Returns:
            True 表示属于 10 省自审自发名单
        """
        province = self.resolve_region(region)
        if province is None:
            return False
        return province in SELF_REVIEW_SELF_ISSUE_PROVINCES

    def check_capital_ratio(
        self, region: Optional[str], actual_ratio: Decimal
    ) -> bool:
        """检查资本金比例是否满足地区最低要求。

        Args:
            region: 地区名称
            actual_ratio: 实际资本金比例（百分比）

        Returns:
            True 表示通过（actual_ratio >= 最低要求），False 表示未通过
        """
        rules = self.get_rules(region)
        return actual_ratio >= rules.min_capital_ratio

    def check_coverage_multiple(
        self, region: Optional[str], actual_dscr: Decimal
    ) -> bool:
        """检查覆盖倍数是否满足地区要求。

        Args:
            region: 地区名称
            actual_dscr: 实际 DSCR 覆盖倍数

        Returns:
            True 表示通过（actual_dscr >= 要求），False 表示未通过
        """
        rules = self.get_rules(region)
        return actual_dscr >= rules.coverage_multiple

    def check_special_bond_rate(
        self, region: Optional[str], actual_rate: Decimal
    ) -> bool:
        """检查专项债利率是否不超过地区参考利率。

        Args:
            region: 地区名称
            actual_rate: 实际专项债利率（百分比）

        Returns:
            True 表示通过（actual_rate <= 参考利率），False 表示未通过
        """
        rules = self.get_rules(region)
        return actual_rate <= rules.special_bond_rate_ref

# 债券比例上限
BOND_RATIO_MAX = Decimal("80")

# 商业收入类别关键词（纯商业项目特征）
COMMERCIAL_REVENUE_KEYWORDS = {
    "rent": ["租金", "rent", "租赁"],
    "parking": ["停车", "parking", "车位"],
    "advertising": ["广告", "advertising", "宣传"],
    "service": ["服务", "service", "物业", "商业"],
}

# 公益/公共服务关键词 — 排除商业属性
PUBLIC_WELFARE_KEYWORDS = {"公共", "公益", "民生", "保障", "其他", "补贴", "补助", "政府"}


def _is_commercial_revenue(name: str) -> bool:
    """判断收入项是否为纯商业性质。

    如果名称包含公益/公共服务关键词，则判定为非商业。
    否则检查是否匹配商业类别关键词。
    """
    name_lower = name.lower()
    # 公益关键词优先：如果是公共服务类，不算商业
    for pwk in PUBLIC_WELFARE_KEYWORDS:
        if pwk.lower() in name_lower:
            return False
    # 检查是否匹配商业类别
    for keywords in COMMERCIAL_REVENUE_KEYWORDS.values():
        for kw in keywords:
            if kw.lower() in name_lower:
                return True
    return False


@dataclass
class PolicyIssue:
    """政策引用问题"""
    policy_ref: str
    status: str          # 'repealed' | 'unknown'
    message: str
    replacement: Optional[str] = None


def check_dscr_threshold(financial: "FinancialResult") -> bool:
    """检查 DSCR 是否达标。

    Args:
        financial: 财务测算结果

    Returns:
        True 表示触发红线（DSCR < 1.2），False 表示通过
    """
    dscr = financial.dscr
    if dscr.min_dscr < DSCR_THRESHOLD:
        return True
    return False


def check_bond_ratio(ratio: Decimal) -> bool:
    """检查债券比例是否超标。

    Args:
        ratio: 债券比例（百分比）

    Returns:
        True 表示触发红线（比例 > 80%），False 表示通过
    """
    return ratio > BOND_RATIO_MAX


def check_policy_validity(
    policy_refs: List[str],
    validator: "PolicyValidator",
) -> List[PolicyIssue]:
    """检查政策引用是否包含废止政策。

    Args:
        policy_refs: 政策文号列表
        validator: F002 政策校验器实例

    Returns:
        触发问题的列表（空列表 = 全部通过）
    """
    issues: List[PolicyIssue] = []
    for ref in policy_refs:
        status = validator.validate_policy_reference(ref)
        if status.status == "repealed":
            issues.append(PolicyIssue(
                policy_ref=ref,
                status="repealed",
                message=f"引用已废止政策 {ref}，替代: {status.replacement or '无'}",
                replacement=status.replacement,
            ))
        elif status.status == "unknown":
            issues.append(PolicyIssue(
                policy_ref=ref,
                status="unknown",
                message=f"政策 {ref} 状态未知，无法确认是否有效",
            ))
    return issues


def check_revenue_nature(financial: "FinancialResult") -> bool:
    """检查收益属性是否矛盾 — 纯商业项目包装为公益。

    判定逻辑: 如果所有收入来源均为商业类别
    (rent/parking/advertising/service 对应的中英文关键词)，
    且没有任何非商业收入（"other" 类别或混合来源），
    则判定为收益属性矛盾。

    Args:
        financial: 财务测算结果

    Returns:
        True 表示触发红线（收益属性矛盾），False 表示通过
    """
    vat_details = financial.tax.vat.details

    if not vat_details:
        return False

    # 如果所有收入都是商业类别，触发红线
    all_commercial = all(_is_commercial_revenue(d.name) for d in vat_details)
    if all_commercial and len(vat_details) > 0:
        return True

    return False


def check_formula_integrity(audit_log: "AuditLog") -> bool:
    """检查审计日志哈希链完整性 — 公式错误检测。

    验证 SHA256 哈希链是否完整，检测公式计算过程是否被篡改。

    Args:
        audit_log: 审计日志对象

    Returns:
        True 表示触发红线（哈希链被破坏/篡改），False 表示通过
    """
    if not audit_log.steps:
        return False
    return not audit_log.verify()
