"""tests/test_prompt_cache_config.py — F016 Prompt Cache 配置读取单元测试

验收标准：
1. [test] YAML 配置读取正确
2. [test] 配置默认值回退正确
3. [test] 配置项覆盖正确
4. [test] 缓存层启用/禁用正确
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.prompt_cache import ConfigLoader, DEFAULT_CONFIG


# ───────────────────────────────────────────────────────────────
# 1. 模块可导入验证（基础）
# ───────────────────────────────────────────────────────────────

def test_module_importable() -> None:
    """[command] config_loader 模块可导入"""
    from src.prompt_cache import ConfigLoader, DEFAULT_CONFIG
    assert ConfigLoader is not None
    assert DEFAULT_CONFIG is not None


def test_default_config_structure() -> None:
    """默认配置结构完整"""
    assert "prompt_cache" in DEFAULT_CONFIG
    pc = DEFAULT_CONFIG["prompt_cache"]
    assert "enabled" in pc
    assert "target_hit_rate" in pc
    assert "alert_threshold" in pc
    assert "local_cache_backend" in pc
    assert "vector_cache_backend" in pc
    assert "file_index_backend" in pc
    assert "cache_layers" in pc


# ───────────────────────────────────────────────────────────────
# 2. 默认配置测试
# ───────────────────────────────────────────────────────────────

class TestDefaultConfig:
    def test_default_enabled(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_enabled is True

    def test_default_target_hit_rate(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_target_hit_rate == 0.7

    def test_default_alert_threshold(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_alert_threshold == 0.3

    def test_default_local_cache_backend(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_local_cache_backend == "memory"

    def test_default_vector_cache_backend(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_vector_cache_backend == "none"

    def test_default_file_index_backend(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_file_index_backend == "none"

    def test_default_cache_layers(self) -> None:
        loader = ConfigLoader()
        assert loader.prompt_cache_cache_layers == ["memory"]

    def test_default_to_dict(self) -> None:
        loader = ConfigLoader()
        d = loader.to_dict()
        assert d["prompt_cache"]["enabled"] is True
        assert d["prompt_cache"]["target_hit_rate"] == 0.7


# ───────────────────────────────────────────────────────────────
# 3. YAML 配置读取测试
# ───────────────────────────────────────────────────────────────

class TestYamlConfigLoading:
    def test_load_from_yaml_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: false
  target_hit_rate: 0.85
  alert_threshold: 0.2
  local_cache_backend: sqlite
  vector_cache_backend: faiss
  file_index_backend: sqlite
  cache_layers:
    - memory
    - vector
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.prompt_cache_enabled is False
        assert loader.prompt_cache_target_hit_rate == 0.85
        assert loader.prompt_cache_alert_threshold == 0.2
        assert loader.prompt_cache_local_cache_backend == "sqlite"
        assert loader.prompt_cache_vector_cache_backend == "faiss"
        assert loader.prompt_cache_file_index_backend == "sqlite"
        assert loader.prompt_cache_cache_layers == ["memory", "vector"]
        Path(f.name).unlink(missing_ok=True)

    def test_load_partial_yaml(self) -> None:
        """部分配置覆盖，其余使用默认值"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  target_hit_rate: 0.9
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.prompt_cache_enabled is True
        assert loader.prompt_cache_target_hit_rate == 0.9
        # 其余应为默认值
        assert loader.prompt_cache_alert_threshold == 0.3
        assert loader.prompt_cache_local_cache_backend == "memory"
        Path(f.name).unlink(missing_ok=True)

    def test_load_empty_yaml(self) -> None:
        """空 YAML 文件应使用全部默认值"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.prompt_cache_enabled is True
        assert loader.prompt_cache_target_hit_rate == 0.7
        Path(f.name).unlink(missing_ok=True)

    def test_load_nonexistent_file(self) -> None:
        """不存在的文件应使用全部默认值"""
        loader = ConfigLoader("/nonexistent/path/config.yaml")
        assert loader.prompt_cache_enabled is True
        assert loader.prompt_cache_target_hit_rate == 0.7
        assert loader.prompt_cache_alert_threshold == 0.3

    def test_load_yaml_with_extra_keys(self) -> None:
        """YAML 包含非 prompt_cache 键时应保留"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
other_section:
  key: value
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.get("other_section.key") == "value"
        Path(f.name).unlink(missing_ok=True)

    def test_load_yaml_single_layer(self) -> None:
        """cache_layers 为单个字符串时应转为列表"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  cache_layers: memory
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.prompt_cache_cache_layers == ["memory"]
        Path(f.name).unlink(missing_ok=True)

    def test_load_yaml_multiple_layers(self) -> None:
        """cache_layers 为多个字符串列表"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  cache_layers:
    - memory
    - vector
    - file_index
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.prompt_cache_cache_layers == ["memory", "vector", "file_index"]
        Path(f.name).unlink(missing_ok=True)


# ───────────────────────────────────────────────────────────────
# 4. 配置访问接口测试
# ───────────────────────────────────────────────────────────────

class TestConfigAccess:
    def test_get_existing_key(self) -> None:
        loader = ConfigLoader()
        assert loader.get("prompt_cache.enabled") is True

    def test_get_missing_key(self) -> None:
        loader = ConfigLoader()
        assert loader.get("nonexistent.key") is None

    def test_get_with_default(self) -> None:
        loader = ConfigLoader()
        assert loader.get("nonexistent.key", "fallback") == "fallback"

    def test_get_nested_key(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  nested:
    value: 42
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.get("prompt_cache.nested.value") == 42
        Path(f.name).unlink(missing_ok=True)

    def test_get_prompt_cache_config(self) -> None:
        loader = ConfigLoader()
        pc = loader.get_prompt_cache_config()
        assert isinstance(pc, dict)
        assert "enabled" in pc
        assert "target_hit_rate" in pc

    def test_is_layer_enabled(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  cache_layers:
    - memory
    - vector
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.is_layer_enabled("memory") is True
        assert loader.is_layer_enabled("vector") is True
        assert loader.is_layer_enabled("file_index") is False
        Path(f.name).unlink(missing_ok=True)

    def test_is_layer_enabled_when_disabled(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: false
  cache_layers:
    - memory
""")
            f.flush()
            loader = ConfigLoader(f.name)
        assert loader.is_layer_enabled("memory") is False
        Path(f.name).unlink(missing_ok=True)


# ───────────────────────────────────────────────────────────────
# 5. PromptCache 配置集成测试
# ───────────────────────────────────────────────────────────────

class TestPromptCacheConfigIntegration:
    def test_prompt_cache_loads_config(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  target_hit_rate: 0.8
  cache_layers:
    - memory
""")
            f.flush()
            cache = PromptCache(config_path=f.name)
        config = cache.get_config()
        assert config["enabled"] is True
        assert config["target_hit_rate"] == 0.8
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_disabled_by_config(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: false
""")
            f.flush()
            cache = PromptCache(config_path=f.name)
        assert cache.is_layer_enabled("memory") is False
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_layer_check(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  cache_layers:
    - memory
    - vector
""")
            f.flush()
            cache = PromptCache(config_path=f.name)
        assert cache.is_layer_enabled("memory") is True
        assert cache.is_layer_enabled("vector") is True
        assert cache.is_layer_enabled("file_index") is False
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_no_config_path(self) -> None:
        from prompt_cache import PromptCache
        cache = PromptCache()
        assert cache.is_layer_enabled("memory") is True
        assert cache.get_config() == {}

    def test_prompt_cache_config_does_not_break_sqlite(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  local_cache_backend: sqlite
""")
            f.flush()
            cache = PromptCache(config_path=f.name, sqlite_enabled=True)
        assert cache.is_layer_enabled("memory") is True
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_sqlite_disabled_when_cache_disabled(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: false
""")
            f.flush()
            cache = PromptCache(config_path=f.name, sqlite_enabled=True)
        assert cache._store is None
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_set_project_context(self) -> None:
        from prompt_cache import PromptCache
        cache = PromptCache()
        cache.set_project_context(project_id="proj1", feature_id="feat1", agent="agent1")
        assert cache._project_id == "proj1"
        assert cache._feature_id == "feat1"
        assert cache._agent == "agent1"

    def test_prompt_cache_set_project_context_partial(self) -> None:
        from prompt_cache import PromptCache
        cache = PromptCache()
        cache.set_project_context(project_id="proj1")
        assert cache._project_id == "proj1"
        assert cache._feature_id is None

    def test_prompt_cache_get_trace_writer(self) -> None:
        from prompt_cache import PromptCache
        cache = PromptCache()
        assert cache.get_trace_writer() is None
        dummy_writer = object()
        cache.set_trace_writer(dummy_writer)
        assert cache.get_trace_writer() is dummy_writer

    def test_prompt_cache_config_with_all_backends(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  local_cache_backend: sqlite
  vector_cache_backend: faiss
  file_index_backend: sqlite
  cache_layers:
    - memory
    - vector
    - file_index
""")
            f.flush()
            cache = PromptCache(config_path=f.name)
        config = cache.get_config()
        assert config["local_cache_backend"] == "sqlite"
        assert config["vector_cache_backend"] == "faiss"
        assert config["file_index_backend"] == "sqlite"
        assert cache.is_layer_enabled("memory") is True
        assert cache.is_layer_enabled("vector") is True
        assert cache.is_layer_enabled("file_index") is True
        Path(f.name).unlink(missing_ok=True)

    def test_prompt_cache_config_alert_threshold(self) -> None:
        from prompt_cache import PromptCache
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("""
prompt_cache:
  enabled: true
  alert_threshold: 0.15
""")
            f.flush()
            cache = PromptCache(config_path=f.name)
        config = cache.get_config()
        assert config["alert_threshold"] == 0.15
        Path(f.name).unlink(missing_ok=True)

    def test_config_loader_to_dict_isolation(self) -> None:
        """to_dict 返回副本，修改不影响内部状态"""
        loader = ConfigLoader()
        d = loader.to_dict()
        d["prompt_cache"]["enabled"] = False
        assert loader.prompt_cache_enabled is True
