"""src/config_loader.py — YAML 配置读取器

支持 prompt_cache 配置项：
  - enabled
  - target_hit_rate
  - alert_threshold
  - local_cache_backend
  - vector_cache_backend
  - file_index_backend
  - cache_layers
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULT_CONFIG: Dict[str, Any] = {
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


class ConfigLoader:
    """YAML 配置加载器

    职责：
      1. 从 YAML 文件读取配置
      2. 提供默认值回退
      3. 提供 prompt_cache 配置访问接口
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config: Dict[str, Any] = {}
        if config_path is not None:
            self.load(config_path)
        else:
            self._config = self._merge_defaults({})

    # ── 加载 ──

    def load(self, config_path: str) -> None:
        """从 YAML 文件加载配置"""
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        else:
            loaded = {}
        self._config = self._merge_defaults(loaded)

    def _merge_defaults(self, loaded: Dict[str, Any]) -> Dict[str, Any]:
        """将加载的配置与默认值合并（深拷贝，避免引用共享）"""
        merged: Dict[str, Any] = {}
        for key, default_value in DEFAULT_CONFIG.items():
            if isinstance(default_value, dict):
                merged[key] = {**copy.deepcopy(default_value), **loaded.get(key, {})}
            else:
                merged[key] = loaded.get(key, copy.deepcopy(default_value))
        # 保留其他非默认顶级键
        for key, value in loaded.items():
            if key not in merged:
                merged[key] = copy.deepcopy(value)
        return merged

    # ── 访问 ──

    def get(self, key: str, default: Any = None) -> Any:
        """获取任意配置项"""
        parts = key.split(".")
        value: Any = self._config
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def get_prompt_cache_config(self) -> Dict[str, Any]:
        """获取 prompt_cache 完整配置字典"""
        return dict(self._config.get("prompt_cache", DEFAULT_CONFIG["prompt_cache"]))

    # ── prompt_cache 便捷属性 ──

    @property
    def prompt_cache_enabled(self) -> bool:
        return bool(self.get("prompt_cache.enabled", DEFAULT_CONFIG["prompt_cache"]["enabled"]))

    @property
    def prompt_cache_target_hit_rate(self) -> float:
        return float(self.get("prompt_cache.target_hit_rate", DEFAULT_CONFIG["prompt_cache"]["target_hit_rate"]))

    @property
    def prompt_cache_alert_threshold(self) -> float:
        return float(self.get("prompt_cache.alert_threshold", DEFAULT_CONFIG["prompt_cache"]["alert_threshold"]))

    @property
    def prompt_cache_local_cache_backend(self) -> str:
        return str(self.get("prompt_cache.local_cache_backend", DEFAULT_CONFIG["prompt_cache"]["local_cache_backend"]))

    @property
    def prompt_cache_vector_cache_backend(self) -> str:
        return str(self.get("prompt_cache.vector_cache_backend", DEFAULT_CONFIG["prompt_cache"]["vector_cache_backend"]))

    @property
    def prompt_cache_file_index_backend(self) -> str:
        return str(self.get("prompt_cache.file_index_backend", DEFAULT_CONFIG["prompt_cache"]["file_index_backend"]))

    @property
    def prompt_cache_cache_layers(self) -> List[str]:
        layers = self.get("prompt_cache.cache_layers", DEFAULT_CONFIG["prompt_cache"]["cache_layers"])
        if isinstance(layers, str):
            return [layers]
        return list(layers)

    # ── 状态 ──

    def to_dict(self) -> Dict[str, Any]:
        """返回完整配置字典（深拷贝副本）"""
        return copy.deepcopy(self._config)

    def is_layer_enabled(self, layer: str) -> bool:
        """检查指定缓存层是否启用"""
        return layer in self.prompt_cache_cache_layers and self.prompt_cache_enabled
