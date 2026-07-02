"""src/config.py — Unified configuration management (pydantic-settings).

Loads configuration from environment variables and .env files.
All pipeline settings are centralized here to avoid scattered defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_available_modes() -> dict:
    """Build AVAILABLE_MODES lazily so config.py can be imported before registry.py."""
    try:
        from registry import REGISTRY
    except (ModuleNotFoundError, ImportError):
        from src.registry import REGISTRY

    return {
        "greenfield": {
            "label": "新建项目",
            "phases": [phase for phase in REGISTRY.list_phases()
                      if phase in ["init", "prd", "research", "design", "decompose",
                                   "journey", "develop", "integrate", "test", "evaluate", "accept", "deploy"]],
            "trigger": "default",
            "description": "从零开始，先设计再开发",
        },
        "brownfield": {
            "label": "存量优化",
            "phases": [phase for phase in REGISTRY.list_phases()
                      if phase in ["discover", "benchmark", "analyze", "plan",
                                   "execute", "verify", "deliver"]],
            "trigger": "auto",  # features.json存在且passed>0时自动检测
            "description": "先摸底再对标再优化",
        },
    }
class PipelineConfig(BaseSettings):
    """Pipeline configuration loaded from environment or .env file.

    Priority (highest to lowest):
      1. Environment variables (e.g., PIPELINE__DB_NAME)
      2. .env file in project root
      3. Default values defined here
    """

    model_config = SettingsConfigDict(
        env_prefix="PIPELINE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_name: str = Field(default="pipeline_state.db", description="SQLite database filename")

    # Paths
    base_dir: Path = Field(default=Path("."), description="Projects base directory")
    src_dir_name: str = Field(default="src", description="Source directory name")
    tests_dir_name: str = Field(default="tests", description="Tests directory name")
    specs_dir_name: str = Field(default="specs", description="Specs directory name")
    logs_dir_name: str = Field(default=".logs", description="Logs directory name")

    # Phase settings — 双模式支持
    # 通过 mode 选择 phase 链，默认 greenfield（新建项目）
    # brownfield 用于存量项目优化提升
    # 从REGISTRY获取所有可用的phases
    AVAILABLE_MODES: dict = Field(
        default_factory=_default_available_modes,
        description="可用模式及对应Phase链。插件化：新增模式只需在此字典加一条。",
    )
    pipeline_mode: str = Field(
        default="greenfield",
        description="当前流水线模式（greenfield/brownfield）",
    )

    greenfield_phase_order: List[str] = Field(default_factory=lambda: [
        "init", "prd", "research", "design", "decompose",
        "journey", "develop", "integrate", "test", "evaluate", "accept", "deploy"
    ])
    brownfield_phase_order: List[str] = Field(default_factory=lambda: [
        "discover", "benchmark", "analyze", "plan", "execute", "verify", "deliver"
    ])

    agent_cli_paths: Dict[str, str] = Field(default_factory=dict)
    """Map agent name -> absolute CLI path. Falls back to registry cli_path then PATH."""

    @property
    def phase_order(self) -> List[str]:
        """返回当前模式的Phase链。PhaseFlow只读此属性，模式透明。

        权威顺序来自配置字段 ``greenfield_phase_order`` / ``brownfield_phase_order``；
        ``AVAILABLE_MODES`` 仅用于校验模式名及允许出现的 phase 集合。
        """
        mode = self.pipeline_mode
        if mode not in self.AVAILABLE_MODES:
            # fallback: 兼容旧的 phase_order 配置
            return ["init", "design", "decompose", "research", "prd", "journey",
                    "develop", "integrate", "test", "evaluate", "accept", "deploy"]
        if mode == "greenfield":
            return list(self.greenfield_phase_order)
        if mode == "brownfield":
            return list(self.brownfield_phase_order)
        return []

    @staticmethod
    def detect_mode(project_dir: str | Path) -> str:
        """自动检测项目适用的模式。
        
        Brownfield触发（满足任一）：
        1. features.json存在且有passed的feature
        2. src/目录存在且有.py文件（存量代码项目）
        3. docs/audit-*.md存在（已执行过审计）
        否则返回greenfield。
        """
        import json
        proj = Path(project_dir)
        # 信号1: features.json with passed features
        ff = proj / "features.json"
        if ff.exists():
            try:
                data = json.loads(ff.read_text(encoding="utf-8"))
                features = data.get("features", [])
                if any(f.get("status") == "passed" for f in features):
                    return "brownfield"
            except (json.JSONDecodeError, KeyError):
                pass
        # 信号2: 已有源代码
        src_dir = proj / "src"
        if src_dir.is_dir() and list(src_dir.rglob("*.py")):
            return "brownfield"
        # 信号3: 已有审计报告
        docs_dir = proj / "docs"
        if docs_dir.is_dir() and list(docs_dir.glob("audit-*.md")):
            return "brownfield"
        return "greenfield"

    # Approval settings
    default_approval_timeout_blocking: int = Field(default=1800, description="Blocking approval timeout (seconds)")
    default_approval_timeout_async: int = Field(default=7200, description="Async approval timeout (seconds)")
    default_approval_timeout_auto: int = Field(default=300, description="Auto approval timeout (seconds)")

    # Adapter settings
    adapter_timeout: int = Field(default=120, description="Adapter run timeout (seconds)")
    adapter_max_retries: int = Field(default=3, description="Adapter max retries on failure")

    # Fallback channel settings
    fallback_inbox_dir: Path = Field(default=Path(".fallback_inbox"), description="FileBased fallback inbox directory")
    mcp_endpoint: str = Field(default="", description="MCP fallback endpoint URL")

    # Observability
    trace_limit: int = Field(default=100, description="Default trace query limit")
    checkpoint_limit: int = Field(default=50, description="Default checkpoint query limit")

    # GitHub sync
    github_repo: str = Field(default="", description="GitHub repository for issue sync")
    github_token: str = Field(default="", description="GitHub personal access token")

    def db_path(self, project_dir: Path) -> Path:
        """Return the database path for a given project directory."""
        return project_dir / self.db_name

    def ensure_dirs(self, project_dir: Path) -> None:
        """Create standard project subdirectories if they don't exist."""
        for name in (self.src_dir_name, self.tests_dir_name, self.specs_dir_name, self.logs_dir_name):
            (project_dir / name).mkdir(parents=True, exist_ok=True)


# Global singleton (lazy-loaded)
_config: Optional[PipelineConfig] = None


def get_config() -> PipelineConfig:
    """Return the global PipelineConfig singleton."""
    global _config
    if _config is None:
        _config = PipelineConfig()
    return _config


def reload_config() -> PipelineConfig:
    """Force reload configuration from environment / .env."""
    global _config
    _config = PipelineConfig()
    return _config
