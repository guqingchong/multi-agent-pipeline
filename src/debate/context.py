"""
辩论上下文构建模块
用于构建完整的prompt，整合各种上下文信息
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json
from datetime import datetime


@dataclass
class Statement:
    """发言记录数据结构"""
    speaker: str
    content: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Participant:
    """参与者信息数据结构"""
    name: str
    role: str
    expertise: str
    position: str


class PromptType(Enum):
    """Prompt类型枚举"""
    INITIAL = "initial"
    REPLY = "reply"
    EVALUATION = "evaluation"
    CONVERGENCE_CHECK = "convergence_check"


class ContextBuilder:
    """
    上下文构建器，用于构建辩论所需的完整prompt
    """
    
    def __init__(self):
        self.participants: List[Participant] = []
        self.statements: List[Statement] = []
        self.topic = ""
        self.rules = ""
        self.constraints = {}
        self.metadata = {}

    def add_participant(self, participant: Participant):
        """添加参与者"""
        self.participants.append(participant)

    def add_statement(self, statement: Statement):
        """添加发言"""
        self.statements.append(statement)

    def set_topic(self, topic: str):
        """设置辩论主题"""
        self.topic = topic

    def set_rules(self, rules: str):
        """设置辩论规则"""
        self.rules = rules

    def add_constraint(self, key: str, value: Any):
        """添加约束条件"""
        self.constraints[key] = value

    def set_metadata(self, metadata: Dict[str, Any]):
        """设置元数据"""
        self.metadata = metadata

    def build_initial_prompt(self) -> str:
        """构建初始prompt"""
        prompt_parts = []
        
        # 添加系统指令
        prompt_parts.append("# 辩论系统指令")
        prompt_parts.append("你是一个高级AI辩论助手，参与多轮辩论以达成共识或最佳解决方案。")
        prompt_parts.append("请遵循辩论礼仪，尊重其他参与者观点，并基于逻辑和事实进行论证。")
        prompt_parts.append("")
        
        # 添加辩论主题
        prompt_parts.append("## 辩论主题")
        prompt_parts.append(self.topic)
        prompt_parts.append("")
        
        # 添加参与者信息
        prompt_parts.append("## 参与者信息")
        for i, participant in enumerate(self.participants):
            prompt_parts.append(f"### 参与者{i+1}: {participant.name}")
            prompt_parts.append(f"- 角色: {participant.role}")
            prompt_parts.append(f"- 专业领域: {participant.expertise}")
            prompt_parts.append(f"- 立场: {participant.position}")
            prompt_parts.append("")
        
        # 添加辩论规则
        if self.rules:
            prompt_parts.append("## 辩论规则")
            prompt_parts.append(self.rules)
            prompt_parts.append("")
        
        # 添加历史发言
        if self.statements:
            prompt_parts.append("## 历史发言记录")
            for stmt in self.statements:
                prompt_parts.append(f"**{stmt.speaker}** ({stmt.timestamp.strftime('%H:%M:%S')}):")
                prompt_parts.append(f"{stmt.content}")
                prompt_parts.append("")
        
        # 添加约束条件
        if self.constraints:
            prompt_parts.append("## 约束条件")
            for key, value in self.constraints.items():
                prompt_parts.append(f"- {key}: {value}")
            prompt_parts.append("")
        
        # 添加输出格式要求
        prompt_parts.append("## 输出要求")
        prompt_parts.append("请按以下格式输出你的回应:")
        prompt_parts.append("### 观点阐述")
        prompt_parts.append("[在此处阐述你的观点]")
        prompt_parts.append("")
        prompt_parts.append("### 论证支撑")
        prompt_parts.append("[在此处提供支撑你观点的论据]")
        prompt_parts.append("")
        prompt_parts.append("### 对他人观点的回应")
        prompt_parts.append("[在此处回应其他参与者提出的观点]")
        prompt_parts.append("")
        prompt_parts.append("### 建设性建议")
        prompt_parts.append("[如有，提出建设性意见或妥协方案]")
        prompt_parts.append("")
        
        return "\n".join(prompt_parts)

    def build_reply_prompt(self, current_speaker: str) -> str:
        """构建回复prompt"""
        prompt_parts = []
        
        # 添加系统指令
        prompt_parts.append("# 回复构建指令")
        prompt_parts.append(f"你是{current_speaker}，正在参与辩论。")
        prompt_parts.append("请根据之前的讨论内容，构建一个有建设性的回复。")
        prompt_parts.append("")
        
        # 添加辩论主题
        prompt_parts.append("## 辩论主题")
        prompt_parts.append(self.topic)
        prompt_parts.append("")
        
        # 添加当前发言者信息
        current_participant = next((p for p in self.participants if p.name == current_speaker), None)
        if current_participant:
            prompt_parts.append(f"## 你的信息")
            prompt_parts.append(f"- 你的角色: {current_participant.role}")
            prompt_parts.append(f"- 你的专业领域: {current_participant.expertise}")
            prompt_parts.append(f"- 你的立场: {current_participant.position}")
            prompt_parts.append("")
        
        # 添加最近的发言记录
        prompt_parts.append("## 最近的发言记录")
        # 只显示最近的几条发言
        recent_statements = self.statements[-5:]  # 显示最近5条发言
        for stmt in recent_statements:
            prompt_parts.append(f"**{stmt.speaker}** ({stmt.timestamp.strftime('%H:%M:%S')}):")
            prompt_parts.append(f"{stmt.content}")
            prompt_parts.append("")
        
        # 添加辩论规则
        if self.rules:
            prompt_parts.append("## 辩论规则")
            prompt_parts.append(self.rules)
            prompt_parts.append("")
        
        # 添加约束条件
        if self.constraints:
            prompt_parts.append("## 约束条件")
            for key, value in self.constraints.items():
                prompt_parts.append(f"- {key}: {value}")
            prompt_parts.append("")
        
        # 添加回复指导
        prompt_parts.append("## 回复指导")
        prompt_parts.append(f"作为{current_speaker}，请构建一个合适的回复，考虑:")
        prompt_parts.append("- 与其他参与者观点的一致性和差异")
        prompt_parts.append("- 提供新的视角或证据来支持你的立场")
        prompt_parts.append("- 尝试寻找共同点以促进共识")
        prompt_parts.append("- 避免重复之前已经充分讨论的内容")
        prompt_parts.append("")
        
        # 添加输出格式要求
        prompt_parts.append("## 输出要求")
        prompt_parts.append("请按以下格式输出你的回应:")
        prompt_parts.append("### 观点阐述")
        prompt_parts.append("[在此处阐述你的观点]")
        prompt_parts.append("")
        prompt_parts.append("### 论证支撑")
        prompt_parts.append("[在此处提供支撑你观点的论据]")
        prompt_parts.append("")
        prompt_parts.append("### 对他人观点的回应")
        prompt_parts.append("[在此处回应其他参与者提出的观点]")
        prompt_parts.append("")
        prompt_parts.append("### 建设性建议")
        prompt_parts.append("[如有，提出建设性意见或妥协方案]")
        prompt_parts.append("")
        
        return "\n".join(prompt_parts)

    def build_evaluation_prompt(self) -> str:
        """构建评估prompt"""
        prompt_parts = []
        
        # 添加系统指令
        prompt_parts.append("# 评估指令")
        prompt_parts.append("你正在评估辩论的进展和质量。")
        prompt_parts.append("请分析辩论的有效性、参与者的表现以及达成共识的可能性。")
        prompt_parts.append("")
        
        # 添加辩论主题
        prompt_parts.append("## 辩论主题")
        prompt_parts.append(self.topic)
        prompt_parts.append("")
        
        # 添加参与者信息
        prompt_parts.append("## 参与者信息")
        for i, participant in enumerate(self.participants):
            prompt_parts.append(f"### 参与者{i+1}: {participant.name}")
            prompt_parts.append(f"- 角色: {participant.role}")
            prompt_parts.append(f"- 专业领域: {participant.expertise}")
            prompt_parts.append(f"- 立场: {participant.position}")
            prompt_parts.append("")
        
        # 添加完整的发言记录
        prompt_parts.append("## 完整发言记录")
        for stmt in self.statements:
            prompt_parts.append(f"**{stmt.speaker}** ({stmt.timestamp.strftime('%H:%M:%S')}):")
            prompt_parts.append(f"{stmt.content}")
            prompt_parts.append("")
        
        # 添加评估标准
        prompt_parts.append("## 评估标准")
        prompt_parts.append("请从以下角度进行评估:")
        prompt_parts.append("- 逻辑一致性: 论点是否有逻辑支撑")
        prompt_parts.append("- 证据质量: 提供的证据是否可靠有效")
        prompt_parts.append("- 参与度: 各参与者是否积极参与讨论")
        prompt_parts.append("- 建设性: 是否有助于达成共识或找到解决方案")
        prompt_parts.append("- 礼貌性: 是否尊重其他参与者")
        prompt_parts.append("")
        
        # 添加输出格式要求
        prompt_parts.append("## 输出要求")
        prompt_parts.append("请按以下格式提供评估:")
        prompt_parts.append("### 整体评价")
        prompt_parts.append("[对辩论整体进展的评价]")
        prompt_parts.append("")
        prompt_parts.append("### 参与者表现")
        for participant in self.participants:
            prompt_parts.append(f"#### {participant.name}")
            prompt_parts.append("- 优点: [列出优点]")
            prompt_parts.append("- 改进空间: [指出可改进之处]")
            prompt_parts.append("- 贡献度: [评估其对辩论的贡献]")
            prompt_parts.append("")
        prompt_parts.append("")
        prompt_parts.append("### 共识可能性")
        prompt_parts.append("[评估各方达成共识的可能性及建议]")
        prompt_parts.append("")
        prompt_parts.append("### 后续步骤建议")
        prompt_parts.append("[如果辩论继续，提出下一步的建议]")
        prompt_parts.append("")
        
        return "\n".join(prompt_parts)

    def build_convergence_check_prompt(self) -> str:
        """构建收敛检查prompt"""
        prompt_parts = []
        
        # 添加系统指令
        prompt_parts.append("# 收敛检查指令")
        prompt_parts.append("你正在检查辩论是否已达到收敛状态。")
        prompt_parts.append("请分析当前辩论状态并判断是否已达成共识或接近解决方案。")
        prompt_parts.append("")
        
        # 添加辩论主题
        prompt_parts.append("## 辩论主题")
        prompt_parts.append(self.topic)
        prompt_parts.append("")
        
        # 添加参与者信息
        prompt_parts.append("## 参与者信息")
        for i, participant in enumerate(self.participants):
            prompt_parts.append(f"### 参与者{i+1}: {participant.name}")
            prompt_parts.append(f"- 角色: {participant.role}")
            prompt_parts.append(f"- 专业领域: {participant.expertise}")
            prompt_parts.append(f"- 当前立场: {participant.position}")
            prompt_parts.append("")
        
        # 添加近期发言记录
        prompt_parts.append("## 近期发言记录")
        # 只显示最近的发言
        recent_statements = self.statements[-8:]  # 显示最近8条发言
        for stmt in recent_statements:
            prompt_parts.append(f"**{stmt.speaker}** ({stmt.timestamp.strftime('%H:%M:%S')}):")
            prompt_parts.append(f"{stmt.content}")
            prompt_parts.append("")
        
        # 添加收敛判断标准
        prompt_parts.append("## 收敛判断标准")
        prompt_parts.append("请根据以下标准判断辩论是否已收敛:")
        prompt_parts.append("- 观点趋同: 各方观点是否趋于一致")
        prompt_parts.append("- 争议减少: 是否还有重大分歧需要解决")
        prompt_parts.append("- 解决方案明确: 是否提出了可行的解决方案")
        prompt_parts.append("- 参与者满意度: 各方是否对当前状态满意")
        prompt_parts.append("- 讨论效率: 继续讨论是否可能带来显著改进")
        prompt_parts.append("")
        
        # 添加输出格式要求
        prompt_parts.append("## 输出要求")
        prompt_parts.append("请按以下格式提供收敛分析:")
        prompt_parts.append("### 收敛评估")
        prompt_parts.append("- 当前收敛程度 (0-1): [数值]")
        prompt_parts.append("- 是否已收敛: [是/否]")
        prompt_parts.append("- 判断依据: [说明判断依据]")
        prompt_parts.append("")
        prompt_parts.append("### 详细分析")
        prompt_parts.append("- 观点一致性: [分析各方观点的一致性程度]")
        prompt_parts.append("- 主要分歧: [如仍有分歧，指出主要分歧点]")
        prompt_parts.append("- 解决方案评估: [评估提出的解决方案可行性]")
        prompt_parts.append("")
        prompt_parts.append("### 建议")
        prompt_parts.append("- 是否继续辩论: [建议是否继续辩论]")
        prompt_parts.append("- 如继续，重点方向: [如继续辩论，应关注的重点]")
        prompt_parts.append("- 如结束，总结要点: [如结束辩论，总结关键成果]")
        prompt_parts.append("")
        
        return "\n".join(prompt_parts)

    def build_prompt(self, prompt_type: PromptType, **kwargs) -> str:
        """根据类型构建prompt"""
        if prompt_type == PromptType.INITIAL:
            return self.build_initial_prompt()
        elif prompt_type == PromptType.REPLY:
            current_speaker = kwargs.get('current_speaker', '')
            return self.build_reply_prompt(current_speaker)
        elif prompt_type == PromptType.EVALUATION:
            return self.build_evaluation_prompt()
        elif prompt_type == PromptType.CONVERGENCE_CHECK:
            return self.build_convergence_check_prompt()
        else:
            raise ValueError(f"不支持的prompt类型: {prompt_type}")

    def get_context_summary(self) -> Dict[str, Any]:
        """获取上下文摘要"""
        return {
            "topic": self.topic,
            "participant_count": len(self.participants),
            "statement_count": len(self.statements),
            "participants": [p.name for p in self.participants],
            "latest_statement_time": self.statements[-1].timestamp.isoformat() if self.statements else None,
            "constraints_keys": list(self.constraints.keys()) if self.constraints else []
        }