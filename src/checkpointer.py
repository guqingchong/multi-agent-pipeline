"""src/checkpointer.py — Unified checkpoint write/resume

W2-A05: Subtask-level checkpoint granularity.
- Save checkpoint after every subtask completes
- Resume from last successful checkpoint
- Uses existing StateStore DB connection

CheckpointRecord fields:
  id / task_id / subtask_id / phase / state_json / result / agent_id / created_at
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from state_store import StateStore
except ImportError:
    from src.state_store import StateStore


# ───────────────────────────────────────────────────────────────
# Data Model
# ───────────────────────────────────────────────────────────────


@dataclass
class CheckpointRecord:
    """Checkpoint record at subtask granularity.

    Fields:
        id: Auto-incremented checkpoint ID (None before persisting).
        task_id: Parent task identifier.
        subtask_id: Subtask identifier within the task.
        phase: Pipeline phase name (e.g. 'develop', 'test').
        state_json: JSON-encoded state snapshot.
        result: Execution outcome — 'success', 'failed', or 'timeout'.
        agent_id: The agent that executed this subtask.
        created_at: ISO-8601 timestamp (UTC).
    """

    id: Optional[int] = None
    task_id: str = ""
    subtask_id: str = ""
    phase: str = ""
    state_json: str = "{}"
    result: str = ""  # success / failed / timeout
    agent_id: str = ""
    created_at: Optional[str] = None

    def state_dict(self) -> Dict[str, Any]:
        """Decode the state_json field into a Python dict."""
        return json.loads(self.state_json) if self.state_json else {}

    def is_success(self) -> bool:
        """Return True if this checkpoint represents a successful subtask."""
        return self.result == "success"


# ───────────────────────────────────────────────────────────────
# Checkpointer — Subtask-level checkpoint manager
# ───────────────────────────────────────────────────────────────


class Checkpointer:
    """Unified checkpoint write/resume manager.

    Uses the existing StateStore's SQLite connection for persistence.
    Checkpoints are stored in a dedicated ``checkpointer_subtasks`` table
    (separate from ``state_store.py``'s ``checkpoints`` table) at
    subtask-level granularity.

    Usage::

        store = StateStore(Path("pipeline_state.db"))
        cp = Checkpointer(store)

        # After each subtask:
        cp.save("task-1", "subtask-3", result="success",
                phase="develop", agent_id="claude")

        # On resume:
        remaining = cp.resume("task-1", all_subtasks=["st-1","st-2","st-3","st-4"])
        #  → ["st-4"]  (st-1, st-2, st-3 were successful)
    """

    # Separate table to avoid conflicting with state_store.py's ``checkpoints`` schema.
    SUBTASK_CHECKPOINTS_SQL: str = """\
CREATE TABLE IF NOT EXISTS checkpointer_subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

    def __init__(
        self,
        store: Optional[StateStore] = None,
        db_path: Optional[Union[str, Path]] = None,
    ) -> None:
        """Initialize the Checkpointer.

        Provide **either** an existing ``StateStore`` **or** a ``db_path``.
        When ``store`` is given the checkpointer shares that store's
        connection and database file.

        Args:
            store: Existing ``StateStore`` instance (preferred).
            db_path: Path to SQLite database file or directory.
                Only used when ``store`` is None.

        Raises:
            ValueError: If neither ``store`` nor ``db_path`` is provided.
        """
        if store is not None:
            self._store = store
        elif db_path is not None:
            self._store = StateStore(Path(db_path))
        else:
            raise ValueError(
                "Checkpointer requires either an existing StateStore (store=) "
                "or a db_path to create one."
            )
        self._ensure_table()

    # ── Internal helpers ──────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create the ``checkpointer_subtasks`` table if it doesn't exist."""
        with self._store._conn() as conn:
            conn.execute(self.SUBTASK_CHECKPOINTS_SQL)
            conn.commit()

    @staticmethod
    def _now() -> str:
        """Return current UTC timestamp as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_record(row: Any) -> CheckpointRecord:
        """Convert a sqlite3.Row into a ``CheckpointRecord``."""
        return CheckpointRecord(
            id=row["id"],
            task_id=row["task_id"],
            subtask_id=row["subtask_id"],
            phase=row["phase"] or "",
            state_json=row["state_json"] or "{}",
            result=row["result"] or "",
            agent_id=row["agent_id"] or "",
            created_at=row["created_at"],
        )

    # ── Public API ────────────────────────────────────────────

    def save(
        self,
        task_id: str,
        subtask_id: str,
        result: str,
        *,
        phase: str = "",
        state: Optional[Dict[str, Any]] = None,
        agent_id: str = "",
    ) -> int:
        """Save a checkpoint after a subtask completes.

        Args:
            task_id: The parent task identifier.
            subtask_id: The subtask identifier.
            result: Execution outcome. One of ``'success'``, ``'failed'``,
                or ``'timeout'``.
            phase: Current pipeline phase name (e.g. ``'develop'``).
            state: Optional state dictionary to snapshot.
            agent_id: The agent that executed this subtask.

        Returns:
            The auto-generated checkpoint record ID.
        """
        state_json = json.dumps(state or {}, ensure_ascii=False)
        now = self._now()
        with self._store._conn() as conn:
            cur = conn.execute(
                """\
INSERT INTO checkpointer_subtasks
    (task_id, subtask_id, phase, state_json, result, agent_id, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
""",
                (task_id, subtask_id, phase, state_json, result, agent_id, now),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_last_success(self, task_id: str) -> Optional[CheckpointRecord]:
        """Return the most recent *successful* checkpoint for a task.

        Args:
            task_id: The task identifier.

        Returns:
            The latest ``CheckpointRecord`` with ``result='success'``,
            or ``None`` if no successful checkpoint exists.
        """
        with self._store._conn() as conn:
            row = conn.execute(
                """\
SELECT * FROM checkpointer_subtasks
WHERE task_id = ? AND result = 'success'
ORDER BY id DESC
LIMIT 1
""",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def resume(
        self,
        task_id: str,
        all_subtask_ids: List[str],
    ) -> List[str]:
        """Resume from the last successful checkpoint.

        Given an ordered list of all subtask IDs for a task, this method
        looks up the last successful checkpoint and returns the list of
        subtasks that **still need to run**.

        Args:
            task_id: The task identifier.
            all_subtask_ids: Ordered list of every subtask ID belonging
                to this task (in execution order).

        Returns:
            The suffix of ``all_subtask_ids`` that has not yet been
            successfully completed.  If no successful checkpoint exists
            the full list is returned unchanged.
        """
        last = self.get_last_success(task_id)
        if last is None:
            return list(all_subtask_ids)

        try:
            idx = all_subtask_ids.index(last.subtask_id)
            return all_subtask_ids[idx + 1:]
        except ValueError:
            # Last successful subtask ID is not in the current list.
            # This can happen when the subtask list was modified between
            # runs.  Conservative choice: re-run everything.
            return list(all_subtask_ids)

    def get_checkpoint(self, checkpoint_id: int) -> Optional[CheckpointRecord]:
        """Retrieve a specific checkpoint by its ID.

        Args:
            checkpoint_id: The auto-incremented checkpoint ID.

        Returns:
            The matching ``CheckpointRecord`` or ``None``.
        """
        with self._store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpointer_subtasks WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_checkpoints(
        self,
        task_id: str,
        limit: int = 50,
    ) -> List[CheckpointRecord]:
        """List checkpoints for a task, most recent first.

        Args:
            task_id: The task identifier.
            limit: Maximum number of records to return (default 50).

        Returns:
            List of ``CheckpointRecord`` objects, newest first.
        """
        with self._store._conn() as conn:
            rows = conn.execute(
                """\
SELECT * FROM checkpointer_subtasks
WHERE task_id = ?
ORDER BY id DESC
LIMIT ?
""",
                (task_id, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def restore_state(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """Restore the state dictionary from a specific checkpoint.

        Args:
            checkpoint_id: The checkpoint ID to restore from.

        Returns:
            The decoded state dictionary, or ``None`` if the checkpoint
            does not exist.
        """
        cp = self.get_checkpoint(checkpoint_id)
        if cp is None:
            return None
        return cp.state_dict()

    @property
    def store(self) -> StateStore:
        """Access the underlying ``StateStore`` instance."""
        return self._store
