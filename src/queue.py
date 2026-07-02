"""src/queue.py — Unified synchronous / asynchronous task queue.

Merges the former ``src/message_queue.py`` (SQLite-backed synchronous queue)
and ``src/task_queue.py`` (async wrapper) into a single ``Queue`` class.

Design notes:
  - SQLite WAL mode with atomic ``UPDATE … RETURNING`` pull semantics.
  - Synchronous API is the source of truth (``*_sync`` methods).
  - Async API delegates to ``asyncio.to_thread`` so the event loop is never
    blocked by SQLite I/O.
  - Lifecycle callbacks (``on(status, callback)``) are preserved for
    orchestrators such as ``event_engine.py``.
  - ``task_type`` validation is performed at runtime via ``REGISTRY``;
    the SQL DDL no longer embeds the allowed task-type list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
# Data models
# ───────────────────────────────────────────────────────────────


@dataclass
class Task:
    """A single task in the queue."""

    target_agent: str
    task_type: str
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


@dataclass
class QueueStats:
    """Aggregated task-queue statistics."""

    total: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    dead_letter: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    by_agent: Dict[str, int] = field(default_factory=dict)


# ───────────────────────────────────────────────────────────────
# Task lifecycle callback protocol (informal)
# ───────────────────────────────────────────────────────────────

TaskCallback = Callable[[Task], None]
"""A callback invoked when a task changes state.

Receives the updated Task (with its new status, result, etc.).
"""

# ───────────────────────────────────────────────────────────────
# Logger
# ───────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Queue
# ───────────────────────────────────────────────────────────────


class Queue:
    """Unified SQLite-backed task queue with both sync and async APIs.

    Thread-safe.  Uses WAL mode for concurrent access across multiple
    agents / processes.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        max_workers: int = 4,
    ) -> None:
        """Create a Queue.

        Args:
            db_path: Path to the SQLite database.  If ``None`` the default
                     from ``PipelineConfig`` or ``"message_queue.db"`` is used.
            max_workers: Ignored; kept for backward compatibility with the
                         previous async wrapper's constructor signature.
        """
        # Resolve DB path: explicit > config > default
        if db_path is None:
            try:
                cfg = PipelineConfig()
                db_path = str(Path(cfg.base_dir) / DEFAULT_DB_PATH)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                db_path = DEFAULT_DB_PATH
        self.db_path = db_path
        self._lock = threading.RLock()

        # Ensure parent directory exists
        parent = Path(db_path).parent
        if parent:
            parent.mkdir(parents=True, exist_ok=True)

        self._tables_initialized = False
        self._callbacks: Dict[str, List[TaskCallback]] = {
            status: [] for status in VALID_STATUSES
        }
        self._closed = False

    # ── Internal helpers ────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL mode enabled."""
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

    # ── Lifecycle callbacks ─────────────────────────────────────

    def on(self, status: str, callback: TaskCallback) -> None:
        """Register a callback for a task status transition.

        Args:
            status: One of the values in ``VALID_STATUSES``.
            callback: Callable receiving the updated ``Task``.

        Raises:
            ValueError: If *status* is not a valid task status.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; must be one of {VALID_STATUSES}"
            )
        self._callbacks[status].append(callback)

    def _fire_callbacks(self, task: Task) -> None:
        """Fire all registered callbacks for *task*'s current status."""
        for cb in self._callbacks.get(task.status, []):
            try:
                cb(task)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                logger.exception(
                    "Queue callback for status=%r on task id=%s raised",
                    task.status,
                    task.id,
                )

    # ── Synchronous API ─────────────────────────────────────────

    def push_sync(self, task: Task) -> int:
        """Enqueue a task.  Returns the assigned task id.

        Args:
            task: Task with at least ``target_agent`` and ``task_type`` set.

        Returns:
            int: The auto-generated task id.

        Raises:
            ValueError: If ``task_type`` is not registered or ``priority``
                        is outside ``0/1/2``.
        """
        self._ensure_tables()

        valid_task_types = REGISTRY.list_task_types()
        if task.task_type not in valid_task_types:
            raise ValueError(
                f"task_type must be one of {valid_task_types}, got {task.task_type!r}"
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
                task_id = cur.lastrowid or 0
                task.id = task_id
                task.created_at = now
                task.status = "queued"
                self._fire_callbacks(task)
                return task_id
            finally:
                conn.close()

    def pull_sync(self, agent_id: str) -> Optional[Task]:
        """Atomically claim the next queued task for *agent_id*.

        Uses an ``UPDATE … RETURNING`` pattern in a transaction so that
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
                task = self._row_to_task(row)
                self._fire_callbacks(task)
                return task
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    def complete_sync(
        self,
        task_id: int,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a task as completed with an optional result payload.

        Args:
            task_id: The task to complete.
            result: Arbitrary result data stored as JSON.

        Returns:
            True if the task existed and was in ``running`` status.
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
                if cur.rowcount > 0:
                    task = self.get_task_sync(task_id)
                    if task is not None:
                        self._fire_callbacks(task)
                return cur.rowcount > 0
            finally:
                conn.close()

    def fail_sync(self, task_id: int, error: str) -> bool:
        """Mark a task as failed.  Auto-retries or moves to dead-letter.

        If ``retry_count < max_retries`` the task is re-queued
        (``status='queued'``).  Otherwise it is moved to ``dead_letter``.

        Args:
            task_id: The failing task.
            error: Error message to record.

        Returns:
            True if the task existed and was in ``running`` status.
        """
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
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
                task = self.get_task_sync(task_id)
                if task is not None:
                    self._fire_callbacks(task)
                return True
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    def recover_orphaned_sync(self) -> int:
        """Reset tasks stuck in ``running`` status back to ``queued``.

        Returns:
            Number of tasks recovered.
        """
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE task_queue
                    SET status = 'queued',
                        started_at = NULL,
                        error_message = COALESCE(error_message, 'Orphaned after restart')
                    WHERE status = 'running'
                    """
                )
                conn.commit()
                return cur.rowcount
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    def stats_sync(self) -> QueueStats:
        """Return aggregated queue statistics.

        Returns:
            A ``QueueStats`` dataclass with counts per status,
            task-type, and target-agent.
        """
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                status_rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status"
                ).fetchall()
                type_rows = conn.execute(
                    "SELECT task_type, COUNT(*) as cnt FROM task_queue GROUP BY task_type"
                ).fetchall()
                agent_rows = conn.execute(
                    "SELECT target_agent, COUNT(*) as cnt FROM task_queue GROUP BY target_agent"
                ).fetchall()

                by_status: Dict[str, int] = {}
                for row in status_rows:
                    by_status[row["status"]] = row["cnt"]

                by_type: Dict[str, int] = {}
                for row in type_rows:
                    by_type[row["task_type"]] = row["cnt"]

                by_agent: Dict[str, int] = {}
                for row in agent_rows:
                    by_agent[row["target_agent"]] = row["cnt"]

                return QueueStats(
                    total=sum(by_status.values()),
                    queued=by_status.get("queued", 0),
                    running=by_status.get("running", 0),
                    completed=by_status.get("completed", 0),
                    failed=by_status.get("failed", 0),
                    dead_letter=by_status.get("dead_letter", 0),
                    by_type=by_type,
                    by_agent=by_agent,
                )
            finally:
                conn.close()

    # ── Query helpers (sync) ────────────────────────────────────

    def get_task_sync(self, task_id: int) -> Optional[Task]:
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

    def list_tasks_sync(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks, optionally filtered by agent and/or status."""
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

    def requeue_sync(self, task_id: int) -> bool:
        """Manually re-queue a dead-letter or failed task (resets retry_count)."""
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
                if cur.rowcount > 0:
                    task = self.get_task_sync(task_id)
                    if task is not None:
                        self._fire_callbacks(task)
                return cur.rowcount > 0
            finally:
                conn.close()

    def purge_completed_sync(self, agent_id: Optional[str] = None) -> int:
        """Delete all completed tasks, optionally filtered by agent."""
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                if agent_id is not None:
                    cur = conn.execute(
                        "DELETE FROM task_queue WHERE status = 'completed' AND target_agent = ?",
                        (agent_id,),
                    )
                else:
                    cur = conn.execute(
                        "DELETE FROM task_queue WHERE status = 'completed'"
                    )
                conn.commit()
                return cur.rowcount
            except (sqlite3.Error, OSError):
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── Asynchronous API ────────────────────────────────────────

    async def _run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous callable in a worker thread."""
        if self._closed:
            raise RuntimeError("Queue is closed")
        return await asyncio.to_thread(func, *args, **kwargs)

    async def enqueue(self, task: Task) -> int:
        """Enqueue a task asynchronously.

        Returns:
            The auto-assigned task id.
        """
        task_id = await self._run_sync(self.push_sync, task)
        logger.debug(
            "Queue enqueued task id=%s type=%s agent=%s",
            task_id, task.task_type, task.target_agent,
        )
        return task_id

    async def batch_enqueue(self, tasks: List[Task]) -> List[int]:
        """Enqueue multiple tasks (preserves input order)."""
        ids: List[int] = []
        for task in tasks:
            tid = await self.enqueue(task)
            ids.append(tid)
        return ids

    async def dequeue(self, agent_id: str) -> Optional[Task]:
        """Atomically claim the next queued task for *agent_id*."""
        task = await self._run_sync(self.pull_sync, agent_id)
        if task is not None:
            logger.debug(
                "Queue agent=%s claimed task id=%s type=%s",
                agent_id, task.id, task.task_type,
            )
        return task

    async def complete_task(
        self,
        task_id: int,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a task as completed asynchronously."""
        ok = await self._run_sync(self.complete_sync, task_id, result)
        if ok:
            logger.debug("Queue completed task id=%s", task_id)
        return ok

    async def fail_task(self, task_id: int, error: str) -> bool:
        """Mark a task as failed asynchronously — auto-retry or dead-letter."""
        ok = await self._run_sync(self.fail_sync, task_id, error)
        if ok:
            logger.debug("Queue failed task id=%s error=%r", task_id, error[:120])
        return ok

    async def requeue(self, task_id: int) -> bool:
        """Re-queue a dead-letter or failed task asynchronously."""
        ok = await self._run_sync(self.requeue_sync, task_id)
        if ok:
            logger.debug("Queue requeued task id=%s", task_id)
        return ok

    async def get_task(self, task_id: int) -> Optional[Task]:
        """Retrieve a task by id asynchronously."""
        return await self._run_sync(self.get_task_sync, task_id)

    async def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks asynchronously."""
        return await self._run_sync(self.list_tasks_sync, agent_id, status, limit)

    async def list_dead_letters(self, limit: int = 100) -> List[Task]:
        """List dead-letter tasks asynchronously."""
        return await self.list_tasks(status="dead_letter", limit=limit)

    async def replay_dead_letters(self, agent_id: Optional[str] = None) -> int:
        """Re-queue all dead-letter tasks (optionally for one agent)."""
        dead = await self.list_dead_letters(limit=10_000)
        count = 0
        for task in dead:
            if agent_id is not None and task.target_agent != agent_id:
                continue
            if await self.requeue(task.id or 0):
                count += 1
        return count

    async def purge_completed(self, agent_id: Optional[str] = None) -> int:
        """Delete completed tasks asynchronously."""
        return await self._run_sync(self.purge_completed_sync, agent_id)

    async def recover_orphaned(self) -> int:
        """Recover tasks stuck in ``running`` status asynchronously."""
        count = await self._run_sync(self.recover_orphaned_sync)
        if count:
            logger.info("Queue recovered %d orphaned task(s)", count)
        return count

    async def stats(self) -> QueueStats:
        """Return aggregated queue statistics asynchronously."""
        return await self._run_sync(self.stats_sync)

    def pending_count_sync(self, agent_id: Optional[str] = None) -> int:
        """Return the number of queued tasks, optionally filtered by agent."""
        self._ensure_tables()

        with self._lock:
            conn = self._conn()
            try:
                if agent_id is not None:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'queued' AND target_agent = ?",
                        (agent_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'queued'"
                    ).fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    async def pending_count(self, agent_id: Optional[str] = None) -> int:
        """Return the number of queued tasks asynchronously."""
        return await self._run_sync(self.pending_count_sync, agent_id)

    async def agent_has_work(self, agent_id: str) -> bool:
        """Check whether *agent_id* has queued tasks."""
        tasks = await self.list_tasks(agent_id=agent_id, status="queued", limit=1)
        return len(tasks) > 0

    # ── Shutdown ────────────────────────────────────────────────

    def close(self) -> None:
        """Mark the Queue as closed.

        After calling ``close()`` the instance cannot be used.
        """
        if self._closed:
            return
        self._closed = True
        logger.debug("Queue closed")

    async def close_async(self) -> None:
        """Asynchronous-friendly close (simply marks closed)."""
        self.close()

    async def __aenter__(self) -> "Queue":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
