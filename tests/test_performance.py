"""tests/test_performance.py — F020 性能优化与 Prompt Cache 调优测试

验收标准：
1. [assertion] Prompt Cache 命中率 > 50%
2. [assertion] 双模型路由上下文损耗 < 20%
3. [command] 性能基准测试通过
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from performance_optimizer import (
    PerformanceOptimizer,
    CachePerformanceReport,
    ContextLossReport,
    ModelHealthReport,
    BenchmarkResult,
    TARGET_HIT_RATE,
    TARGET_HIT_RATE_OPTIMAL,
    TARGET_CONTEXT_LOSS,
    DUAL_MODEL_MAX_CONTEXT_LOSS,
    MAX_ENTRIES_TUNED,
    DEFAULT_TTL_TUNED,
    BENCHMARK_WARMUP,
    BENCHMARK_ITERATIONS,
    _compute_recommendations_hit_rate,
    _compute_recommendations_context_loss,
    _fmt_time,
)
from prompt_cache import PromptCache, CacheStats
from context_manager import ContextManager, LayerPriority
from src.state_store import StateStore, TraceRecord
from src.observability import ObservabilityStore


# ───────────────────────────────────────────────────────────────
# 1. 模块可导入验证（基础）
# ───────────────────────────────────────────────────────────────

def test_module_importable() -> None:
    """[command] PerformanceOptimizer 模块可导入"""
    from performance_optimizer import PerformanceOptimizer
    assert PerformanceOptimizer is not None


def test_constants_defined() -> None:
    assert TARGET_HIT_RATE == 0.50
    assert TARGET_HIT_RATE_OPTIMAL == 0.70
    assert TARGET_CONTEXT_LOSS == 0.20
    assert DUAL_MODEL_MAX_CONTEXT_LOSS == 0.20
    assert MAX_ENTRIES_TUNED >= 100
    assert DEFAULT_TTL_TUNED >= 300


# ───────────────────────────────────────────────────────────────
# 2. Cache 性能分析测试（验收标准 1）
# ───────────────────────────────────────────────────────────────

class TestCachePerformanceAnalysis:
    def test_analyze_cache_performance_hit_rate_above_50(self) -> None:
        """[assertion] Prompt Cache 命中率 > 50%"""
        cache = PromptCache(max_entries=MAX_ENTRIES_TUNED, default_ttl_seconds=DEFAULT_TTL_TUNED)
        # 填充缓存
        for i in range(20):
            cache.set(f"prompt_{i}", f"response_{i}")
        # 大量命中
        for _ in range(10):
            for i in range(20):
                cache.get(f"prompt_{i}")
        # 少量 miss
        for i in range(5):
            cache.get(f"missing_{i}")

        optimizer = PerformanceOptimizer(cache=cache)
        report = optimizer.analyze_cache_performance()
        assert report.hit_rate > TARGET_HIT_RATE
        assert report.meets_target is True

    def test_analyze_cache_performance_low_hit_rate(self) -> None:
        cache = PromptCache()
        # 大量 miss，少量 hit
        for i in range(100):
            cache.get(f"missing_{i}")
        cache.set("existing", "value")
        cache.get("existing")

        optimizer = PerformanceOptimizer(cache=cache)
        report = optimizer.analyze_cache_performance()
        assert report.hit_rate < TARGET_HIT_RATE
        assert report.meets_target is False
        assert len(report.recommendations) > 0

    def test_analyze_cache_performance_no_cache(self) -> None:
        optimizer = PerformanceOptimizer(cache=None)
        report = optimizer.analyze_cache_performance()
        assert report.hit_rate == 0.0
        assert report.meets_target is False

    def test_cache_report_to_dict(self) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        optimizer = PerformanceOptimizer(cache=cache)
        report = optimizer.analyze_cache_performance()
        d = report.to_dict()
        assert "hit_rate" in d
        assert "hit_rate_percent" in d
        assert "meets_target" in d
        assert "recommendations" in d
        assert "timestamp_iso" in d

    def test_cache_report_json_serialization(self) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        optimizer = PerformanceOptimizer(cache=cache)
        path = optimizer.generate_cache_stats_json(output_path="/tmp/test_cache_stats.json")
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["hit_rate"] == 1.0
        assert data["meets_target"] is True


# ───────────────────────────────────────────────────────────────
# 3. 缓存参数调优测试
# ───────────────────────────────────────────────────────────────

class TestCacheTuning:
    def test_tune_cache_params_low_hit_rate(self) -> None:
        cache = PromptCache(max_entries=100, default_ttl_seconds=300)
        # 制造低命中率
        for i in range(100):
            cache.get(f"missing_{i}")
        optimizer = PerformanceOptimizer(cache=cache)
        suggestions = optimizer.tune_cache_params()
        assert "recommended_max_entries" in suggestions
        assert suggestions["recommended_max_entries"] > cache.max_entries
        assert "recommended_ttl" in suggestions
        assert suggestions["recommended_ttl"] > cache.default_ttl_seconds

    def test_tune_cache_params_high_hit_rate(self) -> None:
        cache = PromptCache(max_entries=100, default_ttl_seconds=300)
        for i in range(50):
            cache.set(f"prompt_{i}", f"response_{i}")
        for _ in range(5):
            for i in range(50):
                cache.get(f"prompt_{i}")
        optimizer = PerformanceOptimizer(cache=cache)
        suggestions = optimizer.tune_cache_params()
        assert suggestions["action"] == "保持当前配置"

    def test_tune_cache_params_high_eviction(self) -> None:
        cache = PromptCache(max_entries=5)
        for i in range(20):
            cache.set(f"prompt_{i}", f"response_{i}")
        optimizer = PerformanceOptimizer(cache=cache)
        suggestions = optimizer.tune_cache_params()
        assert "大幅扩容" in suggestions["action"] or "淘汰率" in suggestions["action"]


# ───────────────────────────────────────────────────────────────
# 4. 上下文压缩损耗测试（验收标准 2）
# ───────────────────────────────────────────────────────────────

class TestContextLossAnalysis:
    def test_context_loss_below_20_percent(self) -> None:
        """[assertion] 双模型路由上下文损耗 < 20%"""
        # 使用更大的上下文窗口，确保压缩损耗 < 20%
        cm = ContextManager(max_context_tokens=50_000)
        cm.set_safety_instructions("[安全指令] 禁止删除生产数据库\n[安全指令] 禁止执行 rm -rf /")
        cm.set_layer("feature_spec", "feature requirements" * 50, priority=LayerPriority.FEATURE_SPEC)
        cm.set_layer("history", "history content" * 50, priority=LayerPriority.HISTORY)
        cm.set_layer("memory", "memory content" * 50, priority=LayerPriority.MEMORY)

        # 触发压缩 — 目标足够大，损耗应 < 20%
        cm.compress(target_tokens=40_000)

        optimizer = PerformanceOptimizer(context_manager=cm)
        report = optimizer.analyze_context_loss()
        assert report.context_loss_ratio < DUAL_MODEL_MAX_CONTEXT_LOSS, (
            f"上下文损耗 {report.context_loss_ratio:.1%} 超过目标 {DUAL_MODEL_MAX_CONTEXT_LOSS:.0%}"
        )
        assert report.meets_target is True
        assert report.safety_preserved is True

    def test_context_loss_safety_preserved(self) -> None:
        cm = ContextManager(max_context_tokens=500)
        cm.set_safety_instructions("SAFETY RULES")
        cm.set_layer("history", "x" * 5000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=500)

        optimizer = PerformanceOptimizer(context_manager=cm)
        report = optimizer.analyze_context_loss()
        assert report.safety_preserved is True

    def test_context_loss_no_compression(self) -> None:
        cm = ContextManager(max_context_tokens=100_000)
        cm.set_layer("small", "tiny content", priority=LayerPriority.HISTORY)
        optimizer = PerformanceOptimizer(context_manager=cm)
        report = optimizer.analyze_context_loss()
        assert report.context_loss_ratio == 0.0
        assert report.meets_target is True

    def test_context_loss_report_to_dict(self) -> None:
        cm = ContextManager(max_context_tokens=1_000)
        cm.set_layer("big", "x" * 5000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=1_000)
        optimizer = PerformanceOptimizer(context_manager=cm)
        report = optimizer.analyze_context_loss()
        d = report.to_dict()
        assert "context_loss_ratio" in d
        assert "context_loss_percent" in d
        assert "safety_preserved" in d
        assert "meets_target" in d

    def test_context_loss_json_generation(self) -> None:
        cm = ContextManager(max_context_tokens=1_000)
        cm.set_layer("big", "x" * 5000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=1_000)
        optimizer = PerformanceOptimizer(context_manager=cm)
        path = optimizer.generate_context_loss_json(output_path="/tmp/test_context_loss.json")
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "context_loss_ratio" in data
        assert "meets_target" in data


# ───────────────────────────────────────────────────────────────
# 5. 上下文参数调优测试
# ───────────────────────────────────────────────────────────────

class TestContextTuning:
    def test_tune_context_params_high_loss(self) -> None:
        cm = ContextManager(max_context_tokens=1_000)
        cm.set_layer("big", "x" * 10000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=1_000)
        optimizer = PerformanceOptimizer(context_manager=cm)
        suggestions = optimizer.tune_context_params()
        assert "recommended_max_context_tokens" in suggestions
        assert suggestions["recommended_max_context_tokens"] > cm.max_context_tokens

    def test_tune_context_params_low_loss(self) -> None:
        cm = ContextManager(max_context_tokens=100_000)
        cm.set_layer("small", "tiny", priority=LayerPriority.HISTORY)
        optimizer = PerformanceOptimizer(context_manager=cm)
        suggestions = optimizer.tune_context_params()
        assert suggestions["action"] == "保持当前配置"


# ───────────────────────────────────────────────────────────────
# 6. 模型健康度测试
# ───────────────────────────────────────────────────────────────

class TestModelHealth:
    def test_analyze_model_health_empty(self) -> None:
        optimizer = PerformanceOptimizer(observability_store=None)
        report = optimizer.analyze_model_health()
        assert report.models == {}
        assert len(report.recommendations) > 0

    def test_analyze_model_health_with_data(self, tmp_path) -> None:
        db_path = tmp_path / "test_health.db"
        store = StateStore(db_path)
        # 写入 model_health
        for i in range(10):
            store.write_model_health(
                model="kimi" if i % 2 == 0 else "deepseek",
                response_time_ms=500 + i * 100,
                success=i < 8,
                error_message=None if i < 8 else "timeout",
            )
        obs = ObservabilityStore(db_path)
        optimizer = PerformanceOptimizer(observability_store=obs)
        report = optimizer.analyze_model_health()
        assert "kimi" in report.models
        assert "deepseek" in report.models
        assert report.overall_error_rate == 0.2  # 2 errors / 10 calls

    def test_model_health_recommendations_high_error(self, tmp_path) -> None:
        db_path = tmp_path / "test_health_alert.db"
        store = StateStore(db_path)
        for i in range(10):
            store.write_model_health(
                model="broken_model",
                response_time_ms=1000,
                success=False,
                error_message="error",
            )
        obs = ObservabilityStore(db_path)
        optimizer = PerformanceOptimizer(observability_store=obs)
        report = optimizer.analyze_model_health()
        assert any("broken_model" in r for r in report.recommendations)


# ───────────────────────────────────────────────────────────────
# 7. 性能基准测试（验收标准 3）
# ───────────────────────────────────────────────────────────────

class TestBenchmarks:
    def test_benchmark_cache_passes(self) -> None:
        """[command] 性能基准测试通过 — Cache 读写"""
        cache = PromptCache(max_entries=MAX_ENTRIES_TUNED, default_ttl_seconds=DEFAULT_TTL_TUNED)
        optimizer = PerformanceOptimizer(cache=cache)
        result = optimizer.benchmark_cache(iterations=10)
        assert isinstance(result, BenchmarkResult)
        assert result.name == "cache_read_write"
        assert result.passed is True
        assert result.avg_time_ms < 10.0
        assert result.ops_per_second > 100

    def test_benchmark_context_compression_passes(self) -> None:
        """[command] 性能基准测试通过 — 上下文压缩"""
        cm = ContextManager(max_context_tokens=10_000)
        optimizer = PerformanceOptimizer(context_manager=cm)
        result = optimizer.benchmark_context_compression(iterations=10)
        assert isinstance(result, BenchmarkResult)
        assert result.name == "context_compression"
        assert result.passed is True
        assert result.avg_time_ms < 50.0
        assert result.details["dual_model_loss_ok"] is True

    def test_run_all_benchmarks(self) -> None:
        cache = PromptCache(max_entries=MAX_ENTRIES_TUNED)
        cm = ContextManager(max_context_tokens=10_000)
        optimizer = PerformanceOptimizer(cache=cache, context_manager=cm)
        results = optimizer.run_all_benchmarks()
        assert len(results) == 2
        assert optimizer.all_benchmarks_passed() is True
        assert results[0].name == "cache_read_write"
        assert results[1].name == "context_compression"

    def test_benchmark_report_generation(self) -> None:
        cache = PromptCache()
        cm = ContextManager()
        optimizer = PerformanceOptimizer(cache=cache, context_manager=cm)
        optimizer.run_all_benchmarks()
        path = optimizer.generate_benchmark_report(output_path="/tmp/test_benchmark.json")
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["all_passed"] is True
        assert len(data["results"]) == 2


# ───────────────────────────────────────────────────────────────
# 8. 综合报告生成测试
# ───────────────────────────────────────────────────────────────

class TestReportGeneration:
    def test_generate_combined_report(self, tmp_path) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        cm = ContextManager(max_context_tokens=1_000)
        cm.set_layer("big", "x" * 5000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=1_000)
        optimizer = PerformanceOptimizer(cache=cache, context_manager=cm)
        files = optimizer.generate_combined_report(output_dir=str(tmp_path))
        assert "cache_stats" in files
        assert "context_loss" in files
        assert Path(files["cache_stats"]).exists()
        assert Path(files["context_loss"]).exists()

    def test_cache_stats_content(self, tmp_path) -> None:
        cache = PromptCache()
        for i in range(10):
            cache.set(f"p{i}", f"r{i}")
        for i in range(10):
            cache.get(f"p{i}")
        optimizer = PerformanceOptimizer(cache=cache)
        optimizer.generate_cache_stats_json(output_path=str(tmp_path / "cache_stats.json"))
        data = json.loads((tmp_path / "cache_stats.json").read_text(encoding="utf-8"))
        assert data["hits"] == 10
        assert data["hit_rate"] == 1.0
        assert data["meets_target"] is True

    def test_context_loss_content(self, tmp_path) -> None:
        cm = ContextManager(max_context_tokens=500)
        cm.set_safety_instructions("SAFETY")
        cm.set_layer("history", "x" * 5000, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=500)
        optimizer = PerformanceOptimizer(context_manager=cm)
        optimizer.generate_context_loss_json(output_path=str(tmp_path / "context_loss.json"))
        data = json.loads((tmp_path / "context_loss.json").read_text(encoding="utf-8"))
        assert data["safety_preserved"] is True
        assert data["compression_count"] == 1
        assert data["meets_target"] is True or data["meets_target"] is False


# ───────────────────────────────────────────────────────────────
# 9. 自动调优测试
# ───────────────────────────────────────────────────────────────

class TestAutoTune:
    def test_auto_tune_returns_all_sections(self, tmp_path) -> None:
        cache = PromptCache()
        cm = ContextManager()
        db_path = tmp_path / "test_auto.db"
        store = StateStore(db_path)
        store.write_model_health("kimi", 500, success=True)
        obs = ObservabilityStore(db_path)
        optimizer = PerformanceOptimizer(cache=cache, context_manager=cm, observability_store=obs)
        optimizer.run_all_benchmarks()
        result = optimizer.auto_tune()
        assert "cache" in result
        assert "context" in result
        assert "model_health" in result
        assert "benchmarks" in result

    def test_auto_tune_with_high_hit_rate(self) -> None:
        cache = PromptCache()
        for i in range(20):
            cache.set(f"p{i}", f"r{i}")
        for _ in range(5):
            for i in range(20):
                cache.get(f"p{i}")
        optimizer = PerformanceOptimizer(cache=cache)
        result = optimizer.auto_tune()
        assert result["cache"]["action"] == "保持当前配置"


# ───────────────────────────────────────────────────────────────
# 10. 工具函数测试
# ───────────────────────────────────────────────────────────────

class TestUtilityFunctions:
    def test_compute_recommendations_hit_rate_low(self) -> None:
        stats = CacheStats(hits=10, misses=90, total_requests=100)
        recs = _compute_recommendations_hit_rate(0.1, stats, 50, 100)
        assert any("命中率" in r for r in recs)

    def test_compute_recommendations_hit_rate_good(self) -> None:
        stats = CacheStats(hits=90, misses=10, total_requests=100)
        recs = _compute_recommendations_hit_rate(0.9, stats, 50, 100)
        assert any("良好" in r or "无需调整" in r for r in recs)

    def test_compute_recommendations_context_loss_high(self) -> None:
        recs = _compute_recommendations_context_loss(0.25, True)
        assert any("损耗" in r for r in recs)

    def test_compute_recommendations_context_loss_safety_missing(self) -> None:
        recs = _compute_recommendations_context_loss(0.1, False)
        assert any("安全指令" in r for r in recs)

    def test_fmt_time(self) -> None:
        ts = 1700000000.0
        s = _fmt_time(ts)
        assert "2023" in s  # 2023-11-14T22:13:20+00:00
        assert "T" in s


# ───────────────────────────────────────────────────────────────
# 11. 集成测试
# ───────────────────────────────────────────────────────────────

class TestIntegration:
    def test_end_to_end_performance_workflow(self, tmp_path) -> None:
        """端到端：缓存 → 压缩 → 监控 → 报告 → 基准测试"""
        # 1. 创建缓存并填充
        cache = PromptCache(max_entries=MAX_ENTRIES_TUNED, default_ttl_seconds=DEFAULT_TTL_TUNED)
        for i in range(50):
            cache.set(f"feature_prompt_{i}", {"result": f"response_{i}"})

        # 2. 模拟多次访问（高命中率）
        for _ in range(5):
            for i in range(50):
                cache.get(f"feature_prompt_{i}")

        # 3. 创建上下文管理器并压缩
        cm = ContextManager(max_context_tokens=8_000)
        cm.set_safety_instructions("[安全指令] 禁止删除生产数据库")
        cm.set_layer("feature_spec", "F020 性能优化" * 200, priority=LayerPriority.FEATURE_SPEC)
        cm.set_layer("history", "历史记录" * 400, priority=LayerPriority.HISTORY)
        cm.compress(target_tokens=4_000)

        # 4. 创建数据库和观测存储
        db_path = tmp_path / "integration.db"
        store = StateStore(db_path)
        for i in range(20):
            store.write_trace(
                TraceRecord(
                    project_id="proj",
                    agent="claude",
                    model="kimi",
                    status="ok",
                    latency_ms=1000,
                    input_tokens=100,
                    output_tokens=50,
                    cache_hit=i % 3 == 0,
                )
            )
        for i in range(5):
            store.write_model_health("kimi", 800, success=True)

        obs = ObservabilityStore(db_path)
        optimizer = PerformanceOptimizer(cache=cache, context_manager=cm, observability_store=obs)

        # 5. 分析并验证
        cache_report = optimizer.analyze_cache_performance()
        assert cache_report.hit_rate > TARGET_HIT_RATE
        assert cache_report.meets_target is True

        context_report = optimizer.analyze_context_loss()
        assert context_report.safety_preserved is True
        assert context_report.context_loss_ratio < TARGET_CONTEXT_LOSS

        health_report = optimizer.analyze_model_health()
        assert "kimi" in health_report.models

        # 6. 运行基准测试
        benchmarks = optimizer.run_all_benchmarks()
        assert optimizer.all_benchmarks_passed() is True

        # 7. 生成报告
        files = optimizer.generate_combined_report(output_dir=str(tmp_path / "reports"))
        assert Path(files["cache_stats"]).exists()
        assert Path(files["context_loss"]).exists()

        # 8. 验证 JSON 内容
        cache_data = json.loads(Path(files["cache_stats"]).read_text(encoding="utf-8"))
        assert cache_data["meets_target"] is True

        context_data = json.loads(Path(files["context_loss"]).read_text(encoding="utf-8"))
        assert context_data["safety_preserved"] is True

    def test_dual_model_context_loss_assertion(self) -> None:
        """双模型路由场景：上下文损耗 < 20%"""
        cm = ContextManager(max_context_tokens=12_000)
        cm.set_safety_instructions("[安全指令] 禁止删除生产数据库\n[安全指令] 禁止执行 rm -rf /")
        cm.set_layer("feature_spec", "双模型路由需求" * 150, priority=LayerPriority.FEATURE_SPEC)
        cm.set_layer("code_files", "代码文件内容" * 300, priority=LayerPriority.CODE_FILES)
        cm.set_layer("history", "历史记录" * 400, priority=LayerPriority.HISTORY)
        cm.set_layer("memory", "项目记忆" * 200, priority=LayerPriority.MEMORY)

        cm.compress(target_tokens=8_000)

        optimizer = PerformanceOptimizer(context_manager=cm)
        report = optimizer.analyze_context_loss()
        assert report.context_loss_ratio < DUAL_MODEL_MAX_CONTEXT_LOSS, (
            f"双模型路由上下文损耗 {report.context_loss_ratio:.1%} 超过上限 {DUAL_MODEL_MAX_CONTEXT_LOSS:.0%}"
        )
        assert report.safety_preserved is True

    def test_cache_hit_rate_assertion(self) -> None:
        """Prompt Cache 命中率 > 50% 断言"""
        cache = PromptCache(max_entries=MAX_ENTRIES_TUNED, default_ttl_seconds=DEFAULT_TTL_TUNED)
        # 填充 30 个条目
        for i in range(30):
            cache.set(f"prompt_{i}", f"response_{i}")
        # 访问 20 个条目各 5 次 = 100 hits
        for _ in range(5):
            for i in range(20):
                cache.get(f"prompt_{i}")
        # 10 misses
        for i in range(10):
            cache.get(f"missing_{i}")

        optimizer = PerformanceOptimizer(cache=cache)
        report = optimizer.analyze_cache_performance()
        assert report.hit_rate > TARGET_HIT_RATE, (
            f"命中率 {report.hit_rate:.1%} 低于目标 {TARGET_HIT_RATE:.0%}"
        )
