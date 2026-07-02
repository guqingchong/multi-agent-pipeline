"""src/task_queue.py — Persistent async task queue wrapping message_queue.py.

W3-E03 from v3.0 implementation plan:
  - Async wrapper around the synchronous MessageQueue (SQLite-backed).
  - Supports queued / running / failed / completed / dead_letter states.
  - Auto-retry on failure with dead_letter on exhaustion (delegates to MessageQueue.fail).
  - Resume from queue after process restart — recovers tasks stuck in 'running'.
  - Batch enqueue, detailed stats, dead-letter inspection, and lifecycle callbacks.

Design: thin async layer over MessageQueue.  All persistence lives in SQLite
via message_queue.py; this module adds asyncio ergonomics, resume-on-restart,
and a ready-to-use TaskQueue facade for orchestrators and agent dispatchers.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# ───────────────────────────────────────────────────────────────
# Dual-import pattern (package / flat)
# ───────────────────────────────────────────────────────────────

try:
    from message_queue import (  # type: ignore[import-not-found]
        DEFAULT_DB_PATH,
        MessageQueue,
        Task,
        VALID_STATUSES,
        VALID_TASK_TYPES,
        PRIORITY_HIGH,
        PRIORITY_NORMAL,
        PRIORITY_LOW,
    )
except ImportError:
    from src.message_queue import (  # type: ignore[no-redef]
        DEFAULT_DB_PATH,
        MessageQueue,
        Task,
        VALID_STATUSES,
        VALID_TASK_TYPES,
        PRIORITY_HIGH,
        PRIORITY_NORMAL,
        PRIORITY_LOW,
    )

# ───────────────────────────────────────────────────────────────
# Logger
# ───────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────


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
# TaskQueue
# ───────────────────────────────────────────────────────────────


class TaskQueue:
    """Persistent async task queue wrapping :class:`MessageQueue`.

    Adds asyncio ergonomics, resume-on-restart (recover stuck *running*
    tasks), batch operations, detailed stats, and lifecycle callbacks.
    All durable state lives in the underlying SQLite database via
    ``MessageQueue``.

    Typical usage::

        import asyncio
        from task_queue import TaskQueue, Task

        async def main():
            tq = TaskQueue("pipeline.db")
            await tq.resume()                    # recover orphaned tasks

            task = Task(target_agent="claude-code", task_type="code",
                        context={"feature_id": "F001"}, priority=2)
            task_id = await tq.enqueue(task)

            claimed = await tq.dequeue("claude-code")
            if claimed:
                await tq.complete_task(claimed.id, {"output": "done"})

            print(await tq.stats())
            await tq.close()

        asyncio.run(main())
    """

    # ── init ────────────────────────────────────────────────────

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        max_workers: int = 4,
        message_queue: Optional[MessageQueue] = None,
    ) -> None:
        """Create a TaskQueue.

        Args:
            db_path: Path to SQLite database (passed through to MessageQueue).
                     If ``None`` the default from ``PipelineConfig`` or
                     ``"message_queue.db"`` is used.
            max_workers: Thread-pool size for running synchronous SQLite
                         operations via ``asyncio.to_thread``.
            message_queue: Pre-configured MessageQueue instance.  When
                           supplied, ``db_path`` is ignored.
        """
        if message_queue is not None:
            self._mq = message_queue
        else:
            self._mq = MessageQueue(db_path=db_path)

        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._callbacks: Dict[str, List[TaskCallback]] = {
            "queued": [],
            "running": [],
            "completed": [],
            "failed": [],
            "dead_letter": [],
        }
        self._closed = False

    # ── lifecycle callbacks ─────────────────────────────────────

    def on(self, status: str, callback: TaskCallback) -> None:
        """Register a callback for a task status transition.

        Args:
            status: One of ``'queued'``, ``'running'``, ``'completed'``,
                    ``'failed'``, ``'dead_letter'``.
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
                    "TaskQueue callback for status=%r on task id=%s raised",
                    task.status,
                    task.id,
                )

    # ── async helpers ───────────────────────────────────────────

    async def _run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous callable in the thread pool.

        Uses ``asyncio.to_thread`` (Python ≥ 3.9) so the event loop is
        never blocked by SQLite I/O.
        """
        if self._closed:
            raise RuntimeError("TaskQueue is closed")
        return await asyncio.to_thread(func, *args, **kwargs)

    # ── enqueue ─────────────────────────────────────────────────

    async def enqueue(self, task: Task) -> int:
        """Enqueue a task for asynchronous dispatch.

        Args:
            task: A ``Task`` with at least ``target_agent`` and
                  ``task_type`` populated.

        Returns:
            The auto-assigned task id.

        Raises:
            ValueError: If ``task_type`` or ``priority`` is invalid.
        """
        task_id = await self._run_sync(self._mq.push, task)
        task.id = task_id
        task.status = "queued"
        self._fire_callbacks(task)
        logger.debug("TaskQueue enqueued task id=%s type=%s agent=%s", task_id, task.task_type, task.target_agent)
        return task_id

    async def batch_enqueue(self, tasks: List[Task]) -> List[int]:
        """Enqueue multiple tasks.

        Args:
            tasks: List of ``Task`` objects.

        Returns:
            List of assigned task ids (same order as input).
        """
        ids: List[int] = []
        for task in tasks:
            tid = await self.enqueue(task)
            ids.append(tid)
        return ids

    # ── dequeue ─────────────────────────────────────────────────

    async def dequeue(self, agent_id: str) -> Optional[Task]:
        """Atomically claim the next queued task for *agent_id*.

        Selection order: highest priority first, then earliest creation.

        Args:
            agent_id: The agent requesting work.

        Returns:
            The claimed ``Task``, or ``None`` if no queued task exists
            for this agent.
        """
        task = await self._run_sync(self._mq.pull, agent_id)
        if task is not None:
            self._fire_callbacks(task)
            logger.debug("TaskQueue agent=%s claimed task id=%s type=%s", agent_id, task.id, task.task_type)
        return task

    # ── complete ────────────────────────────────────────────────

    async def complete_task(
        self,
        task_id: int,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a task as completed with an optional result payload.

        Args:
            task_id: The task id.
            result: Arbitrary result data (serialized as JSON).

        Returns:
            ``True`` if the task was found and in *running* status,
            ``False`` otherwise.
        """
        ok = await self._run_sync(self._mq.complete, task_id, result)
        if ok:
            task = await self.get_task(task_id)
            if task:
                self._fire_callbacks(task)
            logger.debug("TaskQueue completed task id=%s", task_id)
        return ok

    # ── fail ────────────────────────────────────────────────────

    async def fail_task(self, task_id: int, error: str) -> bool:
        """Mark a task as failed — auto-retry or dead-letter.

        If the task still has retries remaining it is re-queued
        (status → ``'queued'``).  Otherwise it moves to ``'dead_letter'``.

        Args:
            task_id: The failing task.
            error: Human-readable error description.

        Returns:
            ``True`` if the task existed and was in *running* status.
        """
        ok = await self._run_sync(self._mq.fail, task_id, error)
        if ok:
            task = await self.get_task(task_id)
            if task:
                self._fire_callbacks(task)
            logger.debug("TaskQueue failed task id=%s error=%r", task_id, error[:120])
        return ok

    # ── requeue ─────────────────────────────────────────────────

    async def requeue(self, task_id: int) -> bool:
        """Re-queue a dead-letter or failed task (resets retry_count).

        Args:
            task_id: The task to re-queue.

        Returns:
            ``True`` if the task was re-queued, ``False`` if it didn't
            exist or wasn't in a requeue-able status.
        """
        ok = await self._run_sync(self._mq.requeue, task_id)
        if ok:
            task = await self.get_task(task_id)
            if task:
                self._fire_callbacks(task)
            logger.debug("TaskQueue requeued task id=%s", task_id)
        return ok

    # ── query ───────────────────────────────────────────────────

    async def get_task(self, task_id: int) -> Optional[Task]:
        """Retrieve a task by id (any status).

        Args:
            task_id: The task id.

        Returns:
            The ``Task`` or ``None`` if not found.
        """
        return await self._run_sync(self._mq.get_task, task_id)

    async def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks, optionally filtered by agent and/or status.

        Args:
            agent_id: Filter by ``target_agent``.
            status: Filter by status (``'queued'``, ``'running'``, etc.).
            limit: Maximum results (default 100).

        Returns:
            List of matching ``Task`` objects, ordered by priority
            (descending) then creation time (ascending).
        """
        return await self._run_sync(self._mq.list_tasks, agent_id, status, limit)

    # ── stats ───────────────────────────────────────────────────

    async def stats(self) -> QueueStats:
        """Return aggregated queue statistics.

        Returns:
            A ``QueueStats`` dataclass with counts per status,
            task-type, and target-agent.
        """
        raw = await self._run_sync(self._mq.stats)

        qs = QueueStats(
            total=sum(raw.values()),
            queued=raw.get("queued", 0),
            running=raw.get("running", 0),
            completed=raw.get("completed", 0),
            failed=raw.get("failed", 0),
            dead_letter=raw.get("dead_letter", 0),
        )

        # Per-type breakdown
        all_tasks = await self.list_tasks(limit=10_000)
        by_type: Dict[str, int] = {}
        by_agent: Dict[str, int] = {}
        for t in all_tasks:
            by_type[t.task_type] = by_type.get(t.task_type, 0) + 1
            by_agent[t.target_agent] = by_agent.get(t.target_agent, 0) + 1

        qs.by_type = by_type
        qs.by_agent = by_agent
        return qs

    # ── resume ──────────────────────────────────────────────────

    async def resume(self) -> int:
        """Recover tasks stuck in ``'running'`` status after a restart.

        When a process crashes or is killed, tasks that were claimed
        (status = ``'running'``) are left orphaned.  This method resets
        them back to ``'queued'`` so they will be re-dispatched.

        Returns:
            Number of tasks recovered.
        """
        count = await self._run_sync(self._recover_orphaned_tasks)
        if count:
            logger.info("TaskQueue resumed %d orphaned task(s)", count)
        return count

    def _recover_orphaned_tasks(self) -> int:
        """Synchronous helper: reset 'running' → 'queued'.

        This operates directly on the underlying MessageQueue's
        private state, so it holds the queue lock briefly.
        """
        self._mq._ensure_tables()

        with self._mq._lock:
            conn = self._mq._conn()
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

    # ── dead-letter management ──────────────────────────────────

    async def list_dead_letters(self, limit: int = 100) -> List[Task]:
        """List tasks currently in the dead-letter queue.

        Args:
            limit: Maximum results (default 100).

        Returns:
            List of dead-letter ``Task`` objects.
        """
        return await self.list_tasks(status="dead_letter", limit=limit)

    async def replay_dead_letters(self, agent_id: Optional[str] = None) -> int:
        """Re-queue all dead-letter tasks (optionally for one agent).

        Args:
            agent_id: If provided, only replay dead letters for this agent.

        Returns:
            Number of tasks re-queued.
        """
        dead = await self.list_dead_letters(limit=10_000)
        count = 0
        for task in dead:
            if agent_id is not None and task.target_agent != agent_id:
                continue
            if await self.requeue(task.id or 0):
                count += 1
        return count

    async def purge_completed(self, agent_id: Optional[str] = None) -> int:
        """Delete all completed tasks from the database.

        Args:
            agent_id: If provided, only purge for this agent.

        Returns:
            Number of tasks deleted.
        """
        return await self._run_sync(self._purge_completed_sync, agent_id)

    def _purge_completed_sync(self, agent_id: Optional[str]) -> int:
        """Synchronous helper: DELETE completed rows."""
        self._mq._ensure_tables()

        with self._mq._lock:
            conn = self._mq._conn()
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

    # ── agent helpers ───────────────────────────────────────────

    async def pending_count(self, agent_id: Optional[str] = None) -> int:
        """Return the number of tasks waiting to be processed.

        Args:
            agent_id: If provided, count only for this agent.

        Returns:
            Count of queued tasks.
        """
        s = await self.stats()
        if agent_id is not None:
            return s.by_agent.get(agent_id, 0)
        return s.queued

    async def agent_has_work(self, agent_id: str) -> bool:
        """Check whether *agent_id* has queued tasks.

        Args:
            agent_id: The agent to check.

        Returns:
            ``True`` if at least one queued task exists for this agent.
        """
        tasks = await self.list_tasks(agent_id=agent_id, status="queued", limit=1)
        return len(tasks) > 0

    # ── shutdown ────────────────────────────────────────────────

    async def close(self) -> None:
        """Shut down the TaskQueue and release resources.

        After calling ``close()`` the instance cannot be used.
        """
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True)
        logger.debug("TaskQueue closed")

    async def __aenter__(self) -> "TaskQueue":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
