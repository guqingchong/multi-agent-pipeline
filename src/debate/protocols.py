"""
辩论协议实现模块
包含 NI (Negotiated Iteration)、MORE (Multi-Objective Reasoning Exchange) 和 SAMRE (Structured Argumentative Multi-Reasoning Exchange) 协议
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import random


class ProtocolType(Enum):
    """协议类型枚举"""
    NI = "negotiated_iteration"
    MORE = "multi_objective_reasoning_exchange"
    SAMRE = "structured_argumentative_multi_reasoning_exchange"


@dataclass
class Argument:
    """论证数据结构"""
    speaker: str
    claim: str
    evidence: List[str]
    reasoning: str
    timestamp: datetime
    confidence: float = 1.0
    relevance: float = 1.0


@dataclass
class CounterArgument:
    """反驳数据结构"""
    speaker: str
    target_argument_id: str
    counter_claim: str
    counter_evidence: List[str]
    counter_reasoning: str
    timestamp: datetime
    strength: float = 1.0


@dataclass
class AgreementProposal:
    """协议提案数据结构"""
    proposer: str
    proposed_solution: str
    supporting_arguments: List[str]
    conditions: List[str]
    timestamp: datetime
    acceptance_probability: float = 0.0


class BaseProtocol(ABC):
    """基础协议抽象类"""
    
    def __init__(self, protocol_type: ProtocolType):
        self.protocol_type = protocol_type
        self.arguments: List[Argument] = []
        self.counter_arguments: List[CounterArgument] = []
        self.agreements: List[AgreementProposal] = []
        self.history: List[Dict[str, Any]] = []
        self.current_phase = "initial"

    @abstractmethod
    def initialize_debate(self, participants: List[str], topic: str) -> str:
        """初始化辩论"""
        pass

    @abstractmethod
    def process_turn(self, speaker: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理单个回合"""
        pass

    @abstractmethod
    def check_convergence(self) -> Tuple[bool, float]:
        """检查收敛条件"""
        pass

    def add_argument(self, argument: Argument):
        """添加论证"""
        self.arguments.append(argument)
        self.history.append({
            "action": "add_argument",
            "argument": argument,
            "timestamp": datetime.now()
        })

    def add_counter_argument(self, counter_arg: CounterArgument):
        """添加反驳"""
        self.counter_arguments.append(counter_arg)
        self.history.append({
            "action": "add_counter_argument",
            "counter_argument": counter_arg,
            "timestamp": datetime.now()
        })

    def add_agreement_proposal(self, proposal: AgreementProposal):
        """添加协议提案"""
        self.agreements.append(proposal)
        self.history.append({
            "action": "add_agreement_proposal",
            "proposal": proposal,
            "timestamp": datetime.now()
        })

    def get_protocol_state(self) -> Dict[str, Any]:
        """获取协议状态"""
        return {
            "protocol_type": self.protocol_type.value,
            "current_phase": self.current_phase,
            "argument_count": len(self.arguments),
            "counter_argument_count": len(self.counter_arguments),
            "agreement_count": len(self.agreements),
            "history_length": len(self.history)
        }


class NegotiatedIterationProtocol(BaseProtocol):
    """
    NI (Negotiated Iteration) 协议
    一种迭代式协商协议，允许参与者逐步调整立场并达成共识
    """
    
    def __init__(self):
        super().__init__(ProtocolType.NI)
        self.iteration_limit = 10
        self.current_iteration = 0
        self.min_improvement_threshold = 0.05
        self.last_agreement_score = 0.0

    def initialize_debate(self, participants: List[str], topic: str) -> str:
        """初始化NI协议辩论"""
        self.current_phase = "negotiation_setup"
        self.participants = participants
        self.topic = topic
        
        prompt = f"""
# NI (Negotiated Iteration) 协议启动

## 辩论主题
{topic}

## 参与者
{', '.join(participants)}

## 协议规则
1. 每个参与者依次表达自己的立场和理由
2. 在每轮迭代中，参与者可以调整自己的立场
3. 协议最多进行 {self.iteration_limit} 轮迭代
4. 如果连续两轮没有显著改进（改善幅度 < {self.min_improvement_threshold}），则提前结束
5. 最终目标是达成最大程度的共识

## 当前状态
- 当前轮次: {self.current_iteration + 1}/{self.iteration_limit}
- 当前阶段: {self.current_phase}

请第一位参与者开始阐述其立场。
"""
        return prompt.strip()

    def process_turn(self, speaker: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理NI协议单个回合"""
        self.current_phase = "negotiation_active"
        
        # 解析输入数据
        stance = input_data.get("stance", "")
        reasoning = input_data.get("reasoning", "")
        evidence = input_data.get("evidence", [])
        adjustment = input_data.get("adjustment", 0.0)  # 立场调整程度
        
        # 创建论证对象
        argument = Argument(
            speaker=speaker,
            claim=stance,
            evidence=evidence,
            reasoning=reasoning,
            timestamp=datetime.now(),
            confidence=input_data.get("confidence", 1.0),
            relevance=input_data.get("relevance", 1.0)
        )
        
        self.add_argument(argument)
        
        # 更新迭代计数
        if self.current_iteration < self.iteration_limit:
            self.current_iteration += 1
        
        # 计算当前协议分数
        current_agreement_score = self._calculate_agreement_score()
        
        # 检查是否满足提前终止条件
        improvement = abs(current_agreement_score - self.last_agreement_score)
        early_termination = improvement < self.min_improvement_threshold and self.current_iteration > 2
        
        self.last_agreement_score = current_agreement_score
        
        return {
            "success": True,
            "message": f"{speaker} 的回合已完成",
            "current_iteration": self.current_iteration,
            "agreement_score": current_agreement_score,
            "early_termination": early_termination,
            "next_speaker": self._get_next_speaker(speaker),
            "recommended_action": self._get_recommendation_for_speaker(speaker, current_agreement_score)
        }

    def check_convergence(self) -> Tuple[bool, float]:
        """检查NI协议收敛条件"""
        # 检查迭代次数
        if self.current_iteration >= self.iteration_limit:
            return True, self._calculate_agreement_score()
        
        # 检查协议分数是否稳定
        current_score = self._calculate_agreement_score()
        if abs(current_score - self.last_agreement_score) < self.min_improvement_threshold:
            return True, current_score
        
        return False, current_score

    def _calculate_agreement_score(self) -> float:
        """计算协议分数"""
        if not self.arguments:
            return 0.0
        
        # 计算平均置信度
        avg_confidence = sum(arg.confidence for arg in self.arguments) / len(self.arguments)
        
        # 计算立场相似度（简化版）
        if len(self.arguments) > 1:
            # 这里使用随机值模拟立场相似度计算
            # 在实际应用中，这里应该有更复杂的语义相似度计算
            similarity_score = random.uniform(0.3, 1.0)
        else:
            similarity_score = 1.0
        
        # 综合得分
        agreement_score = (avg_confidence + similarity_score) / 2
        return min(agreement_score, 1.0)  # 限制在0-1范围内

    def _get_next_speaker(self, current_speaker: str) -> str:
        """获取下一个发言人"""
        if hasattr(self, 'participants'):
            current_idx = self.participants.index(current_speaker)
            return self.participants[(current_idx + 1) % len(self.participants)]
        return ""

    def _get_recommendation_for_speaker(self, speaker: str, agreement_score: float) -> str:
        """为发言人提供建议"""
        if agreement_score > 0.8:
            return f"{speaker}，当前协议分数较高({agreement_score:.2f})，建议总结共识点并提出具体行动方案"
        elif agreement_score > 0.5:
            return f"{speaker}，当前协议分数中等({agreement_score:.2f})，建议寻找更多共同点或提出折衷方案"
        else:
            return f"{speaker}，当前协议分数较低({agreement_score:.2f})，建议澄清核心分歧并尝试理解对方立场"


class MultiObjectiveReasoningExchangeProtocol(BaseProtocol):
    """
    MORE (Multi-Objective Reasoning Exchange) 协议
    多目标推理交换协议，允许参与者同时考虑多个目标维度进行辩论
    """
    
    def __init__(self):
        super().__init__(ProtocolType.MORE)
        self.objectives: List[str] = []
        self.weights: Dict[str, float] = {}
        self.current_objective_idx = 0

    def initialize_debate(self, participants: List[str], topic: str) -> str:
        """初始化MORE协议辩论"""
        self.current_phase = "objective_setup"
        self.participants = participants
        self.topic = topic
        
        # 默认目标（可根据具体话题调整）
        self.objectives = ["有效性", "效率", "公平性", "可持续性"]
        for obj in self.objectives:
            self.weights[obj] = 1.0 / len(self.objectives)  # 平均权重
        
        prompt = f"""
# MORE (Multi-Objective Reasoning Exchange) 协议启动

## 辩论主题
{topic}

## 参与者
{', '.join(participants)}

## 多目标框架
本辩论将围绕以下目标进行评估：
"""
        for i, objective in enumerate(self.objectives):
            prompt += f"{i+1}. {objective} (权重: {self.weights[objective]:.2f})\n"
        
        prompt += f"""
## 协议规则
1. 每个参与者需针对每个目标分别阐述观点
2. 提供跨目标的权衡分析
3. 评估不同方案在各目标下的表现
4. 寻找帕累托最优解或最小化遗憾解

## 当前状态
- 当前聚焦目标: {self.objectives[self.current_objective_idx]}
- 当前阶段: {self.current_phase}

请第一位参与者开始针对第一个目标“{self.objectives[0]}”阐述观点。
"""
        return prompt.strip()

    def process_turn(self, speaker: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理MORE协议单个回合"""
        self.current_phase = "reasoning_exchange_active"
        
        # 解析输入数据
        objective_assessment = input_data.get("objective_assessment", {})
        cross_objective_analysis = input_data.get("cross_objective_analysis", "")
        solution_proposal = input_data.get("solution_proposal", "")
        
        # 为每个目标创建论证
        for objective, assessment in objective_assessment.items():
            argument = Argument(
                speaker=speaker,
                claim=f"关于{objective}的观点: {assessment}",
                evidence=[],
                reasoning=cross_objective_analysis,
                timestamp=datetime.now(),
                confidence=input_data.get("confidence", 1.0)
            )
            self.add_argument(argument)
        
        # 如果提供了整体解决方案，也添加为论证
        if solution_proposal:
            solution_argument = Argument(
                speaker=speaker,
                claim=solution_proposal,
                evidence=[],
                reasoning=f"跨目标综合分析: {cross_objective_analysis}",
                timestamp=datetime.now(),
                confidence=input_data.get("confidence", 1.0)
            )
            self.add_argument(solution_argument)
        
        # 移动到下一个目标（循环）
        self.current_objective_idx = (self.current_objective_idx + 1) % len(self.objectives)
        
        return {
            "success": True,
            "message": f"{speaker} 关于 {self.objectives[self.current_objective_idx-1]} 的回合已完成",
            "current_objective": self.objectives[self.current_objective_idx],
            "next_speaker": self._get_next_speaker(speaker),
            "recommendation": f"请 {self._get_next_speaker(speaker)} 针对 '{self.objectives[self.current_objective_idx]}' 目标发表观点"
        }

    def check_convergence(self) -> Tuple[bool, float]:
        """检查MORE协议收敛条件"""
        # MORE协议的收敛检查较为复杂，这里简化实现
        # 检查是否所有目标都有足够的讨论
        if len(self.arguments) >= len(self.participants) * len(self.objectives) * 2:  # 每人每目标至少两次讨论
            return True, self._calculate_agreement_score()
        
        return False, self._calculate_agreement_score()

    def _calculate_agreement_score(self) -> float:
        """计算MORE协议的协议分数"""
        if not self.arguments:
            return 0.0
        
        # 基于论证数量和参与者数量计算分数
        total_possible_interactions = len(self.participants) * len(self.objectives) * 2
        actual_interactions = len(self.arguments)
        
        score = min(actual_interactions / total_possible_interactions, 1.0)
        
        # 考虑论证的多样性
        unique_claims = set(arg.claim for arg in self.arguments)
        diversity_bonus = len(unique_claims) / len(self.arguments) * 0.2
        
        return min(score + diversity_bonus, 1.0)

    def _get_next_speaker(self, current_speaker: str) -> str:
        """获取下一个发言人"""
        if hasattr(self, 'participants'):
            current_idx = self.participants.index(current_speaker)
            return self.participants[(current_idx + 1) % len(self.participants)]
        return ""


class StructuredArgumentativeMultiReasoningExchangeProtocol(BaseProtocol):
    """
    SAMRE (Structured Argumentative Multi-Reasoning Exchange) 协议
    结构化论证多推理交换协议，采用严格的论证结构进行辩论
    """
    
    def __init__(self):
        super().__init__(ProtocolType.SAMRE)
        self.argument_structure = {
            "claim": "",
            "premises": [],
            "supporting_evidence": [],
            "potential_counterarguments": [],
            "rebuttals": [],
            "qualifiers": [],
            "warrants": []
        }
        self.current_stage = "presentation"

    def initialize_debate(self, participants: List[str], topic: str) -> str:
        """初始化SAMRE协议辩论"""
        self.current_phase = "structured_setup"
        self.participants = participants
        self.topic = topic
        
        prompt = f"""
# SAMRE (Structured Argumentative Multi-Reasoning Exchange) 协议启动

## 辩论主题
{topic}

## 参与者
{', '.join(participants)}

## 结构化论证框架
每个参与者需要按照以下结构构建论证：
1. **主张 (Claim)**: 核心论点
2. **前提 (Premises)**: 支撑主张的前提条件
3. **支撑证据 (Supporting Evidence)**: 数据、事实或其他证据
4. **潜在反驳 (Potential Counterarguments)**: 预期的反对意见
5. **反驳回应 (Rebuttals)**: 对潜在反驳的回应
6. **限定词 (Qualifiers)**: 表示确定性的词语（如"通常"、"在某些情况下"）
7. **担保 (Warrants)**: 连接证据和主张的逻辑链条

## 协议阶段
- **呈现阶段 (Presentation)**: 各方陈述结构化论证
- **检验阶段 (Examination)**: 质疑和检验论证的有效性
- **重构阶段 (Reconstruction)**: 基于反馈调整论证
- **收敛阶段 (Convergence)**: 寻求共识或最佳解决方案

## 当前阶段
- 当前阶段: {self.current_stage}

请第一位参与者开始按照结构化框架陈述其论证。
"""
        return prompt.strip()

    def process_turn(self, speaker: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理SAMRE协议单个回合"""
        # 解析结构化论证数据
        claim = input_data.get("claim", "")
        premises = input_data.get("premises", [])
        evidence = input_data.get("supporting_evidence", [])
        potential_counterarguments = input_data.get("potential_counterarguments", [])
        rebuttals = input_data.get("rebuttals", [])
        qualifiers = input_data.get("qualifiers", [])
        warrants = input_data.get("warrants", [])
        
        # 创建论证对象
        argument = Argument(
            speaker=speaker,
            claim=claim,
            evidence=evidence,
            reasoning=f"前提: {premises}\n担保: {warrants}\n限定词: {qualifiers}",
            timestamp=datetime.now(),
            confidence=input_data.get("confidence", 1.0)
        )
        
        self.add_argument(argument)
        
        # 检查是否存在反驳
        received_counterarguments = input_data.get("received_counterarguments", [])
        for counterarg in received_counterarguments:
            counter_argument = CounterArgument(
                speaker=counterarg.get("speaker", ""),
                target_argument_id=str(len(self.arguments)),  # 简化的ID分配
                counter_claim=counterarg.get("counter_claim", ""),
                counter_evidence=counterarg.get("counter_evidence", []),
                counter_reasoning=counterarg.get("counter_reasoning", ""),
                timestamp=datetime.now(),
                strength=counterarg.get("strength", 0.5)
            )
            self.add_counter_argument(counter_argument)
        
        # 更新阶段（简化）
        stages = ["presentation", "examination", "reconstruction", "convergence"]
        current_idx = stages.index(self.current_stage)
        if current_idx < len(stages) - 1:
            self.current_stage = stages[current_idx + 1]
        
        return {
            "success": True,
            "message": f"{speaker} 的结构化论证回合已完成",
            "current_stage": self.current_stage,
            "argument_validated": True,
            "next_speaker": self._get_next_speaker(speaker),
            "recommendation": self._get_stage_recommendation()
        }

    def check_convergence(self) -> Tuple[bool, float]:
        """检查SAMRE协议收敛条件"""
        # 检查是否完成所有阶段
        if self.current_stage == "convergence":
            # 在收敛阶段，检查是否提出了协议提案
            return len(self.agreements) > 0, self._calculate_agreement_score()
        
        return False, self._calculate_agreement_score()

    def _calculate_agreement_score(self) -> float:
        """计算SAMRE协议的协议分数"""
        if not self.arguments:
            return 0.0
        
        # 计算论证质量和反驳处理情况
        avg_confidence = sum(arg.confidence for arg in self.arguments) / len(self.arguments)
        
        # 考虑反驳的处理情况
        if self.counter_arguments:
            handled_ratio = sum(1 for ca in self.counter_arguments if ca.strength > 0.5) / len(self.counter_arguments)
        else:
            handled_ratio = 1.0
        
        # 综合得分
        score = (avg_confidence + handled_ratio) / 2
        return min(score, 1.0)

    def _get_next_speaker(self, current_speaker: str) -> str:
        """获取下一个发言人"""
        if hasattr(self, 'participants'):
            current_idx = self.participants.index(current_speaker)
            return self.participants[(current_idx + 1) % len(self.participants)]
        return ""

    def _get_stage_recommendation(self) -> str:
        """获取当前阶段的推荐"""
        if self.current_stage == "presentation":
            return "请专注于清晰地陈述你的结构化论证"
        elif self.current_stage == "examination":
            return "请仔细检验其他参与者的论证，提出有针对性的问题"
        elif self.current_stage == "reconstruction":
            return "请根据收到的反馈调整和完善你的论证"
        elif self.current_stage == "convergence":
            return "请考虑提出具体的解决方案或协议"
        else:
            return "请继续参与辩论"


class ProtocolFactory:
    """协议工厂类，用于创建不同的辩论协议实例"""
    
    @staticmethod
    def create_protocol(protocol_type: ProtocolType) -> BaseProtocol:
        """根据协议类型创建协议实例"""
        if protocol_type == ProtocolType.NI:
            return NegotiatedIterationProtocol()
        elif protocol_type == ProtocolType.MORE:
            return MultiObjectiveReasoningExchangeProtocol()
        elif protocol_type == ProtocolType.SAMRE:
            return StructuredArgumentativeMultiReasoningExchangeProtocol()
        else:
            raise ValueError(f"不支持的协议类型: {protocol_type}")


def get_available_protocols() -> List[ProtocolType]:
    """获取可用协议列表"""
    return [ProtocolType.NI, ProtocolType.MORE, ProtocolType.SAMRE]


def select_protocol_by_topic(topic: str) -> ProtocolType:
    """
    根据话题自动选择最合适的协议
    这是一个简化的实现，实际应用中可能需要更复杂的决策逻辑
    """
    topic_lower = topic.lower()
    
    # 根据话题关键词选择协议
    ni_keywords = ["协商", "谈判", "迭代", "调整", "妥协"]
    more_keywords = ["多目标", "权衡", "平衡", "评估", "比较"]
    samre_keywords = ["结构化", "论证", "逻辑", "分析", "审查"]
    
    # 计算匹配度
    ni_score = sum(1 for keyword in ni_keywords if keyword in topic_lower)
    more_score = sum(1 for keyword in more_keywords if keyword in topic_lower)
    samre_score = sum(1 for keyword in samre_keywords if keyword in topic_lower)
    
    # 返回得分最高的协议类型
    scores = {
        ProtocolType.NI: ni_score,
        ProtocolType.MORE: more_score,
        ProtocolType.SAMRE: samre_score
    }
    
    return max(scores, key=scores.get)