"""src/message_queue.py — SQLite-based async message queue for multi-agent task dispatch.

Implements W2-A03 from v3.0 implementation plan:
  - SQLite task_queue table with atomic push/pull via transactions
  - WAL mode for concurrent multi-agent access
  - Auto-retry with dead-letter queue on exhaustion
  - Lazy table initialization on first use

Design: no external message-broker dependencies (no RabbitMQ/Kafka)."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ───────────────────────────────────────────────────────────────
# Dual-import pattern (package / flat)
# ───────────────────────────────────────────────────────────────
try:
    from config import PipelineConfig
    from registry import REGISTRY
except (ModuleNotFoundError, ImportError):
    from src.config import PipelineConfig
    from src.registry import REGISTRY

# ───────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = "message_queue.db"

VALID_STATUSES = ("queued", "running", "completed", "failed", "dead_letter")
# 从REGISTRY获取有效的任务类型
VALID_TASK_TYPES = tuple(REGISTRY.list_task_types())
PRIORITY_HIGH = 2
PRIORITY_NORMAL = 1
PRIORITY_LOW = 0

# ───────────────────────────────────────────────────────────────
# DDL
# ───────────────────────────────────────────────────────────────

TASK_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS task_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_agent    TEXT    NOT NULL,
    task_type       TEXT    NOT NULL,
    feature_id      TEXT,
    context_json    TEXT    NOT NULL DEFAULT '{}',
    priority        INTEGER NOT NULL DEFAULT 1 CHECK(priority IN (0,1,2)),
    status          TEXT    NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','dead_letter')),
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT,
    result_json     TEXT,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_queue_status_priority
    ON task_queue(status, priority DESC, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_task_queue_target_agent
    ON task_queue(target_agent);
"""

# ───────────────────────────────────────────────────────────────
# Data model
# ───────────────────────────────────────────────────────────────


@dataclass
class Task:
    """A single task in the message queue."""

    target_agent: str
    task_type: str  # 'code', 'review', 'test'
    context: Dict[str, Any] = field(default_factory=dict)
    feature_id: Optional[str] = None
    priority: int = PRIORITY_NORMAL  # 0=low, 1=normal, 2=high
    max_retries: int = 3

    # Populated by pull / get_task
    id: Optional[int] = None
    status: str = "queued"
    retry_count: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


# ───────────────────────────────────────────────────────────────
# MessageQueue
# ───────────────────────────────────────────────────────────────


class MessageQueue:
    """SQLite-backed async message queue with atomic pull semantics.

    Thread-safe.  Uses WAL mode for concurrent access across
    multiple agents / processes.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        # Resolve DB path: explicit > env > default
        if db_path is None:
            try:
                cfg = PipelineConfig()
                db_path = str(Path(cfg.base_dir) / "message_queue.db")
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                db_path = DEFAULT_DB_PATH
        self.db_path = db_path
        self._lock = threading.RLock()

        # Ensure parent directory exists
        parent = Path(db_path).parent
        if parent:
            parent.mkdir(parents=True, exist_ok=True)

        self._tables_initialized = False

    # ── Internal helpers ──

    def _conn(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL mode enabled.

        PRAGMA journal_mode=WAL is idempotent and cheap — we set it
        unconditionally on every new connection to guarantee WAL even
        when the underlying DB file is created externally.
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self) -> None:
        """Create tables and indices on first use (lazy init)."""
        if self._tables_initialized:
            return
        with self._lock:
            if self._tables_initialized:
                return
            conn = self._conn()
            try:
                conn.executescript(TASK_QUEUE_DDL)
                conn.commit()
                self._tables_initialized = True
            finally:
                conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        """Convert a DB row to a Task dataclass."""
        return Task(
            id=row["id"],
            target_agent=row["target_agent"],
            task_type=row["task_type"],
            feature_id=row["feature_id"],
            context=json.loads(row["context_json"] or "{}"),
            priority=row["priority"],
            status=row["status"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_message=row["error_message"],
        )

    # ── push ──

    def push(self, task: Task) -> int:
        """Enqueue a task.  Returns the assigned task id.

        Args:
            task: Task with at least target_agent, task_type filled.

        Returns:
            int: The auto-generated task id.
        """
        self._ensure_tables()

        if task.task_type not in VALID_TASK_TYPES:
            raise ValueError(
                f"task_type must be one of {VALID_TASK_TYPES}, got {task.task_type!r}"
            )
        if task.priority not in (0, 1, 2):
            raise ValueError(f"priority must be 0/1/2, got {task.priority}")

        now = self._now()
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO task_queue
                        (target_agent, task_type, feature_id, context_json,
                         priority, max_retries, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.target_agent,
                        task.task_type,
                        task.feature_id,
                        json.dumps(task.context, ensure_ascii=False),
                        task.priority,
                        task.max_retries,
                        now,
                    ),
                )
                conn.commit()
                task_id = cur.lastrowid
                task.id = task_id
                task.created_at = now
                return task_id or 0
            finally:
                conn.close()

    # ── pull ──

    def pull(self, agent_id: str) -> Optional[Task]:
        """Atomically claim the next queued task for *agent_id*.

        Uses an UPDATE … RETURNING pattern in a transaction so that
        no two agents can ever claim the same task.

        Selection order: highest priority first, then earliest creation.

        Args:
            agent_id: The agent requesting a task (must match target_agent).

        Returns:
            Task if one was claimed, None otherwise.
        """
        self._ensure_tables()

        now = self._now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Atomically claim the best match
                row = conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'running', started_at = ?
                    WHERE id = (
                        SELECT id FROM task_queue
                        WHERE target_agent = ? AND status = 'queued'
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (now, agent_id),
                ).fetchone()
                conn.commit()

                if row is None:
                    return None
                return self._row_to_task(row)
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── complete ──

    def complete(self, task_id: int, result: Optional[Dict[str, Any]] = None) -> bool:
        """Mark a task as completed with an optional result payload.

        Args:
            task_id: The task to complete.
            result: Arbitrary result data stored as JSON.

        Returns:
            True if the task existed, False otherwise.
        """
        self._ensure_tables()

        now = self._now()
        result_json = json.dumps(result, ensure_ascii=False) if result else None

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'completed',
                        completed_at = ?,
                        result_json = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, result_json, task_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ── fail ──

    def fail(self, task_id: int, error: str) -> bool:
        """Mark a task as failed.  Auto-retries or moves to dead-letter.

        If retry_count < max_retries the task is re-queued (status='queued').
        Otherwise it is moved to dead_letter.

        Args:
            task_id: The failing task.
            error: Error message to record.

        Returns:
            True if the task existed and was in 'running' status.
        """
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Fetch current retry count & max
                row = conn.execute(
                    "SELECT retry_count, max_retries FROM task_queue WHERE id = ? AND status = 'running'",
                    (task_id,),
                ).fetchone()

                if row is None:
                    conn.rollback()
                    return False

                current_retries = row["retry_count"]
                max_retries = row["max_retries"]

                if current_retries < max_retries:
                    # Auto-retry: bump retry_count and re-queue
                    conn.execute(
                        """
                        UPDATE task_queue
                        SET status = 'queued',
                            retry_count = retry_count + 1,
                            error_message = ?,
                            started_at = NULL
                        WHERE id = ?
                        """,
                        (error, task_id),
                    )
                else:
                    # Exhausted retries → dead-letter
                    conn.execute(
                        """
                        UPDATE task_queue
                        SET status = 'dead_letter',
                            error_message = ?,
                            completed_at = ?
                        WHERE id = ?
                        """,
                        (error, self._now(), task_id),
                    )
                conn.commit()
                return True
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── query helpers ──

    def get_task(self, task_id: int) -> Optional[Task]:
        """Retrieve a task by id (any status)."""
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM task_queue WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_task(row)
            finally:
                conn.close()

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks, optionally filtered by agent and/or status.

        Args:
            agent_id: Filter by target_agent.
            status: Filter by status ('queued', 'running', etc.).
            limit: Maximum results (default 100).
        """
        self._ensure_tables()

        conditions = []
        params: List[Any] = []

        if agent_id is not None:
            conditions.append("target_agent = ?")
            params.append(agent_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    f"SELECT * FROM task_queue {where} ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (*params, limit),
                ).fetchall()
                return [self._row_to_task(r) for r in rows]
            finally:
                conn.close()

    def requeue(self, task_id: int) -> bool:
        """Manually re-queue a dead-letter or failed task (resets retry_count).

        Args:
            task_id: The task to re-queue.

        Returns:
            True if the task existed and was in dead_letter/failed status.
        """
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'queued',
                        retry_count = 0,
                        error_message = NULL,
                        started_at = NULL,
                        completed_at = NULL,
                        result_json = NULL
                    WHERE id = ? AND status IN ('dead_letter', 'failed')
                    """,
                    (task_id,),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def stats(self) -> Dict[str, int]:
        """Return a count of tasks grouped by status."""
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status"
                ).fetchall()
                result: Dict[str, int] = {}
                for row in rows:
                    result[row["status"]] = row["cnt"]
                return result
            finally:
                conn.close()
