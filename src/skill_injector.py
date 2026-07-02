"""
src/skill_injector.py — 专业技能注入引擎 v1.0

将 Hermes skills 中的专业知识注入到 pipeline 各 Phase 的 Agent 上下文中。
确保 PRD/DESIGN/REVIEW 阶段的 Agent 不但有深度知识学习，还具备相关专业技能。

注入策略：
  PRD Phase     → product-manager skill
  DESIGN Phase  → domain-driven-design skill
  JOURNEY Phase → product-manager skill (user research methodology)
  DECOMPOSE     → product-manager + domain-driven-design (dual review)
  DEVELOP       → domain-driven-design (tactical patterns)
  INTEGRATE     → domain-driven-design (context mapping)
  EVALUATE      → product-manager + domain-driven-design
  ACCEPT        → product-manager + domain-driven-design
  AUDIT         → product-manager + domain-driven-design
  REVIEW Phase  → product-manager + domain-driven-design

使用方式：
  from skill_injector import get_skill_context
  context = get_skill_context("prd")  # 返回可注入到 Agent prompt 的技能知识
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "SkillInjector",
    "SkillContext",
    "get_skill_context",
    "PHASE_SKILL_MAP",
    "SkillRegistry",
]


# ── Phase → Skills 映射表 ─────────────────────────────────────────

PHASE_SKILL_MAP: Dict[str, List[str]] = {
    # PRD 阶段：产品经理技能（PRD开发/需求分析/市场定位）
    "prd": ["product-manager"],
    # 设计阶段：DDD 领域驱动设计（战略建模/限界上下文/聚合设计）
    "design": ["domain-driven-design"],
    # 任务分解阶段：PM需求验证 + DDD架构验证
    "decompose": ["product-manager", "domain-driven-design"],
    # 旅程设计：PM用户研究
    "journey": ["product-manager"],
    # 研究阶段：PM市场/竞品研究
    "research": ["product-manager"],
    # 开发阶段：DDD 战术模式指导
    "develop": ["domain-driven-design"],
    # 测试阶段：需通过 adapters 注入 Qwen 测试技能
    "test": [],
    # 集成阶段：DDD 上下文映射
    "integrate": ["domain-driven-design"],
    # 评估阶段：PM指标 + DDD质量门禁
    "evaluate": ["product-manager", "domain-driven-design"],
    # 验收阶段：全技能加持
    "accept": ["product-manager", "domain-driven-design"],
    # 审计阶段：独立审查（adversarial_review 的角色由 pipeline entry 直接使用，不依赖 CHECK_REGISTRY）
    "audit": ["product-manager", "domain-driven-design"],
    # 对抗性审查（pipeline 内部角色，不映射到 CHECK_REGISTRY，由 adversarial_review.py 直接使用）
    "adversarial_review": ["product-manager", "domain-driven-design"],
    # Inspector 独立审查（pipeline 内部角色，由 inspector.py 直接使用）
    "inspector": ["product-manager", "domain-driven-design"],
}


@dataclass
class SkillContext:
    """加载后的技能上下文"""
    phase: str
    skills_loaded: List[str] = field(default_factory=list)
    skills_missing: List[str] = field(default_factory=list)
    knowledge_blocks: Dict[str, str] = field(default_factory=dict)
    quality_gates: List[str] = field(default_factory=list)
    frameworks: Dict[str, str] = field(default_factory=dict)
    prompt_suffix: str = ""


class SkillRegistry:
    """技能注册表 — 管理已安装的技能及其知识模块"""

    # 内嵌的关键知识摘要（当完整skill文件不可用时使用）
    EMBEDDED_KNOWLEDGE: Dict[str, Dict[str, Any]] = {
        "product-manager": {
            "description": "高级产品经理技能 — PRD开发、需求分析、市场定位、用户研究、JTBD",
            "quality_gates": [
                "假设必须标注 [assumption]",
                "结果必须可量化（数字+方向+时间框架）",
                "范围必须有边界（一个制品=一个问题）",
                "每个建议必须命名权衡代价",
                "下一步必须具体可执行",
            ],
            "frameworks": {
                "PRD Development": "问题陈述→用户画像→JTBD→功能需求→验收标准→成功指标",
                "JTBD": "当[情境]时，我想[动机]，以便[预期结果]。功能/情感/社交三维度",
                "Problem Framing": "识别→定义→验证→量化。禁止方案走私到问题陈述",
                "Prioritization": "RICE评分=Reach×Impact×Confidence÷Effort。或ICE/WSJF",
                "User Story": "作为[角色]，我想要[功能]，以便[价值]。验收标准用Gherkin格式",
                "Discovery": "假设驱动→PoL探针→实验→学习。控制变量，一次一个假设",
            },
            "prompt_suffix": """
[PM 技能加持]
作为持有产品经理专业技能的Agent，你必须：
1. 每个假设标注 [assumption]
2. 每个结果给出可量化指标（数字+方向+时间）
3. 每个决策命名一个具体的权衡代价
4. PRD必须包含：问题陈述→用户画像→JTBD→功能需求→验收标准→成功指标
5. 禁止方案走私：不要把"建仪表盘"当作问题，真正的问题是"管理者看不到进展"
6. 用户故事格式：作为[角色]，我想要[功能]，以便[价值]
""",
        },
        "domain-driven-design": {
            "description": "领域驱动设计 — 战略建模、限界上下文、聚合设计、事件驱动架构",
            "quality_gates": [
                "业务语言检查：模型必须使用业务术语而非技术术语",
                "边界清晰：限界上下文的责任能一句话说清",
                "聚合大小：30秒内能画完聚合边界——否则太大",
                "无贫血模型：实体应有行为，不只是getter/setter",
                "事件命名：领域事件用过去式+业务语言（OrderPlaced非OrderCreatedEvent）",
            ],
            "frameworks": {
                "Strategic Design": "核心域/支撑子域/通用子域分类→限界上下文→统一语言→上下文映射",
                "Bounded Context": "不同团队→不同上下文。不同语言→不同上下文。不同变更节奏→不同上下文",
                "Context Mapping": "Partnership/SharedKernel/Customer-Supplier/Conformist/ACL/OpenHost/PublishedLanguage/SeparateWays",
                "Aggregate Design": "一个事务=一个聚合。引用用ID。小边界优先。事件驱动跨聚合通信",
                "Domain Events": "过去式+业务语言+足够上下文。OrderPlaced{orderId,customerId,total,placedAt}",
                "Architecture Review": "四维度: user_perspective/knowledge_completeness/journey_fidelity/cross_phase_coherence",
            },
            "prompt_suffix": """
[DDD 技能加持]
作为持有领域驱动设计专业技能的Agent，你必须：
1. 架构设计使用业务术语，禁止技术术语污染模型
2. 每个限界上下文用一句话说清职责
3. 聚合设计遵循：一个事务=一个聚合，引用仅用ID
4. 领域事件用过去式命名（如OrderPlaced, PaymentReceived）
5. 区分核心域/支撑子域/通用子域
6. 上下文映射明确集成关系类型
7. 架构审查四维度：用户视角/知识完整性/旅程保真度/跨阶段一致性
""",
        },
    }

    @classmethod
    def get_skill_dir(cls) -> Path:
        """获取 Hermes skills 目录"""
        return Path.home() / ".hermes" / "skills"

    @classmethod
    def load_skill_file(cls, skill_name: str, file_path: str = "SKILL.md") -> Optional[str]:
        """尝试从磁盘加载 skill 文件"""
        skill_dir = cls.get_skill_dir() / skill_name
        target = skill_dir / file_path
        if target.exists():
            return target.read_text(encoding="utf-8")
        return None

    @classmethod
    def extract_knowledge_summary(cls, skill_name: str, skill_content: str) -> Dict[str, Any]:
        """从 skill markdown 中提取知识摘要"""
        result: Dict[str, Any] = {
            "description": "",
            "quality_gates": [],
            "frameworks": {},
        }

        # 提取描述
        desc_match = re.search(r'description:\s*"([^"]+)"', skill_content)
        if desc_match:
            result["description"] = desc_match.group(1)

        # 提取质量门禁
        gate_section = re.search(
            r'Quality Gates.*?\n(.*?)(?=\n##|\n---|\Z)',
            skill_content, re.DOTALL | re.IGNORECASE
        )
        if gate_section:
            gates = re.findall(r'\d+\.\s*\*\*(.+?)\*\*(.+?)(?=\n\d+\.|\n\n|\Z)',
                              gate_section.group(1), re.DOTALL)
            for title, desc in gates:
                result["quality_gates"].append(f"{title.strip()}: {desc.strip()[:100]}")

        # 提取框架
        framework_section = re.search(
            r'(?:Frameworks?|Framework|Routing Table).*?\n(.*?)(?=\n##|\n---|\Z)',
            skill_content, re.DOTALL | re.IGNORECASE
        )
        if framework_section:
            entries = re.findall(
                r'[-*]\s*\*?\*?(.+?)\*?\*?\s*[:：]\s*(.+?)(?=\n[-*]|\n\n|\Z)',
                framework_section.group(1), re.DOTALL
            )
            for name, desc in entries:
                result["frameworks"][name.strip()] = desc.strip()[:150]

        return result


class SkillInjector:
    """技能注入器 — 将专业技能注入到 pipeline phase 的 Agent 上下文中"""

    @classmethod
    def get_skills_for_phase(cls, phase: str) -> List[str]:
        """获取指定 phase 需要的技能列表"""
        return PHASE_SKILL_MAP.get(phase.lower(), [])

    @classmethod
    def inject(cls, phase: str) -> SkillContext:
        """为指定 phase 注入技能上下文

        加载顺序：
          1. 从 PHASE_SKILL_MAP 获取需要的技能列表
          2. 尝试从磁盘加载完整 SKILL.md 并提取知识
          3. 如果提取结果不完整，用内嵌知识补充
          4. 如果磁盘不可用，使用内嵌知识摘要
          5. 构建可注入到 Agent prompt 的上下文块
        """
        skills_needed = cls.get_skills_for_phase(phase)
        context = SkillContext(phase=phase)

        for skill_name in skills_needed:
            embedded = SkillRegistry.EMBEDDED_KNOWLEDGE.get(skill_name)

            # 尝试加载完整 skill
            skill_content = SkillRegistry.load_skill_file(skill_name)
            if skill_content:
                knowledge = SkillRegistry.extract_knowledge_summary(skill_name, skill_content)
                context.skills_loaded.append(skill_name)
                context.knowledge_blocks[skill_name] = knowledge.get("description", "")

                # 如果磁盘提取不完整，用内嵌补充
                disk_gates = knowledge.get("quality_gates", [])
                disk_frameworks = knowledge.get("frameworks", {})

                if embedded and len(disk_gates) < 2:
                    disk_gates = embedded.get("quality_gates", [])
                if embedded and len(disk_frameworks) < 2:
                    disk_frameworks = embedded.get("frameworks", {})

                for gate in disk_gates:
                    if gate not in context.quality_gates:
                        context.quality_gates.append(gate)
                for fw_name, fw_desc in disk_frameworks.items():
                    context.frameworks[f"{skill_name}/{fw_name}"] = fw_desc

                # 注入 prompt suffix（优先内嵌）
                if embedded and embedded.get("prompt_suffix"):
                    context.prompt_suffix += embedded["prompt_suffix"]
            elif embedded:
                # 磁盘不可用，使用内嵌知识
                context.skills_loaded.append(skill_name)
                context.knowledge_blocks[skill_name] = embedded.get("description", "")
                for gate in embedded.get("quality_gates", []):
                    if gate not in context.quality_gates:
                        context.quality_gates.append(gate)
                for fw_name, fw_desc in embedded.get("frameworks", {}).items():
                    context.frameworks[f"{skill_name}/{fw_name}"] = fw_desc
                suffix = embedded.get("prompt_suffix", "")
                if suffix:
                    context.prompt_suffix += suffix
            else:
                context.skills_missing.append(skill_name)

        return context

    @classmethod
    def build_context_prompt(cls, phase: str) -> str:
        """构建可直接注入到 Agent prompt 的上下文块"""
        ctx = cls.inject(phase)

        if not ctx.skills_loaded:
            return ""

        lines: List[str] = []
        lines.append(f"\n[专业技能上下文 — Phase: {phase}]")
        lines.append(f"已加载技能: {', '.join(ctx.skills_loaded)}")

        if ctx.frameworks:
            lines.append("\n适用框架:")
            for fw_name, fw_desc in ctx.frameworks.items():
                lines.append(f"  - {fw_name}: {fw_desc}")

        if ctx.quality_gates:
            lines.append("\n质量门禁:")
            for i, gate in enumerate(ctx.quality_gates, 1):
                lines.append(f"  {i}. {gate}")

        if ctx.prompt_suffix:
            lines.append(ctx.prompt_suffix)

        if ctx.skills_missing:
            lines.append(f"\n[WARNING] 以下技能未找到: {', '.join(ctx.skills_missing)}")

        return "\n".join(lines)


# ── 便捷函数 ──────────────────────────────────────────────────────

def get_skill_context(phase: str) -> SkillContext:
    """获取指定 phase 的技能上下文（向后兼容）"""
    return SkillInjector.inject(phase)
