"""src/agent_daemon.py — Agent daemon for multi-agent task execution.

W2-A01 from v3.0 implementation plan:
  - AgentConfig dataclass: agent_id, cli_path, work_dir, max_subtask_timeout(600), max_retries(3), checkpoint_dir
  - AgentDaemon class: __init__(config, mq: Queue), run() main loop
    (pull task → execute with retry → push result → save checkpoint → loop)
  - _execute_with_retry(task) → try up to max_retries, each with timeout, return TaskResult
  - _execute_subtask(task) → calls external CLI subprocess with actual timeout
  - On SHUTDOWN task type → graceful exit
  - No global timeout (only per-subtask timeout)
  - No iteration limit
  - Uses src/queue.py and src/checkpointer.py (already implemented)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ───────────────────────────────────────────────────────────────
# Imports
# ───────────────────────────────────────────────────────────────

from src.queue import Queue, Task

try:
    from src.checkpointer import Checkpointer
except (ModuleNotFoundError, ImportError):
    from checkpointer import Checkpointer

# ───────────────────────────────────────────────────────────────
# Logger
# ───────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Task types
# ───────────────────────────────────────────────────────────────

TASK_TYPE_SHUTDOWN = "shutdown"

# ───────────────────────────────────────────────────────────────
# Data models
# ───────────────────────────────────────────────────────────────


@dataclass
class AgentConfig:
    """Configuration for a single agent daemon.

    Attributes:
        agent_id: Unique identifier for this agent (e.g. 'claude-code', 'codewhale', 'qwen-code').
        cli_path: Absolute path to the external CLI tool executable.
        work_dir: Working directory where the agent operates (default: current directory).
        max_subtask_timeout: Maximum seconds a single subtask execution can run (default: 600).
        max_retries: Maximum retry attempts per subtask on failure (default: 3).
        checkpoint_dir: Directory path for storing checkpoints (default: '.checkpoints').
    """

    agent_id: str
    cli_path: str
    work_dir: str = ""
    max_subtask_timeout: int = 600
    max_retries: int = 3
    checkpoint_dir: str = ".checkpoints"

    def __post_init__(self) -> None:
        if not self.work_dir:
            self.work_dir = str(Path.cwd())
        if not self.checkpoint_dir:
            self.checkpoint_dir = ".checkpoints"


@dataclass
class TaskResult:
    """Result of executing a single subtask.

    Attributes:
        success: Whether the subtask completed successfully.
        output: Captured stdout from the CLI execution.
        error: Error message if the subtask failed or timed out.
        subtask_id: Identifier of the subtask that was executed.
        latency_ms: Wall-clock time in milliseconds for this execution.
        exit_code: Process exit code from the subprocess.
        attempt: Which attempt number produced this result (1-based).
        agent_id: The agent that produced this result.
    """

    success: bool
    output: str = ""
    error: str = ""
    subtask_id: str = ""
    latency_ms: int = 0
    exit_code: int = -1
    attempt: int = 1
    agent_id: str = ""


# ───────────────────────────────────────────────────────────────
# AgentDaemon
# ───────────────────────────────────────────────────────────────


class AgentDaemon:
    """Long-running agent daemon that pulls tasks from a Queue and executes them.

    The daemon runs a continuous loop:
      1. Pull next task for its agent_id from the queue.
      2. Execute the task with retry logic (up to max_retries).
      3. Push the result back to the queue (complete or fail).
      4. Save a checkpoint for the subtask.
      5. Repeat until a SHUTDOWN task is received.

    There is no global timeout and no iteration limit — the daemon runs
    until explicitly shut down via a shutdown task.

    Usage::

        config = AgentConfig(agent_id="claude-code", cli_path="/usr/bin/claude")
        mq = Queue("pipeline.db")
        cp = Checkpointer(db_path="pipeline.db")
        daemon = AgentDaemon(config, mq, checkpointer=cp)
        daemon.run()
    """

    def __init__(
        self,
        config: AgentConfig,
        mq: Queue,
        *,
        checkpointer: Optional[Checkpointer] = None,
    ) -> None:
        """Initialize the agent daemon.

        Args:
            config: Agent configuration (id, CLI path, timeouts, etc.).
            mq: Shared message queue for task dispatch and result reporting.
            checkpointer: Optional checkpointer for persisting subtask state.
        """
        self.config = config
        self.mq = mq
        self.checkpointer = checkpointer
        self._running = False

    # ── run ────────────────────────────────────────────────────

    def run(self) -> None:
        """Main daemon loop.

        Continuously pulls tasks for this agent, executes each one, reports
        results, and saves checkpoints.  Exits gracefully when a SHUTDOWN
        task is received.
        """
        self._running = True
        agent_id = self.config.agent_id

        logger.info(
            "AgentDaemon [%s] starting — cli=%s work_dir=%s timeout=%ds retries=%d",
            agent_id,
            self.config.cli_path,
            self.config.work_dir,
            self.config.max_subtask_timeout,
            self.config.max_retries,
        )

        while self._running:
            task = self._pull_task()
            if task is None:
                # No task available — brief sleep to avoid busy-waiting
                time.sleep(0.5)
                continue

            # Check for shutdown before execution
            if task.task_type == TASK_TYPE_SHUTDOWN:
                logger.info(
                    "AgentDaemon [%s] received SHUTDOWN task (id=%s) — exiting gracefully",
                    agent_id,
                    task.id,
                )
                self.mq.complete_sync(task.id)
                self._running = False
                break

            # Execute with retry
            result = self._execute_with_retry(task)

            # Report result back to the queue
            self._report_result(task, result)

            # Save checkpoint
            self._save_checkpoint(task, result)

        logger.info("AgentDaemon [%s] stopped", agent_id)

    # ── _pull_task ─────────────────────────────────────────────

    def _pull_task(self) -> Optional[Task]:
        """Pull the next queued task for this agent.

        Returns:
            A Task if one was claimed, None if the queue is empty.
        """
        try:
            return self.mq.pull_sync(self.config.agent_id)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
            logger.error(
                "AgentDaemon [%s] failed to pull task: %s",
                self.config.agent_id,
                exc,
            )
            return None

    # ── _execute_with_retry ────────────────────────────────────

    def _execute_with_retry(self, task: Task) -> TaskResult:
        """Execute a task with automatic retry on failure.

        Retries up to ``max_retries`` times.  Each attempt is subject
        to ``max_subtask_timeout``.

        Args:
            task: The task to execute.

        Returns:
            A TaskResult describing the outcome of the (possibly retried) execution.
        """
        max_attempts = min(self.config.max_retries, task.max_retries)
        last_result: Optional[TaskResult] = None

        for attempt in range(1, max_attempts + 1):
            logger.info(
                "AgentDaemon [%s] executing task id=%s type=%s attempt=%d/%d",
                self.config.agent_id,
                task.id,
                task.task_type,
                attempt,
                max_attempts,
            )

            result = self._execute_subtask(task, attempt=attempt)

            if result.success:
                logger.info(
                    "AgentDaemon [%s] task id=%s succeeded on attempt %d (latency=%dms)",
                    self.config.agent_id,
                    task.id,
                    attempt,
                    result.latency_ms,
                )
                return result

            # Record the failure for potential retry
            last_result = result
            logger.warning(
                "AgentDaemon [%s] task id=%s failed on attempt %d: %s",
                self.config.agent_id,
                task.id,
                attempt,
                result.error,
            )

            if attempt < max_attempts:
                # Brief backoff before retry
                time.sleep(1.0 * attempt)

        # All attempts exhausted
        if last_result is None:
            last_result = TaskResult(
                success=False,
                error="No execution attempts completed",
                subtask_id=f"task-{task.id}",
                agent_id=self.config.agent_id,
            )

        logger.error(
            "AgentDaemon [%s] task id=%s failed after %d attempts",
            self.config.agent_id,
            task.id,
            max_attempts,
        )
        return last_result

    # ── _execute_subtask ───────────────────────────────────────

    def _execute_subtask(self, task: Task, *, attempt: int = 1) -> TaskResult:
        """Execute a single subtask by calling the external CLI.

        Args:
            task: The task to execute.
            attempt: The attempt number (1-based, for retry tracking).

        Returns:
            A TaskResult with the execution outcome.
        """
        subtask_id = f"task-{task.id}-attempt-{attempt}"
        start = time.monotonic()

        # Build the CLI command
        cmd = self._build_command(task)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.max_subtask_timeout,
                cwd=self.config.work_dir,
                env=self._build_env(task),
            )

            latency_ms = int((time.monotonic() - start) * 1000)
            output = proc.stdout.strip()
            error_output = proc.stderr.strip() if proc.stderr else ""

            if proc.returncode == 0:
                return TaskResult(
                    success=True,
                    output=output or error_output,
                    error="",
                    subtask_id=subtask_id,
                    latency_ms=latency_ms,
                    exit_code=proc.returncode,
                    attempt=attempt,
                    agent_id=self.config.agent_id,
                )
            else:
                return TaskResult(
                    success=False,
                    output=output,
                    error=error_output or f"CLI exited with code {proc.returncode}",
                    subtask_id=subtask_id,
                    latency_ms=latency_ms,
                    exit_code=proc.returncode,
                    attempt=attempt,
                    agent_id=self.config.agent_id,
                )

        except subprocess.TimeoutExpired as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                success=False,
                output=(exc.stdout or "").strip() if exc.stdout else "",
                error=f"Subtask timed out after {self.config.max_subtask_timeout}s",
                subtask_id=subtask_id,
                latency_ms=latency_ms,
                exit_code=-1,
                attempt=attempt,
                agent_id=self.config.agent_id,
            )

        except FileNotFoundError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                success=False,
                error=f"CLI not found at {self.config.cli_path}: {exc}",
                subtask_id=subtask_id,
                latency_ms=latency_ms,
                exit_code=-1,
                attempt=attempt,
                agent_id=self.config.agent_id,
            )

        except (subprocess.SubprocessError, OSError) as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                success=False,
                error=f"Unexpected error: {exc}",
                subtask_id=subtask_id,
                latency_ms=latency_ms,
                exit_code=-1,
                attempt=attempt,
                agent_id=self.config.agent_id,
            )

    # ── _build_command ─────────────────────────────────────────

    def _build_command(self, task: Task) -> list[str]:
        """Build the CLI command for a task.

        The base command is ``[cli_path, task_type]`` plus context
        arguments derived from the task's context dict.

        Args:
            task: The task to build a command for.

        Returns:
            List of command-line argument strings.
        """
        cmd: list[str] = [self.config.cli_path, task.task_type]

        # Pass context as arguments when available
        ctx = task.context or {}
        if ctx.get("feature_id"):
            cmd.extend(["--feature-id", str(ctx["feature_id"])])
        if ctx.get("project_dir"):
            cmd.extend(["--project-dir", str(ctx["project_dir"])])
        if ctx.get("prompt"):
            cmd.extend(["--prompt", str(ctx["prompt"])])
        if ctx.get("file"):
            cmd.extend(["--file", str(ctx["file"])])
        if ctx.get("output"):
            cmd.extend(["--output", str(ctx["output"])])
        if ctx.get("args"):
            extra_args = ctx["args"]
            if isinstance(extra_args, list):
                cmd.extend([str(a) for a in extra_args])
            else:
                cmd.append(str(extra_args))

        return cmd

    # ── _build_env ─────────────────────────────────────────────

    def _build_env(self, task: Task) -> dict[str, str]:
        """Build environment variables for subprocess execution.

        Merges the current process environment with task-specific overrides.

        Args:
            task: The task whose context may contain env overrides.

        Returns:
            A complete environment dict for the subprocess.
        """
        env = os.environ.copy()

        ctx = task.context or {}
        if ctx.get("env"):
            task_env = ctx["env"]
            if isinstance(task_env, dict):
                env.update({str(k): str(v) for k, v in task_env.items()})

        return env

    # ── _report_result ─────────────────────────────────────────

    def _report_result(self, task: Task, result: TaskResult) -> None:
        """Report the execution result back to the queue.

        On success the task is marked complete.  On failure it is
        marked failed (which triggers the queue's auto-retry logic).

        Args:
            task: The original task that was executed.
            result: The execution result.
        """
        if task.id is None:
            return

        try:
            if result.success:
                self.mq.complete_sync(
                    task.id,
                    result={
                        "output": result.output,
                        "subtask_id": result.subtask_id,
                        "latency_ms": result.latency_ms,
                        "attempt": result.attempt,
                        "agent_id": result.agent_id,
                    },
                )
            else:
                self.mq.fail_sync(task.id, error=result.error)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
            logger.error(
                "AgentDaemon [%s] failed to report result for task id=%s: %s",
                self.config.agent_id,
                task.id,
                exc,
            )

    # ── _save_checkpoint ───────────────────────────────────────

    def _save_checkpoint(self, task: Task, result: TaskResult) -> None:
        """Save a checkpoint after subtask execution.

        Args:
            task: The task that was executed.
            result: The execution result.
        """
        if self.checkpointer is None:
            return

        try:
            outcome = "success" if result.success else "failed"
            self.checkpointer.save(
                task_id=str(task.id or "unknown"),
                subtask_id=result.subtask_id or f"task-{task.id}",
                result=outcome,
                phase=task.task_type,
                state={
                    "output": result.output,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                    "exit_code": result.exit_code,
                    "attempt": result.attempt,
                },
                agent_id=self.config.agent_id,
            )
            logger.debug(
                "AgentDaemon [%s] checkpoint saved for task id=%s outcome=%s",
                self.config.agent_id,
                task.id,
                outcome,
            )
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as exc:
            logger.error(
                "AgentDaemon [%s] failed to save checkpoint for task id=%s: %s",
                self.config.agent_id,
                task.id,
                exc,
            )

    # ── shutdown ───────────────────────────────────────────────

    def request_shutdown(self) -> None:
        """Signal the daemon to stop at the next loop iteration."""
        self._running = False
        logger.info("AgentDaemon [%s] shutdown requested", self.config.agent_id)


# ───────────────────────────────────────────────────────────────
# Convenience helpers
# ───────────────────────────────────────────────────────────────

def create_shutdown_task(agent_id: str) -> Task:
    """Create a shutdown task to gracefully stop an agent daemon.

    Args:
        agent_id: The target agent to shut down.

    Returns:
        A Task with task_type='shutdown' that the daemon will
        interpret as an exit signal.
    """
    return Task(
        target_agent=agent_id,
        task_type=TASK_TYPE_SHUTDOWN,
        priority=2,  # High priority so it's picked up quickly
        max_retries=0,
    )
