"""src/prompt_cache.py — Prompt Cache 机制

F016 实现：
- 缓存最近 N 个 prompt 的响应
- 支持 TTL 过期
- 支持缓存命中率统计
- 支持 SQLite 持久化（跨进程/跨会话恢复）
- 支持 traces 表写入（observability / state_store）
- 支持 YAML 配置读取（config_loader）

PRD 第 7 节 / 第 20 节定义。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

DEFAULT_MAX_ENTRIES = 100
DEFAULT_TTL_SECONDS = 300  # 5 分钟

# Default configuration for prompt caching
DEFAULT_CONFIG = {
    "prompt_cache": {
        "enabled": True,
        "target_hit_rate": 0.7,
        "alert_threshold": 0.3,
        "local_cache_backend": "memory",
        "vector_cache_backend": "none",
        "file_index_backend": "none",
        "cache_layers": ["memory"],
    }
}


# ───────────────────────────────────────────────────────────────
# Configuration Loader
# ───────────────────────────────────────────────────────────────

class ConfigLoader:
    """Configuration loader for prompt cache and related components."""

    def __init__(self, config_path: str = None):
        """Initialize the config loader, optionally from a YAML file."""
        import yaml
        import copy
        self._config = copy.deepcopy(DEFAULT_CONFIG)
        
        if config_path:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    yaml_data = yaml.safe_load(f)
                    if yaml_data:
                        # Deep merge YAML data with default config
                        self._deep_merge(self._config, yaml_data)
            except (FileNotFoundError, yaml.YAMLError):
                # If config file doesn't exist or is invalid, use defaults
                pass
    
    def _deep_merge(self, base_dict, update_dict):
        """Recursively merge update_dict into base_dict."""
        import copy
        for key, value in update_dict.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._deep_merge(base_dict[key], value)
            else:
                base_dict[key] = copy.deepcopy(value)
    
    def get(self, key: str, default=None):
        """Get a config value using dot notation (e.g., 'prompt_cache.enabled')."""
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
                
        return value
    
    def get_prompt_cache_config(self):
        """Get the prompt cache configuration section."""
        return self._config.get("prompt_cache", {})
    
    def is_layer_enabled(self, layer: str):
        """Check if a specific cache layer is enabled."""
        if not self.get("prompt_cache.enabled", True):
            return False
        layers = self.get("prompt_cache.cache_layers", ["memory"])
        if isinstance(layers, str):
            layers = [layers]
        return layer in layers
    
    # Properties for accessing prompt cache config values
    @property
    def prompt_cache_enabled(self):
        return self.get("prompt_cache.enabled", True)
    
    @property
    def prompt_cache_target_hit_rate(self):
        return self.get("prompt_cache.target_hit_rate", 0.7)
    
    @property
    def prompt_cache_alert_threshold(self):
        return self.get("prompt_cache.alert_threshold", 0.3)
    
    @property
    def prompt_cache_local_cache_backend(self):
        return self.get("prompt_cache.local_cache_backend", "memory")
    
    @property
    def prompt_cache_vector_cache_backend(self):
        return self.get("prompt_cache.vector_cache_backend", "none")
    
    @property
    def prompt_cache_file_index_backend(self):
        return self.get("prompt_cache.file_index_backend", "none")
    
    @property
    def prompt_cache_cache_layers(self):
        layers = self.get("prompt_cache.cache_layers", ["memory"])
        if isinstance(layers, str):
            layers = [layers]
        return layers
    
    def to_dict(self):
        """Return a copy of the internal config dictionary."""
        import copy
        return copy.deepcopy(self._config)


# ───────────────────────────────────────────────────────────────
# Data Models
# ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    """缓存条目"""
    prompt_hash: str
    prompt: str
    response: Any
    created_at: float
    ttl_seconds: float
    access_count: int = 0
    last_accessed_at: float = 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        """检查条目是否已过期"""
        if self.ttl_seconds <= 0:
            return False  # TTL <= 0 表示永不过期
        now = now or time.time()
        return (now - self.created_at) > self.ttl_seconds

    def touch(self, now: Optional[float] = None) -> None:
        """更新访问时间和计数"""
        self.access_count += 1
        self.last_accessed_at = now or time.time()


@dataclass
class CacheStats:
    """缓存统计信息"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    total_requests: int = 0

    @property
    def hit_rate(self) -> float:
        """缓存命中率（0.0 ~ 1.0）"""
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    @property
    def hit_rate_percent(self) -> float:
        """缓存命中率百分比"""
        return self.hit_rate * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "total_requests": self.total_requests,
            "hit_rate": self.hit_rate,
            "hit_rate_percent": self.hit_rate_percent,
        }


# ───────────────────────────────────────────────────────────────
# PromptCache 核心
# ───────────────────────────────────────────────────────────────

class PromptCache:
    """Prompt 缓存管理器

    职责：
      1. 缓存最近 N 个 prompt 的响应（LRU 淘汰）
      2. 支持 TTL 过期
      3. 支持缓存命中率统计
      4. 支持 SQLite 持久化（跨进程/跨会话恢复）
      5. 支持 traces 表写入（cache_hit / cache_miss）
      6. 支持 YAML 配置读取（config_loader）
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        sqlite_enabled: bool = False,
        sqlite_db_path: Optional[str] = None,
        config_path: Optional[str] = None,
        trace_writer: Optional[Any] = None,
        project_id: Optional[str] = None,
        feature_id: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> None:
        # 配置加载（优先使用传入参数，其次读取 YAML）
        self._config: Dict[str, Any] = {}
        self._layers_enabled: List[str] = ["memory"]
        if config_path is not None:
            self._load_config(config_path)

        self.max_entries = max_entries
        self.default_ttl_seconds = default_ttl_seconds
        self.sqlite_enabled = sqlite_enabled
        # 如果配置禁用了缓存，则不启用 SQLite
        if not self._config.get("enabled", True):
            self.sqlite_enabled = False

        # 存储：prompt_hash -> CacheEntry
        self._entries: Dict[str, CacheEntry] = {}
        # 统计
        self._stats = CacheStats()
        # 访问顺序记录（用于 LRU）
        self._access_order: List[str] = []

        # traces 写入器
        self._trace_writer = trace_writer
        self._project_id = project_id or "default"
        self._feature_id = feature_id
        self._agent = agent or "prompt_cache"

        # SQLite 后端
        self._store = None
        if self.sqlite_enabled and self.is_layer_enabled("memory"):
            from prompt_cache_store import PromptCacheStore
            self._store = PromptCacheStore(
                db_path=sqlite_db_path or "prompt_cache.db"
            )
            self._load_from_sqlite()

    # ── 配置加载 ──

    def _load_config(self, config_path: str) -> None:
        """从 YAML 加载 prompt_cache 配置"""
        try:
            # ConfigLoader is now defined in this module
            loader = ConfigLoader(config_path)
            self._config = loader.get_prompt_cache_config()
            self._layers_enabled = loader.prompt_cache_cache_layers
            # 用配置值覆盖默认值（如果未显式传入）
            if self._config.get("local_cache_backend") == "sqlite":
                # 如果配置指定了 sqlite 后端，自动启用 sqlite
                pass
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
            # 配置加载失败时保持默认
            self._config = {}
            self._layers_enabled = ["memory"]

    def is_layer_enabled(self, layer: str) -> bool:
        """检查指定缓存层是否启用"""
        if not self._config.get("enabled", True):
            return False
        return layer in self._layers_enabled

    # ── traces 写入 ──

    def _write_trace(self, cache_hit: bool, status: str = "ok") -> None:
        """写入 trace 记录到 traces 表"""
        if self._trace_writer is None:
            return
        try:
            # 支持两种 trace_writer 接口：
            # 1. state_store.StateStore.write_trace(trace: TraceRecord)
            # 2. observability.ObservabilityStore（只读，不支持写入）
            # 3. 直接传入一个 callable: write_trace_fn(cache_hit, ...)
            if callable(self._trace_writer) and not hasattr(self._trace_writer, "write_trace"):
                # 直接 callable
                self._trace_writer(
                    project_id=self._project_id,
                    feature_id=self._feature_id,
                    agent=self._agent,
                    cache_hit=cache_hit,
                    status=status,
                )
                return

            # 尝试使用 state_store.TraceRecord
            from state_store import TraceRecord
            trace = TraceRecord(
                project_id=self._project_id,
                feature_id=self._feature_id,
                agent=self._agent,
                model="cache",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
                latency_ms=None,
                status=status,
                cache_hit=cache_hit,
            )
            self._trace_writer.write_trace(trace)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
            # trace 写入失败不应影响缓存功能
            pass

    # ── SQLite 持久化 ──

    # ── SQLite 持久化 ──

    def _load_from_sqlite(self) -> None:
        """从 SQLite 加载缓存条目到内存"""
        if self._store is None:
            return
        entries = self._store.load_all_entries()
        now = time.time()
        for prompt_hash, data in entries.items():
            # 跳过已过期条目
            if data["ttl"] > 0 and (now - data["created_at"]) > data["ttl"]:
                continue
            entry = CacheEntry(
                prompt_hash=prompt_hash,
                prompt=data["prompt"],
                response=data["response"],
                created_at=data["created_at"],
                ttl_seconds=data["ttl"],
                access_count=data.get("access_count", 0),
                last_accessed_at=data.get("accessed_at") or 0.0,
            )
            self._entries[prompt_hash] = entry
            self._access_order.append(prompt_hash)
        # 如果加载后超出容量，执行 LRU 淘汰
        self._evict_if_needed()

    def _sync_to_sqlite(self, prompt_hash: str, prompt: str, response: Any, ttl: float, now: Optional[float] = None) -> None:
        """将条目同步到 SQLite"""
        if self._store is None:
            return
        self._store.save_entry(prompt_hash, prompt, response, ttl, now)

    def _sync_touch_to_sqlite(self, prompt_hash: str, now: Optional[float] = None) -> None:
        """将访问更新同步到 SQLite"""
        if self._store is None:
            return
        self._store.touch_entry(prompt_hash, now)

    def _sync_delete_from_sqlite(self, prompt_hash: str) -> None:
        """从 SQLite 删除条目"""
        if self._store is None:
            return
        self._store.delete_entry(prompt_hash)

    def _sync_clear_sqlite(self) -> None:
        """清空 SQLite 缓存"""
        if self._store is None:
            return
        self._store.clear_all()

    # ── 核心操作 ──

    def _hash_prompt(self, prompt: str) -> str:
        """计算 prompt 的哈希值"""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _evict_if_needed(self) -> None:
        """如果超出容量限制，淘汰最久未使用的条目"""
        while len(self._entries) > self.max_entries:
            if not self._access_order:
                break
            # 淘汰最久未使用的
            lru_hash = self._access_order.pop(0)
            if lru_hash in self._entries:
                del self._entries[lru_hash]
                self._stats.evictions += 1

    def _remove_expired_entries(self, now: Optional[float] = None) -> int:
        """清理所有过期条目，返回清理数量"""
        now = now or time.time()
        expired_hashes = [
            h for h, entry in self._entries.items() if entry.is_expired(now)
        ]
        for h in expired_hashes:
            del self._entries[h]
            if h in self._access_order:
                self._access_order.remove(h)
            self._stats.expirations += 1
        return len(expired_hashes)

    def _update_access_order(self, prompt_hash: str) -> None:
        """更新访问顺序（将最近访问的移到末尾）"""
        if prompt_hash in self._access_order:
            self._access_order.remove(prompt_hash)
        self._access_order.append(prompt_hash)

    def get(self, prompt: str, now: Optional[float] = None) -> Optional[Any]:
        """获取缓存中的响应

        如果命中且未过期，返回响应并更新统计。
        如果命中但已过期，删除条目并返回 None。
        如果未命中，返回 None。
        """
        self._stats.total_requests += 1
        prompt_hash = self._hash_prompt(prompt)
        entry = self._entries.get(prompt_hash)

        if entry is None:
            self._stats.misses += 1
            self._write_trace(cache_hit=False, status="cache_miss")
            if self._store is not None:
                self._store.record_miss()
            return None

        if entry.is_expired(now):
            # 过期：删除并计为 miss
            del self._entries[prompt_hash]
            if prompt_hash in self._access_order:
                self._access_order.remove(prompt_hash)
            self._stats.expirations += 1
            self._stats.misses += 1
            self._write_trace(cache_hit=False, status="cache_expired")
            if self._store is not None:
                self._store.record_miss()
            return None

        # 命中
        entry.touch(now)
        self._update_access_order(prompt_hash)
        self._stats.hits += 1
        self._write_trace(cache_hit=True, status="cache_hit")
        if self._store is not None:
            self._store.record_hit()
            self._sync_touch_to_sqlite(prompt_hash, now)
        return entry.response

    def set(
        self,
        prompt: str,
        response: Any,
        ttl_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        """设置缓存条目

        如果已存在相同 prompt 的条目，会覆盖更新。
        """
        prompt_hash = self._hash_prompt(prompt)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds

        # 如果已存在，先移除旧访问顺序
        if prompt_hash in self._access_order:
            self._access_order.remove(prompt_hash)

        self._entries[prompt_hash] = CacheEntry(
            prompt_hash=prompt_hash,
            prompt=prompt,
            response=response,
            created_at=now or time.time(),
            ttl_seconds=ttl,
            access_count=0,
            last_accessed_at=0.0,
        )
        self._access_order.append(prompt_hash)
        self._evict_if_needed()

        # 同步到 SQLite
        if self.sqlite_enabled and self._store is not None:
            self._sync_to_sqlite(prompt_hash, prompt, response, ttl, now)

    def delete(self, prompt: str) -> bool:
        """删除指定 prompt 的缓存条目，返回是否成功"""
        prompt_hash = self._hash_prompt(prompt)
        if prompt_hash in self._entries:
            del self._entries[prompt_hash]
            if prompt_hash in self._access_order:
                self._access_order.remove(prompt_hash)
            if self._store is not None:
                self._sync_delete_from_sqlite(prompt_hash)
            return True
        return False

    def clear(self) -> None:
        """清空所有缓存条目"""
        self._entries.clear()
        self._access_order.clear()
        self._sync_clear_sqlite()

    def has(self, prompt: str, now: Optional[float] = None) -> bool:
        """检查缓存中是否存在未过期的条目"""
        prompt_hash = self._hash_prompt(prompt)
        entry = self._entries.get(prompt_hash)
        if entry is None:
            return False
        return not entry.is_expired(now)

    # ── 统计 ──

    def get_stats(self) -> CacheStats:
        """获取缓存统计信息（副本）"""
        return CacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
            evictions=self._stats.evictions,
            expirations=self._stats.expirations,
            total_requests=self._stats.total_requests,
        )

    def reset_stats(self) -> None:
        """重置缓存统计"""
        self._stats = CacheStats()

    def hit_rate(self) -> float:
        """当前缓存命中率（0.0 ~ 1.0）"""
        return self._stats.hit_rate

    def hit_rate_percent(self) -> float:
        """当前缓存命中率百分比"""
        return self._stats.hit_rate_percent

    def get_config(self) -> Dict[str, Any]:
        """获取当前 prompt_cache 配置字典"""
        return dict(self._config)

    def get_trace_writer(self) -> Optional[Any]:
        """获取当前 trace_writer"""
        return self._trace_writer

    def set_trace_writer(self, trace_writer: Optional[Any]) -> None:
        """设置 trace_writer"""
        self._trace_writer = trace_writer

    def set_project_context(
        self,
        project_id: Optional[str] = None,
        feature_id: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> None:
        """更新 project 上下文（用于 trace 写入）"""
        if project_id is not None:
            self._project_id = project_id
        if feature_id is not None:
            self._feature_id = feature_id
        if agent is not None:
            self._agent = agent

    # ── 状态查询 ──

    def size(self) -> int:
        """当前缓存条目数量"""
        return len(self._entries)

    def is_full(self) -> bool:
        """缓存是否已满"""
        return len(self._entries) >= self.max_entries

    def list_entries(self) -> List[Dict[str, Any]]:
        """列出所有缓存条目的元信息（不含响应内容）"""
        result = []
        for entry in self._entries.values():
            result.append({
                "prompt_hash": entry.prompt_hash,
                "prompt_preview": entry.prompt[:100] + "..." if len(entry.prompt) > 100 else entry.prompt,
                "created_at": entry.created_at,
                "ttl_seconds": entry.ttl_seconds,
                "access_count": entry.access_count,
                "last_accessed_at": entry.last_accessed_at,
                "is_expired": entry.is_expired(),
            })
        return result

    # ── 批量 / 高级操作 ──

    def get_or_compute(
        self,
        prompt: str,
        compute_fn: callable,
        ttl_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Any:
        """获取缓存值，如果不存在则调用 compute_fn 计算并缓存

        这是 get + set 的便捷组合。
        """
        cached = self.get(prompt, now)
        if cached is not None:
            return cached
        response = compute_fn()
        self.set(prompt, response, ttl_seconds, now)
        return response

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        """主动清理过期条目，返回清理数量"""
        return self._remove_expired_entries(now)

    def to_dict(self) -> Dict[str, Any]:
        """序列化缓存状态（不含响应内容，只含元信息）"""
        return {
            "max_entries": self.max_entries,
            "default_ttl_seconds": self.default_ttl_seconds,
            "current_size": self.size(),
            "stats": self._stats.to_dict(),
            "entries": self.list_entries(),
        }

    # ── SQLite 相关 ──

    def get_sqlite_stats(self) -> Optional[Dict[str, int]]:
        """获取 SQLite 存储的统计信息"""
        if self._store is None:
            return None
        return self._store.get_stats()

    def sync_all_to_sqlite(self) -> None:
        """将所有内存中的条目同步到 SQLite"""
        if self._store is None:
            return
        for entry in self._entries.values():
            self._store.save_entry(
                entry.prompt_hash,
                entry.prompt,
                entry.response,
                entry.ttl_seconds,
            )
