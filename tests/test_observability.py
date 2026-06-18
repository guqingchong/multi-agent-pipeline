"""tests/test_observability.py — F015 可观测性系统测试

验收标准：
1. 可观测性单元测试通过
2. 仪表盘可启动（text / rich 模式）
3. 告警触发正确
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.state_store import StateStore, TraceRecord, AuditLogRecord, CheckpointRecord
from src.observability import (
    ObservabilityStore,
    AlertManager,
    Dashboard,
    ReportGenerator,
    AlertEvent,
    DashboardMetrics,
    get_dashboard,
    get_report,
    get_model_health_report,
    run_alert_check,
)


# ───────────────────────────────────────────────────────────────
#  fixtures
# ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db():
    """创建临时数据库并返回路径（使用独立文件路径，避免Windows临时目录清理问题）"""
    db_path = Path("/tmp/test_observability.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    store = StateStore(db_path)
    yield db_path
    # 清理：显式关闭所有连接后删除
    import gc
    gc.collect()
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        pass


@pytest.fixture
def populated_db(tmp_db):
    """创建带有测试数据的数据库"""
    store = StateStore(tmp_db)

    # 创建项目
    store.create_project("test_proj", "Test Project", "init")

    # 创建 features
    from src.state_store import FeatureRecord
    for i, status in enumerate(["passed", "in_progress", "pending", "failed", "passed"]):
        store.create_feature(
            FeatureRecord(
                id=f"F{i:03d}",
                project_id="test_proj",
                title=f"Feature {i}",
                status=status,
            )
        )

    # 创建 traces
    for i in range(20):
        store.write_trace(
            TraceRecord(
                project_id="test_proj",
                feature_id=f"F{i:03d}",
                agent="claude" if i % 2 == 0 else "qwen",
                model="kimi" if i % 3 == 0 else "deepseek",
                input_tokens=1000 + i * 100,
                output_tokens=500 + i * 50,
                cost_usd=0.01 + i * 0.001,
                latency_ms=1000 + i * 200,
                status="ok" if i < 15 else "error",
                cache_hit=i % 3 == 0,
            )
        )

    # 创建 audit_logs
    for i in range(10):
        store.write_audit_log(
            AuditLogRecord(
                project_id="test_proj",
                agent="claude",
                command=f"cmd_{i}",
                allowed=i % 2 == 0,
            )
        )

    # 创建 checkpoints
    for i in range(5):
        store.write_checkpoint(
            project_id="test_proj",
            phase="init" if i == 0 else "develop",
            state_dict={"phase": "init" if i == 0 else "develop", "iter": i},
            agent="pipeline",
            action=f"action_{i}",
            result="ok",
        )

    # 创建 model_health
    for i in range(10):
        store.write_model_health(
            model="kimi" if i % 2 == 0 else "deepseek",
            response_time_ms=500 + i * 100,
            success=i < 8,
            error_message=None if i < 8 else "timeout",
        )

    return tmp_db


# ───────────────────────────────────────────────────────────────
#  ObservabilityStore 测试
# ───────────────────────────────────────────────────────────────

class TestObservabilityStore:
    def test_get_traces(self, populated_db):
        store = ObservabilityStore(populated_db)
        traces = store.get_traces("test_proj", limit=10)
        assert len(traces) == 10
        assert all(t.project_id == "test_proj" for t in traces)

    def test_get_all_traces(self, populated_db):
        store = ObservabilityStore(populated_db)
        traces = store.get_all_traces(limit=50)
        assert len(traces) == 20

    def test_get_audit_logs(self, populated_db):
        store = ObservabilityStore(populated_db)
        logs = store.get_audit_logs("test_proj", limit=5)
        assert len(logs) == 5
        assert all(l.project_id == "test_proj" for l in logs)

    def test_get_checkpoints(self, populated_db):
        store = ObservabilityStore(populated_db)
        cps = store.get_checkpoints("test_proj", limit=3)
        assert len(cps) == 3
        assert cps[0].project_id == "test_proj"

    def test_get_model_health(self, populated_db):
        store = ObservabilityStore(populated_db)
        health = store.get_model_health(limit=5)
        assert len(health) == 5

    def test_get_model_health_summary(self, populated_db):
        store = ObservabilityStore(populated_db)
        summary = store.get_model_health_summary()
        assert "kimi" in summary
        assert "deepseek" in summary
        assert summary["kimi"]["total_calls"] > 0
        assert "error_rate" in summary["kimi"]
        assert "avg_latency_ms" in summary["kimi"]

    def test_get_feature_counts(self, populated_db):
        store = ObservabilityStore(populated_db)
        counts = store.get_feature_counts("test_proj")
        assert counts["passed"] == 2
        assert counts["in_progress"] == 1
        assert counts["pending"] == 1
        assert counts["failed"] == 1

    def test_get_project_phase(self, populated_db):
        store = ObservabilityStore(populated_db)
        phase = store.get_project_phase("test_proj")
        assert phase == "init"

    def test_empty_db(self, tmp_db):
        store = ObservabilityStore(tmp_db)
        traces = store.get_traces("nonexistent", limit=10)
        assert traces == []
        logs = store.get_audit_logs("nonexistent", limit=10)
        assert logs == []
        summary = store.get_model_health_summary()
        assert summary == {}


# ───────────────────────────────────────────────────────────────
#  AlertManager 测试
# ───────────────────────────────────────────────────────────────

class TestAlertManager:
    def test_error_rate_alert(self):
        manager = AlertManager()
        # 构造高错误率 traces
        traces = [
            TraceRecord(status="error", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(15)
        ] + [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(5)
        ]
        alerts = manager.check_traces(traces)
        error_alerts = [a for a in alerts if a.alert_type == "error_rate"]
        assert len(error_alerts) >= 1
        assert error_alerts[0].severity in ("warning", "critical")
        assert error_alerts[0].metric_value == 0.75  # 15/20

    def test_latency_alert(self):
        manager = AlertManager(thresholds={"latency_ms": 5000})
        traces = [
            TraceRecord(status="ok", latency_ms=80000, input_tokens=100, output_tokens=50)
            for _ in range(20)
        ]
        alerts = manager.check_traces(traces)
        latency_alerts = [a for a in alerts if a.alert_type == "latency"]
        assert len(latency_alerts) >= 1
        assert latency_alerts[0].severity == "critical"

    def test_token_spike_alert(self):
        manager = AlertManager(thresholds={"token_spike_multiplier": 2.0})
        # 正常 token 消耗
        traces = [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(9)
        ]
        # 突增
        traces.append(
            TraceRecord(status="ok", latency_ms=1000, input_tokens=1000, output_tokens=500)
        )
        alerts = manager.check_traces(traces)
        spike_alerts = [a for a in alerts if a.alert_type == "token_spike"]
        assert len(spike_alerts) >= 1

    def test_cache_hit_rate_alert(self):
        manager = AlertManager(thresholds={"cache_hit_rate_min": 0.5})
        traces = [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50, cache_hit=False)
            for _ in range(10)
        ]
        alerts = manager.check_traces(traces)
        cache_alerts = [a for a in alerts if a.alert_type == "cache_hit_rate"]
        assert len(cache_alerts) >= 1
        assert cache_alerts[0].metric_value == 0.0

    def test_model_health_alert(self):
        manager = AlertManager()
        summary = {
            "kimi": {"error_rate": 0.5, "avg_latency_ms": 1000, "total_calls": 10, "success_calls": 5},
        }
        alerts = manager.check_model_health(summary)
        assert len(alerts) >= 1
        assert alerts[0].alert_type == "model_health"
        assert alerts[0].severity == "critical"

    def test_no_alert_on_normal(self):
        manager = AlertManager()
        traces = [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50, cache_hit=True)
            for _ in range(20)
        ]
        alerts = manager.check_traces(traces)
        assert len(alerts) == 0

    def test_alert_history(self):
        manager = AlertManager()
        traces = [
            TraceRecord(status="error", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(20)
        ]
        manager.check_traces(traces)
        alerts = manager.get_alerts()
        assert len(alerts) >= 1
        manager.clear_alerts()
        assert manager.get_alerts() == []

    def test_notify_callback(self):
        manager = AlertManager()
        received: List[AlertEvent] = []
        manager.register_notify(lambda a: received.append(a))
        traces = [
            TraceRecord(status="error", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(20)
        ]
        manager.check_traces(traces)
        assert len(received) >= 1

    def test_custom_thresholds(self):
        manager = AlertManager(thresholds={"error_rate": 0.1})
        traces = [
            TraceRecord(status="error", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(3)
        ] + [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50)
            for _ in range(17)
        ]
        alerts = manager.check_traces(traces)
        # 3/20 = 15% > 10% threshold
        error_alerts = [a for a in alerts if a.alert_type == "error_rate"]
        assert len(error_alerts) >= 1


# ───────────────────────────────────────────────────────────────
#  Dashboard 测试
# ───────────────────────────────────────────────────────────────

class TestDashboard:
    def test_get_metrics(self, populated_db):
        store = ObservabilityStore(populated_db)
        dashboard = Dashboard(store)
        m = dashboard.get_metrics("test_proj")
        assert isinstance(m, DashboardMetrics)
        assert m.project_id == "test_proj"
        assert m.total_features == 5
        assert m.passed_features == 2
        assert m.total_traces == 20
        assert m.total_cost_usd > 0
        assert m.total_tokens > 0
        assert m.model_health is not None

    def test_render_text(self, populated_db):
        store = ObservabilityStore(populated_db)
        dashboard = Dashboard(store)
        text = dashboard.render_text("test_proj")
        assert "test_proj" in text
        assert "Phase" in text
        assert "Features" in text
        assert "Budget" in text
        assert "kimi" in text or "deepseek" in text

    def test_render_rich(self, populated_db):
        store = ObservabilityStore(populated_db)
        dashboard = Dashboard(store)
        rich_text = dashboard.render_rich("test_proj")
        assert "test_proj" in rich_text
        assert "Dashboard" in rich_text or "Model Health" in rich_text

    def test_empty_project(self, tmp_db):
        store = ObservabilityStore(tmp_db)
        dashboard = Dashboard(store)
        text = dashboard.render_text("empty_proj")
        assert "empty_proj" in text


# ───────────────────────────────────────────────────────────────
#  ReportGenerator 测试
# ───────────────────────────────────────────────────────────────

class TestReportGenerator:
    def test_generate_report(self, populated_db):
        store = ObservabilityStore(populated_db)
        gen = ReportGenerator(store)
        report = gen.generate("test_proj")
        assert "# 可观测性报告" in report
        assert "test_proj" in report
        assert "项目概况" in report
        assert "模型健康度" in report
        assert "Recent Traces" in report or "最近 Traces" in report
        assert "Audit Logs" in report or "审计日志" in report
        assert "Checkpoints" in report or "checkpoint" in report.lower()

    def test_empty_report(self, tmp_db):
        store = ObservabilityStore(tmp_db)
        gen = ReportGenerator(store)
        report = gen.generate("empty_proj")
        assert "# 可观测性报告" in report
        assert "empty_proj" in report


# ───────────────────────────────────────────────────────────────
#  便捷函数测试
# ───────────────────────────────────────────────────────────────

class TestConvenienceFunctions:
    def test_get_dashboard_text(self, populated_db):
        text = get_dashboard(populated_db, "test_proj", rich_mode=False)
        assert "test_proj" in text
        assert "Phase" in text

    def test_get_dashboard_rich(self, populated_db):
        rich_text = get_dashboard(populated_db, "test_proj", rich_mode=True)
        assert "test_proj" in rich_text

    def test_get_report(self, populated_db):
        report = get_report(populated_db, "test_proj")
        assert "# 可观测性报告" in report

    def test_get_model_health_report(self, populated_db):
        report = get_model_health_report(populated_db)
        assert "kimi" in report
        assert "deepseek" in report

    def test_get_model_health_report_empty(self, tmp_db):
        report = get_model_health_report(tmp_db)
        assert "暂无" in report or "no data" in report.lower() or "empty" in report.lower()

    def test_run_alert_check(self, populated_db):
        alerts = run_alert_check(populated_db, "test_proj")
        # 测试数据中有 5 个 error traces out of 20 = 25% > 30%? No, 25% < 30%
        # But with 20 traces, 5 errors = 25%, which is below default 30% threshold
        # However we have enough data to trigger if threshold is met
        assert isinstance(alerts, list)
        # Should at least not crash

    def test_run_alert_check_with_errors(self, tmp_db):
        store = StateStore(tmp_db)
        store.create_project("err_proj", "Error Project", "develop")
        # 写入大量 error traces
        for i in range(20):
            store.write_trace(
                TraceRecord(
                    project_id="err_proj",
                    agent="test",
                    status="error",
                    latency_ms=80000,
                    input_tokens=100,
                    output_tokens=50,
                )
            )
        alerts = run_alert_check(tmp_db, "err_proj")
        assert len(alerts) >= 1
        error_alerts = [a for a in alerts if a.alert_type == "error_rate"]
        assert len(error_alerts) >= 1


# ───────────────────────────────────────────────────────────────
#  集成测试
# ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_end_to_end(self, tmp_db):
        """端到端测试：写入数据 → 读取 → 仪表盘 → 告警 → 报告"""
        store = StateStore(tmp_db)
        store.create_project("int_proj", "Integration Project", "test")

        # 写入 traces
        for i in range(25):
            store.write_trace(
                TraceRecord(
                    project_id="int_proj",
                    agent="claude",
                    model="kimi",
                    status="error" if i >= 18 else "ok",
                    latency_ms=5000 + i * 1000,
                    input_tokens=200,
                    output_tokens=100,
                    cache_hit=i % 5 == 0,
                )
            )

        # 写入 model_health
        for i in range(5):
            store.write_model_health("kimi", 3000 + i * 500, success=i < 2, error_message="timeout" if i >= 2 else None)

        # 仪表盘
        obs = ObservabilityStore(tmp_db)
        dashboard = Dashboard(obs)
        metrics = dashboard.get_metrics("int_proj")
        assert metrics.total_traces == 25
        assert metrics.error_rate == 7 / 25  # 7 errors out of 25

        # 告警
        manager = AlertManager()
        alerts = manager.check_traces(obs.get_traces("int_proj", limit=25))
        health = obs.get_model_health_summary()
        alerts.extend(manager.check_model_health(health))
        assert len(alerts) >= 1

        # 报告
        gen = ReportGenerator(obs)
        report = gen.generate("int_proj")
        assert "int_proj" in report
        assert "kimi" in report

    def test_alert_window_sliding(self):
        """测试滑动窗口正确工作"""
        manager = AlertManager(windows={"error_rate": 5})
        # 先写入 5 个 ok (with cache_hit=True to avoid cache alert)
        ok_traces = [
            TraceRecord(status="ok", latency_ms=1000, input_tokens=100, output_tokens=50, cache_hit=True)
            for _ in range(5)
        ]
        alerts = manager.check_traces(ok_traces)
        assert len(alerts) == 0

        # 再写入 5 个 error
        error_traces = [
            TraceRecord(status="error", latency_ms=1000, input_tokens=100, output_tokens=50, cache_hit=True)
            for _ in range(5)
        ]
        alerts = manager.check_traces(error_traces)
        # 窗口大小为 5，最近 5 个都是 error，错误率 100%
        error_alerts = [a for a in alerts if a.alert_type == "error_rate"]
        assert len(error_alerts) >= 1
        assert error_alerts[0].metric_value == 1.0
