#!/usr/bin/env python3
"""src/entry.py — 入口层自动加载

职责：
  1. 每次对话自动加载项目状态（auto_load）
  2. 显示驾驶舱（show_dashboard）
  3. 识别用户意图（identify_intent）

集成组件：
  - state_store.py  (SQLite State persistence)
  - pipeline.py     (Phase 状态机)
  - observability.py (仪表盘数据)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.state_store import StateStore, ProjectRecord, FeatureRecord
from src.models import ProjectState, Phase
try:
    from config import get_config
except (ModuleNotFoundError, ImportError):
    from src.config import get_config
from src.observability import (
    ObservabilityStore,
    Dashboard,
    AlertManager,
    get_dashboard,
    get_report,
    run_alert_check,
    AlertEvent,
    DashboardMetrics,
)


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DB_FILENAME = get_config().db_name

# 用户意图类型
class UserIntent(Enum):
    DEVELOP = "develop"      # 开发新功能
    MODIFY = "modify"        # 修改现有功能
    QUERY = "query"          # 查询/查看状态
    UNKNOWN = "unknown"      # 未知意图


@dataclass
class EntryContext:
    """入口层上下文 — 包含加载的项目状态和仪表盘数据"""
    project_id: str
    project_name: str
    current_phase: str
    project_exists: bool = False
    state: Optional[ProjectState] = None
    metrics: Optional[DashboardMetrics] = None
    alerts: List[AlertEvent] = field(default_factory=list)
    features: List[FeatureRecord] = field(default_factory=list)
    intent: Optional[UserIntent] = None
    intent_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "current_phase": self.current_phase,
            "project_exists": self.project_exists,
            "state": self.state.to_dict() if self.state else None,
            "intent": self.intent.value if self.intent else None,
            "intent_confidence": self.intent_confidence,
            "feature_count": len(self.features),
            "alert_count": len(self.alerts),
        }


# ───────────────────────────────────────────────────────────────
# 自动加载项目状态
# ───────────────────────────────────────────────────────────────

def auto_load(
    project_id: str,
    base_dir: Optional[Path] = None,
) -> EntryContext:
    """自动加载项目状态

    1. 查找项目目录
    2. 连接 SQLite 数据库
    3. 读取项目记录、features、checkpoints
    4. 恢复 pipeline 状态
    5. 返回 EntryContext

    Args:
        project_id: 项目 ID
        base_dir: 项目根目录（默认当前工作目录）

    Returns:
        EntryContext: 包含项目状态和仪表盘数据的上下文
    """
    if base_dir is None:
        base_dir = Path.cwd()

    proj_dir = base_dir / project_id
    db_path = proj_dir / DB_FILENAME

    ctx = EntryContext(
        project_id=project_id,
        project_name=project_id,
        current_phase="unknown",
    )

    # 1. 检查项目目录是否存在
    if not proj_dir.exists():
        ctx.project_exists = False
        return ctx

    # 2. 检查数据库是否存在
    if not db_path.exists():
        # 目录存在但无数据库 — 可能是未初始化的项目
        ctx.project_exists = True
        ctx.current_phase = "uninitialized"
        return ctx

    # 3. 连接数据库并读取状态
    store = StateStore(db_path)
    project_record = store.get_project(project_id)

    if project_record is not None:
        ctx.project_exists = True
        ctx.project_name = project_record.name
        ctx.current_phase = project_record.current_phase
    else:
        # 向后兼容：尝试从 legacy 表恢复项目信息
        raw_state = store.legacy_load("state")
        if raw_state is not None:
            ctx.project_exists = True
            try:
                state_dict = json.loads(raw_state)
                ctx.project_name = state_dict.get("name", project_id)
                ctx.current_phase = state_dict.get("phase", "unknown")
            except (json.JSONDecodeError, KeyError):
                ctx.project_name = project_id
                ctx.current_phase = "unknown"

    # 4. 恢复 pipeline 状态（从 legacy 表）
    raw_state = store.legacy_load("state")
    if raw_state is not None:
        try:
            ctx.state = ProjectState.from_dict(json.loads(raw_state))
        except (json.JSONDecodeError, KeyError, ValueError):
            ctx.state = None

    # 5. 读取 features
    ctx.features = store.list_features(project_id)

    return ctx


def auto_load_with_checkpoints(
    project_id: str,
    base_dir: Optional[Path] = None,
    checkpoint_limit: int = 5,
) -> EntryContext:
    """自动加载项目状态（包含最近 checkpoints）

    扩展 auto_load，额外读取最近 checkpoints 用于恢复上下文。
    """
    ctx = auto_load(project_id, base_dir)

    if not ctx.project_exists or base_dir is None:
        base_dir = base_dir or Path.cwd()

    proj_dir = base_dir / project_id
    db_path = proj_dir / DB_FILENAME

    if db_path.exists():
        store = StateStore(db_path)
        checkpoints = store.list_checkpoints(project_id, limit=checkpoint_limit)
        # 将 checkpoints 信息附加到 state 中（如果 state 存在）
        if ctx.state is not None and checkpoints:
            ctx.state.check_results["recent_checkpoints"] = [
                {"id": cp.id, "phase": cp.phase, "action": cp.action, "result": cp.result}
                for cp in checkpoints
            ]

    return ctx


# ───────────────────────────────────────────────────────────────
# 显示驾驶舱
# ───────────────────────────────────────────────────────────────

def show_dashboard(
    project_id: str,
    base_dir: Optional[Path] = None,
    rich_mode: bool = False,
    include_alerts: bool = True,
) -> str:
    """显示驾驶舱

    1. 加载项目状态
    2. 聚合仪表盘指标
    3. 执行告警检测
    4. 返回仪表盘字符串

    Args:
        project_id: 项目 ID
        base_dir: 项目根目录
        rich_mode: 是否使用 rich 格式
        include_alerts: 是否包含告警信息

    Returns:
        str: 仪表盘字符串
    """
    if base_dir is None:
        base_dir = Path.cwd()

    proj_dir = base_dir / project_id
    db_path = proj_dir / DB_FILENAME

    # 项目不存在
    if not proj_dir.exists():
        return f"[ERROR] 项目目录不存在: {proj_dir}"

    if not db_path.exists():
        return f"[WARN] 项目目录存在但数据库未初始化: {db_path}"

    # 生成仪表盘
    dashboard_text = get_dashboard(db_path, project_id, rich_mode=rich_mode)

    # 执行告警检测
    if include_alerts:
        alerts = run_alert_check(db_path, project_id)
        if alerts:
            alert_lines = ["\n[ALERTS] 检测到告警:"]
            for alert in alerts:
                icon = "🔴" if alert.severity == "critical" else "🟡" if alert.severity == "warning" else "🔵"
                alert_lines.append(f"  {icon} [{alert.severity.upper()}] {alert.alert_type}: {alert.message}")
            dashboard_text += "\n".join(alert_lines)

    return dashboard_text


def show_dashboard_for_context(
    ctx: EntryContext,
    rich_mode: bool = False,
) -> str:
    """基于 EntryContext 显示驾驶舱

    使用已加载的上下文直接生成仪表盘，避免重复查询数据库。
    """
    if not ctx.project_exists:
        return f"[ERROR] 项目不存在: {ctx.project_id}"

    # 如果有 metrics 直接使用
    if ctx.metrics is not None:
        return _format_metrics(ctx.metrics, rich_mode)

    # 否则回退到 show_dashboard
    return show_dashboard(ctx.project_id, rich_mode=rich_mode)


def _format_metrics(metrics: DashboardMetrics, rich_mode: bool = False) -> str:
    """格式化 DashboardMetrics 为字符串"""
    lines = [
        "┌─────────────────────────────────────────────┐",
        f"│ 项目: {metrics.project_id:<29} Phase: {metrics.current_phase:<8} │",
        "├─────────────────────────────────────────────┤",
        f"│ Features: {metrics.passed_features}/{metrics.total_features} passed                    │",
        f"│ Budget: ${metrics.total_cost_usd:.2f}                          │",
        f"│ Avg Latency: {metrics.avg_latency_ms:.0f}ms                      │",
        f"│ Cache Hit Rate: {metrics.cache_hit_rate:.0%}                       │",
        f"│ Error Rate: {metrics.error_rate:.0%}                          │",
        "└─────────────────────────────────────────────┘",
    ]
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────
# 识别用户意图
# ───────────────────────────────────────────────────────────────

# 意图关键词映射
INTENT_KEYWORDS = {
    UserIntent.DEVELOP: [
        "开发", "实现", "新建", "创建", "添加", "add", "create", "implement",
        "develop", "build", "write", "coding", "program", "feature",
        "新功能", "新特性", "开发新", "写一个", "做一个",
    ],
    UserIntent.MODIFY: [
        "修改", "更新", "调整", "优化", "重构", "修复", "改", "改一下",
        "update", "modify", "change", "fix", "refactor", "optimize",
        "improve", "patch", "edit", "upgrade", "tweak", "enhance",
        "修复bug", "改bug", "调优", "改进", "重写",
    ],
    UserIntent.QUERY: [
        "查询", "查看", "状态", "进度", "报告", "显示", "看看", "怎么样",
        "query", "check", "status", "report", "show", "display", "view",
        "look", "get", "list", "info", "dashboard", "仪表盘", "驾驶舱",
        "什么情况", "如何", "结果", "summary", "overview", "metrics",
    ],
}


def identify_intent(user_input: str) -> Tuple[UserIntent, float]:
    """识别用户意图

    基于关键词匹配识别用户意图类型：
      - develop: 开发新功能
      - modify: 修改现有功能
      - query: 查询/查看状态

    Args:
        user_input: 用户输入文本

    Returns:
        Tuple[UserIntent, float]: (意图类型, 置信度 0-1)
    """
    if not user_input or not user_input.strip():
        return UserIntent.UNKNOWN, 0.0

    text = user_input.lower().strip()

    # 计算每个意图的匹配分数
    scores: Dict[UserIntent, float] = {}

    for intent, keywords in INTENT_KEYWORDS.items():
        score = 0.0
        matched_keywords = 0
        for keyword in keywords:
            keyword_lower = keyword.lower()
            # 完整单词匹配权重更高
            if re.search(r'\b' + re.escape(keyword_lower) + r'\b', text):
                score += 2.0
                matched_keywords += 1
            # 子串匹配权重较低
            elif keyword_lower in text:
                score += 1.0
                matched_keywords += 1

        # 归一化分数（基于匹配的关键词数量）
        if matched_keywords > 0:
            scores[intent] = score / (matched_keywords + 1) + (matched_keywords * 0.1)
        else:
            scores[intent] = 0.0

    # 选择最高分的意图
    if not scores or max(scores.values()) == 0:
        return UserIntent.UNKNOWN, 0.0

    best_intent = max(scores, key=scores.get)
    best_score = scores[best_intent]

    # 计算置信度（0-1 范围）
    confidence = min(best_score / 2.0, 1.0)

    # 如果分数过低，标记为未知
    if best_score < 0.5:
        return UserIntent.UNKNOWN, confidence

    return best_intent, confidence


def identify_intent_with_context(
    user_input: str,
    ctx: EntryContext,
) -> Tuple[UserIntent, float]:
    """结合上下文识别用户意图

    在项目上下文的基础上增强意图识别：
      - 如果项目不存在，query 意图更可能
      - 如果项目处于 init 阶段，develop 意图更可能
      - 如果项目有 failed features，modify 意图更可能
    """
    intent, confidence = identify_intent(user_input)

    # 上下文增强
    if ctx.project_exists:
        # 项目存在时，根据当前 phase 调整意图
        phase = ctx.current_phase.lower()

        if phase in ("init", "design", "decompose") and intent == UserIntent.DEVELOP:
            confidence = min(confidence + 0.1, 1.0)

        if phase in ("develop", "test") and intent == UserIntent.MODIFY:
            # 检查是否有 failed features
            failed_count = sum(1 for f in ctx.features if f.status == "failed")
            if failed_count > 0:
                confidence = min(confidence + 0.15, 1.0)

        if phase in ("accept", "deploy") and intent == UserIntent.QUERY:
            confidence = min(confidence + 0.1, 1.0)
    else:
        # 项目不存在时，query 或 develop 都有可能
        if intent == UserIntent.MODIFY:
            # 无法修改不存在的项目，降低置信度
            confidence = max(confidence - 0.3, 0.0)
            if confidence < 0.3:
                intent = UserIntent.UNKNOWN

    return intent, confidence


# ───────────────────────────────────────────────────────────────
# 入口层主流程
# ───────────────────────────────────────────────────────────────

def entry_main(
    project_id: str,
    user_input: str,
    base_dir: Optional[Path] = None,
    rich_mode: bool = False,
) -> Tuple[EntryContext, str]:
    """入口层主流程

    每次对话执行：
      1. 自动加载项目状态
      2. 显示驾驶舱
      3. 识别用户意图
      4. 返回上下文和仪表盘

    Args:
        project_id: 项目 ID
        user_input: 用户输入
        base_dir: 项目根目录
        rich_mode: 是否使用 rich 格式仪表盘

    Returns:
        Tuple[EntryContext, str]: (上下文, 仪表盘字符串)
    """
    # 1. 自动加载
    ctx = auto_load(project_id, base_dir)

    # 2. 识别意图
    ctx.intent, ctx.intent_confidence = identify_intent_with_context(user_input, ctx)

    # 3. 显示驾驶舱
    dashboard = show_dashboard_for_context(ctx, rich_mode=rich_mode)

    # 4. 在仪表盘头部添加意图信息
    intent_line = f"[INTENT] {ctx.intent.value} (confidence: {ctx.intent_confidence:.0%})"
    dashboard = intent_line + "\n" + dashboard

    return ctx, dashboard


# ───────────────────────────────────────────────────────────────
# 便捷函数
# ───────────────────────────────────────────────────────────────

def quick_status(project_id: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """快速获取项目状态摘要"""
    ctx = auto_load(project_id, base_dir)
    return ctx.to_dict()


def list_projects(base_dir: Optional[Path] = None) -> List[str]:
    """列出 base_dir 下的所有项目目录"""
    if base_dir is None:
        base_dir = Path.cwd()

    projects = []
    for item in base_dir.iterdir():
        if item.is_dir() and (item / DB_FILENAME).exists():
            projects.append(item.name)
    return projects
