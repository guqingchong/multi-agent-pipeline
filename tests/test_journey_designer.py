"""
tests/test_journey_designer.py — 用户旅程设计引擎测试

覆盖：
  - JourneyMap 数据模型
  - JourneyDesigner 最小旅程生成
  - JourneyValidator 专业验证
  - Markdown 输出
  - JSON 序列化
  - Phase check 集成
"""

import json
import tempfile
from pathlib import Path

import pytest

# Allow import from src/
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.journey_designer import (
    JourneyMap,
    JourneyStage,
    Touchpoint,
    Persona,
    ExceptionPath,
    JobToBeDone,
    JourneyDesigner,
    JourneyValidator,
    JourneyValidationResult,
    StageType,
    EmotionLevel,
    Channel,
    design_journey,
    validate_journey,
    generate_journey_markdown,
)


# ══════════════════════════════════════════════════════════════════
# 1. 数据模型测试
# ══════════════════════════════════════════════════════════════════

class TestDataModels:
    """测试数据模型的创建和属性"""

    def test_touchpoint_creation(self) -> None:
        tp = Touchpoint(
            name="注册",
            channel=Channel.WEB,
            emotion_before=EmotionLevel.NEUTRAL,
            emotion_after=EmotionLevel.POSITIVE,
            is_critical=True,
        )
        assert tp.name == "注册"
        assert tp.emotion_before == EmotionLevel.NEUTRAL
        assert tp.emotion_after == EmotionLevel.POSITIVE
        assert tp.is_critical is True

    def test_journey_stage_creation(self) -> None:
        stage = JourneyStage(
            stage_type=StageType.DECISION,
            name="决策阶段",
            user_goal="完成注册",
            actions=["填写表单", "验证邮箱"],
            touchpoints=[
                Touchpoint("注册表单", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.POSITIVE),
                Touchpoint("邮箱验证", Channel.EMAIL, EmotionLevel.POSITIVE, EmotionLevel.VERY_POSITIVE),
            ],
        )
        assert stage.stage_type == StageType.DECISION
        assert len(stage.touchpoints) == 2

    def test_persona_creation(self) -> None:
        persona = Persona(
            name="张三",
            role="项目经理",
            goals=["高效管理任务"],
            frustrations=["工具太多"],
        )
        assert persona.name == "张三"
        assert len(persona.goals) == 1

    def test_exception_path_creation(self) -> None:
        ep = ExceptionPath(
            name="支付失败",
            trigger="余额不足",
            happy_path_stage="决策阶段",
            user_reaction="放弃购买",
            resolution="提供替代支付",
            severity="critical",
        )
        assert ep.severity == "critical"


# ══════════════════════════════════════════════════════════════════
# 2. JourneyDesigner 测试
# ══════════════════════════════════════════════════════════════════

class TestJourneyDesigner:
    """测试旅程设计器"""

    def test_create_minimal_journey(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("测试产品", "测试用户")
        assert isinstance(journey, JourneyMap)
        assert journey.stage_count == 5
        assert journey.persona.role == "测试用户"
        assert len(journey.exception_paths) >= 4
        # 检查所有阶段类型
        stage_types = [s.stage_type for s in journey.stages]
        assert StageType.AWARENESS in stage_types
        assert StageType.CONSIDERATION in stage_types
        assert StageType.DECISION in stage_types
        assert StageType.RETENTION in stage_types
        assert StageType.ADVOCACY in stage_types

    def test_emotional_curve(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("P", "U")
        curve = journey.emotional_curve
        assert len(curve) == 5
        for name, score in curve:
            assert isinstance(name, str)
            assert 1.0 <= score <= 5.0

    def test_critical_touchpoints(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("P", "U")
        critical = journey.critical_touchpoints
        assert len(critical) >= 1  # 至少注册触点是关键触点

    def test_exception_coverage(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("P", "U")
        coverage = journey.exception_coverage_pct
        assert coverage >= 60.0  # 至少覆盖 3/5 阶段

    def test_export_markdown(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("Markdown产品", "测试")
        md = journey.to_markdown()
        assert "Markdown产品 用户旅程图" in md
        assert "## 用户画像" in md
        assert "## 核心旅程" in md
        assert "## 异常路径" in md
        assert "## 情感曲线" in md
        assert "## 旅程摘要" in md

    def test_export_json_and_md_to_file(self, tmp_path: Path) -> None:
        journey = JourneyDesigner.create_minimal_journey("文件产品", "用户")
        md_path = tmp_path / "journey.md"
        json_path = tmp_path / "journey.json"

        JourneyDesigner.export_to_file(journey, md_path)
        JourneyDesigner.export_to_json(journey, json_path)

        assert md_path.exists()
        assert json_path.exists()

        # 验证 JSON 可解析
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["title"] == "文件产品 用户旅程图"
        assert len(data["stages"]) == 5


# ══════════════════════════════════════════════════════════════════
# 3. JourneyValidator 测试
# ══════════════════════════════════════════════════════════════════

class TestJourneyValidator:
    """测试专业验证器"""

    def test_validate_minimal_journey_passes(self) -> None:
        """最小旅程应通过验证"""
        journey = JourneyDesigner.create_minimal_journey("V产品", "用户")
        result = JourneyValidator.validate(journey)
        assert result.passed is True, f"Validation failed: {result.errors}"
        assert result.score >= 60.0

    def test_validate_empty_journey_fails(self) -> None:
        """空旅程应失败"""
        persona = Persona(name="空", role="空角色", goals=[], frustrations=[])
        journey = JourneyMap(title="空旅程", persona=persona, stages=[])
        result = JourneyValidator.validate(journey)
        assert result.passed is False
        assert len(result.errors) > 0

    def test_validate_low_exception_coverage(self) -> None:
        """低异常覆盖率应报错"""
        persona = Persona(name="T", role="T", goals=["g"], frustrations=["f"])
        stage = JourneyStage(
            stage_type=StageType.AWARENESS, name="A",
            user_goal="g", actions=["a"],
            touchpoints=[Touchpoint("t", Channel.WEB, EmotionLevel.NEUTRAL, EmotionLevel.NEUTRAL)],
        )
        journey = JourneyMap(title="低覆盖", persona=persona, stages=[stage]*5)
        result = JourneyValidator.validate(journey)
        assert len(result.errors) > 0  # should flag low coverage

    def test_validate_missing_persona_goals(self) -> None:
        """缺少用户目标应报错"""
        persona = Persona(name="T", role="T", goals=[], frustrations=[])
        journey = JourneyDesigner.create_minimal_journey("P", "U")
        # 覆盖掉 persona
        journey.persona = persona
        result = JourneyValidator.validate(journey)
        assert len(result.errors) > 0
        assert any("目标" in e for e in result.errors)

    def test_validate_from_markdown_valid(self, tmp_path: Path) -> None:
        """从有效 Markdown 验证"""
        journey = JourneyDesigner.create_minimal_journey("MD产品", "用户")
        md_path = tmp_path / "journey.md"
        JourneyDesigner.export_to_file(journey, md_path)

        result = JourneyValidator.validate_from_markdown(md_path)
        assert result.passed is True, f"Failed: {result.errors}"
        assert result.score >= 80.0

    def test_validate_from_markdown_missing_file(self, tmp_path: Path) -> None:
        """文件缺失"""
        result = JourneyValidator.validate_from_markdown(tmp_path / "nonexistent.md")
        assert result.passed is False
        assert result.score == 0

    def test_validation_result_structure(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("S产品", "用户")
        result = JourneyValidator.validate(journey)
        assert hasattr(result, "passed")
        assert hasattr(result, "score")
        assert hasattr(result, "errors")
        assert hasattr(result, "warnings")
        assert hasattr(result, "details")
        assert isinstance(result.details, dict)
        assert "stage_count" in result.details
        assert "exception_coverage_pct" in result.details
        assert "avg_emotion_score" in result.details


# ══════════════════════════════════════════════════════════════════
# 4. 便捷函数测试
# ══════════════════════════════════════════════════════════════════

class TestConvenienceFunctions:
    """测试顶层便捷函数"""

    def test_design_journey(self) -> None:
        journey = design_journey("快捷产品", "用户")
        assert isinstance(journey, JourneyMap)
        assert journey.stage_count == 5

    def test_generate_journey_markdown(self, tmp_path: Path) -> None:
        md_path = tmp_path / "specs" / "journey.md"
        # ensure parent directory creation works
        if md_path.parent.exists():
            import shutil
            shutil.rmtree(md_path.parent)
        journey = generate_journey_markdown("生成产品", "角色A", md_path)
        assert md_path.exists()
        assert isinstance(journey, JourneyMap)

    def test_validate_journey(self, tmp_path: Path) -> None:
        journey = JourneyDesigner.create_minimal_journey("V产品", "U")
        md_path = tmp_path / "journey.md"
        JourneyDesigner.export_to_file(journey, md_path)
        result = validate_journey(md_path)
        assert result.passed is True


# ══════════════════════════════════════════════════════════════════
# 5. 枚举测试
# ══════════════════════════════════════════════════════════════════

class TestEnums:
    """测试枚举值"""

    def test_stage_types(self) -> None:
        assert StageType.AWARENESS.value == "awareness"
        assert len(StageType) == 8  # awareness to exit

    def test_emotion_levels(self) -> None:
        assert EmotionLevel.VERY_NEGATIVE.value == 1
        assert EmotionLevel.VERY_POSITIVE.value == 5

    def test_channels(self) -> None:
        assert Channel.WEB.value == "web"
        assert Channel.CHAT.value == "chat"


# ══════════════════════════════════════════════════════════════════
# 6. JTBD 集成测试
# ══════════════════════════════════════════════════════════════════

class TestJTBD:
    """测试 JTBD 集成"""

    def test_jtbd_creation(self) -> None:
        jtbd = JobToBeDone(
            job_statement="当我需要快速审批时，我想一键审批，以便不耽误项目进度",
            functional_aspects=["减少点击次数"],
            emotional_aspects=["减少焦虑"],
            social_aspects=["显得高效"],
            current_alternatives=["邮件审批"],
        )
        assert "一键审批" in jtbd.job_statement

    def test_journey_with_jtbd(self) -> None:
        journey = JourneyDesigner.create_minimal_journey("JTBD产品", "用户")
        journey.jobs_to_be_done = [
            JobToBeDone(
                job_statement="完成任务管理",
                functional_aspects=["创建任务", "分配任务"],
                emotional_aspects=["掌控感"],
                social_aspects=["团队协作"],
                current_alternatives=["Excel"],
            )
        ]
        md = journey.to_markdown()
        assert "Jobs to Be Done" in md
        assert "完成任务管理" in md
