"""src/state_store.py — Layer 2 SQLite state persistence and checkpoint layer

Implements the full table structure defined in PRD section 3.2:
  projects / features / checkpoints / traces / audit_logs / model_health
  dispatch_history / approval_records

Core capabilities:
  - Auto checkpoint after every meaningful action
  - Resume from latest checkpoint
  - Rollback to a specific checkpoint
  - Schema version control (v2)
  - Dispatch history for strategy advice (sync/async)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# Constants / Config
# ───────────────────────────────────────────────────────────────

SCHEMA_VERSION = 2

# PRD defined core table DDL (v2)
# Added: features.wave, features.dependencies_json, features.acceptance_criteria_json
#        features.github_issue_number, features.sync_status
CORE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    schema_version INTEGER DEFAULT 2,
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
    wave INTEGER DEFAULT 0,
    dependencies_json TEXT DEFAULT '[]',
    acceptance_criteria_json TEXT DEFAULT '[]',
    github_issue_number INTEGER,
    sync_status TEXT CHECK(sync_status IN ('unsynced','syncing','synced','failed')) DEFAULT 'unsynced',
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
    phase TEXT,
    event TEXT,
    details_json TEXT DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS approval_records (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    operation TEXT NOT NULL,
    level TEXT NOT NULL,
    risk TEXT DEFAULT 'low',
    cost REAL DEFAULT 0.0,
    alternatives_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    summary TEXT DEFAULT '',
    created_at REAL NOT NULL,
    resolved_at REAL,
    checkpoint_id INTEGER,
    db_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dispatch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    agent TEXT,
    task_type TEXT,
    success BOOLEAN DEFAULT FALSE,
    latency_ms INTEGER DEFAULT 0,
    exec_mode TEXT DEFAULT 'async',
    output TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Backward compatibility: F005 project_state table (single key-value store)
LEGACY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS project_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# ───────────────────────────────────────────────────────────────
# Data Models
# ───────────────────────────────────────────────────────────────

@dataclass
class ProjectRecord:
    """projects table record"""
    id: str
    name: str
    current_phase: str
    schema_version: int = SCHEMA_VERSION
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class FeatureRecord:
    """features table record (v2)"""
    id: str
    project_id: str
    title: str
    description: str = ""
    status: str = "pending"
    owner_agent: str = ""
    token_cost: int = 0
    wave: int = 0
    dependencies: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    github_issue_number: Optional[int] = None
    sync_status: str = "unsynced"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class CheckpointRecord:
    """checkpoints table record"""
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
    """traces table record"""
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
    """audit_logs table record"""
    id: Optional[int] = None
    project_id: Optional[str] = None
    phase: Optional[str] = None
    event: Optional[str] = None
    details_json: str = "{}"
    agent: Optional[str] = None
    command: Optional[str] = None
    allowed: Optional[bool] = None
    created_at: Optional[str] = None

    def details(self) -> Dict[str, Any]:
        """Return parsed details_json as a dict."""
        try:
            return json.loads(self.details_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}


@dataclass
class DispatchHistoryRecord:
    """dispatch_history table record"""
    id: Optional[int] = None
    task_id: Optional[str] = None
    agent: Optional[str] = None
    task_type: Optional[str] = None
    success: bool = False
    latency_ms: int = 0
    exec_mode: str = "async"
    output: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None


# ───────────────────────────────────────────────────────────────
# StateStore — Core persistence layer
# ───────────────────────────────────────────────────────────────

class StateStore:
    """SQLite state persistence store

    Responsibilities:
      1. Create / maintain all core tables (projects, features, checkpoints, traces, audit_logs, model_health)
      2. Provide CRUD interfaces
      3. Checkpoint write / restore / rollback
      4. Backward compatible with F005 project_state table
      5. Schema migration from v1 to v2
    """

    def __init__(self, db_path: Path) -> None:
        # Compatible with F005: if a directory is passed, auto-append DB filename
        if db_path.is_dir():
            db_path = db_path / "pipeline_state.db"
        self.db_path = db_path
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()
        self._migrate_v1_to_v2()

    # ── Internal helpers ──

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

    def _migrate_v1_to_v2(self) -> None:
        """Migrate schema from v1 to v2 if needed."""
        with self._conn() as conn:
            # Check if features table has the v2 columns
            cursor = conn.execute("PRAGMA table_info(features)")
            columns = {row["name"] for row in cursor.fetchall()}

            if "wave" not in columns:
                conn.execute("ALTER TABLE features ADD COLUMN wave INTEGER DEFAULT 0")
            if "dependencies_json" not in columns:
                conn.execute("ALTER TABLE features ADD COLUMN dependencies_json TEXT DEFAULT '[]'")
            if "acceptance_criteria_json" not in columns:
                conn.execute("ALTER TABLE features ADD COLUMN acceptance_criteria_json TEXT DEFAULT '[]'")
            if "github_issue_number" not in columns:
                conn.execute("ALTER TABLE features ADD COLUMN github_issue_number INTEGER")
            if "sync_status" not in columns:
                conn.execute("ALTER TABLE features ADD COLUMN sync_status TEXT DEFAULT 'unsynced'")
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS features_sync_status_check_insert
                    BEFORE INSERT ON features
                    BEGIN
                        SELECT CASE
                            WHEN NEW.sync_status NOT IN ('unsynced','syncing','synced','failed')
                            THEN RAISE(ABORT, 'Invalid sync_status')
                        END;
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS features_sync_status_check_update
                    BEFORE UPDATE ON features
                    BEGIN
                        SELECT CASE
                            WHEN NEW.sync_status NOT IN ('unsynced','syncing','synced','failed')
                            THEN RAISE(ABORT, 'Invalid sync_status')
                        END;
                    END;
                """)

            # Migrate audit_logs table: add structured audit columns
            cursor = conn.execute("PRAGMA table_info(audit_logs)")
            audit_columns = {row["name"] for row in cursor.fetchall()}
            if "phase" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN phase TEXT")
            if "event" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN event TEXT")
            if "details_json" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN details_json TEXT DEFAULT '{}'")

            conn.commit()

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
                (id, project_id, title, description, status, owner_agent, token_cost,
                 wave, dependencies_json, acceptance_criteria_json, github_issue_number, sync_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature.id,
                    feature.project_id,
                    feature.title,
                    feature.description,
                    feature.status,
                    feature.owner_agent,
                    feature.token_cost,
                    feature.wave,
                    json.dumps(feature.dependencies, ensure_ascii=False),
                    json.dumps(feature.acceptance_criteria, ensure_ascii=False),
                    feature.github_issue_number,
                    feature.sync_status,
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
        return self._row_to_feature(row)

    def list_features(self, project_id: str) -> List[FeatureRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM features WHERE project_id = ?", (project_id,)
            ).fetchall()
        return [self._row_to_feature(r) for r in rows]

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

    def update_feature_sync(self, feature_id: str, sync_status: str, github_issue_number: Optional[int] = None) -> None:
        """Update feature sync status and optional GitHub issue number."""
        with self._conn() as conn:
            if github_issue_number is not None:
                conn.execute(
                    """
                    UPDATE features
                    SET sync_status = ?, github_issue_number = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (sync_status, github_issue_number, self._now(), feature_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE features
                    SET sync_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (sync_status, self._now(), feature_id),
                )
            conn.commit()

    def _row_to_feature(self, row: sqlite3.Row) -> FeatureRecord:
        """Convert a DB row to FeatureRecord, handling v2 fields."""
        deps = row["dependencies_json"] if "dependencies_json" in row.keys() else None
        ac = row["acceptance_criteria_json"] if "acceptance_criteria_json" in row.keys() else None
        return FeatureRecord(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"] or "",
            status=row["status"],
            owner_agent=row["owner_agent"] if "owner_agent" in row.keys() else "",
            token_cost=row["token_cost"] if "token_cost" in row.keys() else 0,
            wave=row["wave"] if "wave" in row.keys() else 0,
            dependencies=json.loads(deps) if deps else [],
            acceptance_criteria=json.loads(ac) if ac else [],
            github_issue_number=row["github_issue_number"] if "github_issue_number" in row.keys() else None,
            sync_status=row["sync_status"] if "sync_status" in row.keys() else "unsynced",
            created_at=row["created_at"] if "created_at" in row.keys() else None,
            updated_at=row["updated_at"] if "updated_at" in row.keys() else None,
        )

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
        """Write a checkpoint and return the checkpoint id."""
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
        """Restore state dict from a specific checkpoint."""
        cp = self.get_checkpoint(checkpoint_id)
        if cp is None:
            return None
        return json.loads(cp.state_json)

    def rollback(self, project_id: str, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """Rollback to a specific checkpoint and update project phase."""
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
                (project_id, phase, event, details_json, agent, command, allowed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.project_id,
                    log.phase,
                    log.event,
                    log.details_json,
                    log.agent,
                    log.command,
                    log.allowed,
                    self._now(),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0

    def log_audit(
        self,
        project_id: str,
        phase: str,
        event: str,
        details: Dict[str, Any],
    ) -> int:
        """Write a structured audit event to audit_logs."""
        log = AuditLogRecord(
            project_id=project_id,
            phase=phase,
            event=event,
            details_json=json.dumps(details, ensure_ascii=False, default=str),
        )
        return self.write_audit_log(log)

    def list_audit_logs(
        self,
        project_id: str,
        event: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditLogRecord]:
        with self._conn() as conn:
            query = "SELECT * FROM audit_logs WHERE project_id = ?"
            params: List[Any] = [project_id]
            if event is not None:
                query += " AND event = ?"
                params.append(event)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [
            AuditLogRecord(
                id=r["id"],
                project_id=r["project_id"],
                phase=r["phase"],
                event=r["event"],
                details_json=r["details_json"],
                agent=r["agent"],
                command=r["command"],
                allowed=r["allowed"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── dispatch_history ──

    def write_dispatch_history(
        self,
        task_id: Optional[str] = None,
        agent: Optional[str] = None,
        task_type: Optional[str] = None,
        success: bool = False,
        latency_ms: int = 0,
        exec_mode: str = "async",
        output: Optional[str] = None,
        error: Optional[str] = None,
    ) -> int:
        """写入一次 dispatch 历史记录，同步路径应标注 exec_mode='sync'。"""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO dispatch_history
                (task_id, agent, task_type, success, latency_ms, exec_mode, output, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    agent,
                    task_type,
                    success,
                    latency_ms,
                    exec_mode,
                    output,
                    error,
                    self._now(),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0

    def list_dispatch_history(
        self, agent: Optional[str] = None, task_type: Optional[str] = None, limit: int = 100
    ) -> List[DispatchHistoryRecord]:
        """查询 dispatch 历史，可按 agent / task_type 过滤。"""
        query = "SELECT * FROM dispatch_history"
        params: List[Any] = []
        conditions: List[str] = []
        if agent is not None:
            conditions.append("agent = ?")
            params.append(agent)
        if task_type is not None:
            conditions.append("task_type = ?")
            params.append(task_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            DispatchHistoryRecord(
                id=r["id"],
                task_id=r["task_id"],
                agent=r["agent"],
                task_type=r["task_type"],
                success=bool(r["success"]),
                latency_ms=r["latency_ms"],
                exec_mode=r["exec_mode"],
                output=r["output"],
                error=r["error"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def count_dispatch_history(self) -> int:
        """返回 dispatch_history 总记录数。"""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM dispatch_history").fetchone()
        return row[0] if row else 0

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

    # ── approval_records ──

    def save_approval_record(
        self,
        record_id: str,
        project_id: str,
        operation: str,
        level: str,
        risk: str = "low",
        cost: float = 0.0,
        alternatives: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        summary: str = "",
        created_at: float = 0.0,
        resolved_at: Optional[float] = None,
        checkpoint_id: Optional[int] = None,
    ) -> None:
        """Save or update an approval record in the database."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approval_records
                (id, project_id, operation, level, risk, cost, alternatives_json,
                 metadata_json, status, summary, created_at, resolved_at, checkpoint_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    project_id,
                    operation,
                    level,
                    risk,
                    cost,
                    json.dumps(alternatives or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    status,
                    summary,
                    created_at,
                    resolved_at,
                    checkpoint_id,
                ),
            )
            conn.commit()

    def get_approval_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Load a single approval record from the database."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM approval_records WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "operation": row["operation"],
            "level": row["level"],
            "risk": row["risk"],
            "cost": row["cost"],
            "alternatives": json.loads(row["alternatives_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "status": row["status"],
            "summary": row["summary"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
            "checkpoint_id": row["checkpoint_id"],
        }

    def list_approval_records(self, project_id: str) -> List[Dict[str, Any]]:
        """List all approval records for a given project."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_records WHERE project_id = ? ORDER BY db_created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "operation": r["operation"],
                "level": r["level"],
                "risk": r["risk"],
                "cost": r["cost"],
                "alternatives": json.loads(r["alternatives_json"]),
                "metadata": json.loads(r["metadata_json"]),
                "status": r["status"],
                "summary": r["summary"],
                "created_at": r["created_at"],
                "resolved_at": r["resolved_at"],
                "checkpoint_id": r["checkpoint_id"],
            }
            for r in rows
        ]

    def update_approval_status(
        self,
        record_id: str,
        status: str,
        resolved_at: Optional[float] = None,
        checkpoint_id: Optional[int] = None,
    ) -> None:
        """Update the status of an approval record."""
        with self._conn() as conn:
            if resolved_at is not None and checkpoint_id is not None:
                conn.execute(
                    """UPDATE approval_records
                       SET status = ?, resolved_at = ?, checkpoint_id = ?
                       WHERE id = ?""",
                    (status, resolved_at, checkpoint_id, record_id),
                )
            elif resolved_at is not None:
                conn.execute(
                    """UPDATE approval_records
                       SET status = ?, resolved_at = ?
                       WHERE id = ?""",
                    (status, resolved_at, record_id),
                )
            else:
                conn.execute(
                    "UPDATE approval_records SET status = ? WHERE id = ?",
                    (status, record_id),
                )
            conn.commit()

    # ── Backward compatibility with F005 ──

    def legacy_save(self, key: str, value: str) -> None:
        """Compatible with F005 key-value store."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO project_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def legacy_load(self, key: str) -> Optional[str]:
        """Compatible with F005 key-value read."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM project_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return row["value"]

    # Compatible with old F005 tests that directly call save / load interfaces
    def save(self, state: "ProjectState") -> None:  # type: ignore # noqa: F821
        """Backward compatible: save ProjectState to legacy table."""
        from models import ProjectState as _ProjectState
        self.legacy_save("state", json.dumps(state.to_dict(), ensure_ascii=False))

    def load(self, name: str) -> Optional["ProjectState"]:  # type: ignore # noqa: F821
        """Backward compatible: load ProjectState from legacy table."""
        from models import ProjectState as _ProjectState
        raw = self.legacy_load("state")
        if raw is None:
            return None
        return _ProjectState.from_dict(json.loads(raw))

    # ── Schema version control ──

    def get_schema_version(self) -> int:
        """Return current database schema version."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT schema_version FROM projects LIMIT 1"
                ).fetchone()
            if row is None:
                # No project rows yet; infer from features table columns
                with self._conn() as conn:
                    cur = conn.execute("PRAGMA table_info(features)")
                    columns = {r["name"] for r in cur.fetchall()}
                return 2 if "wave" in columns else 1
            return row["schema_version"]
        except sqlite3.OperationalError:
            return 0
