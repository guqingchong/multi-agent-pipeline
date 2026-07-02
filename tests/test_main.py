"""tests/test_main.py — Tests for src/main.py FastAPI app."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tmp_base_dir(monkeypatch) -> Generator[Path, None, None]:
    tmpdir = tempfile.mkdtemp(prefix="main_api_test_")
    monkeypatch.setenv("MULTI_AGENT_PIPELINE_BASE_DIR", tmpdir)
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["registry_ready"] is True


def test_status(client: TestClient) -> None:
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "phase_order" in data
    assert "mode" in data


def test_agents(client: TestClient) -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    names = {a["name"] for a in data["agents"]}
    assert "claude-code" in names
    assert "codewhale" in names


def test_queue_stats(client: TestClient) -> None:
    response = client.get("/queue/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "queued" in data
    assert "completed" in data


def test_get_project_not_found(client: TestClient, tmp_base_dir: Path) -> None:
    response = client.get("/projects/nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is False
    assert data["phase"] == "unknown"


def test_get_project_exists(client: TestClient, tmp_base_dir: Path) -> None:
    from state_store import StateStore

    project_name = "demo"
    proj_dir = tmp_base_dir / project_name
    proj_dir.mkdir(parents=True)
    store = StateStore(proj_dir / "pipeline_state.db")
    store.create_project(project_id=project_name, name=project_name, current_phase="init")

    response = client.get(f"/projects/{project_name}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == project_name
    assert data["phase"] == "init"


def test_advance_project_not_found(client: TestClient, tmp_base_dir: Path) -> None:
    response = client.post("/projects/nonexistent/advance")
    assert response.status_code == 404


def test_mock_endpoints_removed(client: TestClient) -> None:
    """Legacy mock endpoints must no longer exist."""
    removed_get = [
        "/finance/calculate",
        "/finance/budget",
        "/knowledge/search",
        "/knowledge/add",
        "/documents/generate",
        "/documents/template",
        "/system/status",
        "/system/config",
    ]
    for path in removed_get:
        response = client.get(path)
        assert response.status_code == 404, f"{path} should return 404"

    # These were POST/PUT mock endpoints; their path now maps to a real
    # parameterized GET route, so the original methods return 404/405.
    assert client.post("/projects/create", json={}).status_code in (404, 405)
    assert client.put("/projects/demo/features", json={}).status_code in (404, 405)
