"""src/prompt_cache_store.py — SQLite 持久化缓存存储

F016 实现：
- 缓存条目持久化到 SQLite
- 支持跨进程/跨会话恢复
- 与 PromptCache 内存缓存配合使用

PRD 第 7 节 / 第 20 节定义。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = "prompt_cache.db"


# ───────────────────────────────────────────────────────────────
# PromptCacheStore
# ───────────────────────────────────────────────────────────────

class PromptCacheStore:
    """Prompt 缓存 SQLite 持久化存储

    职责：
      1. 将缓存条目持久化到 SQLite
      2. 支持跨进程/跨会话恢复
      3. 提供独立的命中率统计（与内存统计分离）
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
    ) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        # 确保父目录存在
        parent = Path(db_path).parent
        if parent:
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── 数据库初始化 ──

    def _init_db(self) -> None:
        """初始化数据库表结构"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        hash TEXT PRIMARY KEY,
                        prompt TEXT NOT NULL,
                        response TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        accessed_at REAL,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        ttl REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_stats (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                # 初始化统计值
                for key in ("hits", "misses"):
                    conn.execute(
                        "INSERT OR IGNORE INTO cache_stats (key, value) VALUES (?, 0)",
                        (key,),
                    )
                conn.commit()
            finally:
                conn.close()

    # ── 核心操作 ──

    def _serialize_response(self, response: Any) -> str:
        """序列化响应对象"""
        return json.dumps(response, ensure_ascii=False)

    def _deserialize_response(self, data: str) -> Any:
        """反序列化响应对象"""
        return json.loads(data)

    def save_entry(
        self,
        prompt_hash: str,
        prompt: str,
        response: Any,
        ttl: float,
        now: Optional[float] = None,
    ) -> None:
        """保存或更新缓存条目到 SQLite"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                created_at = now or time.time()
                conn.execute(
                    """
                    INSERT INTO cache_entries
                        (hash, prompt, response, created_at, accessed_at, access_count, ttl)
                    VALUES
                        (?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(hash) DO UPDATE SET
                        prompt=excluded.prompt,
                        response=excluded.response,
                        created_at=excluded.created_at,
                        accessed_at=NULL,
                        access_count=0,
                        ttl=excluded.ttl
                    """,
                    (
                        prompt_hash,
                        prompt,
                        self._serialize_response(response),
                        created_at,
                        created_at,
                        ttl,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_entry(self, prompt_hash: str) -> Optional[Tuple[Any, float, int, float]]:
        """从 SQLite 加载缓存条目

        返回: (response, created_at, access_count, ttl) 或 None
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                cursor = conn.execute(
                    "SELECT response, created_at, access_count, ttl FROM cache_entries WHERE hash = ?",
                    (prompt_hash,),
                )
                row = cursor.fetchone()
                if row is None:
                    self._incr_stat("misses")
                    return None
                response, created_at, access_count, ttl = row
                return (
                    self._deserialize_response(response),
                    created_at,
                    access_count,
                    ttl,
                )
            finally:
                conn.close()

    def touch_entry(self, prompt_hash: str, now: Optional[float] = None) -> None:
        """更新条目的访问时间和访问计数"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                accessed_at = now or time.time()
                conn.execute(
                    """
                    UPDATE cache_entries
                    SET accessed_at = ?, access_count = access_count + 1
                    WHERE hash = ?
                    """,
                    (accessed_at, prompt_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_entry(self, prompt_hash: str) -> bool:
        """删除指定缓存条目，返回是否成功"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                cursor = conn.execute(
                    "DELETE FROM cache_entries WHERE hash = ?", (prompt_hash,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def clear_all(self) -> None:
        """清空所有缓存条目和统计"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                conn.execute("DELETE FROM cache_entries")
                conn.execute("UPDATE cache_stats SET value = 0")
                conn.commit()
            finally:
                conn.close()

    # ── 统计 ──

    def _incr_stat(self, key: str) -> None:
        """增加统计值（内部使用，需在锁内调用）"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.execute(
                "INSERT INTO cache_stats (key, value) VALUES (?, 1) "
                "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                (key,),
            )
            conn.commit()
        finally:
            conn.close()

    def record_hit(self) -> None:
        """记录一次命中"""
        with self._lock:
            self._incr_stat("hits")

    def record_miss(self) -> None:
        """记录一次未命中"""
        with self._lock:
            self._incr_stat("misses")

    def get_stats(self) -> Dict[str, int]:
        """获取 SQLite 存储的统计信息

        返回: {"hits": int, "misses": int, "count": int}
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                cursor = conn.execute(
                    "SELECT key, value FROM cache_stats WHERE key IN ('hits', 'misses')"
                )
                stats = {row[0]: row[1] for row in cursor.fetchall()}
                cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
                count = cursor.fetchone()[0]
                return {
                    "hits": stats.get("hits", 0),
                    "misses": stats.get("misses", 0),
                    "count": count,
                }
            finally:
                conn.close()

    # ── 批量操作 ──

    def load_all_entries(self) -> Dict[str, Dict[str, Any]]:
        """加载所有未过期的缓存条目

        返回: {hash: {prompt, response, created_at, accessed_at, access_count, ttl}}
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                cursor = conn.execute(
                    "SELECT hash, prompt, response, created_at, accessed_at, access_count, ttl FROM cache_entries"
                )
                result = {}
                for row in cursor.fetchall():
                    (
                        h,
                        prompt,
                        response,
                        created_at,
                        accessed_at,
                        access_count,
                        ttl,
                    ) = row
                    result[h] = {
                        "prompt": prompt,
                        "response": self._deserialize_response(response),
                        "created_at": created_at,
                        "accessed_at": accessed_at,
                        "access_count": access_count,
                        "ttl": ttl,
                    }
                return result
            finally:
                conn.close()

    def delete_expired(self, now: Optional[float] = None) -> int:
        """删除所有过期条目，返回删除数量"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                now = now or time.time()
                cursor = conn.execute(
                    "DELETE FROM cache_entries WHERE ttl > 0 AND (? - created_at) > ttl",
                    (now,),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def entry_count(self) -> int:
        """当前缓存条目数量"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
                return cursor.fetchone()[0]
            finally:
                conn.close()

    def close(self) -> None:
        """关闭存储（SQLite 连接已按操作关闭，此方法用于兼容性）"""
        pass
