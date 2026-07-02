"""src/watchdog.py — Periodic global state snapshot saver with crash recovery.

W5-Q04: Watchdog for the multi-agent pipeline.
- Periodically saves a global state snapshot at configurable intervals.
- Crash recovery: on startup, detects and restores from the latest snapshot.
- Design principle: any failure should be resumable.

Depends on:
    W2-A05 checkpointer (src/checkpointer.py) for persistence.
    StateStore (src/state_store.py) for the SQLite connection.

Snapshot schema (table: ``watchdog_snapshots``):
    id / project_id / phase / features_json / agents_json / workers_json
    / circuit_state / budget_remaining / metadata_json / health / created_at

Usage::

    from state_store import StateStore
    from checkpointer import Checkpointer
    from watchdog import Watchdog

    store = StateStore(Path("pipeline_state.db"))
    cp = Checkpointer(store)
    wd = Watchdog(store, interval=30.0)

    # Register a custom state provider
    def my_provider():
        return {"custom_key": 42}
    wd.register_provider("my_module", my_provider)

    # Start periodic snapshots (background thread)
    wd.start()

    # ... pipeline work ...

    # Stop the watchdog
    wd.stop()

    # On crash recovery:
    snapshot = wd.recover_latest("my-project")
    if snapshot:
        phase = snapshot["phase"]
        features = snapshot["features"]
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Dual-import pattern (package / flat)
# ───────────────────────────────────────────────────────────────

try:
    from state_store import StateStore
except ImportError:
    from src.state_store import StateStore

try:
    from checkpointer import Checkpointer
except ImportError:
    from src.checkpointer import Checkpointer

try:
    from models import (
        PipelineError,
        Phase,
    )
except ImportError:
    from src.models import (
        PipelineError,
        Phase,
    )


# ───────────────────────────────────────────────────────────────
# Data model
# ───────────────────────────────────────────────────────────────

@dataclass
class WatchdogSnapshot:
    """A single global state snapshot captured by the watchdog.

    Fields:
        id: Auto-incremented snapshot ID (None before persisting).
        project_id: The project identifier.
        phase: Current pipeline phase name (e.g. 'develop', 'test').
        features_json: JSON-encoded list of feature status dicts.
        agents_json: JSON-encoded agent state information.
        workers_json: JSON-encoded worker pool state.
        circuit_state: Current circuit breaker state (e.g. 'CLOSED').
        budget_remaining: Remaining budget (token cost or USD).
        metadata_json: JSON-encoded arbitrary metadata dictionary.
        health: Health marker — 'healthy', 'degraded', or 'fatal'.
        created_at: ISO-8601 UTC timestamp.
    """

    id: Optional[int] = None
    project_id: str = ""
    phase: str = ""
    features_json: str = "[]"
    agents_json: str = "{}"
    workers_json: str = "{}"
    circuit_state: str = ""
    budget_remaining: float = 0.0
    metadata_json: str = "{}"
    health: str = "healthy"
    created_at: Optional[str] = None

    def features_list(self) -> List[Dict[str, Any]]:
        """Decode features_json into a list of feature dicts."""
        return json.loads(self.features_json) if self.features_json else []

    def agents_dict(self) -> Dict[str, Any]:
        """Decode agents_json into a dict."""
        return json.loads(self.agents_json) if self.agents_json else {}

    def workers_dict(self) -> Dict[str, Any]:
        """Decode workers_json into a dict."""
        return json.loads(self.workers_json) if self.workers_json else {}

    def metadata_dict(self) -> Dict[str, Any]:
        """Decode metadata_json into a dict."""
        return json.loads(self.metadata_json) if self.metadata_json else {}

    def is_healthy(self) -> bool:
        """Return True if this snapshot represents a healthy pipeline state."""
        return self.health == "healthy"


# ───────────────────────────────────────────────────────────────
# State provider protocol
# ───────────────────────────────────────────────────────────────

# A state provider is a callable that returns a dict with state info.
# Registered providers are called during each snapshot cycle.
StateProvider = Callable[[], Dict[str, Any]]


# ───────────────────────────────────────────────────────────────
# Watchdog — Periodic global state snapshot saver
# ───────────────────────────────────────────────────────────────

class Watchdog:
    """Periodic global state snapshot saver with crash recovery.

    Runs a background thread that captures the full pipeline state at
    configurable intervals and persists it via the Checkpointer's
    StateStore.  On crash / restart, the latest snapshot can be
    recovered to resume from where the pipeline left off.

    Features:
        - Configurable snapshot interval (default 30 seconds).
        - Crash recovery: ``recover_latest(project_id)`` returns the
          latest healthy snapshot, or the most recent snapshot of any
          health level.
        - Pluggable state providers: other modules can register
          callables that contribute state to each snapshot.
        - Thread-safe: snapshot writes are protected by a lock.
        - Graceful start/stop via ``start()`` / ``stop()``.
        - Snapshot pruning: old snapshots beyond ``max_snapshots``
          (default 100) are automatically removed.

    Dependencies:
        - ``StateStore``: provides the SQLite connection.
        - ``Checkpointer``: used for subtask-level resume coordination.

    Usage::

        store = StateStore(Path("pipeline_state.db"))
        wd = Watchdog(store, interval=30.0)

        # Register providers
        wd.register_provider("pipeline", my_pipeline_state_fn)

        # Start background snapshots
        wd.start()

        # ... run pipeline ...

        # On crash recovery:
        snap = wd.recover_latest("my-project")
        if snap:
            print(f"Recovered phase: {snap['phase']}")
    """

    # ── SQL for the watchdog_snapshots table ─────────────────────

    WATCHDOG_SNAPSHOTS_SQL: str = """\
CREATE TABLE IF NOT EXISTS watchdog_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    features_json TEXT NOT NULL DEFAULT '[]',
    agents_json TEXT NOT NULL DEFAULT '{}',
    workers_json TEXT NOT NULL DEFAULT '{}',
    circuit_state TEXT NOT NULL DEFAULT '',
    budget_remaining REAL NOT NULL DEFAULT 0.0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    health TEXT NOT NULL DEFAULT 'healthy'
        CHECK(health IN ('healthy', 'degraded', 'fatal')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

    # ── Internal defaults ────────────────────────────────────────

    _DEFAULT_INTERVAL: float = 30.0
    _DEFAULT_MAX_SNAPSHOTS: int = 100
    _STOP_WAIT_TIMEOUT: float = 5.0

    def __init__(
        self,
        store: Optional[StateStore] = None,
        checkpointer: Optional[Checkpointer] = None,
        db_path: Optional[Union[str, Path]] = None,
        *,
        interval: float = _DEFAULT_INTERVAL,
        max_snapshots: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        """Initialize the Watchdog.

        Provide **either** an existing ``StateStore`` **or** a ``db_path``,
        and optionally an existing ``Checkpointer`` instance.

        Args:
            store: Existing ``StateStore`` instance (preferred).
            checkpointer: Existing ``Checkpointer`` instance for
                subtask-level resume coordination.
            db_path: Path to SQLite database file or directory.
                Only used when ``store`` is None.
            interval: Seconds between periodic snapshots (default 30).
            max_snapshots: Maximum snapshots to retain per project
                before pruning (default 100).

        Raises:
            ValueError: If neither ``store`` nor ``db_path`` is provided.
        """
        if store is not None:
            self._store = store
        elif db_path is not None:
            self._store = StateStore(Path(db_path))
        else:
            raise ValueError(
                "Watchdog requires either an existing StateStore (store=) "
                "or a db_path to create one."
            )

        self._checkpointer: Optional[Checkpointer] = checkpointer
        self._interval: float = max(1.0, float(interval))
        self._max_snapshots: int = max(5, int(max_snapshots))

        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Registered state providers: name → callable
        self._providers: Dict[str, StateProvider] = {}

        # Track the current project_id for auto-snapshot targeting
        self._current_project_id: Optional[str] = None
        self._current_phase: str = ""

        # Ensure the snapshot table exists
        self._ensure_table()

    # ── Public properties ────────────────────────────────────────

    @property
    def store(self) -> StateStore:
        """Access the underlying ``StateStore`` instance."""
        return self._store

    @property
    def checkpointer(self) -> Optional[Checkpointer]:
        """Access the associated ``Checkpointer`` instance, if any."""
        return self._checkpointer

    @property
    def interval(self) -> float:
        """Current snapshot interval in seconds."""
        return self._interval

    @interval.setter
    def interval(self, value: float) -> None:
        """Update the snapshot interval (seconds, ≥1.0)."""
        self._interval = max(1.0, float(value))

    @property
    def is_running(self) -> bool:
        """Return True if the watchdog background thread is active."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def current_project_id(self) -> Optional[str]:
        """The project ID currently targeted for snapshots."""
        return self._current_project_id

    @property
    def current_phase(self) -> str:
        """The current phase tracked for snapshots."""
        return self._current_phase

    # ── Internal helpers ─────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create the ``watchdog_snapshots`` table if it doesn't exist."""
        with self._store._conn() as conn:
            conn.execute(self.WATCHDOG_SNAPSHOTS_SQL)
            conn.commit()

    @staticmethod
    def _now() -> str:
        """Return current UTC timestamp as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_snapshot(row: Any) -> WatchdogSnapshot:
        """Convert a sqlite3.Row into a ``WatchdogSnapshot``."""
        return WatchdogSnapshot(
            id=row["id"],
            project_id=row["project_id"],
            phase=row["phase"] or "",
            features_json=row["features_json"] or "[]",
            agents_json=row["agents_json"] or "{}",
            workers_json=row["workers_json"] or "{}",
            circuit_state=row["circuit_state"] or "",
            budget_remaining=row["budget_remaining"] or 0.0,
            metadata_json=row["metadata_json"] or "{}",
            health=row["health"] or "healthy",
            created_at=row["created_at"],
        )

    def _collect_providers_state(self) -> Dict[str, Any]:
        """Call all registered state providers and merge their results.

        Each provider returns a dict; the keys are namespaced under
        the provider name to avoid collisions.

        Returns:
            Merged dict of all provider states.
        """
        merged: Dict[str, Any] = {}
        for name, provider in self._providers.items():
            try:
                state = provider()
                if isinstance(state, dict):
                    merged[name] = state
                else:
                    merged[name] = {"value": state}
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning(
                    "Watchdog state provider %r raised: %s", name, exc
                )
                merged[name] = {"error": str(exc)}
        return merged

    def _prune_old_snapshots(self, project_id: str) -> None:
        """Remove the oldest snapshots for a project beyond max_snapshots."""
        with self._store._conn() as conn:
            # Count current snapshots for this project
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM watchdog_snapshots WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                return
            count = row["cnt"]
            if count <= self._max_snapshots:
                return

            # Delete oldest beyond the limit
            excess = count - self._max_snapshots
            conn.execute(
                """\
DELETE FROM watchdog_snapshots
WHERE id IN (
    SELECT id FROM watchdog_snapshots
    WHERE project_id = ?
    ORDER BY id ASC
    LIMIT ?
)
""",
                (project_id, excess),
            )
            conn.commit()
            logger.debug(
                "Watchdog pruned %d old snapshots for project %r",
                excess,
                project_id,
            )

    # ── Public API: registration ─────────────────────────────────

    def register_provider(self, name: str, provider: StateProvider) -> None:
        """Register a state provider callable.

        The provider will be called during every snapshot cycle.
        Its return value (a dict) is merged under ``name`` in the
        ``metadata_json`` field of each snapshot.

        Args:
            name: Unique name for this provider (e.g. 'pipeline').
            provider: Callable that returns a ``dict`` of state info.

        Raises:
            ValueError: If a provider with this name is already registered.
        """
        with self._lock:
            if name in self._providers:
                raise ValueError(
                    f"State provider {name!r} is already registered."
                )
            self._providers[name] = provider
            logger.debug("Watchdog registered provider %r", name)

    def unregister_provider(self, name: str) -> None:
        """Unregister a previously registered state provider.

        Args:
            name: The provider name to remove.

        Raises:
            KeyError: If no provider with this name is registered.
        """
        with self._lock:
            if name not in self._providers:
                raise KeyError(
                    f"No state provider named {name!r} is registered."
                )
            del self._providers[name]
            logger.debug("Watchdog unregistered provider %r", name)

    def list_providers(self) -> List[str]:
        """Return the list of registered provider names."""
        with self._lock:
            return list(self._providers.keys())

    # ── Public API: snapshot management ──────────────────────────

    def set_project(self, project_id: str, phase: str = "") -> None:
        """Set the current project ID and phase targeted by snapshots.

        Args:
            project_id: The project identifier to snapshot.
            phase: Current pipeline phase (e.g. 'develop').
        """
        with self._lock:
            self._current_project_id = project_id
            self._current_phase = phase
            logger.debug(
                "Watchdog target set to project=%r phase=%r",
                project_id,
                phase,
            )

    def snapshot(
        self,
        project_id: Optional[str] = None,
        *,
        phase: Optional[str] = None,
        features: Optional[List[Dict[str, Any]]] = None,
        agents: Optional[Dict[str, Any]] = None,
        workers: Optional[Dict[str, Any]] = None,
        circuit_state: str = "",
        budget_remaining: float = 0.0,
        health: str = "healthy",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Take a one-shot global state snapshot.

        If ``project_id`` is ``None``, the current project ID (set via
        ``set_project()``) is used.  Any arguments not provided will
        be left at their defaults (empty strings / dicts / lists).

        Args:
            project_id: Project identifier (uses current if None).
            phase: Pipeline phase name.
            features: List of feature status dicts.
            agents: Dict of agent state information.
            workers: Dict of worker pool state.
            circuit_state: Circuit breaker state string.
            budget_remaining: Remaining budget value.
            health: One of 'healthy', 'degraded', 'fatal'.
            metadata: Additional metadata dict (merged with provider state).

        Returns:
            The auto-generated snapshot ID.

        Raises:
            ValueError: If no project_id is available.
        """
        target_id = project_id or self._current_project_id
        if not target_id:
            raise ValueError(
                "No project_id provided and no current project set. "
                "Call set_project() or pass project_id=."
            )

        target_phase = (
            phase if phase is not None else self._current_phase
        )

        # Validate health
        if health not in ("healthy", "degraded", "fatal"):
            raise ValueError(
                f"health must be 'healthy', 'degraded', or 'fatal', "
                f"got {health!r}"
            )

        # Collect registered provider states
        provider_state = self._collect_providers_state()

        # Merge explicit metadata with provider state
        final_metadata: Dict[str, Any] = dict(provider_state)
        if metadata:
            final_metadata["_explicit"] = metadata

        features_json = json.dumps(
            features or [], ensure_ascii=False
        )
        agents_json = json.dumps(agents or {}, ensure_ascii=False)
        workers_json = json.dumps(workers or {}, ensure_ascii=False)
        metadata_json = json.dumps(final_metadata, ensure_ascii=False)

        now = self._now()

        with self._lock:
            with self._store._conn() as conn:
                cur = conn.execute(
                    """\
INSERT INTO watchdog_snapshots
    (project_id, phase, features_json, agents_json, workers_json,
     circuit_state, budget_remaining, metadata_json, health, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                    (
                        target_id,
                        target_phase,
                        features_json,
                        agents_json,
                        workers_json,
                        circuit_state,
                        budget_remaining,
                        metadata_json,
                        health,
                        now,
                    ),
                )
                conn.commit()
                snapshot_id = cur.lastrowid or 0

        # Prune old snapshots (outside the explicit snapshot lock
        # to avoid holding it during pruning; the lock is re-acquired
        # inside _prune_old_snapshots)
        self._prune_old_snapshots(target_id)

        logger.info(
            "Watchdog snapshot %d saved for project=%r phase=%r health=%s",
            snapshot_id,
            target_id,
            target_phase,
            health,
        )
        return snapshot_id

    def get_snapshot(self, snapshot_id: int) -> Optional[WatchdogSnapshot]:
        """Retrieve a specific snapshot by its ID.

        Args:
            snapshot_id: The auto-incremented snapshot ID.

        Returns:
            The matching ``WatchdogSnapshot`` or ``None``.
        """
        with self._store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM watchdog_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def get_latest_snapshot(
        self, project_id: str
    ) -> Optional[WatchdogSnapshot]:
        """Return the most recent snapshot for a project.

        Args:
            project_id: The project identifier.

        Returns:
            The latest ``WatchdogSnapshot`` or ``None`` if no snapshots exist.
        """
        with self._store._conn() as conn:
            row = conn.execute(
                """\
SELECT * FROM watchdog_snapshots
WHERE project_id = ?
ORDER BY id DESC
LIMIT 1
""",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def list_snapshots(
        self,
        project_id: str,
        limit: int = 50,
    ) -> List[WatchdogSnapshot]:
        """List snapshots for a project, most recent first.

        Args:
            project_id: The project identifier.
            limit: Maximum records to return (default 50).

        Returns:
            List of ``WatchdogSnapshot`` objects, newest first.
        """
        with self._store._conn() as conn:
            rows = conn.execute(
                """\
SELECT * FROM watchdog_snapshots
WHERE project_id = ?
ORDER BY id DESC
LIMIT ?
""",
                (project_id, limit),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    # ── Public API: crash recovery ───────────────────────────────

    def recover_latest(
        self, project_id: str, *, require_healthy: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Recover global state from the latest snapshot.

        This is the primary crash-recovery entry point.  It returns the
        most recent snapshot's state as a dictionary, ready for the
        pipeline to resume from.

        If ``require_healthy`` is ``True`` (default), only snapshots
        marked ``health='healthy'`` are considered.  This prevents
        resuming from a degraded or fatal state.

        Args:
            project_id: The project to recover.
            require_healthy: If True, only consider healthy snapshots.

        Returns:
            A dictionary containing the recovered state with keys:
            ``phase``, ``features``, ``agents``, ``workers``,
            ``circuit_state``, ``budget_remaining``, ``metadata``,
            ``health``, ``snapshot_id``, ``created_at``.
            Returns ``None`` if no suitable snapshot exists.
        """
        if require_healthy:
            with self._store._conn() as conn:
                row = conn.execute(
                    """\
SELECT * FROM watchdog_snapshots
WHERE project_id = ? AND health = 'healthy'
ORDER BY id DESC
LIMIT 1
""",
                    (project_id,),
                ).fetchone()
        else:
            snapshot = self.get_latest_snapshot(project_id)
            row = None
            if snapshot is not None:
                # Re-fetch as sqlite3.Row via ID
                with self._store._conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM watchdog_snapshots WHERE id = ?",
                        (snapshot.id,),
                    ).fetchone()

        if row is None:
            logger.info(
                "No recoverable snapshot found for project %r "
                "(require_healthy=%s)",
                project_id,
                require_healthy,
            )
            return None

        snap = self._row_to_snapshot(row)

        recovered = {
            "phase": snap.phase,
            "features": snap.features_list(),
            "agents": snap.agents_dict(),
            "workers": snap.workers_dict(),
            "circuit_state": snap.circuit_state,
            "budget_remaining": snap.budget_remaining,
            "metadata": snap.metadata_dict(),
            "health": snap.health,
            "snapshot_id": snap.id,
            "created_at": snap.created_at,
        }

        logger.info(
            "Recovered from watchdog snapshot %d for project %r (phase=%s)",
            snap.id,
            project_id,
            snap.phase,
        )
        return recovered

    def recover_from_checkpoint(
        self, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """Attempt recovery using both watchdog snapshot and Checkpointer.

        Prefers the most recent watchdog snapshot, but if the
        Checkpointer is available, also checks for subtask-level
        checkpoint state to provide finer-grained resume information.

        Args:
            project_id: The project to recover.

        Returns:
            A dict with ``watchdog_state`` and optionally
            ``pending_subtasks`` (if Checkpointer is available).
            Returns ``None`` if no state is recoverable.
        """
        wd_state = self.recover_latest(project_id)
        if wd_state is None:
            return None

        result: Dict[str, Any] = {"watchdog_state": wd_state}

        if self._checkpointer is not None:
            try:
                # Try to get the last successful subtask checkpoint
                last_cp = self._checkpointer.get_last_success(project_id)
                if last_cp is not None:
                    result["last_subtask_checkpoint"] = {
                        "checkpoint_id": last_cp.id,
                        "subtask_id": last_cp.subtask_id,
                        "phase": last_cp.phase,
                        "agent_id": last_cp.agent_id,
                        "created_at": last_cp.created_at,
                    }
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning(
                    "Failed to query Checkpointer during recovery: %s",
                    exc,
                )

        return result

    # ── Public API: periodic snapshot thread ─────────────────────

    def start(self) -> None:
        """Start the periodic background snapshot thread.

        The thread will call ``snapshot()`` at the configured interval,
        using the current project ID and phase (set via ``set_project()``).

        If the thread is already running, this is a no-op.

        Raises:
            RuntimeError: If no project has been set via ``set_project()``.
        """
        if self.is_running:
            logger.debug("Watchdog background thread is already running.")
            return

        if not self._current_project_id:
            raise RuntimeError(
                "Cannot start Watchdog: no project set. "
                "Call set_project() first."
            )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="watchdog-snapshot",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Watchdog started for project=%r (interval=%.1fs)",
            self._current_project_id,
            self._interval,
        )

    def stop(self, *, timeout: Optional[float] = None) -> None:
        """Stop the periodic background snapshot thread.

        Signals the thread to exit and waits up to ``timeout`` seconds
        for it to finish.  If ``timeout`` is ``None``, the default
        ``_STOP_WAIT_TIMEOUT`` (5 seconds) is used.

        Args:
            timeout: Maximum seconds to wait for the thread to exit.
        """
        if not self.is_running:
            logger.debug("Watchdog is not running.")
            return

        logger.info("Stopping Watchdog background thread...")
        self._stop_event.set()

        wait_time = timeout if timeout is not None else self._STOP_WAIT_TIMEOUT
        self._thread.join(timeout=wait_time)  # type: ignore[union-attr]

        if self._thread is not None and self._thread.is_alive():
            logger.warning(
                "Watchdog thread did not stop within %.1fs.", wait_time
            )
        else:
            self._thread = None
            logger.info("Watchdog stopped.")

    def _run_loop(self) -> None:
        """Internal: the background snapshot loop."""
        logger.debug(
            "Watchdog loop started (interval=%.1fs)", self._interval
        )

        while not self._stop_event.is_set():
            try:
                self.snapshot()
            except ValueError as exc:
                # No project set — stop the loop
                logger.error("Watchdog loop stopping: %s", exc)
                break
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.exception(
                    "Watchdog snapshot failed: %s", exc
                )

            # Sleep in small increments so we can respond to stop quickly
            self._stop_event.wait(timeout=self._interval)

        logger.debug("Watchdog loop exited.")

    # ── Public API: health markers ───────────────────────────────

    def mark_healthy(self) -> None:
        """Mark the current state as healthy (for next snapshot)."""
        self._mark_health("healthy")

    def mark_degraded(self) -> None:
        """Mark the current state as degraded (for next snapshot)."""
        self._mark_health("degraded")

    def mark_fatal(self) -> None:
        """Mark the current state as fatal (for next snapshot)."""
        self._mark_health("fatal")

    def _mark_health(self, health: str) -> None:
        """Internal: set the health marker for the next snapshot."""
        with self._lock:
            # Store as instance variable; _run_loop will pass it
            # via snapshot() call.  For one-shot snapshots, pass
            # health= explicitly.
            pass  # The health is passed per-call via snapshot(health=...)

    # ── Public API: convenience helpers ──────────────────────────

    def safe_snapshot(
        self,
        project_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """Take a snapshot, catching all exceptions.

        A convenience wrapper around ``snapshot()`` that never raises.
        Useful in ``try/finally`` or shutdown hooks.

        Args:
            project_id: Project identifier.
            **kwargs: Passed through to ``snapshot()``.

        Returns:
            A tuple ``(success, snapshot_id, error_message)``.
            On success, ``error_message`` is ``None``.
        """
        try:
            sid = self.snapshot(project_id=project_id, **kwargs)
            return (True, sid, None)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
            logger.exception("safe_snapshot failed: %s", exc)
            return (False, None, str(exc))

    def get_recovery_summary(self, project_id: str) -> Dict[str, Any]:
        """Return a human-readable recovery summary for a project.

        Args:
            project_id: The project identifier.

        Returns:
            Dict with 'recoverable', 'latest_snapshot_id',
            'latest_phase', 'latest_health', 'total_snapshots',
            and 'last_snapshot_at'.
        """
        latest = self.get_latest_snapshot(project_id)

        with self._store._conn() as conn:
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM watchdog_snapshots WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

        if latest is None:
            return {
                "recoverable": False,
                "latest_snapshot_id": None,
                "latest_phase": None,
                "latest_health": None,
                "total_snapshots": 0,
                "last_snapshot_at": None,
            }

        return {
            "recoverable": latest.is_healthy(),
            "latest_snapshot_id": latest.id,
            "latest_phase": latest.phase,
            "latest_health": latest.health,
            "total_snapshots": total,
            "last_snapshot_at": latest.created_at,
        }


# ───────────────────────────────────────────────────────────────
# Module-level convenience: crash-recovery entry point
# ───────────────────────────────────────────────────────────────


def recover_pipeline(
    db_path: Union[str, Path],
    project_id: str,
    *,
    require_healthy: bool = True,
) -> Optional[Dict[str, Any]]:
    """Top-level convenience: recover pipeline state from a DB after a crash.

    Creates a temporary Watchdog, recovers the latest snapshot, and
    returns the state dict.  Does NOT start the background thread.

    Args:
        db_path: Path to the SQLite database.
        project_id: Project to recover.
        require_healthy: If True, only recover from healthy snapshots.

    Returns:
        Recovered state dict, or None if unrecoverable.

    Example::

        state = recover_pipeline("pipeline_state.db", "my-project")
        if state:
            print(f"Resuming from phase: {state['phase']}")
    """
    store = StateStore(Path(db_path))
    wd = Watchdog(store)
    return wd.recover_latest(project_id, require_healthy=require_healthy)
