"""
src/journey_designer.py — 专业用户旅程设计引擎 v1.0

基于 NNGroup、Smashing Magazine、Service Design Doing 等权威方法论，
提供结构化的用户旅程设计能力。

核心框架：
  1. JourneyMap — 5 阶段旅程图（Awareness→Consideration→Decision→Retention→Advocacy）
  2. ServiceBlueprint — 前后台服务蓝图（frontstage/backstage/support）
  3. EmotionalCurve — 情感曲线量化评分（1-5）
  4. ExceptionPath — 异常路径覆盖（≥80% 目标）
  5. JobsToBeDone — JTBD 集成

设计标准：
  - NNGroup Journey Mapping 101: 5 组件模型
  - Smashing Magazine: 分析性+趣闻性双重研究方法
  - 每阶段: 用户目标 → 行为 → 触点 → 情感 → 痛点 → 机会
  - 异常路径覆盖率 ≥ 80%
  - 跨渠道一致性检查
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "JourneyMap",
    "JourneyStage",
    "Touchpoint",
    "ServiceBlueprintLayer",
    "ExceptionPath",
    "JobToBeDone",
    "JourneyValidator",
    "JourneyDesigner",
    "design_journey",
    "validate_journey",
    "generate_journey_markdown",
    "StageType",
    "EmotionLevel",
    "Channel",
]


# ── Enums ──────────────────────────────────────────────────────────

class StageType(str, Enum):
    """标准旅程阶段类型（AIDRA 模型扩展）"""
    AWARENESS = "awareness"         # 认知：用户首次接触
    CONSIDERATION = "consideration"  # 考虑：用户评估比较
    DECISION = "decision"           # 决策：用户选择使用
    ONBOARDING = "onboarding"       # 上手：首次使用体验
    USAGE = "usage"                 # 使用：日常使用
    RETENTION = "retention"         # 留存：持续使用/复购
    ADVOCACY = "advocacy"           # 推荐：口碑传播
    EXIT = "exit"                   # 退出：流失/注销


class EmotionLevel(int, Enum):
    """情感等级 1-5（1=极负面, 5=极正面）"""
    VERY_NEGATIVE = 1
    NEGATIVE = 2
    NEUTRAL = 3
    POSITIVE = 4
    VERY_POSITIVE = 5


class Channel(str, Enum):
    """交互渠道"""
    WEB = "web"
    MOBILE = "mobile"
    DESKTOP = "desktop"
    EMAIL = "email"
    PHONE = "phone"
    IN_PERSON = "in_person"
    SOCIAL_MEDIA = "social_media"
    API = "api"
    CHAT = "chat"
    NOTIFICATION = "notification"


# ── Data Models ────────────────────────────────────────────────────

@dataclass
class Touchpoint:
    """用户触点（一次交互）"""
    name: str                           # 触点名称，如"点击注册按钮"
    channel: Channel                    # 交互渠道
    emotion_before: EmotionLevel        # 交互前情感
    emotion_after: EmotionLevel         # 交互后情感
    description: str = ""               # 详细描述
    pain_points: List[str] = field(default_factory=list)     # 痛点
    opportunities: List[str] = field(default_factory=list)   # 改进机会
    expected_duration_sec: int = 0      # 预期耗时（秒）
    is_critical: bool = False           # 是否关键触点（失败=流失）


@dataclass
class JourneyStage:
    """旅程阶段"""
    stage_type: StageType               # 阶段类型
    name: str                           # 中文名称，如"认知阶段"
    user_goal: str                      # 用户目标
    actions: List[str] = field(default_factory=list)        # 用户行为
    touchpoints: List[Touchpoint] = field(default_factory=list)  # 触点列表
    entry_condition: str = ""           # 进入条件
    exit_condition: str = ""            # 退出条件
    duration_est: str = ""              # 预估时长，如"5分钟"
    success_metric: str = ""            # 成功指标


@dataclass
class Persona:
    """用户画像"""
    name: str
    role: str                           # 角色
    goals: List[str] = field(default_factory=list)
    frustrations: List[str] = field(default_factory=list)
    tech_level: str = "medium"          # 技术水平: low/medium/high
    accessibility_needs: List[str] = field(default_factory=list)


@dataclass
class ExceptionPath:
    """异常路径"""
    name: str                           # 异常场景名称
    trigger: str                        # 触发条件
    happy_path_stage: str               # 对应的正常路径阶段
    user_reaction: str                  # 用户反应
    resolution: str                     # 解决方案
    severity: str = "medium"            # 严重程度: low/medium/high/critical


@dataclass
class JobToBeDone:
    """JTBD — 用户雇佣产品完成的"工作" """
    job_statement: str                  # JTBD 陈述，如"当我…我想…以便…"
    functional_aspects: List[str] = field(default_factory=list)
    emotional_aspects: List[str] = field(default_factory=list)
    social_aspects: List[str] = field(default_factory=list)
    current_alternatives: List[str] = field(default_factory=list)


@dataclass
class ServiceBlueprintLayer:
    """服务蓝图的一个层次"""
    name: str                           # 层名: frontstage/backstage/support
    actions: List[str] = field(default_factory=list)
    systems: List[str] = field(default_factory=list)
    actors: List[str] = field(default_factory=list)


@dataclass
class JourneyMap:
    """完整的用户旅程图"""
    title: str
    persona: Persona
    stages: List[JourneyStage]
    jobs_to_be_done: List[JobToBeDone] = field(default_factory=list)
    exception_paths: List[ExceptionPath] = field(default_factory=list)
    cross_channel_consistency: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    @property
    def stage_count(self) -> int:
        return len(self.stages)

    @property
    def touchpoint_count(self) -> int:
        return sum(len(s.touchpoints) for s in self.stages)

    @property
    def exception_path_count(self) -> int:
        return len(self.exception_paths)

    @property
    def emotional_curve(self) -> List[Tuple[str, float]]:
        """计算情感曲线（每阶段平均情感分）"""
        curve: List[Tuple[str, float]] = []
        for stage in self.stages:
            if not stage.touchpoints:
                curve.append((stage.name, 3.0))
                continue
            # 使用交互后情感
            scores = [tp.emotion_after.value for tp in stage.touchpoints]
            curve.append((stage.name, sum(scores) / len(scores)))
        return curve

    @property
    def exception_coverage_pct(self) -> float:
        """异常路径覆盖率"""
        if not self.stages:
            return 0.0
        covered = len({ep.happy_path_stage for ep in self.exception_paths})
        return (covered / len(self.stages)) * 100.0

    @property
    def critical_touchpoints(self) -> List[Touchpoint]:
        """关键触点（失败=流失）"""
        result: List[Touchpoint] = []
        for stage in self.stages:
            for tp in stage.touchpoints:
                if tp.is_critical:
                    result.append(tp)
        return result

    @property
    def pain_point_count(self) -> int:
        """总痛点数"""
        return sum(len(tp.pain_points) for s in self.stages for tp in s.touchpoints)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "title": self.title,
            "persona": asdict(self.persona),
            "stages": [
                {
                    "stage_type": s.stage_type.value,
                    "name": s.name,
                    "user_goal": s.user_goal,
                    "actions": s.actions,
                    "touchpoints": [
                        {
                            "name": tp.name,
                            "channel": tp.channel.value,
                            "emotion_before": tp.emotion_before.value,
                            "emotion_after": tp.emotion_after.value,
                            "description": tp.description,
                            "pain_points": tp.pain_points,
                            "opportunities": tp.opportunities,
                            "expected_duration_sec": tp.expected_duration_sec,
                            "is_critical": tp.is_critical,
                        }
                        for tp in s.touchpoints
                    ],
                    "entry_condition": s.entry_condition,
                    "exit_condition": s.exit_condition,
                    "duration_est": s.duration_est,
                    "success_metric": s.success_metric,
                }
                for s in self.stages
            ],
            "jobs_to_be_done": [asdict(j) for j in self.jobs_to_be_done],
            "exception_paths": [asdict(ep) for ep in self.exception_paths],
            "cross_channel_consistency": self.cross_channel_consistency,
            "version": self.version,
        }

    def to_markdown(self) -> str:
        """输出为 Markdown 格式（用于 specs/journey.md）"""
        lines: List[str] = []

        lines.append(f"# {self.title}")
        lines.append(f"")
        lines.append(f"版本: {self.version}")
        lines.append(f"")

        # Persona
        lines.append(f"## 用户画像")
        lines.append(f"")
        lines.append(f"- **角色**: {self.persona.role}")
        lines.append(f"- **姓名**: {self.persona.name}")
        lines.append(f"- **技术水平**: {self.persona.tech_level}")
        lines.append(f"- **目标**: {', '.join(self.persona.goals)}")
        lines.append(f"- **痛点**: {', '.join(self.persona.frustrations)}")
        if self.persona.accessibility_needs:
            lines.append(f"- **无障碍需求**: {', '.join(self.persona.accessibility_needs)}")
        lines.append(f"")

        # JTBD
        if self.jobs_to_be_done:
            lines.append(f"## Jobs to Be Done")
            lines.append(f"")
            for jtbd in self.jobs_to_be_done:
                lines.append(f"### {jtbd.job_statement}")
                lines.append(f"- 功能维度: {', '.join(jtbd.functional_aspects)}")
                lines.append(f"- 情感维度: {', '.join(jtbd.emotional_aspects)}")
                lines.append(f"- 社交维度: {', '.join(jtbd.social_aspects)}")
                lines.append(f"- 当前替代方案: {', '.join(jtbd.current_alternatives)}")
                lines.append(f"")

        # 情感曲线总览
        lines.append(f"## 情感曲线")
        lines.append(f"")
        curve = self.emotional_curve
        bar = "".join("█" * max(1, int(score)) for _, score in curve)
        labels = " → ".join(name for name, _ in curve)
        lines.append(f"```")
        lines.append(f"{labels}")
        lines.append(f"{bar}")
        lines.append(f"```")
        lines.append(f"")

        # Stages
        lines.append(f"## 核心旅程（{len(self.stages)} 阶段）")
        lines.append(f"")
        for i, stage in enumerate(self.stages, 1):
            lines.append(f"### 旅程 {i}: {stage.name}（{stage.stage_type.value}）")
            lines.append(f"")
            lines.append(f"| 属性 | 值 |")
            lines.append(f"|------|-----|")
            lines.append(f"| 用户目标 | {stage.user_goal} |")
            lines.append(f"| 进入条件 | {stage.entry_condition} |")
            lines.append(f"| 退出条件 | {stage.exit_condition} |")
            lines.append(f"| 预估时长 | {stage.duration_est} |")
            lines.append(f"| 成功指标 | {stage.success_metric} |")
            lines.append(f"")

            if stage.actions:
                lines.append(f"**用户行为**:")
                for action in stage.actions:
                    lines.append(f"- {action}")
                lines.append(f"")

            if stage.touchpoints:
                lines.append(f"**触点列表**:")
                lines.append(f"")
                lines.append(f"| 触点 | 渠道 | 情感(前→后) | 耗时 | 关键 |")
                lines.append(f"|------|------|------------|------|------|")
                for tp in stage.touchpoints:
                    critical = "⚠️" if tp.is_critical else ""
                    lines.append(
                        f"| {tp.name} | {tp.channel.value} | "
                        f"{'😡😟😐😊😍'[tp.emotion_before.value-1]}→{'😡😟😐😊😍'[tp.emotion_after.value-1]} | "
                        f"{tp.expected_duration_sec}s | {critical} |"
                    )
                lines.append(f"")

                # 每个触点的详情
                for tp in stage.touchpoints:
                    if tp.pain_points or tp.opportunities:
                        lines.append(f"**{tp.name}**:")
                        if tp.description:
                            lines.append(f"  - 描述: {tp.description}")
                        for pp in tp.pain_points:
                            lines.append(f"  - 痛点: {pp}")
                        for op in tp.opportunities:
                            lines.append(f"  - 机会: {op}")
                        lines.append(f"")

        # Exception Paths
        lines.append(f"## 异常路径（{len(self.exception_paths)} 条，覆盖率 {self.exception_coverage_pct:.0f}%）")
        lines.append(f"")
        if self.exception_paths:
            lines.append(f"| 异常场景 | 对应阶段 | 触发条件 | 严重程度 | 解决方案 |")
            lines.append(f"|---------|---------|---------|---------|---------|")
            for ep in self.exception_paths:
                sev_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(ep.severity, "⚪")
                lines.append(f"| {ep.name} | {ep.happy_path_stage} | {ep.trigger} | {sev_icon} {ep.severity} | {ep.resolution} |")
            lines.append(f"")

        # Cross-channel
        if self.cross_channel_consistency:
            lines.append(f"## 跨渠道一致性")
            lines.append(f"")
            for k, v in self.cross_channel_consistency.items():
                lines.append(f"- **{k}**: {v}")
            lines.append(f"")

        # Summary
        lines.append(f"## 旅程摘要")
        lines.append(f"")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 阶段数 | {self.stage_count} |")
        lines.append(f"| 触点数 | {self.touchpoint_count} |")
        lines.append(f"| 关键触点 | {len(self.critical_touchpoints)} |")
        lines.append(f"| 痛点总数 | {self.pain_point_count} |")
        lines.append(f"| 异常路径数 | {self.exception_path_count} |")
        lines.append(f"| 异常覆盖率 | {self.exception_coverage_pct:.0f}% |")
        lines.append(f"")

        return "\n".join(lines)


# ── Validator ──────────────────────────────────────────────────────

@dataclass
class JourneyValidationResult:
    """旅程验证结果"""
    passed: bool
    score: float                        # 0-100 综合分
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class JourneyValidator:
    """专业旅程验证器 — 对标行业标准"""

    MIN_STAGES = 5                      # 最少 5 阶段
    MIN_TOUCHPOINTS_PER_STAGE = 2       # 每阶段最少 2 触点
    MIN_EXCEPTION_COVERAGE_PCT = 80.0   # 异常覆盖率 ≥80%
    MIN_EMOTION_SCORE = 2.5             # 平均情感分 ≥2.5
    MIN_CRITICAL_TOUCHPOINTS = 1        # 至少 1 个关键触点
    MIN_ACTIONS_PER_STAGE = 1           # 每阶段至少 1 个用户行为

    @classmethod
    def validate(cls, journey: JourneyMap) -> JourneyValidationResult:
        """全面验证旅程设计质量"""
        errors: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {}

        # ── 1. 阶段数量 ──
        details["stage_count"] = journey.stage_count
        if journey.stage_count < cls.MIN_STAGES:
            errors.append(
                f"只有 {journey.stage_count} 个旅程阶段，最少需要 {cls.MIN_STAGES} 个"
                "（Awareness/Consideration/Decision/Retention/Advocacy）"
            )
        elif journey.stage_count < 7:
            warnings.append(
                f"建议 ≥7 个阶段以覆盖完整用户生命周期，当前 {journey.stage_count} 个"
            )

        # ── 2. 每阶段触点 ──
        low_touchpoint_stages: List[str] = []
        for stage in journey.stages:
            if len(stage.touchpoints) < cls.MIN_TOUCHPOINTS_PER_STAGE:
                low_touchpoint_stages.append(stage.name)
        details["low_touchpoint_stages"] = low_touchpoint_stages
        if low_touchpoint_stages:
            errors.append(
                f"以下阶段触点不足 {cls.MIN_TOUCHPOINTS_PER_STAGE} 个: "
                f"{', '.join(low_touchpoint_stages)}"
            )

        # ── 3. 异常覆盖率 ──
        coverage = journey.exception_coverage_pct
        details["exception_coverage_pct"] = coverage
        if coverage < cls.MIN_EXCEPTION_COVERAGE_PCT:
            errors.append(
                f"异常路径覆盖率 {coverage:.0f}%，未达到 {cls.MIN_EXCEPTION_COVERAGE_PCT:.0f}% 阈值"
            )

        # ── 4. 情感曲线 ──
        curve = journey.emotional_curve
        avg_emotion = sum(s for _, s in curve) / len(curve) if curve else 0
        details["avg_emotion_score"] = round(avg_emotion, 1)
        details["emotional_curve"] = curve
        if avg_emotion < cls.MIN_EMOTION_SCORE:
            warnings.append(
                f"平均情感分 {avg_emotion:.1f} < {cls.MIN_EMOTION_SCORE}，整体用户体验偏负面"
            )

        # ── 5. 关键触点 ──
        critical = journey.critical_touchpoints
        details["critical_touchpoint_count"] = len(critical)
        if len(critical) < cls.MIN_CRITICAL_TOUCHPOINTS:
            warnings.append("未标记任何关键触点（is_critical=True）")

        # ── 6. 用户行为 ──
        no_action_stages = [s.name for s in journey.stages if len(s.actions) < cls.MIN_ACTIONS_PER_STAGE]
        details["no_action_stages"] = no_action_stages
        if no_action_stages:
            warnings.append(f"以下阶段缺少用户行为描述: {', '.join(no_action_stages)}")

        # ── 7. 严重异常路径 ──
        high_severity = [ep for ep in journey.exception_paths if ep.severity in ("high", "critical")]
        details["high_severity_exception_count"] = len(high_severity)
        uncovered_high = []
        for stage in journey.stages:
            stage_high = [ep for ep in high_severity if ep.happy_path_stage == stage.name]
            if not stage_high and journey.stages.index(stage) < len(journey.stages) - 1:
                pass  # 最后阶段可以没有
        if journey.stages and len(high_severity) < len(journey.stages) // 2:
            warnings.append(
                f"高严重度异常路径 {len(high_severity)} 条，建议每个阶段至少覆盖 1 条"
            )

        # ── 8. Persona 完整性 ──
        if not journey.persona.goals:
            errors.append("用户画像缺少目标")
        if not journey.persona.frustrations:
            warnings.append("用户画像缺少痛点（frustrations）")

        # ── 计算综合分 ──
        max_score = 100.0
        deductions = 0.0
        deductions += len(errors) * 15.0       # 每个错误扣 15 分
        deductions += len(warnings) * 5.0       # 每个警告扣 5 分
        deductions += max(0, cls.MIN_STAGES - journey.stage_count) * 10.0
        deductions += max(0, cls.MIN_EXCEPTION_COVERAGE_PCT - coverage) * 0.5

        score = max(0.0, max_score - deductions)
        details["score"] = score
        passed = len(errors) == 0

        return JourneyValidationResult(
            passed=passed,
            score=score,
            errors=errors,
            warnings=warnings,
            details=details,
        )

    @classmethod
    def validate_from_markdown(cls, markdown_path: Path) -> JourneyValidationResult:
        """从 Markdown 文件验证（快速检查）"""
        errors: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {}

        if not markdown_path.exists():
            return JourneyValidationResult(
                passed=False, score=0, errors=["journey.md 文件不存在"],
                details={}
            )

        content = markdown_path.read_text(encoding="utf-8")

        # 检查关键章节
        checks = {
            "## 用户画像": "缺少用户画像章节",
            "## 核心旅程": "缺少核心旅程章节",
            "## 异常路径": "缺少异常路径章节",
            "## 情感曲线": "缺少情感曲线章节",
            "## 旅程摘要": "缺少旅程摘要章节",
        }
        for key, msg in checks.items():
            if key not in content:
                errors.append(msg)
            details[f"has_{key.strip('# ').replace(' ','_')}"] = key in content

        # 统计旅程数
        import re
        journey_count = len(re.findall(r'### 旅程 \d+:', content))
        details["journey_count"] = journey_count
        if journey_count < cls.MIN_STAGES:
            errors.append(
                f"核心旅程数 {journey_count} < {cls.MIN_STAGES}，"
                "最低要求 Awareness/Consideration/Decision/Retention/Advocacy"
            )

        # 统计异常覆盖率
        exception_section = re.search(r'## 异常路径.*?(?=##|\Z)', content, re.DOTALL)
        if exception_section:
            ep_text = exception_section.group(0)
            ep_count = len(re.findall(r'^\| [^|]+ \|', ep_text, re.MULTILINE)) - 1  # 减去表头
            if journey_count > 0:
                coverage = (ep_count / journey_count) * 100
                details["exception_path_count"] = ep_count
                details["exception_coverage_pct"] = round(coverage, 1)
                if coverage < cls.MIN_EXCEPTION_COVERAGE_PCT:
                    errors.append(
                        f"异常路径覆盖率 {coverage:.0f}%，未达到 {cls.MIN_EXCEPTION_COVERAGE_PCT:.0f}% 阈值"
                    )

        # 计算得分
        max_score = 100.0
        deductions = len(errors) * 20.0
        score = max(0.0, max_score - deductions)
        passed = len(errors) == 0

        return JourneyValidationResult(
            passed=passed, score=score,
            errors=errors, warnings=warnings, details=details,
        )


# ── Designer (LLM 辅助) ────────────────────────────────────────────

class JourneyDesigner:
    """旅程设计器 — 提供 LLM 可调用的设计原语"""

    @staticmethod
    def create_minimal_journey(product_name: str, persona_role: str) -> JourneyMap:
        """创建最小可行的 5 阶段旅程模板"""
        persona = Persona(
            name=f"{persona_role}_用户",
            role=persona_role,
            goals=[f"高效完成工作任务", f"获得满意的使用体验"],
            frustrations=["操作复杂", "找不到所需功能"],
        )

        stages = [
            JourneyStage(
                stage_type=StageType.AWARENESS,
                name="认知阶段",
                user_goal=f"发现 {product_name} 并了解其价值",
                actions=["搜索解决方案", "浏览产品介绍", "阅读案例/评价"],
                touchpoints=[
                    Touchpoint("搜索结果点击", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.POSITIVE,
                               pain_points=["搜索结果不相关"], opportunities=["SEO优化"]),
                    Touchpoint("浏览首页", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.POSITIVE,
                               description="用户首次访问产品首页"),
                ],
                entry_condition="用户有相关需求",
                exit_condition="用户决定进一步了解或离开",
            ),
            JourneyStage(
                stage_type=StageType.CONSIDERATION,
                name="考虑阶段",
                user_goal=f"评估 {product_name} 是否满足需求",
                actions=["查看功能列表", "对比竞品", "查看定价"],
                touchpoints=[
                    Touchpoint("查看功能页面", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.POSITIVE,
                               pain_points=["功能描述不清晰"]),
                    Touchpoint("查看定价", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.NEUTRAL,
                               pain_points=["价格不透明"], opportunities=["提供免费试用"]),
                ],
                entry_condition="用户对产品产生兴趣",
                exit_condition="用户决定试用或放弃",
            ),
            JourneyStage(
                stage_type=StageType.DECISION,
                name="决策阶段",
                user_goal=f"注册并开始使用 {product_name}",
                actions=["注册账号", "完成初始设置", "首次使用"],
                touchpoints=[
                    Touchpoint("注册表单", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.NEGATIVE,
                               pain_points=["注册流程过长"], opportunities=["简化注册", "社交登录"],
                               is_critical=True),
                    Touchpoint("新手引导", Channel.WEB, EmotionLevel.POSITIVE, EmotionLevel.POSITIVE,
                               description="首次登录后的引导流程"),
                ],
                entry_condition="用户决定试用",
                exit_condition="用户完成首次核心任务或放弃",
            ),
            JourneyStage(
                stage_type=StageType.RETENTION,
                name="留存阶段",
                user_goal=f"持续使用 {product_name} 获得价值",
                actions=["日常使用核心功能", "探索高级功能", "接收通知/更新"],
                touchpoints=[
                    Touchpoint("核心功能使用", Channel.WEB, EmotionLevel.POSITIVE, EmotionLevel.VERY_POSITIVE,
                               pain_points=["功能不稳定", "响应慢"]),
                    Touchpoint("客服支持", Channel.CHAT, EmotionLevel.NEUTRAL, EmotionLevel.POSITIVE,
                               pain_points=["响应慢", "无法解决问题"]),
                ],
                entry_condition="用户完成首次核心任务",
                exit_condition="用户持续使用或流失",
            ),
            JourneyStage(
                stage_type=StageType.ADVOCACY,
                name="推荐阶段",
                user_goal=f"向他人推荐 {product_name}",
                actions=["分享使用体验", "邀请团队成员", "撰写评价"],
                touchpoints=[
                    Touchpoint("邀请功能", Channel.WEB, EmotionLevel.POSITIVE, EmotionLevel.VERY_POSITIVE,
                               opportunities=["邀请奖励机制"]),
                    Touchpoint("评价入口", Channel.WEB, EmotionLevel.POSITIVE, EmotionLevel.POSITIVE,
                               description="应用商店或社区评价"),
                ],
                entry_condition="用户获得持续价值",
                exit_condition="用户完成推荐或保持沉默",
            ),
        ]

        exception_paths = [
            ExceptionPath("搜索不到", "SEO差/关键词不匹配", "认知阶段", "用户找不到产品", "优化SEO+精准广告", "high"),
            ExceptionPath("对比后放弃", "竞品功能更强/价格更低", "考虑阶段", "用户流失到竞品", "强化差异化卖点+免费试用", "critical"),
            ExceptionPath("注册失败", "邮箱已被注册", "决策阶段", "用户困惑", "提示用户登录或找回密码", "high"),
            ExceptionPath("支付失败", "银行卡余额不足", "决策阶段", "用户放弃", "提供替代支付方式", "critical"),
            ExceptionPath("功能报错", "服务器 500 错误", "留存阶段", "用户沮丧", "友好错误页+自动重试+客服入口", "high"),
            ExceptionPath("数据加载慢", "网络超时 5s", "留存阶段", "用户等待/离开", "骨架屏+超时重试+缓存策略", "medium"),
            ExceptionPath("邀请失败", "被邀请人已注册", "推荐阶段", "邀请人困惑", "提示已注册并引导协作", "low"),
        ]

        return JourneyMap(
            title=f"{product_name} 用户旅程图",
            persona=persona,
            stages=stages,
            exception_paths=exception_paths,
            cross_channel_consistency={
                "品牌一致性": "所有渠道使用统一的品牌标识和语气",
                "数据同步": "用户数据在各渠道间实时同步",
            },
        )

    @staticmethod
    def export_to_file(journey: JourneyMap, output_path: Path) -> None:
        """导出为 journey.md 文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        md = journey.to_markdown()
        output_path.write_text(md, encoding="utf-8")

    @staticmethod
    def export_to_json(journey: JourneyMap, output_path: Path) -> None:
        """导出为 JSON 文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = journey.to_dict()
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 快速入口 ───────────────────────────────────────────────────────

def design_journey(product_name: str, persona_role: str) -> JourneyMap:
    """快速创建标准旅程图"""
    return JourneyDesigner.create_minimal_journey(product_name, persona_role)


def validate_journey(markdown_path: Path) -> JourneyValidationResult:
    """从 Markdown 文件验证旅程质量"""
    return JourneyValidator.validate_from_markdown(markdown_path)


def generate_journey_markdown(product_name: str, persona_role: str, output_path: Path) -> JourneyMap:
    """生成并保存旅程 Markdown"""
    journey = JourneyDesigner.create_minimal_journey(product_name, persona_role)
    JourneyDesigner.export_to_file(journey, output_path)
    return journey
