"""src/dispatch_strategy.py — 调度策略建议层

调度策略建议层：基于历史性能数据智能推荐最优 Agent，
冷启动返回 None，有数据时返回得分最高 Agent 名。

核心职责：
  1. suggest(task_type) — 根据任务类型建议最佳 Agent
  2. 基于历史性能数据进行 Agent 选择
  3. 不替换 TASK_ADAPTER_MAP，仅提供建议
  4. 与 system_constraint 集成，建议需经约束层校验

使用示例：
    strategy = DispatchStrategy()
    suggested_agent = strategy.suggest("code")  # 返回建议的 Agent 名称
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from registry import REGISTRY
except ModuleNotFoundError:
    from src.registry import REGISTRY


# ───────────────────────────────────────────────────────────────
# 性能指标数据类
# ───────────────────────────────────────────────────────────────

@dataclass
class PerformanceRecord:
    """性能记录数据类"""
    agent_name: str
    task_type: str
    score: float  # 得分越高越好 (0.0-1.0)
    timestamp: float
    success_rate: float  # 成功率
    avg_response_time: float  # 平均响应时间（秒）
    error_count: int  # 错误次数

    def to_dict(self) -> Dict[str, any]:
        """转换为字典格式"""
        return {
            "agent_name": self.agent_name,
            "task_type": self.task_type,
            "score": self.score,
            "timestamp": self.timestamp,
            "success_rate": self.success_rate,
            "avg_response_time": self.avg_response_time,
            "error_count": self.error_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> PerformanceRecord:
        """从字典创建实例"""
        return cls(
            agent_name=data["agent_name"],
            task_type=data["task_type"],
            score=data["score"],
            timestamp=data["timestamp"],
            success_rate=data["success_rate"],
            avg_response_time=data["avg_response_time"],
            error_count=data["error_count"],
        )


# ───────────────────────────────────────────────────────────────
# 调度策略核心类
# ───────────────────────────────────────────────────────────────

class DispatchStrategy:
    """调度策略建议层

    基于历史性能数据智能推荐最优 Agent，
    冷启动返回 None，有数据时返回得分最高 Agent 名。

    职责：
      1. suggest(task_type) — 根据任务类型建议最佳 Agent
      2. 基于历史性能数据进行 Agent 选择
      3. 不替换 TASK_ADAPTER_MAP，仅提供建议
      4. 与 system_constraint 集成，建议需经约束层校验
    """

    def __init__(self, history_file: Optional[Path] = None) -> None:
        """初始化调度策略

        Args:
            history_file: 性能历史记录文件路径（可选）
        """
        self.history_file = history_file or Path(".dispatch_history.json")
        self.performance_records: List[PerformanceRecord] = []
        self._load_history()

    def _load_history(self) -> None:
        """从文件加载历史性能数据"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.performance_records = [
                        PerformanceRecord.from_dict(record) for record in data
                    ]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                # 加载失败时使用空列表
                self.performance_records = []

    def _save_history(self) -> None:
        """保存历史性能数据到文件"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump([
                    record.to_dict() for record in self.performance_records
                ], f, ensure_ascii=False, indent=2)
        except (TypeError, ValueError, OSError):
            # 保存失败时不中断流程
            pass

    def record_performance(
        self,
        agent_name: str,
        task_type: str,
        score: float,
        success_rate: float = 1.0,
        avg_response_time: float = 0.0,
        error_count: int = 0,
    ) -> None:
        """记录 Agent 执行任务的性能数据

        Args:
            agent_name: Agent 名称
            task_type: 任务类型
            score: 性能得分 (0.0-1.0)
            success_rate: 成功率
            avg_response_time: 平均响应时间（秒）
            error_count: 错误次数
        """
        record = PerformanceRecord(
            agent_name=agent_name,
            task_type=task_type,
            score=score,
            timestamp=time.time(),
            success_rate=success_rate,
            avg_response_time=avg_response_time,
            error_count=error_count,
        )
        self.performance_records.append(record)
        self._save_history()

    def suggest(self, task_type: str) -> Optional[str]:
        """根据任务类型建议最佳 Agent

        冷启动（无历史数据）返回 None，
        有数据时返回该任务类型得分最高的 Agent 名。

        Args:
            task_type: 任务类型（如 "code", "review", "test" 等）

        Returns:
            建议的 Agent 名称，或 None（冷启动时）
        """
        # 过滤出指定任务类型的性能记录
        relevant_records = [
            record for record in self.performance_records
            if record.task_type == task_type
        ]

        # 如果没有相关历史数据，返回 None（冷启动）
        if not relevant_records:
            return None

        # 按得分排序，返回得分最高的 Agent
        best_record = max(relevant_records, key=lambda r: r.score)
        return best_record.agent_name

    def get_top_agents(self, task_type: str, count: int = 3) -> List[Tuple[str, float]]:
        """获取指定任务类型得分最高的前 N 个 Agent

        Args:
            task_type: 任务类型
            count: 返回前 N 个 Agent

        Returns:
            [(agent_name, score), ...] 列表，按得分降序排列
        """
        relevant_records = [
            record for record in self.performance_records
            if record.task_type == task_type
        ]

        if not relevant_records:
            return []

        # 按得分排序并去重，返回前 count 个
        unique_best = {}
        for record in relevant_records:
            if record.agent_name not in unique_best or \
               unique_best[record.agent_name].score < record.score:
                unique_best[record.agent_name] = record

        sorted_agents = sorted(
            unique_best.values(),
            key=lambda r: r.score,
            reverse=True
        )

        return [(r.agent_name, r.score) for r in sorted_agents[:count]]

    def get_agent_performance_history(self, agent_name: str, task_type: str) -> List[PerformanceRecord]:
        """获取特定 Agent 执行特定任务的历史性能记录

        Args:
            agent_name: Agent 名称
            task_type: 任务类型

        Returns:
            性能记录列表，按时间倒序排列
        """
        records = [
            record for record in self.performance_records
            if record.agent_name == agent_name and record.task_type == task_type
        ]
        return sorted(records, key=lambda r: r.timestamp, reverse=True)

    def clear_history(self) -> None:
        """清空历史性能数据"""
        self.performance_records = []
        if self.history_file.exists():
            self.history_file.unlink()  # 删除文件

    def get_supported_task_types(self) -> List[str]:
        """获取有历史数据支持的任务类型

        Returns:
            有历史性能数据的任务类型列表
        """
        task_types = set(record.task_type for record in self.performance_records)
        return list(task_types)


# ───────────────────────────────────────────────────────────────
# 高层便捷函数
# ───────────────────────────────────────────────────────────────

def suggest_dispatch_agent(task_type: str) -> Optional[str]:
    """建议调度 Agent（全局便捷函数）

    Args:
        task_type: 任务类型

    Returns:
        建议的 Agent 名称，或 None（冷启动时）
    """
    strategy = DispatchStrategy()
    return strategy.suggest(task_type)


def record_agent_performance(
    agent_name: str,
    task_type: str,
    score: float,
    success_rate: float = 1.0,
    avg_response_time: float = 0.0,
    error_count: int = 0,
) -> None:
    """记录 Agent 性能（全局便捷函数）

    Args:
        agent_name: Agent 名称
        task_type: 任务类型
        score: 性能得分 (0.0-1.0)
        success_rate: 成功率
        avg_response_time: 平均响应时间（秒）
        error_count: 错误次数
    """
    strategy = DispatchStrategy()
    strategy.record_performance(
        agent_name, task_type, score, success_rate, avg_response_time, error_count
    )