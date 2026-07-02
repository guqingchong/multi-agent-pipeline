"""Official test fixtures for the multi-agent pipeline."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def set_mock_mode():
    """Enable AGENT_MOCK by default so tests pass without real CLI tools."""
    os.environ.setdefault("AGENT_MOCK", "true")


@pytest.fixture
def fresh_queue(tmp_path):
    """Provide a temporary Queue backed by an isolated SQLite db."""
    from queue import Queue

    db = tmp_path / "queue.db"
    return Queue(db_path=str(db))
