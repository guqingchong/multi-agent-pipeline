"""src/state_store.py — Layer 2 SQLite 状态持久化与检查点层

实现 PRD 3.2 节定义的完整表结构：
  projects / features / checkpoints / traces / audit_logs / model_health

核心能力：
  - 每个有意义 action 后自动 checkpoint
  - 支持 resume 从最新 checkpoint 恢复
  - 支持 rollback 到指定 checkpoint
  - schema 版本控制（v1）
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# 常量 / 配置
# ───────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# PRD 定义的核心表 DDL
CORE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    schema_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT CHECK(status IN ('pending','in_progress','review','test','passed','failed','needs_rework')),
    owner_agent TEXT,
    token_cost INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    phase TEXT NOT NULL,
    feature_id TEXT,
    agent TEXT,
    action TEXT,
    result TEXT,
    state_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    feature_id TEXT,
    agent TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    status TEXT,
    cache_hit BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    agent TEXT,
    command TEXT,
    allowed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    response_time_ms INTEGER,
    success BOOLEAN,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 向后兼容：F005 的 project_state 表（单 key-value 存储）
LEGACY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS project_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class ProjectRecord:
    """projects 表记录"""
    id: str
    name: str
    current_phase: str
    schema_version: int = SCHEMA_VERSION
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class FeatureRecord:
    """features 表记录"""
    id: str
    project_id: str
    title: str
    description: str = ""
    status: str = "pending"
    owner_agent: str = ""
    token_cost: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class CheckpointRecord:
    """checkpoints 表记录"""
    id: Optional[int] = None
    project_id: str = ""
    phase: str = ""
    feature_id: Optional[str] = None
    agent: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None
    state_json: str = "{}"
    created_at: Optional[str] = None


@dataclass
class TraceRecord:
    """traces 表记录"""
    id: Optional[int] = None
    project_id: Optional[str] = None
    feature_id: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    latency_ms: Optional[int] = None
    status: Optional[str] = None
    cache_hit: bool = False
    created_at: Optional[str] = None


@dataclass
class AuditLogRecord:
    """audit_logs 表记录"""
    id: Optional[int] = None
    project_id: Optional[str] = None
    agent: Optional[str] = None
    command: Optional[str] = None
    allowed: Optional[bool] = None
    created_at: Optional[str] = None


# ───────────────────────────────────────────────────────────────
# StateStore — 核心持久化层
# ───────────────────────────────────────────────────────────────

class StateStore:
    """SQLite 状态持久化存储

    职责：
      1. 创建 / 维护所有核心表（projects, features, checkpoints, traces, audit_logs, model_health）
      2. 提供 CRUD 接口
      3. checkpoint 写入 / 恢复 / 回滚
      4. 向后兼容 F005 的 project_state 表
    """

    def __init__(self, db_path: Path) -> None:
        # 兼容 F005: 如果传入的是目录，自动拼接 DB 文件名
        if db_path.is_dir():
            db_path = db_path / "pipeline_state.db"
        self.db_path = db_path
        # 确保父目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    # ── 内部工具 ──

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        with self._conn() as conn:
            conn.executescript(CORE_TABLES_SQL)
            conn.executescript(LEGACY_TABLE_SQL)
            conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── projects ──

    def create_project(
        self,
        project_id: str,
        name: str,
        current_phase: str = "init",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO projects
                (id, name, current_phase, schema_version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, name, current_phase, SCHEMA_VERSION, self._now()),
            )
            conn.commit()

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        return ProjectRecord(
            id=row["id"],
            name=row["name"],
            current_phase=row["current_phase"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_project_phase(self, project_id: str, phase: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE projects
                SET current_phase = ?, updated_at = ?
                WHERE id = ?
                """,
                (phase, self._now(), project_id),
            )
            conn.commit()

    # ── features ──

    def create_feature(self, feature: FeatureRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO features
                (id, project_id, title, description, status, owner_agent, token_cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature.id,
                    feature.project_id,
                    feature.title,
                    feature.description,
                    feature.status,
                    feature.owner_agent,
                    feature.token_cost,
                    self._now(),
                ),
            )
            conn.commit()

    def get_feature(self, feature_id: str) -> Optional[FeatureRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id,)
            ).fetchone()
        if row is None:
            return None
        return FeatureRecord(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"] or "",
            status=row["status"],
            owner_agent=row["owner_agent"] or "",
            token_cost=row["token_cost"] or 0,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_features(self, project_id: str) -> List[FeatureRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM features WHERE project_id = ?", (project_id,)
            ).fetchall()
        return [
            FeatureRecord(
                id=r["id"],
                project_id=r["project_id"],
                title=r["title"],
                description=r["description"] or "",
                status=r["status"],
                owner_agent=r["owner_agent"] or "",
                token_cost=r["token_cost"] or 0,
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def update_feature_status(self, feature_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE features
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, self._now(), feature_id),
            )
            conn.commit()

    # ── checkpoints ──

    def write_checkpoint(
        self,
        project_id: str,
        phase: str,
        state_dict: Dict[str, Any],
        feature_id: Optional[str] = None,
        agent: Optional[str] = None,
        action: Optional[str] = None,
        result: Optional[str] = None,
    ) -> int:
        """写入 checkpoint，返回 checkpoint id"""
        state_json = json.dumps(state_dict, ensure_ascii=False)
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO checkpoints
                (project_id, phase, feature_id, agent, action, result, state_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, phase, feature_id, agent, action, result, state_json, self._now()),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_checkpoint(self, checkpoint_id: int) -> Optional[CheckpointRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
            ).fetchone()
        if row is None:
            return None
        return CheckpointRecord(
            id=row["id"],
            project_id=row["project_id"],
            phase=row["phase"],
            feature_id=row["feature_id"],
            agent=row["agent"],
            action=row["action"],
            result=row["result"],
            state_json=row["state_json"],
            created_at=row["created_at"],
        )

    def list_checkpoints(self, project_id: str, limit: int = 50) -> List[CheckpointRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            CheckpointRecord(
                id=r["id"],
                project_id=r["project_id"],
                phase=r["phase"],
                feature_id=r["feature_id"],
                agent=r["agent"],
                action=r["action"],
                result=r["result"],
                state_json=r["state_json"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_latest_checkpoint(self, project_id: str) -> Optional[CheckpointRecord]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return CheckpointRecord(
            id=row["id"],
            project_id=row["project_id"],
            phase=row["phase"],
            feature_id=row["feature_id"],
            agent=row["agent"],
            action=row["action"],
            result=row["result"],
            state_json=row["state_json"],
            created_at=row["created_at"],
        )

    def restore_checkpoint(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """恢复指定 checkpoint 的状态字典"""
        cp = self.get_checkpoint(checkpoint_id)
        if cp is None:
            return None
        return json.loads(cp.state_json)

    def rollback(self, project_id: str, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """回滚到指定 checkpoint，并更新项目 phase"""
        state = self.restore_checkpoint(checkpoint_id)
        if state is None:
            return None
        phase = state.get("phase", "init")
        self.update_project_phase(project_id, phase)
        return state

    # ── traces ──

    def write_trace(self, trace: TraceRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO traces
                (project_id, feature_id, agent, model, input_tokens, output_tokens,
                 cost_usd, latency_ms, status, cache_hit, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.project_id,
                    trace.feature_id,
                    trace.agent,
                    trace.model,
                    trace.input_tokens,
                    trace.output_tokens,
                    trace.cost_usd,
                    trace.latency_ms,
                    trace.status,
                    trace.cache_hit,
                    self._now(),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0

    def list_traces(self, project_id: str, limit: int = 100) -> List[TraceRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM traces
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            TraceRecord(
                id=r["id"],
                project_id=r["project_id"],
                feature_id=r["feature_id"],
                agent=r["agent"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=r["cost_usd"],
                latency_ms=r["latency_ms"],
                status=r["status"],
                cache_hit=bool(r["cache_hit"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── audit_logs ──

    def write_audit_log(self, log: AuditLogRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_logs
                (project_id, agent, command, allowed, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (log.project_id, log.agent, log.command, log.allowed, self._now()),
            )
            conn.commit()
            return cur.lastrowid or 0

    def list_audit_logs(self, project_id: str, limit: int = 100) -> List[AuditLogRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM audit_logs
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            AuditLogRecord(
                id=r["id"],
                project_id=r["project_id"],
                agent=r["agent"],
                command=r["command"],
                allowed=r["allowed"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── model_health ──

    def write_model_health(
        self,
        model: str,
        response_time_ms: int,
        success: bool,
        error_message: Optional[str] = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO model_health
                (model, response_time_ms, success, error_message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (model, response_time_ms, success, error_message, self._now()),
            )
            conn.commit()
            return cur.lastrowid or 0

    # ── 向后兼容 F005 ──

    def legacy_save(self, key: str, value: str) -> None:
        """兼容 F005 的 key-value 存储"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def legacy_load(self, key: str) -> Optional[str]:
        """兼容 F005 的 key-value 读取"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM project_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return row["value"]

    # 兼容旧 F005 测试直接调用的 save / load 接口
    def save(self, state: ProjectState) -> None:
        """向后兼容：保存 ProjectState 到 legacy 表"""
        from pipeline import ProjectState as _ProjectState
        self.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))

    def load(self, name: str) -> Optional[ProjectState]:
        """向后兼容：从 legacy 表加载 ProjectState"""
        from pipeline import ProjectState as _ProjectState
        raw = self.legacy_load("state")
        if raw is None:
            return None
        return _ProjectState.from_dict(json.loads(raw))

    # ── schema 版本控制 ──

    def get_schema_version(self) -> int:
        """返回当前数据库 schema 版本"""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT schema_version FROM projects LIMIT 1"
                ).fetchone()
            if row is None:
                return 0
            return row["schema_version"]
        except sqlite3.OperationalError:
            return 0
