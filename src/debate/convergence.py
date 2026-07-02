"""
辩论收敛判断与预算停止机制
用于判断辩论是否达到收敛状态以及是否需要因预算耗尽而停止
"""

from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import math


class ConvergenceStatus(Enum):
    """收敛状态枚举"""
    NOT_CONVERGED = "not_converged"
    CONVERGED = "converged"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STALEMATE = "stalemate"


class BudgetType(Enum):
    """预算类型枚举"""
    ITERATIONS = "iterations"
    TIME = "time"
    COMPUTATION = "computation"
    TOKENS = "tokens"


class ConvergenceChecker:
    """
    收敛检查器
    负责判断辩论是否已达到收敛状态
    """
    
    def __init__(self, threshold: float = 0.85, min_iteration: int = 3):
        self.threshold = threshold  # 收敛阈值
        self.min_iteration = min_iteration  # 最少迭代次数
        self.agreement_history: List[float] = []  # 协议分数历史
        self.divergence_counter = 0  # 发散计数器
        self.max_divergence_count = 3  # 最大发散次数
        self.stability_window = 5  # 稳定性检查窗口大小

    def update_agreement_score(self, score: float):
        """更新协议分数历史"""
        self.agreement_history.append(score)
        
        # 检查是否出现发散
        if len(self.agreement_history) >= 2:
            if self.agreement_history[-1] < self.agreement_history[-2]:
                self.divergence_counter += 1
            else:
                self.divergence_counter = 0  # 重置发散计数

    def check_convergence(self, current_iteration: int) -> Tuple[ConvergenceStatus, float, str]:
        """
        检查收敛状态
        返回: (收敛状态, 当前协议分数, 描述信息)
        """
        if not self.agreement_history:
            return ConvergenceStatus.NOT_CONVERGED, 0.0, "尚未有足够的数据进行收敛判断"
        
        current_score = self.agreement_history[-1]
        
        # 检查是否达到收敛阈值
        if current_score >= self.threshold and current_iteration >= self.min_iteration:
            return ConvergenceStatus.CONVERGED, current_score, f"协议分数 {current_score:.3f} 已达到收敛阈值 {self.threshold}"
        
        # 检查是否出现僵局（持续发散）
        if self.divergence_counter >= self.max_divergence_count:
            return ConvergenceStatus.STALEMATE, current_score, f"辩论出现僵局，已连续 {self.divergence_counter} 次发散"
        
        # 检查稳定性（在稳定窗口内分数变化很小）
        if len(self.agreement_history) >= self.stability_window:
            recent_scores = self.agreement_history[-self.stability_window:]
            score_variance = sum((score - current_score) ** 2 for score in recent_scores) / len(recent_scores)
            
            if score_variance < 0.001 and current_score > 0.6:  # 方差很小且分数不低
                return ConvergenceStatus.CONVERGED, current_score, f"协议分数在最近{self.stability_window}次迭代中保持稳定"
        
        return ConvergenceStatus.NOT_CONVERGED, current_score, f"协议分数 {current_score:.3f} 未达到收敛阈值 {self.threshold}"

    def get_convergence_trend(self) -> str:
        """获取收敛趋势分析"""
        if len(self.agreement_history) < 2:
            return "需要更多数据点以分析趋势"
        
        recent_scores = self.agreement_history[-5:]  # 最近5次分数
        if len(recent_scores) < 2:
            return "需要更多数据点以分析趋势"
        
        # 计算趋势
        differences = [recent_scores[i+1] - recent_scores[i] for i in range(len(recent_scores)-1)]
        avg_change = sum(differences) / len(differences)
        
        if avg_change > 0.05:
            trend = "积极提升"
        elif avg_change < -0.05:
            trend = "持续下降"
        else:
            trend = "相对稳定"
        
        return f"最近趋势: {trend} (平均变化: {avg_change:+.3f})"


class BudgetTracker:
    """
    预算跟踪器
    负责跟踪和管理辩论过程中的各种预算消耗
    """
    
    def __init__(self):
        self.budget_limits: Dict[BudgetType, Any] = {}
        self.budget_consumed: Dict[BudgetType, Any] = {}
        self.start_time = datetime.now()
        self.budget_exceeded_callbacks: List[callable] = []

    def set_budget_limit(self, budget_type: BudgetType, limit: Any):
        """设置预算限制"""
        self.budget_limits[budget_type] = limit
        self.budget_consumed[budget_type] = 0 if isinstance(limit, (int, float)) else self.start_time

    def consume_budget(self, budget_type: BudgetType, amount: Any = 1):
        """消耗预算"""
        if budget_type in self.budget_consumed:
            if budget_type == BudgetType.TIME:
                # 时间预算特殊处理
                self.budget_consumed[budget_type] = datetime.now()
            elif budget_type in [BudgetType.ITERATIONS, BudgetType.COMPUTATION, BudgetType.TOKENS]:
                # 数值型预算累加
                self.budget_consumed[budget_type] += amount
        else:
            # 首次记录
            self.budget_consumed[budget_type] = amount

    def is_budget_exceeded(self) -> Tuple[bool, List[BudgetType]]:
        """检查是否超出预算"""
        exceeded_types = []
        
        for budget_type, limit in self.budget_limits.items():
            consumed = self.budget_consumed.get(budget_type, 0)
            
            if budget_type == BudgetType.TIME:
                # 时间预算：检查是否超过限制的秒数
                elapsed = (datetime.now() - self.start_time).total_seconds()
                if elapsed > limit:
                    exceeded_types.append(budget_type)
            elif budget_type in [BudgetType.ITERATIONS, BudgetType.COMPUTATION, BudgetType.TOKENS]:
                # 数值型预算：直接比较
                if consumed > limit:
                    exceeded_types.append(budget_type)
        
        return len(exceeded_types) > 0, exceeded_types

    def get_remaining_budget(self, budget_type: BudgetType) -> Any:
        """获取剩余预算"""
        limit = self.budget_limits.get(budget_type)
        consumed = self.budget_consumed.get(budget_type, 0)
        
        if budget_type == BudgetType.TIME:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            return max(0, limit - elapsed) if limit else float('inf')
        elif budget_type in [BudgetType.ITERATIONS, BudgetType.COMPUTATION, BudgetType.TOKENS]:
            return max(0, limit - consumed) if limit else float('inf')
        else:
            return None

    def get_budget_utilization(self) -> Dict[BudgetType, float]:
        """获取预算利用率"""
        utilization = {}
        
        for budget_type, limit in self.budget_limits.items():
            consumed = self.budget_consumed.get(budget_type, 0)
            
            if budget_type == BudgetType.TIME:
                elapsed = (datetime.now() - self.start_time).total_seconds()
                utilization[budget_type] = min(elapsed / limit, 1.0) if limit != 0 else 0
            elif budget_type in [BudgetType.ITERATIONS, BudgetType.COMPUTATION, BudgetType.TOKENS]:
                utilization[budget_type] = min(consumed / limit, 1.0) if limit != 0 else 0
        
        return utilization

    def register_budget_exceeded_callback(self, callback: callable):
        """注册预算超限回调函数"""
        self.budget_exceeded_callbacks.append(callback)

    def notify_budget_exceeded(self, exceeded_types: List[BudgetType]):
        """通知预算超限"""
        for callback in self.budget_exceeded_callbacks:
            callback(exceeded_types)


class ConvergenceAnalyzer:
    """
    收敛分析器
    综合收敛检查和预算跟踪功能
    """
    
    def __init__(self, convergence_threshold: float = 0.85):
        self.convergence_checker = ConvergenceChecker(threshold=convergence_threshold)
        self.budget_tracker = BudgetTracker()
        self.iteration_count = 0

    def set_budget_limits(self, limits: Dict[BudgetType, Any]):
        """设置预算限制"""
        for budget_type, limit in limits.items():
            self.budget_tracker.set_budget_limit(budget_type, limit)

    def record_iteration(self, agreement_score: float, computation_cost: float = 1.0, token_usage: int = 0):
        """记录一次迭代"""
        self.iteration_count += 1
        self.convergence_checker.update_agreement_score(agreement_score)
        
        # 更新预算消耗
        self.budget_tracker.consume_budget(BudgetType.ITERATIONS, 1)
        self.budget_tracker.consume_budget(BudgetType.COMPUTATION, computation_cost)
        if token_usage > 0:
            self.budget_tracker.consume_budget(BudgetType.TOKENS, token_usage)

    def evaluate_state(self) -> Tuple[ConvergenceStatus, Dict[str, Any]]:
        """
        评估当前状态
        返回: (收敛状态, 状态详情)
        """
        # 检查预算是否超限
        budget_exceeded, exceeded_types = self.budget_tracker.is_budget_exceeded()
        if budget_exceeded:
            return ConvergenceStatus.BUDGET_EXHAUSTED, {
                "exceeded_budget_types": [bt.value for bt in exceeded_types],
                "current_iteration": self.iteration_count,
                "current_agreement_score": self.convergence_checker.agreement_history[-1] if self.convergence_checker.agreement_history else 0.0,
                "budget_utilization": self.budget_tracker.get_budget_utilization()
            }
        
        # 检查收敛状态
        status, score, message = self.convergence_checker.check_convergence(self.iteration_count)
        
        return status, {
            "current_iteration": self.iteration_count,
            "current_agreement_score": score,
            "message": message,
            "convergence_trend": self.convergence_checker.get_convergence_trend(),
            "budget_utilization": self.budget_tracker.get_budget_utilization(),
            "remaining_budget": {
                bt.value: self.budget_tracker.get_remaining_budget(bt) 
                for bt in self.budget_tracker.budget_limits.keys()
            }
        }

    def get_final_report(self) -> Dict[str, Any]:
        """获取最终报告"""
        final_status, details = self.evaluate_state()
        
        return {
            "final_status": final_status.value,
            "total_iterations": self.iteration_count,
            "peak_agreement_score": max(self.convergence_checker.agreement_history) if self.convergence_checker.agreement_history else 0.0,
            "final_agreement_score": details["current_agreement_score"],
            "convergence_trend": details["convergence_trend"],
            "budget_utilization": details["budget_utilization"],
            "summary": self._generate_summary(final_status, details)
        }

    def _generate_summary(self, status: ConvergenceStatus, details: Dict[str, Any]) -> str:
        """生成摘要"""
        if status == ConvergenceStatus.CONVERGED:
            return f"辩论成功收敛！经过 {details['current_iteration']} 次迭代，最终协议分数为 {details['current_agreement_score']:.3f}。"
        elif status == ConvergenceStatus.BUDGET_EXHAUSTED:
            return f"辩论因预算耗尽而终止。已超限的预算类型: {', '.join(details['exceeded_budget_types'])}。"
        elif status == ConvergenceStatus.STALEMATE:
            return f"辩论陷入僵局。协议分数连续下降，最终分数为 {details['current_agreement_score']:.3f}。"
        else:
            return f"辩论仍在进行中。当前为第 {details['current_iteration']} 次迭代，协议分数为 {details['current_agreement_score']:.3f}。"


class AdaptiveConvergenceController:
    """
    自适应收敛控制器
    根据辩论进展动态调整收敛参数和预算
    """
    
    def __init__(self, initial_threshold: float = 0.85):
        self.analyzer = ConvergenceAnalyzer(convergence_threshold=initial_threshold)
        self.performance_history: List[Dict[str, Any]] = []
        self.adaptation_enabled = True

    def update_performance_metrics(self, metrics: Dict[str, Any]):
        """更新性能指标"""
        self.performance_history.append({
            **metrics,
            "timestamp": datetime.now()
        })
        
        # 如果启用了自适应，根据历史性能调整参数
        if self.adaptation_enabled:
            self._adapt_parameters()

    def _adapt_parameters(self):
        """根据历史性能自适应调整参数"""
        if len(self.performance_history) < 5:
            return  # 需要足够数据才能进行自适应调整
        
        # 分析最近几次迭代的进展
        recent_metrics = self.performance_history[-5:]
        agreement_changes = []
        
        for i in range(1, len(recent_metrics)):
            prev_score = recent_metrics[i-1].get('agreement_score', 0)
            curr_score = recent_metrics[i].get('agreement_score', 0)
            agreement_changes.append(curr_score - prev_score)
        
        avg_change = sum(agreement_changes) / len(agreement_changes) if agreement_changes else 0
        
        # 如果进展缓慢，调整收敛阈值
        if avg_change < 0.01:  # 进展非常缓慢
            current_threshold = self.analyzer.convergence_checker.threshold
            new_threshold = max(0.7, current_threshold - 0.05)  # 降低阈值
            self.analyzer.convergence_checker.threshold = new_threshold
        
        # 如果进展良好，增加预算
        elif avg_change > 0.05:  # 进展良好
            current_iter_budget = self.analyzer.budget_tracker.budget_limits.get(BudgetType.ITERATIONS, 10)
            extended_budget = min(20, current_iter_budget + 2)  # 增加迭代预算
            self.analyzer.budget_tracker.set_budget_limit(BudgetType.ITERATIONS, extended_budget)

    def adjust_budget_based_on_complexity(self, complexity_score: float):
        """根据任务复杂度调整预算"""
        # 基于复杂度分数调整预算
        base_iterations = 10
        adjusted_iterations = int(base_iterations * (1 + complexity_score))
        
        self.analyzer.budget_tracker.set_budget_limit(BudgetType.ITERATIONS, adjusted_iterations)
        
        # 也可以相应调整其他预算
        base_tokens = 10000
        adjusted_tokens = int(base_tokens * (1 + complexity_score))
        self.analyzer.budget_tracker.set_budget_limit(BudgetType.TOKENS, adjusted_tokens)

    def get_recommendations(self) -> List[str]:
        """获取建议"""
        recommendations = []
        
        if len(self.performance_history) >= 5:
            recent_metrics = self.performance_history[-5:]
            avg_change = sum(
                m.get('agreement_score', 0) - (self.performance_history[max(0, self.performance_history.index(m)-1)].get('agreement_score', 0) if self.performance_history.index(m) > 0 else 0)
                for m in recent_metrics[1:]
            ) / (len(recent_metrics) - 1) if len(recent_metrics) > 1 else 0
            
            if avg_change < 0.01:
                recommendations.append("进展缓慢，建议引入外部信息或改变讨论角度")
            elif avg_change > 0.05:
                recommendations.append("进展良好，可适当增加预算以追求更高协议分数")
        
        # 检查预算使用情况
        utilization = self.analyzer.budget_tracker.get_budget_utilization()
        for budget_type, util in utilization.items():
            if util > 0.8:
                recommendations.append(f"{budget_type.value}预算使用率已达{util:.1%}，注意控制消耗")
        
        return recommendations