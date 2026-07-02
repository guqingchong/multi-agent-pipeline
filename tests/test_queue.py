"""Tests for src/pipeline_queue.py — unified sync/async task queue."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC))

from src.pipeline_queue import Queue, Task, QueueStats, VALID_STATUSES


# ───────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a fresh SQLite path for a single test."""
    return str(tmp_path / "queue.db")


@pytest.fixture
def queue(db_path: str) -> Queue:
    """Return a fresh Queue instance backed by a temp DB."""
    return Queue(db_path)


# ───────────────────────────────────────────────────────────────
# Sync API tests
# ───────────────────────────────────────────────────────────────


def test_push_pull_complete_sync(queue: Queue) -> None:
    task = Task(target_agent="codewhale", task_type="review", context={"prompt": "review"})
    task_id = queue.push_sync(task)
    assert task_id > 0
    assert task.id == task_id

    claimed = queue.pull_sync("codewhale")
    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.status == "running"
    assert claimed.target_agent == "codewhale"
    assert claimed.task_type == "review"

    ok = queue.complete_sync(task_id, {"output": "lgtm"})
    assert ok is True

    completed = queue.get_task_sync(task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == {"output": "lgtm"}


def test_pull_returns_none_when_empty(queue: Queue) -> None:
    assert queue.pull_sync("codewhale") is None


def test_priority_order_sync(queue: Queue) -> None:
    low = Task(target_agent="agent", task_type="code", priority=0)
    high = Task(target_agent="agent", task_type="code", priority=2)
    normal = Task(target_agent="agent", task_type="code", priority=1)

    queue.push_sync(low)
    queue.push_sync(normal)
    queue.push_sync(high)

    first = queue.pull_sync("agent")
    assert first is not None and first.priority == 2
    second = queue.pull_sync("agent")
    assert second is not None and second.priority == 1
    third = queue.pull_sync("agent")
    assert third is not None and third.priority == 0


def test_fail_and_retry_then_dead_letter_sync(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="code", max_retries=2)
    task_id = queue.push_sync(task)

    # Claim the task
    claimed = queue.pull_sync("agent")
    assert claimed is not None

    # First failure -> re-queued, retry_count=1
    assert queue.fail_sync(task_id, "boom 1") is True
    t1 = queue.get_task_sync(task_id)
    assert t1 is not None
    assert t1.status == "queued"
    assert t1.retry_count == 1
    assert t1.error_message == "boom 1"

    # Second failure -> re-queued, retry_count=2
    claimed2 = queue.pull_sync("agent")
    assert claimed2 is not None
    assert queue.fail_sync(task_id, "boom 2") is True
    t2 = queue.get_task_sync(task_id)
    assert t2 is not None
    assert t2.status == "queued"
    assert t2.retry_count == 2

    # Third failure -> dead_letter (max_retries exhausted)
    claimed3 = queue.pull_sync("agent")
    assert claimed3 is not None
    assert queue.fail_sync(task_id, "boom 3") is True
    t3 = queue.get_task_sync(task_id)
    assert t3 is not None
    assert t3.status == "dead_letter"
    assert t3.error_message == "boom 3"

    # Subsequent fail on dead_letter returns False
    assert queue.fail_sync(task_id, "boom 4") is False


def test_recover_orphaned_sync(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="code")
    task_id = queue.push_sync(task)

    # Simulate a crash: mark the task as running without completing it.
    claimed = queue.pull_sync("agent")
    assert claimed is not None

    recovered = queue.recover_orphaned_sync()
    assert recovered == 1

    t = queue.get_task_sync(task_id)
    assert t is not None
    assert t.status == "queued"
    assert t.started_at is None


def test_task_type_validation_sync(queue: Queue) -> None:
    valid = Task(target_agent="agent", task_type="code")
    assert queue.push_sync(valid) > 0

    invalid = Task(target_agent="agent", task_type="not_a_real_type")
    with pytest.raises(ValueError, match="task_type"):
        queue.push_sync(invalid)


def test_priority_validation_sync(queue: Queue) -> None:
    invalid = Task(target_agent="agent", task_type="code", priority=99)
    with pytest.raises(ValueError, match="priority"):
        queue.push_sync(invalid)


def test_stats_sync(queue: Queue) -> None:
    queue.push_sync(Task(target_agent="a1", task_type="code"))
    queue.push_sync(Task(target_agent="a1", task_type="review"))
    queue.push_sync(Task(target_agent="a2", task_type="code"))

    t1 = queue.pull_sync("a1")
    assert t1 is not None
    queue.complete_sync(t1.id or 0)

    stats = queue.stats_sync()
    assert isinstance(stats, QueueStats)
    assert stats.total == 3
    assert stats.completed == 1
    assert stats.queued == 2
    assert stats.by_agent == {"a1": 2, "a2": 1}
    assert stats.by_type == {"code": 2, "review": 1}


def test_requeue_and_purge_sync(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="code", max_retries=0)
    task_id = queue.push_sync(task)
    claimed = queue.pull_sync("agent")
    assert claimed is not None
    queue.fail_sync(task_id, "dead")

    assert queue.requeue_sync(task_id) is True
    t = queue.get_task_sync(task_id)
    assert t is not None
    assert t.status == "queued"
    assert t.retry_count == 0

    claimed2 = queue.pull_sync("agent")
    assert claimed2 is not None
    queue.complete_sync(task_id)
    assert queue.purge_completed_sync() == 1
    assert queue.get_task_sync(task_id) is None


# ───────────────────────────────────────────────────────────────
# Async API tests
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_dequeue_complete_async(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="test")
    task_id = await queue.enqueue(task)
    assert task_id > 0
    assert task.status == "queued"

    claimed = await queue.dequeue("agent")
    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.status == "running"

    ok = await queue.complete_task(task_id, {"result": "ok"})
    assert ok is True

    completed = await queue.get_task(task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == {"result": "ok"}


@pytest.mark.asyncio
async def test_fail_task_async(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="test", max_retries=1)
    task_id = await queue.enqueue(task)
    await queue.dequeue("agent")

    ok = await queue.fail_task(task_id, "err")
    assert ok is True

    t = await queue.get_task(task_id)
    assert t is not None
    assert t.status == "queued"
    assert t.retry_count == 1

    await queue.dequeue("agent")
    ok2 = await queue.fail_task(task_id, "err2")
    assert ok2 is True

    t2 = await queue.get_task(task_id)
    assert t2 is not None
    assert t2.status == "dead_letter"


@pytest.mark.asyncio
async def test_recover_orphaned_async(queue: Queue) -> None:
    task = Task(target_agent="agent", task_type="test")
    task_id = await queue.enqueue(task)
    await queue.dequeue("agent")

    count = await queue.recover_orphaned()
    assert count == 1

    t = await queue.get_task(task_id)
    assert t is not None
    assert t.status == "queued"


@pytest.mark.asyncio
async def test_stats_async(queue: Queue) -> None:
    await queue.enqueue(Task(target_agent="agent", task_type="test"))
    await queue.enqueue(Task(target_agent="agent", task_type="code"))
    claimed = await queue.dequeue("agent")
    assert claimed is not None
    await queue.complete_task(claimed.id or 0)

    stats = await queue.stats()
    assert isinstance(stats, QueueStats)
    assert stats.total == 2
    assert stats.completed == 1
    assert stats.queued == 1
    assert stats.by_type.get("test") == 1
    assert stats.by_type.get("code") == 1


@pytest.mark.asyncio
async def test_callbacks_async(queue: Queue) -> None:
    events: List[Dict[str, Any]] = []

    def capture(task: Task) -> None:
        events.append({"id": task.id, "status": task.status})

    for status in VALID_STATUSES:
        queue.on(status, capture)

    task = Task(target_agent="agent", task_type="test", max_retries=0)
    task_id = await queue.enqueue(task)
    assert any(e["id"] == task_id and e["status"] == "queued" for e in events)

    claimed = await queue.dequeue("agent")
    assert claimed is not None
    assert any(e["id"] == task_id and e["status"] == "running" for e in events)

    await queue.complete_task(task_id)
    assert any(e["id"] == task_id and e["status"] == "completed" for e in events)


@pytest.mark.asyncio
async def test_callback_error_isolated(queue: Queue) -> None:
    def bad_cb(_task: Task) -> None:
        raise RuntimeError("boom")

    queue.on("queued", bad_cb)

    task = Task(target_agent="agent", task_type="test")
    # Should not propagate the callback exception
    task_id = await queue.enqueue(task)
    assert task_id > 0


@pytest.mark.asyncio
async def test_close_prevents_async_use(queue: Queue) -> None:
    queue.close()
    with pytest.raises(RuntimeError, match="closed"):
        await queue.enqueue(Task(target_agent="agent", task_type="test"))


@pytest.mark.asyncio
async def test_async_context_manager(queue: Queue) -> None:
    async with queue as q:
        task = Task(target_agent="agent", task_type="test")
        task_id = await q.enqueue(task)
        assert task_id > 0


# ───────────────────────────────────────────────────────────────
# Default path resolution
# ───────────────────────────────────────────────────────────────


def test_explicit_db_path() -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
        path = tf.name
    try:
        q = Queue(db_path=path)
        assert q.db_path == path
        q.push_sync(Task(target_agent="agent", task_type="code"))
    finally:
        os.unlink(path)


def test_default_db_path_is_string() -> None:
    q = Queue()
    assert isinstance(q.db_path, str)
    assert q.db_path.endswith("message_queue.db")


def test_flat_import_regression() -> None:
    """Importing src.pipeline_queue from the project root resolves our Queue,
    while ``import queue`` resolves the standard library module."""
    root = str(PROJECT_ROOT)
    original_path = sys.path[:]
    original_modules = dict(sys.modules)
    try:
        # Clear any cached src.pipeline_queue import so the next import uses the
        # project-root path we inject.
        for mod_name in list(sys.modules):
            if mod_name == "src" or mod_name.startswith("src."):
                del sys.modules[mod_name]
        sys.path.insert(0, root)
        import src.pipeline_queue as queue_mod
        import queue as stdlib_queue

        assert hasattr(queue_mod, "Queue")
        assert queue_mod.Queue is not stdlib_queue.Queue
        assert queue_mod.Queue.__module__ == "src.pipeline_queue"
    finally:
        sys.path[:] = original_path
        sys.modules.clear()
        sys.modules.update(original_modules)


def test_pending_count_semantics(queue: Queue) -> None:
    """pending_count_sync(agent_id) totals all tasks; without agent counts queued."""
    queue.push_sync(Task(target_agent="a1", task_type="code"))
    queue.push_sync(Task(target_agent="a1", task_type="review"))
    queue.push_sync(Task(target_agent="a2", task_type="code"))

    claimed = queue.pull_sync("a1")
    assert claimed is not None

    assert queue.pending_count_sync("a1") == 2
    assert queue.pending_count_sync() == 2

    queue.complete_sync(claimed.id or 0)
    # a1 still has one completed + one queued task
    assert queue.pending_count_sync("a1") == 2
    assert queue.pending_count_sync() == 2

    queue.purge_completed_sync()
    assert queue.pending_count_sync("a1") == 1
    assert queue.pending_count_sync() == 2


@pytest.mark.asyncio
async def test_pending_count_async(queue: Queue) -> None:
    await queue.enqueue(Task(target_agent="a1", task_type="code"))
    await queue.enqueue(Task(target_agent="a2", task_type="code"))
    assert await queue.pending_count("a1") == 1
    assert await queue.pending_count() == 2
