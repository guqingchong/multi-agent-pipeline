"""tests/test_prompt_cache_store.py — F016 SQLite 持久化缓存存储单元测试

验收标准：
1. [test] PromptCacheStore 单元测试通过
2. [command] SQLite 缓存条目 CRUD 正确
3. [command] 跨进程/跨会话恢复正确
4. [command] 统计信息正确
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prompt_cache_store import PromptCacheStore, DEFAULT_DB_PATH


# ───────────────────────────────────────────────────────────────
# 1. 模块可导入验证（基础）
# ───────────────────────────────────────────────────────────────

def test_module_importable() -> None:
    """[command] PromptCacheStore 模块可导入"""
    from prompt_cache_store import PromptCacheStore
    assert PromptCacheStore is not None


def test_default_db_path_defined() -> None:
    assert isinstance(DEFAULT_DB_PATH, str)
    assert len(DEFAULT_DB_PATH) > 0


# ───────────────────────────────────────────────────────────────
# 2. 基本 CRUD 操作测试
# ───────────────────────────────────────────────────────────────

class TestBasicCRUD:
    def test_save_and_load_entry(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == "response1"
        assert result[3] == 100

    def test_load_nonexistent(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        assert store.load_entry("nonexistent") is None

    def test_delete_existing(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        assert store.delete_entry("hash1") is True
        assert store.load_entry("hash1") is None

    def test_delete_nonexistent(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        assert store.delete_entry("nonexistent") is False

    def test_clear_all(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        store.save_entry("hash2", "prompt2", "response2", ttl=100)
        store.clear_all()
        assert store.load_entry("hash1") is None
        assert store.load_entry("hash2") is None
        assert store.entry_count() == 0

    def test_overwrite_existing(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        store.save_entry("hash1", "prompt1_new", "response2", ttl=200)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == "response2"
        assert result[3] == 200

    def test_entry_count(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        assert store.entry_count() == 0
        store.save_entry("hash1", "p1", "r1", ttl=100)
        assert store.entry_count() == 1
        store.save_entry("hash2", "p2", "r2", ttl=100)
        assert store.entry_count() == 2


# ───────────────────────────────────────────────────────────────
# 3. 序列化/反序列化测试
# ───────────────────────────────────────────────────────────────

class TestSerialization:
    def test_save_dict_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        response = {"key": "value", "nested": {"a": 1}}
        store.save_entry("hash1", "prompt1", response, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == response

    def test_save_list_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        response = [1, 2, 3, "hello"]
        store.save_entry("hash1", "prompt1", response, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == response

    def test_save_int_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", 42, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == 42

    def test_save_bool_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", True, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] is True

    def test_save_none_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", None, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] is None

    def test_save_unicode_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        response = "你好，世界 🌍"
        store.save_entry("hash1", "prompt1", response, ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[0] == response


# ───────────────────────────────────────────────────────────────
# 4. 访问统计测试
# ───────────────────────────────────────────────────────────────

class TestAccessStats:
    def test_touch_entry_updates_access(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        store.touch_entry("hash1")
        result = store.load_entry("hash1")
        assert result is not None
        assert result[2] == 1  # access_count

    def test_touch_entry_multiple_times(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        for _ in range(5):
            store.touch_entry("hash1")
        result = store.load_entry("hash1")
        assert result is not None
        assert result[2] == 5

    def test_touch_nonexistent(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        # 不应抛出异常
        store.touch_entry("nonexistent")

    def test_save_resets_access_count(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("hash1", "prompt1", "response1", ttl=100)
        store.touch_entry("hash1")
        store.save_entry("hash1", "prompt1", "response2", ttl=100)
        result = store.load_entry("hash1")
        assert result is not None
        assert result[2] == 0  # 覆盖后 access_count 重置


# ───────────────────────────────────────────────────────────────
# 5. 统计信息测试
# ───────────────────────────────────────────────────────────────

class TestStats:
    def test_initial_stats(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        stats = store.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["count"] == 0

    def test_record_hit(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.record_hit()
        store.record_hit()
        stats = store.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 0

    def test_record_miss(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.record_miss()
        stats = store.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1

    def test_mixed_stats(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        for _ in range(3):
            store.record_hit()
        for _ in range(2):
            store.record_miss()
        stats = store.get_stats()
        assert stats["hits"] == 3
        assert stats["misses"] == 2
        assert stats["count"] == 0

    def test_stats_with_entries(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("h1", "p1", "r1", ttl=100)
        store.save_entry("h2", "p2", "r2", ttl=100)
        stats = store.get_stats()
        assert stats["count"] == 2

    def test_clear_all_resets_stats(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.record_hit()
        store.record_miss()
        store.save_entry("h1", "p1", "r1", ttl=100)
        store.clear_all()
        stats = store.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["count"] == 0


# ───────────────────────────────────────────────────────────────
# 6. 过期清理测试
# ───────────────────────────────────────────────────────────────

class TestExpiration:
    def test_delete_expired(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        now = 1000.0
        store.save_entry("h1", "p1", "r1", ttl=10, now=now)
        store.save_entry("h2", "p2", "r2", ttl=100, now=now)
        store.save_entry("h3", "p3", "r3", ttl=10, now=now)
        deleted = store.delete_expired(now=now + 20)
        assert deleted == 2
        assert store.load_entry("h1") is None
        assert store.load_entry("h2") is not None
        assert store.load_entry("h3") is None

    def test_delete_expired_zero_ttl(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        now = 1000.0
        store.save_entry("h1", "p1", "r1", ttl=0, now=now)
        deleted = store.delete_expired(now=now + 999999)
        assert deleted == 0
        assert store.load_entry("h1") is not None

    def test_delete_expired_negative_ttl(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        now = 1000.0
        store.save_entry("h1", "p1", "r1", ttl=-1, now=now)
        deleted = store.delete_expired(now=now + 999999)
        assert deleted == 0

    def test_delete_expired_none(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        now = 1000.0
        store.save_entry("h1", "p1", "r1", ttl=100, now=now)
        deleted = store.delete_expired(now=now + 10)
        assert deleted == 0
        assert store.load_entry("h1") is not None


# ───────────────────────────────────────────────────────────────
# 7. 批量加载测试
# ───────────────────────────────────────────────────────────────

class TestLoadAll:
    def test_load_all_entries(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("h1", "p1", "r1", ttl=100)
        store.save_entry("h2", "p2", "r2", ttl=100)
        entries = store.load_all_entries()
        assert len(entries) == 2
        assert "h1" in entries
        assert "h2" in entries
        assert entries["h1"]["prompt"] == "p1"
        assert entries["h1"]["response"] == "r1"

    def test_load_all_empty(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        entries = store.load_all_entries()
        assert entries == {}


# ───────────────────────────────────────────────────────────────
# 8. 跨进程/跨会话恢复测试
# ───────────────────────────────────────────────────────────────

class TestCrossSession:
    def test_new_instance_reads_existing_db(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store1 = PromptCacheStore(db_path=db)
        store1.save_entry("h1", "p1", "r1", ttl=100)
        # 模拟新会话：创建新实例
        store2 = PromptCacheStore(db_path=db)
        result = store2.load_entry("h1")
        assert result is not None
        assert result[0] == "r1"

    def test_stats_persist_across_sessions(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store1 = PromptCacheStore(db_path=db)
        store1.record_hit()
        store1.record_miss()
        store2 = PromptCacheStore(db_path=db)
        stats = store2.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_clear_all_across_sessions(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store1 = PromptCacheStore(db_path=db)
        store1.save_entry("h1", "p1", "r1", ttl=100)
        store1.clear_all()
        store2 = PromptCacheStore(db_path=db)
        assert store2.load_entry("h1") is None
        assert store2.entry_count() == 0


# ───────────────────────────────────────────────────────────────
# 9. PromptCache + SQLite 集成测试
# ───────────────────────────────────────────────────────────────

class TestPromptCacheSQLiteIntegration:
    def test_sqlite_enabled_loads_entries(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        # 先创建 store 写入数据（使用 SHA256 hash 作为 key，与 PromptCache 一致）
        from prompt_cache import PromptCache
        store = PromptCacheStore(db_path=db)
        prompt = "p1"
        prompt_hash = PromptCache()._hash_prompt(prompt)
        store.save_entry(prompt_hash, prompt, "r1", ttl=100)
        # 再创建 PromptCache（启用 SQLite）
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        assert cache.get(prompt) == "r1"

    def test_sqlite_set_syncs(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("hello", "world", ttl_seconds=100)
        # 验证 SQLite 中有数据
        store = PromptCacheStore(db_path=db)
        result = store.load_entry(cache._hash_prompt("hello"))
        assert result is not None
        assert result[0] == "world"

    def test_sqlite_delete_syncs(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("hello", "world", ttl_seconds=100)
        cache.delete("hello")
        store = PromptCacheStore(db_path=db)
        assert store.load_entry(cache._hash_prompt("hello")) is None

    def test_sqlite_clear_syncs(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("a", 1, ttl_seconds=100)
        cache.set("b", 2, ttl_seconds=100)
        cache.clear()
        store = PromptCacheStore(db_path=db)
        assert store.entry_count() == 0

    def test_sqlite_hit_stats(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("hello", "world", ttl_seconds=100)
        cache.get("hello")
        cache.get("hello")
        sqlite_stats = cache.get_sqlite_stats()
        assert sqlite_stats is not None
        assert sqlite_stats["hits"] == 2

    def test_sqlite_miss_stats(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.get("missing")
        sqlite_stats = cache.get_sqlite_stats()
        assert sqlite_stats is not None
        assert sqlite_stats["misses"] == 1

    def test_sqlite_disabled_by_default(self) -> None:
        from prompt_cache import PromptCache
        cache = PromptCache()
        assert cache.sqlite_enabled is False
        assert cache._store is None
        assert cache.get_sqlite_stats() is None

    def test_sqlite_cross_session_recovery(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache1 = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache1.set("hello", "world", ttl_seconds=100)
        # 模拟新会话
        cache2 = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        assert cache2.get("hello") == "world"

    def test_sqlite_does_not_load_expired(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        store = PromptCacheStore(db_path=db)
        now = 1000.0
        store.save_entry("h1", "p1", "r1", ttl=10, now=now)
        # 创建新 cache，已过期条目不应加载
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        assert cache.get("p1") is None

    def test_sqlite_touch_updates_access(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("hello", "world", ttl_seconds=100)
        cache.get("hello")
        store = PromptCacheStore(db_path=db)
        result = store.load_entry(cache._hash_prompt("hello"))
        assert result is not None
        assert result[2] == 1  # access_count

    def test_sqlite_sync_all(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        from prompt_cache import PromptCache
        cache = PromptCache(sqlite_enabled=True, sqlite_db_path=db)
        cache.set("a", 1, ttl_seconds=100)
        cache.set("b", 2, ttl_seconds=100)
        cache.sync_all_to_sqlite()
        store = PromptCacheStore(db_path=db)
        assert store.entry_count() == 2


# ───────────────────────────────────────────────────────────────
# 10. 边界/异常测试
# ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_large_prompt(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        large_prompt = "x" * 100000
        store.save_entry("h1", large_prompt, "response", ttl=100)
        result = store.load_entry("h1")
        assert result is not None
        assert result[0] == "response"

    def test_large_response(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        large_response = {"data": "y" * 100000}
        store.save_entry("h1", "p1", large_response, ttl=100)
        result = store.load_entry("h1")
        assert result is not None
        assert result[0] == large_response

    def test_special_characters_in_prompt(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        prompt = "Hello \"world\" <tag> & 'quote' \n\t"
        store.save_entry("h1", prompt, "response", ttl=100)
        result = store.load_entry("h1")
        assert result is not None

    def test_db_file_created(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        assert os.path.exists(db)

    def test_multiple_instances_same_db(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store1 = PromptCacheStore(db_path=db)
        store2 = PromptCacheStore(db_path=db)
        store1.save_entry("h1", "p1", "r1", ttl=100)
        result = store2.load_entry("h1")
        assert result is not None

    def test_close_does_not_raise(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.close()

    def test_custom_db_path(self, tmp_path) -> None:
        custom_path = str(tmp_path / "custom" / "cache.db")
        store = PromptCacheStore(db_path=custom_path)
        store.save_entry("h1", "p1", "r1", ttl=100)
        assert os.path.exists(custom_path)

    def test_load_entry_returns_tuple(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = PromptCacheStore(db_path=db)
        store.save_entry("h1", "p1", "r1", ttl=100, now=1000.0)
        result = store.load_entry("h1")
        assert result is not None
        assert len(result) == 4
        response, created_at, access_count, ttl = result
        assert response == "r1"
        assert created_at == 1000.0
        assert access_count == 0
        assert ttl == 100
