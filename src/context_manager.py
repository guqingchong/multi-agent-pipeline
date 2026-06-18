"""src/context_manager.py — ContextManager 上下文窗口管理器

F011 实现：
- 分层上下文注入策略（安全指令永不压缩）
- Reinforcement 强化机制（每次工具调用返回时重复注入任务提醒）
- Agentic Search 按需加载（替代完整文件注入）
- 上下文压缩策略（按优先级保留/丢弃）

PRD 第 3.3 节 / 第 6 节定义。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DEFAULT_MAX_CONTEXT_TOKENS = 100_000
DEFAULT_SAFETY_RESERVE_TOKENS = 5_000
DEFAULT_TASK_RESERVE_TOKENS = 20_000

# 简单 token 估算：中文字符 ≈ 1 token，英文单词 ≈ 1.3 tokens
# 这里用保守的字符数估算（1 char ≈ 0.5 token 对中文偏保守）
TOKEN_ESTIMATE_FACTOR = 0.5


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

class LayerPriority(Enum):
    """上下文分层优先级（从高到低）"""
    SAFETY = auto()          # 永不压缩
    FEATURE_SPEC = auto()    # 高优先级
    ARCHITECTURE = auto()    # 中优先级
    CODE_FILES = auto()       # 按需加载
    MEMORY = auto()           # 按需加载
    HISTORY = auto()          # 可压缩


@dataclass
class ContextLayer:
    """上下文层"""
    name: str
    priority: LayerPriority
    content: str
    compressible: bool = True
    # 元数据：用于 Agentic Search 按需加载
    tags: List[str] = field(default_factory=list)
    source: str = ""  # 来源标识（如文件路径）

    def estimated_tokens(self) -> int:
        """估算该层占用的 token 数"""
        return max(1, int(len(self.content) * TOKEN_ESTIMATE_FACTOR))


@dataclass
class ReinforcementPrompt:
    """Reinforcement 强化提示结构"""
    current_task: str = ""
    acceptance_criteria: str = ""
    completed_steps: str = ""
    current_step: str = ""
    tool_result: str = ""
    reminder: str = ""

    def build(self) -> str:
        """构建 Reinforcement 提示文本"""
        parts = []
        if self.current_task:
            parts.append(f"[当前任务] {self.current_task}")
        if self.acceptance_criteria:
            parts.append(f"[验收标准] {self.acceptance_criteria}")
        if self.completed_steps:
            parts.append(f"[已完成] {self.completed_steps}")
        if self.current_step:
            parts.append(f"[当前步骤] {self.current_step}")
        if self.tool_result:
            parts.append(f"[工具结果] {self.tool_result}")
        if self.reminder:
            parts.append(f"[提醒] {self.reminder}")
        return "\n".join(parts)


@dataclass
class SearchResult:
    """Agentic Search 搜索结果"""
    content: str
    source: str
    relevance_score: float = 0.0


# ───────────────────────────────────────────────────────────────
# ContextManager 核心
# ───────────────────────────────────────────────────────────────

class ContextManager:
    """每个 Agent 的上下文窗口管理器

    职责：
      1. 分层上下文注入（安全指令永不压缩）
      2. Reinforcement 强化机制（每次工具调用返回时注入任务提醒）
      3. Agentic Search 按需加载（替代完整文件注入）
      4. 上下文压缩（按优先级丢弃/保留）
    """

    priority_layers = [
        "safety_instructions",     # 永不压缩：安全指令
        "current_feature_spec",    # 高优先级：当前 feature 需求
        "architecture_contract",   # 中优先级：接口定义
        "related_code_files",      # 按需加载：相关代码
        "memory_and_pitfalls",     # 按需加载：项目记忆
        "progress_history",        # 可压缩：历史进度
    ]

    def __init__(
        self,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        safety_reserve_tokens: int = DEFAULT_SAFETY_RESERVE_TOKENS,
        task_reserve_tokens: int = DEFAULT_TASK_RESERVE_TOKENS,
        agent_name: str = "default",
    ) -> None:
        self.max_context_tokens = max_context_tokens
        self.safety_reserve_tokens = safety_reserve_tokens
        self.task_reserve_tokens = task_reserve_tokens
        self.agent_name = agent_name

        # 分层存储
        self._layers: Dict[str, ContextLayer] = {}
        # 当前 Reinforcement 状态
        self._reinforcement: Optional[ReinforcementPrompt] = None
        # 可搜索的知识库（模拟文件索引）
        self._search_index: Dict[str, List[str]] = {}
        # 压缩历史（记录每次压缩丢弃了什么）
        self._compression_log: List[Dict[str, Any]] = []

    # ── 分层管理 ──

    def set_layer(
        self,
        name: str,
        content: str,
        priority: LayerPriority = LayerPriority.HISTORY,
        compressible: bool = True,
        tags: Optional[List[str]] = None,
        source: str = "",
    ) -> None:
        """设置一个上下文层"""
        self._layers[name] = ContextLayer(
            name=name,
            priority=priority,
            content=content,
            compressible=compressible,
            tags=tags or [],
            source=source,
        )

    def get_layer(self, name: str) -> Optional[ContextLayer]:
        """获取指定层"""
        return self._layers.get(name)

    def remove_layer(self, name: str) -> bool:
        """移除指定层，返回是否成功"""
        if name in self._layers:
            del self._layers[name]
            return True
        return False

    def list_layers(self) -> List[str]:
        """列出所有层名称"""
        return list(self._layers.keys())

    # ── Reinforcement 强化机制 ──

    def set_reinforcement(
        self,
        current_task: str = "",
        acceptance_criteria: str = "",
        completed_steps: str = "",
        current_step: str = "",
        reminder: str = "",
    ) -> None:
        """设置 Reinforcement 状态"""
        self._reinforcement = ReinforcementPrompt(
            current_task=current_task,
            acceptance_criteria=acceptance_criteria,
            completed_steps=completed_steps,
            current_step=current_step,
            reminder=reminder,
        )

    def build_reinforcement_prompt(self, tool_result: str = "") -> str:
        """构建 Reinforcement 提示（每次工具调用返回时注入）"""
        if self._reinforcement is None:
            return ""
        rp = ReinforcementPrompt(
            current_task=self._reinforcement.current_task,
            acceptance_criteria=self._reinforcement.acceptance_criteria,
            completed_steps=self._reinforcement.completed_steps,
            current_step=self._reinforcement.current_step,
            tool_result=tool_result,
            reminder=self._reinforcement.reminder,
        )
        return rp.build()

    def get_reinforcement(self) -> Optional[ReinforcementPrompt]:
        """获取当前 Reinforcement 状态"""
        return self._reinforcement

    # ── Agentic Search 按需加载 ──

    def index_document(self, doc_id: str, content: str, tags: Optional[List[str]] = None) -> None:
        """将文档加入搜索索引"""
        self._search_index[doc_id] = {
            "content": content,
            "tags": tags or [],
        }

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        """Agentic Search：按需搜索知识库

        实现简单的关键词匹配 + 标签匹配，返回最相关的片段。
        """
        results: List[SearchResult] = []
        query_lower = query.lower()
        # 支持中英文分词：按字符粒度拆分中文词，保留英文单词
        # 对于中文查询，每个字符都可能是独立关键词
        query_terms = set()
        for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", query_lower):
            if all('\u4e00' <= c <= '\u9fff' for c in token):
                # 中文字符串：拆分为单个字符作为搜索词
                query_terms.update(token)
            else:
                query_terms.add(token)

        for doc_id, doc in self._search_index.items():
            content = doc["content"]
            tags = doc.get("tags", [])
            content_lower = content.lower()

            # 计算相关性分数
            score = 0.0
            # 关键词匹配
            for term in query_terms:
                if term in content_lower:
                    score += 1.0
                # 标签匹配：支持中英文标签
                tag_text = " ".join(t.lower() for t in tags)
                if term in tag_text:
                    score += 2.0

            # 精确短语匹配加分
            if query_lower in content_lower:
                score += 5.0

            if score > 0:
                # 提取匹配片段（前后 100 字符）
                # 先尝试精确短语匹配位置
                match_pos = content_lower.find(query_lower)
                if match_pos < 0 and query_terms:
                    # 退而求其次：找第一个匹配词的位置
                    for term in query_terms:
                        match_pos = content_lower.find(term)
                        if match_pos >= 0:
                            break
                if match_pos >= 0:
                    start = max(0, match_pos - 100)
                    end = min(len(content), match_pos + len(query) + 100)
                    snippet = content[start:end]
                else:
                    # 取前 200 字符作为摘要
                    snippet = content[:200]

                results.append(SearchResult(
                    content=snippet,
                    source=doc_id,
                    relevance_score=score,
                ))

        # 按相关性排序
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:max_results]

    def search_and_inject(self, query: str, layer_name: str = "search_result", max_results: int = 3) -> bool:
        """搜索并注入结果到上下文层"""
        results = self.search(query, max_results)
        if not results:
            return False

        content_parts = [f"# Agentic Search 结果: '{query}'\n"]
        for i, r in enumerate(results, 1):
            content_parts.append(f"## 结果 {i} (来源: {r.source}, 相关度: {r.relevance_score:.1f})\n")
            content_parts.append(r.content)
            content_parts.append("")

        self.set_layer(
            layer_name,
            "\n".join(content_parts),
            priority=LayerPriority.CODE_FILES,
            compressible=True,
            tags=["agentic_search", query],
        )
        return True

    # ── 上下文压缩 ──

    def _total_tokens(self) -> int:
        """计算当前所有层的总 token 估算"""
        return sum(layer.estimated_tokens() for layer in self._layers.values())

    def _priority_order(self) -> List[ContextLayer]:
        """按优先级排序的层列表（高优先级在前）"""
        priority_order = [
            LayerPriority.SAFETY,
            LayerPriority.FEATURE_SPEC,
            LayerPriority.ARCHITECTURE,
            LayerPriority.CODE_FILES,
            LayerPriority.MEMORY,
            LayerPriority.HISTORY,
        ]
        layers = list(self._layers.values())
        layers.sort(key=lambda l: priority_order.index(l.priority))
        return layers

    def compress(self, target_tokens: Optional[int] = None) -> Dict[str, Any]:
        """压缩上下文，按优先级丢弃可压缩层

        策略：
          1. 安全指令层永远保留
          2. 当前任务相关层优先保留
          3. 可压缩层从低优先级开始丢弃
          4. 记录压缩日志
        """
        target = target_tokens or self.max_context_tokens
        total = self._total_tokens()

        if total <= target:
            return {
                "action": "none",
                "before_tokens": total,
                "after_tokens": total,
                "dropped_layers": [],
            }

        dropped: List[str] = []
        layers = self._priority_order()

        # 从最低优先级开始丢弃
        for layer in reversed(layers):
            if total <= target:
                break
            if not layer.compressible:
                continue
            if layer.priority == LayerPriority.SAFETY:
                continue  # 永不压缩安全指令

            layer_tokens = layer.estimated_tokens()
            if layer_tokens <= 0:
                continue
            # 不真正删除，而是将内容替换为摘要标记
            summary = f"[已压缩] {layer.name}: 原内容 {layer_tokens} tokens，因上下文限制被压缩。"
            self._layers[layer.name] = ContextLayer(
                name=layer.name,
                priority=layer.priority,
                content=summary,
                compressible=True,  # 摘要仍可被进一步压缩
                tags=layer.tags,
                source=layer.source,
            )
            total -= layer_tokens
            dropped.append({
                "name": layer.name,
                "tokens": layer_tokens,
                "priority": layer.priority.name,
            })

        after = self._total_tokens()
        log_entry = {
            "action": "compress",
            "before_tokens": total + sum(d["tokens"] for d in dropped),
            "after_tokens": after,
            "dropped_layers": dropped,
            "target_tokens": target,
        }
        self._compression_log.append(log_entry)
        return log_entry

    def get_compression_log(self) -> List[Dict[str, Any]]:
        """获取压缩历史"""
        return list(self._compression_log)

    # ── 上下文组装 ──

    def build_context(self, include_reinforcement: bool = True, tool_result: str = "") -> str:
        """组装完整上下文

        按优先级排序，确保高优先级内容在前。
        如果 include_reinforcement=True，在末尾注入 Reinforcement 提示。
        """
        layers = self._priority_order()
        parts = []

        for layer in layers:
            if layer.content.strip():
                parts.append(f"--- {layer.name} ---")
                parts.append(layer.content)
                parts.append("")

        if include_reinforcement and self._reinforcement is not None:
            rp = self.build_reinforcement_prompt(tool_result)
            if rp:
                parts.append("--- reinforcement ---")
                parts.append(rp)
                parts.append("")

        return "\n".join(parts)

    def build_context_with_token_check(self, tool_result: str = "") -> Tuple[str, Dict[str, Any]]:
        """组装上下文并检查 token 限制，必要时自动压缩

        返回 (context_string, metadata_dict)
        """
        # 先尝试不压缩构建
        context = self.build_context(include_reinforcement=True, tool_result=tool_result)
        total_tokens = int(len(context) * TOKEN_ESTIMATE_FACTOR)

        metadata = {
            "total_tokens": total_tokens,
            "compressed": False,
            "compression_log": None,
        }

        if total_tokens > self.max_context_tokens:
            # 需要压缩
            log = self.compress(self.max_context_tokens)
            context = self.build_context(include_reinforcement=True, tool_result=tool_result)
            metadata["compressed"] = True
            metadata["compression_log"] = log
            metadata["total_tokens"] = int(len(context) * TOKEN_ESTIMATE_FACTOR)

        return context, metadata

    # ── 安全指令专项 ──

    def set_safety_instructions(self, instructions: str) -> None:
        """设置安全指令（永不压缩）"""
        self.set_layer(
            "safety_instructions",
            instructions,
            priority=LayerPriority.SAFETY,
            compressible=False,
            tags=["safety", "critical"],
        )

    def get_safety_instructions(self) -> Optional[str]:
        """获取安全指令内容"""
        layer = self.get_layer("safety_instructions")
        return layer.content if layer else None

    def safety_instructions_present(self, context: str) -> bool:
        """检查给定上下文中是否包含安全指令"""
        safety = self.get_safety_instructions()
        if safety is None:
            return False
        # 检查安全指令的关键内容是否在上下文中
        # 使用首行和尾行作为指纹
        lines = [line.strip() for line in safety.splitlines() if line.strip()]
        if not lines:
            return False
        # 至少检查前 3 行和后 1 行
        fingerprint_lines = lines[:3] + lines[-1:]
        return all(line in context for line in fingerprint_lines)

    # ── 状态序列化 ──

    def to_dict(self) -> dict:
        """序列化当前状态（不含搜索索引内容，只含层和 Reinforcement）"""
        return {
            "agent_name": self.agent_name,
            "max_context_tokens": self.max_context_tokens,
            "safety_reserve_tokens": self.safety_reserve_tokens,
            "task_reserve_tokens": self.task_reserve_tokens,
            "layers": {
                name: {
                    "priority": layer.priority.name,
                    "content": layer.content,
                    "compressible": layer.compressible,
                    "tags": layer.tags,
                    "source": layer.source,
                }
                for name, layer in self._layers.items()
            },
            "reinforcement": {
                "current_task": self._reinforcement.current_task if self._reinforcement else "",
                "acceptance_criteria": self._reinforcement.acceptance_criteria if self._reinforcement else "",
                "completed_steps": self._reinforcement.completed_steps if self._reinforcement else "",
                "current_step": self._reinforcement.current_step if self._reinforcement else "",
                "reminder": self._reinforcement.reminder if self._reinforcement else "",
            } if self._reinforcement else None,
            "compression_log": self._compression_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextManager":
        """从字典反序列化"""
        cm = cls(
            max_context_tokens=data.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS),
            safety_reserve_tokens=data.get("safety_reserve_tokens", DEFAULT_SAFETY_RESERVE_TOKENS),
            task_reserve_tokens=data.get("task_reserve_tokens", DEFAULT_TASK_RESERVE_TOKENS),
            agent_name=data.get("agent_name", "default"),
        )
        for name, layer_data in data.get("layers", {}).items():
            cm.set_layer(
                name=name,
                content=layer_data["content"],
                priority=LayerPriority[layer_data["priority"]],
                compressible=layer_data.get("compressible", True),
                tags=layer_data.get("tags", []),
                source=layer_data.get("source", ""),
            )
        reinf = data.get("reinforcement")
        if reinf:
            cm.set_reinforcement(
                current_task=reinf.get("current_task", ""),
                acceptance_criteria=reinf.get("acceptance_criteria", ""),
                completed_steps=reinf.get("completed_steps", ""),
                current_step=reinf.get("current_step", ""),
                reminder=reinf.get("reminder", ""),
            )
        cm._compression_log = data.get("compression_log", [])
        return cm
