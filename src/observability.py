"""src/observability.py — 可观测性系统：仪表盘 + 告警 + 报告

职责：
  1. 从 SQLite 读取 traces / audit_logs / checkpoints / model_health 数据
  2. 提供实时仪表盘（rich 终端 UI）
  3. 提供实时告警系统（AlertManager）
  4. 生成 Markdown 报告
  5. 提供 pipeline.py status / report / model-health 命令支持
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from config import get_config
except (ModuleNotFoundError, ImportError):
    from src.config import get_config

try:
    from state_store import StateStore, TraceRecord, AuditLogRecord, CheckpointRecord
except (ModuleNotFoundError, ImportError):
    from src.state_store import StateStore, TraceRecord, AuditLogRecord, CheckpointRecord


# ───────────────────────────────────────────────────────────────
# Structured JSON trace logger
# ───────────────────────────────────────────────────────────────

_pipeline_logger = logging.getLogger("pipeline")


def trace(event: str, project: str, details: Dict[str, Any]) -> None:
    """Emit a structured JSON trace record for key pipeline events."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "project": project,
        "details": details,
    }
    _pipeline_logger.info(json.dumps(record, ensure_ascii=False, default=str))


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class DashboardMetrics:
    """仪表盘聚合指标"""
    project_id: str
    current_phase: str = ""
    total_features: int = 0
    passed_features: int = 0
    failed_features: int = 0
    in_progress_features: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    cache_hit_rate: float = 0.0
    error_rate: float = 0.0
    total_traces: int = 0
    total_audit_logs: int = 0
    total_checkpoints: int = 0
    model_health: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    recent_traces: List[TraceRecord] = field(default_factory=list)
    recent_audit_logs: List[AuditLogRecord] = field(default_factory=list)
    recent_checkpoints: List[CheckpointRecord] = field(default_factory=list)


@dataclass
class AlertEvent:
    """告警事件"""
    alert_type: str
    severity: str  # "warning", "critical", "info"
    message: str
    timestamp: str
    metric_value: Optional[float] = None
    threshold: Optional[float] = None


# ───────────────────────────────────────────────────────────────
# ObservabilityStore — 数据读取层
# ───────────────────────────────────────────────────────────────

class ObservabilityStore:
    """可观测性数据存储读取器

    封装从 SQLite 读取 traces / audit_logs / checkpoints / model_health 的查询
    """

    def __init__(self, db_path: Path) -> None:
        if db_path.is_dir():
            db_path = db_path / get_config().db_name
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        conn.close()

    # ── traces ──

    def get_traces(self, project_id: str, limit: int = 100) -> List[TraceRecord]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM traces WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        finally:
            self._close(conn)
        return [
            TraceRecord(
                id=r["id"],
                project_id=r["project_id"],
                feature_id=r["feature_id"],
                agent=r["agent"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=r["cost_usd"],
                latency_ms=r["latency_ms"],
                status=r["status"],
                cache_hit=bool(r["cache_hit"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_all_traces(self, limit: int = 1000) -> List[TraceRecord]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            self._close(conn)
        return [
            TraceRecord(
                id=r["id"],
                project_id=r["project_id"],
                feature_id=r["feature_id"],
                agent=r["agent"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=r["cost_usd"],
                latency_ms=r["latency_ms"],
                status=r["status"],
                cache_hit=bool(r["cache_hit"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── audit_logs ──

    def get_audit_logs(self, project_id: str, limit: int = 100) -> List[AuditLogRecord]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        finally:
            self._close(conn)
        return [
            AuditLogRecord(
                id=r["id"],
                project_id=r["project_id"],
                agent=r["agent"],
                command=r["command"],
                allowed=bool(r["allowed"]) if r["allowed"] is not None else None,
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── checkpoints ──

    def get_checkpoints(self, project_id: str, limit: int = 50) -> List[CheckpointRecord]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        finally:
            self._close(conn)
        return [
            CheckpointRecord(
                id=r["id"],
                project_id=r["project_id"],
                phase=r["phase"],
                feature_id=r["feature_id"],
                agent=r["agent"],
                action=r["action"],
                result=r["result"],
                state_json=r["state_json"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── model_health ──

    def get_model_health(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM model_health ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            self._close(conn)
        return [dict(r) for r in rows]

    def get_model_health_summary(self) -> Dict[str, Dict[str, Any]]:
        """返回每个模型的健康度摘要"""
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT model,
                       COUNT(*) as total_calls,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_calls,
                       AVG(response_time_ms) as avg_latency,
                       MAX(response_time_ms) as max_latency,
                       MIN(response_time_ms) as min_latency
                FROM model_health
                GROUP BY model
                """
            ).fetchall()
        finally:
            self._close(conn)
        summary = {}
        for r in rows:
            total = r["total_calls"] or 0
            success = r["success_calls"] or 0
            summary[r["model"]] = {
                "total_calls": total,
                "success_calls": success,
                "error_rate": (total - success) / total if total > 0 else 0.0,
                "avg_latency_ms": round(r["avg_latency"] or 0, 2),
                "max_latency_ms": r["max_latency"] or 0,
                "min_latency_ms": r["min_latency"] or 0,
            }
        return summary

    # ── features ──

    def get_feature_counts(self, project_id: str) -> Dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM features WHERE project_id = ? GROUP BY status",
                (project_id,),
            ).fetchall()
        finally:
            self._close(conn)
        return {r["status"]: r["cnt"] for r in rows}

    # ── project ──

    def get_project_phase(self, project_id: str) -> str:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT current_phase FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        finally:
            self._close(conn)
        return row["current_phase"] if row else "unknown"


# ───────────────────────────────────────────────────────────────
# AlertManager — 实时告警系统
# ───────────────────────────────────────────────────────────────

class AlertManager:
    """实时异常检测与告警

    滑动窗口统计 + 阈值检测 + 多通道通知
    """

    # 默认告警阈值
    DEFAULT_THRESHOLDS = {
        "token_spike_multiplier": 3.0,
        "error_rate": 0.3,
        "latency_ms": 60000,
        "cache_hit_rate_min": 0.3,
    }

    # 滑动窗口大小
    DEFAULT_WINDOWS = {
        "token_rate": 10,
        "error_rate": 20,
        "latency": 20,
    }

    def __init__(
        self,
        thresholds: Optional[Dict[str, float]] = None,
        windows: Optional[Dict[str, int]] = None,
        channels: Optional[List[str]] = None,
    ) -> None:
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.windows = {**self.DEFAULT_WINDOWS, **(windows or {})}
        self.channels = channels or ["terminal"]
        self._alerts: List[AlertEvent] = []
        self._trace_window: List[TraceRecord] = []
        self._notify_callbacks: List[Callable[[AlertEvent], None]] = []

    def register_notify(self, callback: Callable[[AlertEvent], None]) -> None:
        """注册告警通知回调"""
        self._notify_callbacks.append(callback)

    def _notify(self, alert: AlertEvent) -> None:
        """触发所有通知通道"""
        self._alerts.append(alert)
        for cb in self._notify_callbacks:
            cb(alert)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def check_traces(self, traces: List[TraceRecord]) -> List[AlertEvent]:
        """对 traces 执行告警检测，返回新触发的告警"""
        new_alerts: List[AlertEvent] = []

        if not traces:
            return new_alerts

        # 更新滑动窗口
        self._trace_window.extend(traces)
        max_window = max(self.windows.values())
        if len(self._trace_window) > max_window * 2:
            self._trace_window = self._trace_window[-max_window * 2:]

        # 1. 错误率检测
        window_size = self.windows["error_rate"]
        recent = self._trace_window[-window_size:]
        if len(recent) >= window_size:
            errors = sum(1 for t in recent if t.status and t.status.lower() in ("error", "failed", "failure"))
            error_rate = errors / len(recent)
            if error_rate >= self.thresholds["error_rate"]:
                alert = AlertEvent(
                    alert_type="error_rate",
                    severity="critical" if error_rate >= 0.5 else "warning",
                    message=f"错误率过高: {error_rate:.1%} (阈值: {self.thresholds['error_rate']:.0%})",
                    timestamp=self._now(),
                    metric_value=error_rate,
                    threshold=self.thresholds["error_rate"],
                )
                self._notify(alert)
                new_alerts.append(alert)

        # 2. 延迟检测
        latencies = [t.latency_ms for t in recent if t.latency_ms is not None]
        if latencies:
            avg_latency = statistics.mean(latencies)
            if avg_latency > self.thresholds["latency_ms"]:
                alert = AlertEvent(
                    alert_type="latency",
                    severity="critical",
                    message=f"平均延迟过高: {avg_latency:.0f}ms (阈值: {self.thresholds['latency_ms']:.0f}ms)",
                    timestamp=self._now(),
                    metric_value=avg_latency,
                    threshold=self.thresholds["latency_ms"],
                )
                self._notify(alert)
                new_alerts.append(alert)

        # 3. Token 突增检测
        token_window = self._trace_window[-self.windows["token_rate"]:]
        if len(token_window) >= 2:
            tokens = [
                (t.input_tokens or 0) + (t.output_tokens or 0)
                for t in token_window
            ]
            if len(tokens) >= 2:
                avg_tokens = statistics.mean(tokens[:-1])
                latest_tokens = tokens[-1]
                if avg_tokens > 0 and latest_tokens > avg_tokens * self.thresholds["token_spike_multiplier"]:
                    alert = AlertEvent(
                        alert_type="token_spike",
                        severity="warning",
                        message=f"Token 消耗突增: {latest_tokens} (平均: {avg_tokens:.0f}, 倍数: {latest_tokens/avg_tokens:.1f}x)",
                        timestamp=self._now(),
                        metric_value=float(latest_tokens),
                        threshold=avg_tokens * self.thresholds["token_spike_multiplier"],
                    )
                    self._notify(alert)
                    new_alerts.append(alert)

        # 4. 缓存命中率检测
        cache_window = [t for t in recent if t.cache_hit is not None]
        if len(cache_window) >= 5:
            hits = sum(1 for t in cache_window if t.cache_hit)
            hit_rate = hits / len(cache_window)
            if hit_rate < self.thresholds["cache_hit_rate_min"]:
                alert = AlertEvent(
                    alert_type="cache_hit_rate",
                    severity="warning",
                    message=f"缓存命中率过低: {hit_rate:.1%} (阈值: {self.thresholds['cache_hit_rate_min']:.0%})",
                    timestamp=self._now(),
                    metric_value=hit_rate,
                    threshold=self.thresholds["cache_hit_rate_min"],
                )
                self._notify(alert)
                new_alerts.append(alert)

        return new_alerts

    def check_model_health(self, summary: Dict[str, Dict[str, Any]]) -> List[AlertEvent]:
        """对模型健康度执行告警检测"""
        new_alerts: List[AlertEvent] = []
        for model, stats in summary.items():
            error_rate = stats.get("error_rate", 0.0)
            if error_rate >= self.thresholds["error_rate"]:
                alert = AlertEvent(
                    alert_type="model_health",
                    severity="critical" if error_rate >= 0.5 else "warning",
                    message=f"模型 {model} 错误率过高: {error_rate:.1%}",
                    timestamp=self._now(),
                    metric_value=error_rate,
                    threshold=self.thresholds["error_rate"],
                )
                self._notify(alert)
                new_alerts.append(alert)

            avg_latency = stats.get("avg_latency_ms", 0)
            if avg_latency > self.thresholds["latency_ms"]:
                alert = AlertEvent(
                    alert_type="model_latency",
                    severity="critical",
                    message=f"模型 {model} 平均延迟过高: {avg_latency:.0f}ms",
                    timestamp=self._now(),
                    metric_value=avg_latency,
                    threshold=self.thresholds["latency_ms"],
                )
                self._notify(alert)
                new_alerts.append(alert)
        return new_alerts

    def get_alerts(self, limit: int = 100) -> List[AlertEvent]:
        """获取历史告警"""
        return self._alerts[-limit:]

    def clear_alerts(self) -> None:
        """清空告警历史"""
        self._alerts.clear()
        self._trace_window.clear()


# ───────────────────────────────────────────────────────────────
# Dashboard — 仪表盘生成
# ───────────────────────────────────────────────────────────────

class Dashboard:
    """可观测性仪表盘

    提供 rich 终端 UI 和纯文本两种输出模式
    """

    def __init__(self, store: ObservabilityStore) -> None:
        self.store = store

    def get_metrics(self, project_id: str) -> DashboardMetrics:
        """聚合项目指标"""
        metrics = DashboardMetrics(project_id=project_id)
        metrics.current_phase = self.store.get_project_phase(project_id)

        # features 统计
        feature_counts = self.store.get_feature_counts(project_id)
        metrics.total_features = sum(feature_counts.values())
        metrics.passed_features = feature_counts.get("passed", 0)
        metrics.failed_features = feature_counts.get("failed", 0)
        metrics.in_progress_features = feature_counts.get("in_progress", 0)

        # traces 统计
        traces = self.store.get_traces(project_id, limit=100)
        metrics.recent_traces = traces[:20]
        metrics.total_traces = len(traces)
        if traces:
            metrics.total_tokens = sum(
                (t.input_tokens or 0) + (t.output_tokens or 0) for t in traces
            )
            metrics.total_cost_usd = sum(t.cost_usd or 0 for t in traces)
            latencies = [t.latency_ms for t in traces if t.latency_ms is not None]
            if latencies:
                metrics.avg_latency_ms = statistics.mean(latencies)
            cache_hits = [t.cache_hit for t in traces if t.cache_hit is not None]
            if cache_hits:
                metrics.cache_hit_rate = sum(cache_hits) / len(cache_hits)
            errors = [t for t in traces if t.status and t.status.lower() in ("error", "failed", "failure")]
            metrics.error_rate = len(errors) / len(traces) if traces else 0.0

        # audit_logs
        audit_logs = self.store.get_audit_logs(project_id, limit=100)
        metrics.recent_audit_logs = audit_logs[:10]
        metrics.total_audit_logs = len(audit_logs)

        # checkpoints
        checkpoints = self.store.get_checkpoints(project_id, limit=50)
        metrics.recent_checkpoints = checkpoints[:10]
        metrics.total_checkpoints = len(checkpoints)

        # model_health
        metrics.model_health = self.store.get_model_health_summary()

        return metrics

    def render_text(self, project_id: str) -> str:
        """生成纯文本仪表盘"""
        m = self.get_metrics(project_id)
        lines = [
            "┌─────────────────────────────────────────────┐",
            f"│ 项目: {project_id:<29} Phase: {m.current_phase:<8} │",
            "├─────────────────────────────────────────────┤",
            f"│ Features: {m.passed_features}/{m.total_features} passed                    │",
            f"│ Budget: ${m.total_cost_usd:.2f}                          │",
            f"│ Avg Latency: {m.avg_latency_ms:.0f}ms                      │",
            f"│ Cache Hit Rate: {m.cache_hit_rate:.0%}                       │",
            f"│ Error Rate: {m.error_rate:.0%}                          │",
            "├─────────────────────────────────────────────┤",
            "│ Model Health:                               │",
        ]
        if m.model_health:
            for model, stats in m.model_health.items():
                status = "✓" if stats["error_rate"] < 0.1 else "✗"
                lines.append(f"│   {model:<12} {status}  {stats['avg_latency_ms']:.0f}ms  {stats['error_rate']:.0%}    │")
        else:
            lines.append("│   (无数据)                                   │")
        lines.append("├─────────────────────────────────────────────┤")
        lines.append("│ Recent Traces:                              │")
        if m.recent_traces:
            for t in m.recent_traces[:5]:
                lines.append(f"│   #{t.id} {t.agent or '-':<8} {t.status or '-':<8} {t.latency_ms or 0}ms │")
        else:
            lines.append("│   (无数据)                                   │")
        lines.append("└─────────────────────────────────────────────┘")
        return "\n".join(lines)

    def render_rich(self, project_id: str) -> str:
        """生成 rich 格式的仪表盘（返回可打印的字符串）"""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text
        except ImportError:
            return self.render_text(project_id)

        m = self.get_metrics(project_id)
        console = Console(force_terminal=True, color_system="auto")

        # 主面板
        summary = Text()
        summary.append(f"项目: {project_id}\n", style="bold cyan")
        summary.append(f"Phase: {m.current_phase}\n", style="bold green")
        summary.append(f"Features: {m.passed_features}/{m.total_features} passed\n")
        summary.append(f"Budget: ${m.total_cost_usd:.2f}\n")
        summary.append(f"Avg Latency: {m.avg_latency_ms:.0f}ms\n")
        summary.append(f"Cache Hit Rate: {m.cache_hit_rate:.0%}\n")
        summary.append(f"Error Rate: {m.error_rate:.0%}\n")

        # Model Health 表格
        model_table = Table(title="Model Health", show_header=True, header_style="bold magenta")
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Calls", justify="right")
        model_table.add_column("Error Rate", justify="right")
        model_table.add_column("Avg Latency", justify="right")
        for model, stats in m.model_health.items():
            err_style = "green" if stats["error_rate"] < 0.1 else "red"
            model_table.add_row(
                model,
                str(stats["total_calls"]),
                f"{stats['error_rate']:.1%}",
                f"{stats['avg_latency_ms']:.0f}ms",
                style=err_style,
            )

        # Recent Traces 表格
        trace_table = Table(title="Recent Traces", show_header=True, header_style="bold magenta")
        trace_table.add_column("ID", justify="right")
        trace_table.add_column("Agent", style="cyan")
        trace_table.add_column("Model")
        trace_table.add_column("Status")
        trace_table.add_column("Latency", justify="right")
        for t in m.recent_traces[:10]:
            status_style = "green" if t.status in ("ok", "success", "passed") else "red"
            trace_table.add_row(
                str(t.id),
                t.agent or "-",
                t.model or "-",
                t.status or "-",
                f"{t.latency_ms or 0}ms",
                style=status_style,
            )

        # 输出到字符串
        from io import StringIO
        output = StringIO()
        out_console = Console(file=output, force_terminal=True, color_system="auto")
        out_console.print(Panel(summary, title="Dashboard", border_style="blue"))
        out_console.print(model_table)
        out_console.print(trace_table)
        return output.getvalue()


# ───────────────────────────────────────────────────────────────
# ReportGenerator — Markdown 报告生成
# ───────────────────────────────────────────────────────────────

class ReportGenerator:
    """生成 Markdown 可观测性报告"""

    def __init__(self, store: ObservabilityStore) -> None:
        self.store = store

    def generate(self, project_id: str) -> str:
        """生成完整 Markdown 报告"""
        dashboard = Dashboard(self.store)
        m = dashboard.get_metrics(project_id)

        lines = [
            f"# 可观测性报告 — {project_id}",
            "",
            f"生成时间: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## 项目概况",
            "",
            f"- **当前 Phase**: {m.current_phase}",
            f"- **Features**: {m.total_features} 总计, {m.passed_features} 通过, {m.failed_features} 失败, {m.in_progress_features} 进行中",
            f"- **总 Token 消耗**: {m.total_tokens}",
            f"- **总成本**: ${m.total_cost_usd:.2f}",
            f"- **平均延迟**: {m.avg_latency_ms:.0f}ms",
            f"- **缓存命中率**: {m.cache_hit_rate:.0%}",
            f"- **错误率**: {m.error_rate:.0%}",
            "",
            "## 模型健康度",
            "",
        ]

        if m.model_health:
            lines.append("| Model | Calls | Success | Error Rate | Avg Latency |")
            lines.append("|-------|-------|---------|------------|-------------|")
            for model, stats in m.model_health.items():
                lines.append(
                    f"| {model} | {stats['total_calls']} | {stats['success_calls']} | {stats['error_rate']:.1%} | {stats['avg_latency_ms']:.0f}ms |"
                )
        else:
            lines.append("_暂无模型健康度数据_")

        lines.extend([
            "",
            "## 最近 Traces",
            "",
        ])

        if m.recent_traces:
            lines.append("| ID | Agent | Model | Status | Latency | Tokens |")
            lines.append("|----|-------|-------|--------|---------|--------|")
            for t in m.recent_traces[:20]:
                tokens = (t.input_tokens or 0) + (t.output_tokens or 0)
                lines.append(
                    f"| {t.id} | {t.agent or '-'} | {t.model or '-'} | {t.status or '-'} | {t.latency_ms or 0}ms | {tokens} |"
                )
        else:
            lines.append("_暂无 trace 数据_")

        lines.extend([
            "",
            "## 最近 Audit Logs",
            "",
        ])

        if m.recent_audit_logs:
            lines.append("| ID | Agent | Command | Allowed |")
            lines.append("|----|-------|---------|---------|")
            for log in m.recent_audit_logs[:10]:
                allowed = "✓" if log.allowed else "✗" if log.allowed is not None else "?"
                lines.append(f"| {log.id} | {log.agent or '-'} | {log.command or '-'} | {allowed} |")
        else:
            lines.append("_暂无审计日志_")

        lines.extend([
            "",
            "## 最近 Checkpoints",
            "",
        ])

        if m.recent_checkpoints:
            lines.append("| ID | Phase | Agent | Action | Result |")
            lines.append("|----|-------|-------|--------|--------|")
            for cp in m.recent_checkpoints[:10]:
                lines.append(
                    f"| {cp.id} | {cp.phase} | {cp.agent or '-'} | {cp.action or '-'} | {cp.result or '-'} |"
                )
        else:
            lines.append("_暂无 checkpoint 数据_")

        lines.append("")
        return "\n".join(lines)


# ───────────────────────────────────────────────────────────────
# 便捷函数（供 pipeline.py 调用）
# ───────────────────────────────────────────────────────────────

def get_dashboard(db_path: Path, project_id: str, rich_mode: bool = False) -> str:
    """获取仪表盘字符串"""
    store = ObservabilityStore(db_path)
    dashboard = Dashboard(store)
    if rich_mode:
        return dashboard.render_rich(project_id)
    return dashboard.render_text(project_id)


def get_report(db_path: Path, project_id: str) -> str:
    """获取 Markdown 报告字符串"""
    store = ObservabilityStore(db_path)
    generator = ReportGenerator(store)
    return generator.generate(project_id)


def get_model_health_report(db_path: Path) -> str:
    """获取模型健康度报告"""
    store = ObservabilityStore(db_path)
    summary = store.get_model_health_summary()
    if not summary:
        return "暂无模型健康度数据"
    lines = ["模型健康度:", ""]
    for model, stats in summary.items():
        status = "✓" if stats["error_rate"] < 0.1 else "⚠" if stats["error_rate"] < 0.3 else "✗"
        lines.append(f"  {status} {model}: {stats['total_calls']} calls, {stats['error_rate']:.1%} error, {stats['avg_latency_ms']:.0f}ms avg")
    return "\n".join(lines)


def run_alert_check(db_path: Path, project_id: str) -> List[AlertEvent]:
    """对指定项目执行告警检测"""
    store = ObservabilityStore(db_path)
    manager = AlertManager()
    traces = store.get_traces(project_id, limit=100)
    alerts = manager.check_traces(traces)
    health = store.get_model_health_summary()
    alerts.extend(manager.check_model_health(health))
    return alerts
