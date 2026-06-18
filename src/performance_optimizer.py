"""src/performance_optimizer.py — F020 性能优化与 Prompt Cache 调优

职责：
  1. 监控 Prompt Cache 命中率、模型健康度、上下文压缩效率
  2. 基于实测数据调优预算和策略参数
  3. 生成 cache_stats.json 和 context_loss.json 报告
  4. 提供性能基准测试接口

PRD 第 20 节定义。
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.prompt_cache import PromptCache, CacheStats
from src.context_manager import ContextManager, LayerPriority
from src.observability import ObservabilityStore, AlertManager, Dashboard
from src.state_store import StateStore, TraceRecord


# ───────────────────────────────────────────────────────────────
# 常量 / 调优参数（基于 Week 1-10 实测数据）
# ───────────────────────────────────────────────────────────────

# Prompt Cache 调优参数
TARGET_HIT_RATE = 0.50          # 验收标准：命中率 > 50%
TARGET_HIT_RATE_OPTIMAL = 0.70  # 最优目标命中率
MAX_ENTRIES_TUNED = 200         # 基于实测调优的最大条目数（原 100 → 200）
DEFAULT_TTL_TUNED = 600         # 基于实测调优的 TTL（原 300 → 600 秒）

# 上下文压缩调优参数
TARGET_CONTEXT_LOSS = 0.20      # 验收标准：上下文损耗 < 20%
CONTEXT_LOSS_WARNING = 0.15     # 警告阈值
SAFETY_RESERVE_TUNED = 8_000    # 安全预留调优（原 5K → 8K）
TASK_RESERVE_TUNED = 25_000     # 任务预留调优（原 20K → 25K）

# 双模型路由调优参数
DUAL_MODEL_MAX_CONTEXT_LOSS = 0.20  # 双模型路由上下文损耗上限

# 性能基准参数
BENCHMARK_WARMUP = 3            # 基准测试预热次数
BENCHMARK_ITERATIONS = 10       # 基准测试迭代次数


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class CachePerformanceReport:
    """Prompt Cache 性能报告"""
    timestamp: float
    hit_rate: float
    hits: int
    misses: int
    total_requests: int
    evictions: int
    expirations: int
    current_size: int
    max_entries: int
    avg_ttl_seconds: float
    target_hit_rate: float
    meets_target: bool
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": _fmt_time(self.timestamp),
            "hit_rate": round(self.hit_rate, 4),
            "hit_rate_percent": round(self.hit_rate * 100, 2),
            "hits": self.hits,
            "misses": self.misses,
            "total_requests": self.total_requests,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "current_size": self.current_size,
            "max_entries": self.max_entries,
            "avg_ttl_seconds": round(self.avg_ttl_seconds, 2),
            "target_hit_rate": self.target_hit_rate,
            "target_hit_rate_percent": round(self.target_hit_rate * 100, 2),
            "meets_target": self.meets_target,
            "recommendations": self.recommendations,
        }


@dataclass
class ContextLossReport:
    """上下文压缩损耗报告"""
    timestamp: float
    total_layers: int
    compressed_layers: int
    context_loss_ratio: float          # 被压缩内容 / 总内容
    context_loss_percent: float
    safety_preserved: bool
    target_loss: float
    meets_target: bool
    compression_count: int
    avg_tokens_before: float
    avg_tokens_after: float
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": _fmt_time(self.timestamp),
            "total_layers": self.total_layers,
            "compressed_layers": self.compressed_layers,
            "context_loss_ratio": round(self.context_loss_ratio, 4),
            "context_loss_percent": round(self.context_loss_percent, 2),
            "safety_preserved": self.safety_preserved,
            "target_loss": self.target_loss,
            "target_loss_percent": round(self.target_loss * 100, 2),
            "meets_target": self.meets_target,
            "compression_count": self.compression_count,
            "avg_tokens_before": round(self.avg_tokens_before, 2),
            "avg_tokens_after": round(self.avg_tokens_after, 2),
            "recommendations": self.recommendations,
        }


@dataclass
class ModelHealthReport:
    """模型健康度报告"""
    timestamp: float
    models: Dict[str, Dict[str, Any]]
    overall_error_rate: float
    overall_avg_latency_ms: float
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": _fmt_time(self.timestamp),
            "models": self.models,
            "overall_error_rate": round(self.overall_error_rate, 4),
            "overall_avg_latency_ms": round(self.overall_avg_latency_ms, 2),
            "recommendations": self.recommendations,
        }


@dataclass
class BenchmarkResult:
    """性能基准测试结果"""
    name: str
    iterations: int
    total_time_ms: float
    avg_time_ms: float
    min_time_ms: float
    max_time_ms: float
    median_time_ms: float
    ops_per_second: float
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_time_ms": round(self.total_time_ms, 2),
            "avg_time_ms": round(self.avg_time_ms, 4),
            "min_time_ms": round(self.min_time_ms, 4),
            "max_time_ms": round(self.max_time_ms, 4),
            "median_time_ms": round(self.median_time_ms, 4),
            "ops_per_second": round(self.ops_per_second, 2),
            "passed": self.passed,
            "details": self.details,
        }


# ───────────────────────────────────────────────────────────────
# 工具函数
# ───────────────────────────────────────────────────────────────

def _fmt_time(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _compute_recommendations_hit_rate(hit_rate: float, stats: CacheStats, size: int, max_entries: int) -> List[str]:
    """基于缓存统计生成命中率优化建议"""
    recs: List[str] = []
    if hit_rate < TARGET_HIT_RATE:
        recs.append(f"命中率 {hit_rate:.1%} 低于目标 {TARGET_HIT_RATE:.0%}，建议增加缓存容量或延长 TTL")
    if stats.evictions > stats.hits * 0.1:
        recs.append(f"淘汰率偏高 ({stats.evictions} 次淘汰)，建议增加 max_entries 当前 {max_entries}")
    if size >= max_entries * 0.9:
        recs.append(f"缓存接近满载 ({size}/{max_entries})，建议扩容")
    if stats.expirations > stats.misses * 0.2:
        recs.append("过期条目占比过高，建议延长 TTL 或优化缓存键设计")
    if not recs:
        recs.append("缓存性能良好，无需调整")
    return recs


def _compute_recommendations_context_loss(loss_ratio: float, safety_preserved: bool) -> List[str]:
    """基于上下文压缩生成优化建议"""
    recs: List[str] = []
    if loss_ratio > TARGET_CONTEXT_LOSS:
        recs.append(f"上下文损耗 {loss_ratio:.1%} 超过目标 {TARGET_CONTEXT_LOSS:.0%}，建议增加 max_context_tokens")
    if not safety_preserved:
        recs.append("安全指令未被保留，请检查压缩逻辑")
    if loss_ratio > CONTEXT_LOSS_WARNING:
        recs.append(f"上下文损耗接近警告阈值 {CONTEXT_LOSS_WARNING:.0%}，建议监控")
    if not recs:
        recs.append("上下文压缩效率良好")
    return recs


# ───────────────────────────────────────────────────────────────
# PerformanceOptimizer — 核心优化器
# ───────────────────────────────────────────────────────────────

class PerformanceOptimizer:
    """性能优化与 Prompt Cache 调优器

    职责：
      1. 监控 Prompt Cache 命中率
      2. 监控模型健康度
      3. 监控上下文压缩效率
      4. 基于实测数据调优参数
      5. 生成 JSON 报告
    """

    def __init__(
        self,
        cache: Optional[PromptCache] = None,
        context_manager: Optional[ContextManager] = None,
        observability_store: Optional[ObservabilityStore] = None,
        target_hit_rate: float = TARGET_HIT_RATE,
        target_context_loss: float = TARGET_CONTEXT_LOSS,
    ) -> None:
        self.cache = cache
        self.context_manager = context_manager
        self.observability_store = observability_store
        self.target_hit_rate = target_hit_rate
        self.target_context_loss = target_context_loss
        self._benchmark_results: List[BenchmarkResult] = []

    # ── Cache 监控 ──

    def analyze_cache_performance(self) -> CachePerformanceReport:
        """分析 Prompt Cache 性能并生成报告"""
        if self.cache is None:
            return CachePerformanceReport(
                timestamp=time.time(),
                hit_rate=0.0,
                hits=0,
                misses=0,
                total_requests=0,
                evictions=0,
                expirations=0,
                current_size=0,
                max_entries=0,
                avg_ttl_seconds=0.0,
                target_hit_rate=self.target_hit_rate,
                meets_target=False,
                recommendations=["未提供 PromptCache 实例，无法分析"],
            )

        stats = self.cache.get_stats()
        size = self.cache.size()
        max_entries = self.cache.max_entries
        hit_rate = stats.hit_rate

        # 计算平均 TTL
        entries = self.cache.list_entries()
        avg_ttl = statistics.mean([e["ttl_seconds"] for e in entries]) if entries else self.cache.default_ttl_seconds

        meets = hit_rate >= self.target_hit_rate
        recommendations = _compute_recommendations_hit_rate(hit_rate, stats, size, max_entries)

        return CachePerformanceReport(
            timestamp=time.time(),
            hit_rate=hit_rate,
            hits=stats.hits,
            misses=stats.misses,
            total_requests=stats.total_requests,
            evictions=stats.evictions,
            expirations=stats.expirations,
            current_size=size,
            max_entries=max_entries,
            avg_ttl_seconds=avg_ttl,
            target_hit_rate=self.target_hit_rate,
            meets_target=meets,
            recommendations=recommendations,
        )

    def tune_cache_params(self) -> Dict[str, Any]:
        """基于当前性能数据调优缓存参数，返回建议配置"""
        if self.cache is None:
            return {"error": "未提供 PromptCache 实例"}

        report = self.analyze_cache_performance()
        suggestions: Dict[str, Any] = {
            "current_max_entries": self.cache.max_entries,
            "current_ttl": self.cache.default_ttl_seconds,
        }

        # 调优逻辑
        if report.hit_rate < self.target_hit_rate:
            suggestions["recommended_max_entries"] = int(self.cache.max_entries * 1.5)
            suggestions["recommended_ttl"] = int(self.cache.default_ttl_seconds * 1.5)
            suggestions["action"] = "扩容 + 延长 TTL"
        elif report.hit_rate >= TARGET_HIT_RATE_OPTIMAL:
            suggestions["recommended_max_entries"] = self.cache.max_entries
            suggestions["recommended_ttl"] = self.cache.default_ttl_seconds
            suggestions["action"] = "保持当前配置"
        else:
            suggestions["recommended_max_entries"] = int(self.cache.max_entries * 1.2)
            suggestions["recommended_ttl"] = int(self.cache.default_ttl_seconds * 1.2)
            suggestions["action"] = "适度扩容"

        # 如果淘汰率过高，进一步扩容
        stats = self.cache.get_stats()
        if stats.evictions > max(stats.hits, 1) * 0.15:
            suggestions["recommended_max_entries"] = int(suggestions["recommended_max_entries"] * 1.3)
            suggestions["action"] += " + 大幅扩容（淘汰率过高）"

        suggestions["expected_hit_rate"] = min(report.hit_rate * 1.2, 0.95)
        return suggestions

    # ── 上下文压缩监控 ──

    def analyze_context_loss(self) -> ContextLossReport:
        """分析上下文压缩损耗"""
        if self.context_manager is None:
            return ContextLossReport(
                timestamp=time.time(),
                total_layers=0,
                compressed_layers=0,
                context_loss_ratio=0.0,
                context_loss_percent=0.0,
                safety_preserved=False,
                target_loss=self.target_context_loss,
                meets_target=True,
                compression_count=0,
                avg_tokens_before=0.0,
                avg_tokens_after=0.0,
                recommendations=["未提供 ContextManager 实例，无法分析"],
            )

        layers = self.context_manager.list_layers()
        total_layers = len(layers)
        compression_log = self.context_manager.get_compression_log()
        compression_count = len(compression_log)

        # 计算被压缩的层数
        compressed_layers = 0
        total_tokens_before = 0
        total_tokens_after = 0
        for log in compression_log:
            dropped = log.get("dropped_layers", [])
            compressed_layers += len(dropped)
            total_tokens_before += log.get("before_tokens", 0)
            total_tokens_after += log.get("after_tokens", 0)

        # 计算上下文损耗比例
        if total_tokens_before > 0:
            loss_ratio = (total_tokens_before - total_tokens_after) / total_tokens_before
        else:
            # 如果没有压缩记录，检查当前层大小与限制
            total_tokens = self.context_manager._total_tokens()
            max_tokens = self.context_manager.max_context_tokens
            if max_tokens > 0 and total_tokens > max_tokens:
                loss_ratio = (total_tokens - max_tokens) / total_tokens
            else:
                loss_ratio = 0.0

        # 检查安全指令是否保留
        safety_layer = self.context_manager.get_layer("safety_instructions")
        safety_preserved = safety_layer is not None and not safety_layer.content.startswith("[已压缩]")

        meets = loss_ratio < self.target_context_loss
        recommendations = _compute_recommendations_context_loss(loss_ratio, safety_preserved)

        return ContextLossReport(
            timestamp=time.time(),
            total_layers=total_layers,
            compressed_layers=compressed_layers,
            context_loss_ratio=loss_ratio,
            context_loss_percent=loss_ratio * 100,
            safety_preserved=safety_preserved,
            target_loss=self.target_context_loss,
            meets_target=meets,
            compression_count=compression_count,
            avg_tokens_before=total_tokens_before / max(compression_count, 1),
            avg_tokens_after=total_tokens_after / max(compression_count, 1),
            recommendations=recommendations,
        )

    def tune_context_params(self) -> Dict[str, Any]:
        """基于上下文损耗调优参数"""
        if self.context_manager is None:
            return {"error": "未提供 ContextManager 实例"}

        report = self.analyze_context_loss()
        suggestions: Dict[str, Any] = {
            "current_max_context_tokens": self.context_manager.max_context_tokens,
            "current_safety_reserve": self.context_manager.safety_reserve_tokens,
            "current_task_reserve": self.context_manager.task_reserve_tokens,
        }

        if not report.meets_target:
            suggestions["recommended_max_context_tokens"] = int(self.context_manager.max_context_tokens * 1.3)
            suggestions["recommended_safety_reserve"] = int(max(self.context_manager.safety_reserve_tokens * 1.2, SAFETY_RESERVE_TUNED))
            suggestions["recommended_task_reserve"] = int(max(self.context_manager.task_reserve_tokens * 1.2, TASK_RESERVE_TUNED))
            suggestions["action"] = "增加上下文窗口 + 预留空间"
        else:
            suggestions["recommended_max_context_tokens"] = self.context_manager.max_context_tokens
            suggestions["recommended_safety_reserve"] = self.context_manager.safety_reserve_tokens
            suggestions["recommended_task_reserve"] = self.context_manager.task_reserve_tokens
            suggestions["action"] = "保持当前配置"

        return suggestions

    # ── 模型健康度监控 ──

    def analyze_model_health(self) -> ModelHealthReport:
        """分析模型健康度"""
        if self.observability_store is None:
            return ModelHealthReport(
                timestamp=time.time(),
                models={},
                overall_error_rate=0.0,
                overall_avg_latency_ms=0.0,
                recommendations=["未提供 ObservabilityStore 实例，无法分析"],
            )

        summary = self.observability_store.get_model_health_summary()
        if not summary:
            return ModelHealthReport(
                timestamp=time.time(),
                models={},
                overall_error_rate=0.0,
                overall_avg_latency_ms=0.0,
                recommendations=["暂无模型健康度数据"],
            )

        total_calls = sum(s["total_calls"] for s in summary.values())
        total_errors = sum(s["total_calls"] - s["success_calls"] for s in summary.values())
        overall_error_rate = total_errors / total_calls if total_calls > 0 else 0.0

        latencies = [s["avg_latency_ms"] for s in summary.values() if s.get("avg_latency_ms")]
        overall_avg_latency = statistics.mean(latencies) if latencies else 0.0

        recommendations: List[str] = []
        for model, stats in summary.items():
            if stats["error_rate"] > 0.3:
                recommendations.append(f"模型 {model} 错误率 {stats['error_rate']:.1%} 过高，建议切换或降级")
            if stats["avg_latency_ms"] > 60000:
                recommendations.append(f"模型 {model} 延迟 {stats['avg_latency_ms']:.0f}ms 过高，建议优化或切换")

        if not recommendations:
            recommendations.append("所有模型健康度良好")

        return ModelHealthReport(
            timestamp=time.time(),
            models=summary,
            overall_error_rate=overall_error_rate,
            overall_avg_latency_ms=overall_avg_latency,
            recommendations=recommendations,
        )

    # ── 报告生成 ──

    def generate_cache_stats_json(self, output_path: Optional[str] = None) -> str:
        """生成 cache_stats.json 报告，返回文件路径"""
        report = self.analyze_cache_performance()
        data = report.to_dict()

        if output_path is None:
            output_path = "cache_stats.json"

        path = Path(output_path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.resolve())

    def generate_context_loss_json(self, output_path: Optional[str] = None) -> str:
        """生成 context_loss.json 报告，返回文件路径"""
        report = self.analyze_context_loss()
        data = report.to_dict()

        if output_path is None:
            output_path = "context_loss.json"

        path = Path(output_path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.resolve())

    def generate_combined_report(self, output_dir: Optional[str] = None) -> Dict[str, str]:
        """生成综合性能报告，返回生成的文件路径字典"""
        if output_dir is not None:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            cache_path = str(out / "cache_stats.json")
            context_path = str(out / "context_loss.json")
        else:
            cache_path = None
            context_path = None

        files = {
            "cache_stats": self.generate_cache_stats_json(cache_path),
            "context_loss": self.generate_context_loss_json(context_path),
        }

        # 如果有模型健康度数据，也生成报告
        health = self.analyze_model_health()
        if health.models:
            health_path = Path(output_dir or ".") / "model_health.json"
            health_path.write_text(json.dumps(health.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            files["model_health"] = str(health_path.resolve())

        return files

    # ── 性能基准测试 ──

    def benchmark_cache(self, iterations: int = BENCHMARK_ITERATIONS) -> BenchmarkResult:
        """基准测试：Prompt Cache 读写性能"""
        cache = self.cache or PromptCache(max_entries=MAX_ENTRIES_TUNED, default_ttl_seconds=DEFAULT_TTL_TUNED)

        # 预热
        for i in range(BENCHMARK_WARMUP):
            cache.set(f"warmup_{i}", f"response_{i}")
            cache.get(f"warmup_{i}")

        # 测试写入
        write_times: List[float] = []
        for i in range(iterations):
            t0 = time.perf_counter()
            cache.set(f"bench_{i}", {"data": i, "payload": "x" * 100})
            t1 = time.perf_counter()
            write_times.append((t1 - t0) * 1000)

        # 测试读取（命中）
        read_times: List[float] = []
        for i in range(iterations):
            t0 = time.perf_counter()
            cache.get(f"bench_{i}")
            t1 = time.perf_counter()
            read_times.append((t1 - t0) * 1000)

        all_times = write_times + read_times
        total = sum(all_times)
        avg = statistics.mean(all_times)
        min_t = min(all_times)
        max_t = max(all_times)
        median = statistics.median(all_times)
        ops = (iterations * 2) / (total / 1000)

        # 断言：单次操作应 < 10ms（内存缓存）
        passed = avg < 10.0

        result = BenchmarkResult(
            name="cache_read_write",
            iterations=iterations * 2,
            total_time_ms=total,
            avg_time_ms=avg,
            min_time_ms=min_t,
            max_time_ms=max_t,
            median_time_ms=median,
            ops_per_second=ops,
            passed=passed,
            details={
                "write_avg_ms": round(statistics.mean(write_times), 4),
                "read_avg_ms": round(statistics.mean(read_times), 4),
                "cache_size_after": cache.size(),
            },
        )
        self._benchmark_results.append(result)
        return result

    def benchmark_context_compression(self, iterations: int = BENCHMARK_ITERATIONS) -> BenchmarkResult:
        """基准测试：上下文压缩性能"""
        cm = self.context_manager or ContextManager(max_context_tokens=10_000)

        # 构建测试场景
        cm.set_safety_instructions("[安全指令] 禁止删除生产数据库\n[安全指令] 禁止执行 rm -rf /")
        cm.set_layer("feature_spec", "F020 性能优化需求" * 100, priority=LayerPriority.FEATURE_SPEC)
        cm.set_layer("history", "操作历史记录" * 500, priority=LayerPriority.HISTORY)
        cm.set_layer("memory", "项目记忆" * 500, priority=LayerPriority.MEMORY)

        times: List[float] = []
        for _ in range(iterations):
            # 重置压缩日志
            cm._compression_log.clear()
            t0 = time.perf_counter()
            cm.compress(target_tokens=5_000)
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)

        total = sum(times)
        avg = statistics.mean(times)
        min_t = min(times)
        max_t = max(times)
        median = statistics.median(times)
        ops = iterations / (total / 1000)

        # 断言：压缩操作应 < 50ms
        passed = avg < 50.0

        # 检查上下文损耗
        report = self.analyze_context_loss()
        dual_loss_ok = report.context_loss_ratio < DUAL_MODEL_MAX_CONTEXT_LOSS

        result = BenchmarkResult(
            name="context_compression",
            iterations=iterations,
            total_time_ms=total,
            avg_time_ms=avg,
            min_time_ms=min_t,
            max_time_ms=max_t,
            median_time_ms=median,
            ops_per_second=ops,
            passed=passed and dual_loss_ok,
            details={
                "context_loss_ratio": report.context_loss_ratio,
                "dual_model_loss_ok": dual_loss_ok,
                "safety_preserved": report.safety_preserved,
            },
        )
        self._benchmark_results.append(result)
        return result

    def run_all_benchmarks(self) -> List[BenchmarkResult]:
        """运行所有性能基准测试"""
        self._benchmark_results.clear()
        self.benchmark_cache()
        self.benchmark_context_compression()
        return list(self._benchmark_results)

    def all_benchmarks_passed(self) -> bool:
        """检查所有基准测试是否通过"""
        return all(r.passed for r in self._benchmark_results)

    def get_benchmark_results(self) -> List[BenchmarkResult]:
        """获取所有基准测试结果"""
        return list(self._benchmark_results)

    def generate_benchmark_report(self, output_path: Optional[str] = None) -> str:
        """生成基准测试报告 JSON"""
        data = {
            "timestamp": time.time(),
            "timestamp_iso": _fmt_time(time.time()),
            "all_passed": self.all_benchmarks_passed(),
            "results": [r.to_dict() for r in self._benchmark_results],
        }
        if output_path is None:
            output_path = "benchmark_report.json"
        path = Path(output_path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.resolve())

    # ── 一键调优 ──

    def auto_tune(self) -> Dict[str, Any]:
        """自动调优所有参数，返回调优建议汇总"""
        return {
            "cache": self.tune_cache_params(),
            "context": self.tune_context_params(),
            "model_health": self.analyze_model_health().to_dict(),
            "benchmarks": [r.to_dict() for r in self._benchmark_results],
        }
