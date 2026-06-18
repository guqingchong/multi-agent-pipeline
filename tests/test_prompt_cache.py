"""tests/test_prompt_cache.py — F016 Prompt Cache 机制单元测试

验收标准：
1. [test] Prompt Cache 单元测试通过
2. [command] 缓存命中正确
3. [command] TTL 过期正确
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prompt_cache import (
    PromptCache,
    CacheEntry,
    CacheStats,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
)


# ───────────────────────────────────────────────────────────────
# 1. 模块可导入验证（基础）
# ───────────────────────────────────────────────────────────────

def test_module_importable() -> None:
    """[command] PromptCache 模块可导入"""
    from prompt_cache import PromptCache, CacheEntry, CacheStats
    assert PromptCache is not None
    assert CacheEntry is not None
    assert CacheStats is not None


def test_constants_defined() -> None:
    assert DEFAULT_MAX_ENTRIES > 0
    assert DEFAULT_TTL_SECONDS > 0


# ───────────────────────────────────────────────────────────────
# 2. 基本缓存操作测试
# ───────────────────────────────────────────────────────────────

class TestBasicCacheOperations:
    def test_set_and_get(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        assert cache.get("hello") == "world"

    def test_get_nonexistent(self) -> None:
        cache = PromptCache()
        assert cache.get("nonexistent") is None

    def test_has_existing(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        assert cache.has("hello") is True

    def test_has_nonexistent(self) -> None:
        cache = PromptCache()
        assert cache.has("nonexistent") is False

    def test_delete_existing(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        assert cache.delete("hello") is True
        assert cache.get("hello") is None

    def test_delete_nonexistent(self) -> None:
        cache = PromptCache()
        assert cache.delete("nonexistent") is False

    def test_clear(self) -> None:
        cache = PromptCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.size() == 0

    def test_overwrite(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        cache.set("hello", "new_world")
        assert cache.get("hello") == "new_world"

    def test_size(self) -> None:
        cache = PromptCache()
        assert cache.size() == 0
        cache.set("a", 1)
        assert cache.size() == 1
        cache.set("b", 2)
        assert cache.size() == 2

    def test_is_full(self) -> None:
        cache = PromptCache(max_entries=2)
        assert cache.is_full() is False
        cache.set("a", 1)
        assert cache.is_full() is False
        cache.set("b", 2)
        assert cache.is_full() is True


# ───────────────────────────────────────────────────────────────
# 3. 缓存命中率统计测试（验收标准 2）
# ───────────────────────────────────────────────────────────────

class TestHitRate:
    def test_hit_rate_zero_requests(self) -> None:
        cache = PromptCache()
        assert cache.hit_rate() == 0.0
        assert cache.hit_rate_percent() == 0.0

    def test_hit_rate_all_hits(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        cache.get("hello")
        cache.get("hello")
        cache.get("hello")
        assert cache.hit_rate() == 1.0
        assert cache.hit_rate_percent() == 100.0

    def test_hit_rate_all_misses(self) -> None:
        cache = PromptCache()
        cache.get("missing1")
        cache.get("missing2")
        assert cache.hit_rate() == 0.0
        assert cache.hit_rate_percent() == 0.0

    def test_hit_rate_mixed(self) -> None:
        cache = PromptCache()
        cache.set("existing", "value")
        cache.get("existing")   # hit
        cache.get("missing1")  # miss
        cache.get("existing")  # hit
        cache.get("missing2")  # miss
        # 4 requests, 2 hits
        assert cache.hit_rate() == 0.5
        assert cache.hit_rate_percent() == 50.0

    def test_stats_to_dict(self) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        cache.get("y")
        stats = cache.get_stats()
        d = stats.to_dict()
        assert d["hits"] == 1
        assert d["misses"] == 1
        assert d["total_requests"] == 2
        assert d["hit_rate"] == 0.5
        assert d["hit_rate_percent"] == 50.0

    def test_reset_stats(self) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        cache.get("y")
        assert cache.hit_rate() == 0.5
        cache.reset_stats()
        assert cache.hit_rate() == 0.0
        assert cache.get_stats().total_requests == 0

    def test_hit_increments_total(self) -> None:
        cache = PromptCache()
        cache.set("x", 1)
        cache.get("x")
        stats = cache.get_stats()
        assert stats.total_requests == 1
        assert stats.hits == 1
        assert stats.misses == 0

    def test_miss_increments_total(self) -> None:
        cache = PromptCache()
        cache.get("x")
        stats = cache.get_stats()
        assert stats.total_requests == 1
        assert stats.hits == 0
        assert stats.misses == 1


# ───────────────────────────────────────────────────────────────
# 4. TTL 过期测试（验收标准 3）
# ───────────────────────────────────────────────────────────────

class TestTTL:
    def test_ttl_expired_get_returns_none(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=10, now=now)
        # 在 TTL 内
        assert cache.get("hello", now=now + 5) == "world"
        # 刚好过期
        assert cache.get("hello", now=now + 11) is None

    def test_ttl_expired_has_returns_false(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=10, now=now)
        assert cache.has("hello", now=now + 5) is True
        assert cache.has("hello", now=now + 11) is False

    def test_ttl_zero_means_no_expiry(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=0, now=now)
        assert cache.get("hello", now=now + 999999) == "world"

    def test_ttl_negative_means_no_expiry(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=-1, now=now)
        assert cache.get("hello", now=now + 999999) == "world"

    def test_expired_entry_removed(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=10, now=now)
        assert cache.size() == 1
        cache.get("hello", now=now + 11)  # 触发过期删除
        assert cache.size() == 0

    def test_expired_counts_as_miss(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("hello", "world", ttl_seconds=10, now=now)
        cache.get("hello", now=now + 11)
        stats = cache.get_stats()
        assert stats.misses == 1
        assert stats.hits == 0
        assert stats.expirations == 1

    def test_default_ttl(self) -> None:
        cache = PromptCache(default_ttl_seconds=60)
        now = 1000.0
        cache.set("hello", "world", now=now)
        assert cache.get("hello", now=now + 30) == "world"
        assert cache.get("hello", now=now + 61) is None

    def test_cleanup_expired(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("a", 1, ttl_seconds=10, now=now)
        cache.set("b", 2, ttl_seconds=100, now=now)
        cache.set("c", 3, ttl_seconds=10, now=now)
        assert cache.size() == 3
        cleaned = cache.cleanup_expired(now=now + 20)
        assert cleaned == 2
        assert cache.size() == 1
        assert cache.get("b", now=now + 20) == 2


# ───────────────────────────────────────────────────────────────
# 5. LRU 淘汰测试
# ───────────────────────────────────────────────────────────────

class TestLRUEviction:
    def test_lru_eviction_when_full(self) -> None:
        cache = PromptCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # 应淘汰 a
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_lru_eviction_updates_on_access(self) -> None:
        cache = PromptCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # 访问 a，a 变为最近使用
        cache.set("c", 3)  # 应淘汰 b
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_eviction_stats(self) -> None:
        cache = PromptCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        stats = cache.get_stats()
        assert stats.evictions == 1

    def test_overwrite_does_not_evict(self) -> None:
        cache = PromptCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)  # 覆盖，不应淘汰
        assert cache.get("a") == 10
        assert cache.get("b") == 2
        assert cache.size() == 2


# ───────────────────────────────────────────────────────────────
# 6. get_or_compute 便捷方法测试
# ───────────────────────────────────────────────────────────────

class TestGetOrCompute:
    def test_get_or_compute_miss(self) -> None:
        cache = PromptCache()
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "computed"

        result = cache.get_or_compute("key", compute)
        assert result == "computed"
        assert call_count == 1

    def test_get_or_compute_hit(self) -> None:
        cache = PromptCache()
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "computed"

        cache.get_or_compute("key", compute)
        result = cache.get_or_compute("key", compute)
        assert result == "computed"
        assert call_count == 1  # 第二次命中缓存，不调用 compute

    def test_get_or_compute_ttl(self) -> None:
        cache = PromptCache()
        now = 1000.0

        def compute():
            return "computed"

        cache.get_or_compute("key", compute, ttl_seconds=10, now=now)
        assert cache.get("key", now=now + 5) == "computed"
        assert cache.get("key", now=now + 15) is None


# ───────────────────────────────────────────────────────────────
# 7. 序列化 / 状态查询测试
# ───────────────────────────────────────────────────────────────

class TestSerialization:
    def test_list_entries(self) -> None:
        cache = PromptCache()
        cache.set("hello", "world")
        entries = cache.list_entries()
        assert len(entries) == 1
        assert entries[0]["prompt_preview"] == "hello"
        assert entries[0]["is_expired"] is False

    def test_to_dict(self) -> None:
        cache = PromptCache(max_entries=50)
        cache.set("x", 1)
        cache.get("x")
        d = cache.to_dict()
        assert d["max_entries"] == 50
        assert d["current_size"] == 1
        assert d["stats"]["hits"] == 1
        assert len(d["entries"]) == 1


# ───────────────────────────────────────────────────────────────
# 8. 复杂场景测试
# ───────────────────────────────────────────────────────────────

class TestComplexScenarios:
    def test_hit_rate_above_threshold(self) -> None:
        """模拟高命中率场景：命中率 > 50%"""
        cache = PromptCache()
        for i in range(10):
            cache.set(f"prompt_{i}", f"response_{i}")
        # 访问所有条目多次
        for _ in range(5):
            for i in range(10):
                cache.get(f"prompt_{i}")
        # 10 * 5 = 50 hits, 0 misses
        assert cache.hit_rate() == 1.0
        assert cache.hit_rate_percent() == 100.0

    def test_hit_rate_below_threshold(self) -> None:
        """模拟低命中率场景：命中率 < 30%"""
        cache = PromptCache()
        # 大量 miss
        for i in range(100):
            cache.get(f"missing_{i}")
        # 少量 hit
        cache.set("existing", "value")
        for _ in range(10):
            cache.get("existing")
        # 10 hits, 100 misses = 9.09%
        assert cache.hit_rate() < 0.3

    def test_lru_with_ttl_combined(self) -> None:
        """LRU + TTL 组合场景"""
        cache = PromptCache(max_entries=3)
        now = 1000.0
        cache.set("a", 1, ttl_seconds=100, now=now)
        cache.set("b", 2, ttl_seconds=10, now=now)
        cache.set("c", 3, ttl_seconds=100, now=now)
        # 访问 b 使其变为最近使用
        cache.get("b", now=now + 5)
        # b 过期
        now = now + 15
        cache.set("d", 4, ttl_seconds=100, now=now)
        # b 已过期，应被淘汰的是 a（最久未使用且未过期）
        assert cache.get("a", now=now) is None
        assert cache.get("b", now=now) is None  # 已过期
        assert cache.get("c", now=now) == 3
        assert cache.get("d", now=now) == 4

    def test_access_count_tracking(self) -> None:
        cache = PromptCache()
        now = 1000.0
        cache.set("x", 1, now=now)
        cache.get("x", now=now + 1)
        cache.get("x", now=now + 2)
        cache.get("x", now=now + 3)
        entries = cache.list_entries()
        assert entries[0]["access_count"] == 3
        assert entries[0]["last_accessed_at"] == now + 3

    def test_prompt_hashing_consistency(self) -> None:
        """相同 prompt 产生相同 hash，不同 prompt 产生不同 hash"""
        cache = PromptCache()
        h1 = cache._hash_prompt("hello")
        h2 = cache._hash_prompt("hello")
        h3 = cache._hash_prompt("world")
        assert h1 == h2
        assert h1 != h3

    def test_large_prompt_handling(self) -> None:
        """测试大 prompt 缓存"""
        cache = PromptCache()
        large_prompt = "x" * 10000
        cache.set(large_prompt, "big_response")
        assert cache.get(large_prompt) == "big_response"
        assert cache.size() == 1
