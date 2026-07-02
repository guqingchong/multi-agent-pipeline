"""src/main.py — FastAPI entry point for the multi-agent pipeline.

Exposes the core real endpoints backed by REGISTRY, Queue, StateStore and
PhaseFlow.  Legacy mock endpoints have been removed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# Ensure src/ is importable when running uvicorn from project root
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from registry import REGISTRY
from config import get_config
from state_store import StateStore
from phase_flow import PhaseFlow
import src.queue as queue_mod


app = FastAPI(
    title="Multi-Agent Pipeline API",
    description="Registry-driven multi-agent pipeline API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ───────────────────────────────────────────────────────────────
# Pydantic request/response models
# ───────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    agent_mock: bool
    registry_ready: bool


class AgentInfo(BaseModel):
    name: str
    capabilities: List[str]
    cli_path: str


class AgentsResponse(BaseModel):
    agents: List[AgentInfo]


class QueueStatsResponse(BaseModel):
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    dead_letter: int
    by_agent: Dict[str, int]
    by_type: Dict[str, int]


class ProjectStatusResponse(BaseModel):
    name: str
    exists: bool
    current_phase: str


class AdvanceRequest(BaseModel):
    approved: bool = True


class AdvanceResponse(BaseModel):
    success: bool
    message: str
    phase: Optional[str] = None


# ───────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────

def _base_dir() -> Path:
    env_base = os.environ.get("MULTI_AGENT_PIPELINE_BASE_DIR")
    if env_base:
        return Path(env_base)
    return get_config().base_dir


def _project_dir(name: str) -> Path:
    return _base_dir() / name


def _queue() -> queue_mod.Queue:
    db_path = _base_dir() / "pipeline_queue.db"
    return queue_mod.Queue(str(db_path))


# ───────────────────────────────────────────────────────────────
# Core endpoints
# ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check including registry readiness."""
    return HealthResponse(
        status="ok",
        agent_mock=os.environ.get("AGENT_MOCK", "false").lower() == "true",
        registry_ready=REGISTRY.is_ready(),
    )


@app.get("/status")
async def status():
    """Pipeline status summary."""
    return {
        "status": "ok",
        "mode": get_config().pipeline_mode,
        "phase_order": get_config().phase_order,
        "registry_ready": REGISTRY.is_ready(),
    }


@app.get("/agents", response_model=AgentsResponse)
async def list_agents():
    """List registered agents from REGISTRY."""
    agents = []
    for name, agent_def in REGISTRY.agents.items():
        agents.append(AgentInfo(
            name=name,
            capabilities=list(agent_def.capabilities),
            cli_path=agent_def.cli_path,
        ))
    return AgentsResponse(agents=agents)


@app.get("/queue/stats", response_model=QueueStatsResponse)
async def queue_stats():
    """Return unified queue statistics."""
    queue = _queue()
    stats = queue.stats_sync()
    return QueueStatsResponse(
        total=stats.total,
        queued=stats.queued,
        running=stats.running,
        completed=stats.completed,
        failed=stats.failed,
        dead_letter=stats.dead_letter,
        by_agent=stats.by_agent,
        by_type=stats.by_type,
    )


@app.get("/projects/{name}", response_model=ProjectStatusResponse)
async def get_project(name: str):
    """Get project status from StateStore."""
    project_dir = _project_dir(name)
    db_path = get_config().db_path(project_dir)

    if not db_path.exists():
        return ProjectStatusResponse(name=name, exists=False, current_phase="unknown")

    store = StateStore(db_path)
    record = store.get_project(name)
    phase = record.current_phase if record else "unknown"
    return ProjectStatusResponse(name=name, exists=True, current_phase=phase)


@app.post("/projects/{name}/advance", response_model=AdvanceResponse)
async def advance_project(name: str, request: AdvanceRequest):
    """Advance project to next phase using PhaseFlow."""
    project_dir = _project_dir(name)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    flow = PhaseFlow(name, _base_dir())
    success, message = flow.advance()
    return AdvanceResponse(
        success=success,
        message=message,
        phase=flow.current_phase(),
    )


# ───────────────────────────────────────────────────────────────
# Application entry point
# ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
