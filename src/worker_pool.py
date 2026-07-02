"""src/worker_pool.py — Worker pool for multi-agent process management.

W2-A02 from v3.0 implementation plan:
  - WorkerStatus dataclass: agent_id, pid, state (idle/busy/dead),
    current_task_id, last_heartbeat, tasks_completed.
  - WorkerPool class: start_agent(agent_id, count) → spawns N agent_daemon
    processes via subprocess.Popen; health_check() → returns dead agents
    needing restart; stop_all() → sends SHUTDOWN to all.
  - Tracks PIDs and provides graceful + force-termination.

Depends on W2-A01 (agent_daemon.py) and W2-A03 (message_queue.py).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

# ───────────────────────────────────────────────────────────────
# Dual-import pattern (package / flat)
# ───────────────────────────────────────────────────────────────

try:
    from agent_daemon import (  # type: ignore[import-not-found]
        AgentConfig,
        TASK_TYPE_SHUTDOWN,
        create_shutdown_task,
    )
    from message_queue import MessageQueue, Task  # type: ignore[import-not-found]
except ImportError:
    from src.agent_daemon import AgentConfig, TASK_TYPE_SHUTDOWN, create_shutdown_task
    from src.message_queue import MessageQueue, Task

# ───────────────────────────────────────────────────────────────
# Logger
# ───────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────

_GRACEFUL_SHUTDOWN_TIMEOUT = 10  # seconds to wait for graceful exit
_SIGTERM_TIMEOUT = 3             # seconds to wait after SIGTERM
_FORCE_KILL_TIMEOUT = 2          # seconds to wait after force-kill

# ───────────────────────────────────────────────────────────────
# Agent subprocess entry point (inline for independent execution)
# ───────────────────────────────────────────────────────────────

# This script is injected into each spawned subprocess.  It reads
# configuration from environment variables and runs the AgentDaemon
# main loop until a SHUTDOWN task is received.

_AGENT_ENTRY_SCRIPT = r"""
import json, os, sys, logging, time
from pathlib import Path

# Reconstruct sys.path so we can import project modules
proj_dir = os.environ["PIPELINE_PROJ_DIR"]
if proj_dir and proj_dir not in sys.path:
    sys.path.insert(0, proj_dir)

from agent_daemon import AgentDaemon, AgentConfig, TASK_TYPE_SHUTDOWN
from message_queue import MessageQueue

agent_id       = os.environ["PIPELINE_AGENT_ID"]
cli_path       = os.environ.get("PIPELINE_CLI_PATH", "")
work_dir       = os.environ.get("PIPELINE_WORK_DIR", str(Path.cwd()))
max_timeout    = int(os.environ.get("PIPELINE_MAX_TIMEOUT", "600"))
max_retries    = int(os.environ.get("PIPELINE_MAX_RETRIES", "3"))
db_path        = os.environ.get("PIPELINE_DB_PATH", "message_queue.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

config = AgentConfig(
    agent_id=agent_id,
    cli_path=cli_path,
    work_dir=work_dir,
    max_subtask_timeout=max_timeout,
    max_retries=max_retries,
)
mq = MessageQueue(db_path=db_path)
daemon = AgentDaemon(config, mq)

logger = logging.getLogger("worker_pool.agent")
logger.info("Agent subprocess [%s] starting (pid=%d)", agent_id, os.getpid())
daemon.run()
logger.info("Agent subprocess [%s] exiting (pid=%d)", agent_id, os.getpid())
"""

# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────


@dataclass
class WorkerStatus:
    """Live status of a single worker process.

    Attributes:
        agent_id: Logical agent id this worker belongs to.
        instance_name: Unique per-process label (e.g. 'claude-code#0').
        pid: OS process id of the spawned subprocess.
        state: One of 'idle', 'busy', or 'dead'.
        current_task_id: Task id currently being processed (if known).
        last_heartbeat: Monotonic timestamp of last health probe.
        tasks_completed: Cumulative count of finished tasks.
    """

    agent_id: str
    instance_name: str = ""
    pid: int = -1
    state: str = "idle"  # idle | busy | dead
    current_task_id: Optional[str] = None
    last_heartbeat: float = 0.0
    tasks_completed: int = 0

    def __post_init__(self) -> None:
        if self.instance_name == "":
            self.instance_name = self.agent_id
        if self.last_heartbeat == 0.0:
            self.last_heartbeat = time.monotonic()
        # Validate state
        if self.state not in ("idle", "busy", "dead"):
            raise ValueError(f"Invalid state: {self.state!r}")


# ───────────────────────────────────────────────────────────────
# WorkerPool
# ───────────────────────────────────────────────────────────────


class WorkerPool:
    """Manages a pool of agent subprocesses.

    Provides lifecycle management (start / health-check / stop) for
    multiple agent-daemon processes that share a single MessageQueue.

    Typical usage::

        mq = MessageQueue("pipeline.db")
        pool = WorkerPool(mq)
        pool.start_agent("claude-code", count=3)
        # ... run workloads ...
        dead = pool.health_check()
        for ws in dead:
            pool.restart_worker(ws)
        pool.stop_all()
    """

    def __init__(
        self,
        mq: MessageQueue,
        *,
        project_dir: Optional[str] = None,
    ) -> None:
        """Initialise a WorkerPool.

        Args:
            mq: Shared message queue used by all agent subprocesses.
            project_dir: Absolute path to the project root (so that
                spawned subprocesses can import src.* modules).
                Defaults to the parent of the directory containing this file.
        """
        self.mq = mq
        self._project_dir = project_dir or str(
            Path(__file__).resolve().parent.parent
        )

        # agent_id → list of WorkerStatus
        self._workers: Dict[str, List[WorkerStatus]] = {}
        # pid → subprocess.Popen handle
        self._processes: Dict[int, subprocess.Popen[str]] = {}

    # ── start_agent ─────────────────────────────────────────────

    def start_agent(
        self,
        agent_id: str,
        count: int = 1,
        *,
        cli_path: str = "",
        work_dir: str = "",
        max_subtask_timeout: int = 600,
        max_retries: int = 3,
        db_path: Optional[str] = None,
    ) -> List[WorkerStatus]:
        """Spawn *count* agent-daemon subprocesses for *agent_id*.

        Each subprocess runs an independent ``AgentDaemon`` instance that
        pulls tasks from the shared ``MessageQueue``.

        Args:
            agent_id: Logical agent identifier (must match task target_agent).
            count: Number of worker processes to spawn (default 1).
            cli_path: Path to the external CLI tool (passed to AgentConfig).
            work_dir: Working directory for the agent subprocess.
            max_subtask_timeout: Per-subtask timeout in seconds (default 600).
            max_retries: Max retry attempts per subtask (default 3).
            db_path: Path to the SQLite message-queue database.
                Defaults to ``self.mq.db_path``.

        Returns:
            List of WorkerStatus objects for the newly spawned processes.

        Raises:
            ValueError: If *count* < 1.
            RuntimeError: If any subprocess fails to start.
        """
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")

        db_path = db_path or getattr(self.mq, "db_path", "message_queue.db")
        work_dir = work_dir or str(Path.cwd())

        started: List[WorkerStatus] = []

        # Track max instance number to avoid duplicates on restart
        existing_workers = self._workers.get(agent_id, [])
        max_num = -1
        for w in existing_workers:
            try:
                num = int(w.instance_name.rsplit("#", 1)[-1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass

        for i in range(count):
            instance_num = max_num + 1 + i
            instance_name = f"{agent_id}#{instance_num}"

            # Build the subprocess environment
            env = os.environ.copy()
            env.update(
                {
                    "PIPELINE_PROJ_DIR": self._project_dir,
                    "PIPELINE_AGENT_ID": agent_id,
                    "PIPELINE_CLI_PATH": cli_path,
                    "PIPELINE_WORK_DIR": work_dir,
                    "PIPELINE_MAX_TIMEOUT": str(max_subtask_timeout),
                    "PIPELINE_MAX_RETRIES": str(max_retries),
                    "PIPELINE_DB_PATH": db_path,
                    "PYTHONUNBUFFERED": "1",
                }
            )

            try:
                proc = subprocess.Popen(
                    [sys.executable, "-c", _AGENT_ENTRY_SCRIPT],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=work_dir,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.error(
                    "WorkerPool failed to spawn %s: %s", instance_name, exc
                )
                raise RuntimeError(
                    f"Failed to spawn agent subprocess {instance_name}: {exc}"
                ) from exc

            status = WorkerStatus(
                agent_id=agent_id,
                instance_name=instance_name,
                pid=proc.pid,
                state="idle",
                last_heartbeat=time.monotonic(),
            )

            # Track
            self._workers.setdefault(agent_id, []).append(status)
            self._processes[proc.pid] = proc

            logger.info(
                "WorkerPool spawned %s (pid=%d, agent_id=%s, #%d/%d)",
                instance_name,
                proc.pid,
                agent_id,
                i + 1,
                count,
            )

            started.append(status)

        return started

    # ── health_check ────────────────────────────────────────────

    def health_check(self) -> List[WorkerStatus]:
        """Check liveness of all tracked worker processes.

        For each tracked PID, calls ``poll()`` on the subprocess handle.
        Processes that have exited (``poll()`` is not ``None``) are
        marked ``dead`` and returned.

        Returns:
            List of *dead* WorkerStatus entries that need restarting.
        """
        dead: List[WorkerStatus] = []
        now = time.monotonic()

        for _agent_id, workers in list(self._workers.items()):
            for ws in workers:
                if ws.state == "dead":
                    continue

                proc = self._processes.get(ws.pid)
                if proc is None:
                    # Process handle lost — assume dead
                    ws.state = "dead"
                    dead.append(ws)
                    logger.warning(
                        "WorkerPool %s (pid=%d) has no process handle — marking dead",
                        ws.instance_name,
                        ws.pid,
                    )
                    continue

                exit_code = proc.poll()
                if exit_code is not None:
                    # Process exited
                    ws.state = "dead"
                    dead.append(ws)
                    logger.warning(
                        "WorkerPool %s (pid=%d) exited with code %d",
                        ws.instance_name,
                        ws.pid,
                        exit_code,
                    )
                else:
                    # Still alive — update heartbeat
                    ws.last_heartbeat = now

        return dead

    # ── restart_worker ──────────────────────────────────────────

    def restart_worker(self, ws: WorkerStatus) -> Optional[WorkerStatus]:
        """Restart a single dead worker.

        Removes the old WorkerStatus and subprocess handle, then spawns a
        fresh replacement with the same *agent_id*.

        Args:
            ws: A dead WorkerStatus (typically from ``health_check()``).

        Returns:
            The new WorkerStatus if restart succeeded, or None if the
            worker was not dead.
        """
        if ws.state != "dead":
            logger.info(
                "WorkerPool %s is not dead (state=%s) — skipping restart",
                ws.instance_name,
                ws.state,
            )
            return None

        # Clean up old entries
        self._processes.pop(ws.pid, None)
        agent_workers = self._workers.get(ws.agent_id, [])
        if ws in agent_workers:
            agent_workers.remove(ws)

        logger.info(
            "WorkerPool restarting %s (old pid=%d)", ws.instance_name, ws.pid
        )

        # Spawn a single replacement
        new_workers = self.start_agent(
            agent_id=ws.agent_id,
            count=1,
        )
        if new_workers:
            return new_workers[0]
        return None

    # ── stop_all ────────────────────────────────────────────────

    def stop_all(self) -> None:
        """Gracefully shut down all tracked workers.

        Strategy:
        1. Push a SHUTDOWN task into the queue for every agent_id.
        2. Wait up to ``_GRACEFUL_SHUTDOWN_TIMEOUT`` seconds for each
           process to exit naturally.
        3. Send SIGTERM to any remaining processes.
        4. Force-kill (``proc.kill()``) any stragglers.
        """
        if not self._workers:
            logger.info("WorkerPool has no workers to stop")
            return

        # Phase 1 — enqueue shutdown tasks
        for agent_id in list(self._workers.keys()):
            try:
                shutdown_task = create_shutdown_task(agent_id)
                self.mq.push(shutdown_task)
                logger.info(
                    "WorkerPool enqueued SHUTDOWN for agent_id=%s (task_id=%s)",
                    agent_id,
                    shutdown_task.id,
                )
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.error(
                    "WorkerPool failed to enqueue SHUTDOWN for %s: %s",
                    agent_id,
                    exc,
                )

        # Phase 2 — wait for graceful exit
        _wait_for_exit(
            self._processes,
            timeout=_GRACEFUL_SHUTDOWN_TIMEOUT,
            label="graceful",
        )

        # Phase 3 — SIGTERM any survivors
        remaining = _send_signal_to_active(self._processes, signal.SIGTERM)
        if remaining:
            logger.warning(
                "WorkerPool %d worker(s) still alive after SIGTERM — waiting %ds",
                remaining,
                _SIGTERM_TIMEOUT,
            )
            _wait_for_exit(
                self._processes, timeout=_SIGTERM_TIMEOUT, label="SIGTERM"
            )

        # Phase 4 — force-kill holdouts (proc.kill() is cross-platform)
        remaining = _kill_active(self._processes)
        if remaining:
            logger.error(
                "WorkerPool %d worker(s) required force-kill", remaining
            )
            _wait_for_exit(
                self._processes, timeout=_FORCE_KILL_TIMEOUT, label="force-kill"
            )

        # Clean up tracking structures
        self._workers.clear()
        self._processes.clear()
        logger.info("WorkerPool shutdown complete")

    # ── list_workers ────────────────────────────────────────────

    def list_workers(
        self, agent_id: Optional[str] = None
    ) -> List[WorkerStatus]:
        """Return current status of all (or filtered) workers.

        Args:
            agent_id: If set, only return workers for this agent.

        Returns:
            Flat list of WorkerStatus entries.
        """
        # Refresh liveness first
        self.health_check()

        result: List[WorkerStatus] = []
        for aid, workers in self._workers.items():
            if agent_id is not None and aid != agent_id:
                continue
            result.extend(workers)
        return result

    # ── active_count / dead_count ───────────────────────────────

    def active_count(self, agent_id: Optional[str] = None) -> int:
        """Count workers that are not dead."""
        workers = self.list_workers(agent_id=agent_id)
        return sum(1 for w in workers if w.state != "dead")

    def dead_count(self, agent_id: Optional[str] = None) -> int:
        """Count dead workers."""
        workers = self.list_workers(agent_id=agent_id)
        return sum(1 for w in workers if w.state == "dead")


# ───────────────────────────────────────────────────────────────
# Internal helpers
# ───────────────────────────────────────────────────────────────


def _wait_for_exit(
    processes: Dict[int, subprocess.Popen[str]],
    timeout: float,
    label: str = "",
) -> None:
    """Block until all processes exit, up to *timeout* seconds.

    Args:
        processes: PID → Popen mapping (modified in-place).
        timeout: Maximum seconds to wait.
        label: Human-readable label for log messages.
    """
    if not processes:
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Poll all processes; remove those that have exited
        exited: Set[int] = set()
        for pid, proc in list(processes.items()):
            if proc.poll() is not None:
                exited.add(pid)
        for pid in exited:
            processes.pop(pid, None)

        if not processes:
            logger.info("WorkerPool all processes exited (%s)", label)
            return
        time.sleep(0.2)

    logger.info(
        "WorkerPool %d process(es) still alive after %s phase (%.1fs)",
        len(processes),
        label,
        timeout,
    )


def _send_signal_to_active(
    processes: Dict[int, subprocess.Popen[str]],
    sig: int,
) -> int:
    """Send *sig* to every still-running process in *processes*.

    Returns the number of processes that were signalled (i.e. still alive).
    """
    count = 0
    for pid, proc in list(processes.items()):
        if proc.poll() is None:
            try:
                proc.send_signal(sig)
                count += 1
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning(
                    "WorkerPool failed to send signal %d to pid=%d: %s",
                    sig,
                    pid,
                    exc,
                )
    return count


def _kill_active(
    processes: Dict[int, subprocess.Popen[str]],
) -> int:
    """Force-kill every still-running process (cross-platform).

    Uses ``proc.kill()`` which maps to TerminateProcess on Windows
    and SIGKILL on Unix.  Returns the count of processes that were
    still alive before the kill.
    """
    count = 0
    for pid, proc in list(processes.items()):
        if proc.poll() is None:
            try:
                proc.kill()
                count += 1
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
                logger.warning(
                    "WorkerPool failed to kill pid=%d: %s", pid, exc
                )
    return count
