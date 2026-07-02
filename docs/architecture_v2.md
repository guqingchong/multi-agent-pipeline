# multi-agent-pipeline Architecture v2.0

## Overview

This document defines the redesigned architecture for the multi-agent-pipeline system, addressing all identified design flaws and establishing a clean separation of concerns between static configuration, dynamic state, and collaborative tooling.

## Core Principles

1. **Static vs Dynamic Separation**: features.json defines WHAT to build; DB records WHO is building it and HOW MUCH it costs; GitHub tracks the collaboration artifacts.
2. **Wave Ordering is Advisory**: Wave assignments in features.json are planning hints, not enforced constraints. The actual execution order is determined by the orchestrator (Hermes) based on dependency analysis, agent availability, and task complexity.
3. **Agent Communication Resilience**: Primary channel is delegate_task; fallback channels include direct file-based communication and MCP-based messaging.
4. **Approval Flexibility**: Supports both granular L1/L2/L3 review and blanket user authorization for autonomous execution.
5. **Tool Self-Hosting**: The pipeline manages its own development using itself.

## Three-Tool Synergy

### 1. features.json (Static Configuration)

**Role**: Source of truth for project scope, acceptance criteria, and dependency graph.

**Content**:
- Feature definitions (id, title, description)
- Acceptance criteria (type: command/test/assertion/manual)
- Dependency graph (which features block which)
- Wave assignments (planning hints, not enforced)
- Estimated complexity and token budgets
- Owner agent assignments

**Lifecycle**:
- Created during PRD Phase 2 (Decomposition)
- Updated when scope changes (new features, modified acceptance criteria)
- Imported into DB at project initialization
- Exported from DB for backup/archival

**Schema Version**: 2 (added fields: schema_version, generated_by, generation_date, notes)

### 2. pipeline_state.db (Dynamic State)

**Role**: Runtime record of all development activities, costs, and outcomes.

**Tables**:

#### projects
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
current_phase TEXT NOT NULL,
schema_version INTEGER DEFAULT 2,
base_dir TEXT,           -- project root path
features_json_path TEXT, -- path to features.json
github_repo TEXT,        -- GitHub repo URL
created_at TIMESTAMP,
updated_at TIMESTAMP
```

#### features (enhanced)
```sql
id TEXT PRIMARY KEY,
project_id TEXT REFERENCES projects(id),
title TEXT NOT NULL,
description TEXT,
status TEXT CHECK(status IN ('pending','in_progress','completed','reviewed','tested','passed','failed','skipped')),
owner_agent TEXT,
estimated_complexity TEXT,
wave INTEGER,
max_token_budget INTEGER,
token_cost INTEGER DEFAULT 0,
dependencies TEXT,       -- JSON array of feature IDs
acceptance_criteria TEXT, -- JSON array of criteria objects
created_at TIMESTAMP,
updated_at TIMESTAMP
```

#### checkpoints (unchanged)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
project_id TEXT REFERENCES projects(id),
phase TEXT NOT NULL,
feature_id TEXT,
agent TEXT,
action TEXT,
result TEXT,
state_json TEXT NOT NULL,
created_at TIMESTAMP
```

#### traces (unchanged)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
project_id TEXT,
feature_id TEXT,
agent TEXT,
model TEXT,
input_tokens INTEGER,
output_tokens INTEGER,
cost_usd REAL,
latency_ms INTEGER,
status TEXT,
cache_hit BOOLEAN DEFAULT FALSE,
created_at TIMESTAMP
```

#### audit_logs (unchanged)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
project_id TEXT,
agent TEXT,
command TEXT,
allowed BOOLEAN,
created_at TIMESTAMP
```

#### model_health (unchanged)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
model TEXT NOT NULL,
response_time_ms INTEGER,
success BOOLEAN,
error_message TEXT,
created_at TIMESTAMP
```

#### github_sync (new)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
project_id TEXT,
feature_id TEXT,
github_issue_number INTEGER,
github_issue_state TEXT,  -- open/closed
last_sync_at TIMESTAMP,
sync_status TEXT          -- synced/pending/conflict
```

### 3. GitHub (Collaboration Layer)

**Role**: External collaboration and version control integration.

**Integration Points**:
- **Issues**: Each feature auto-creates a GitHub Issue; status bidirectionally syncs with DB
- **PRs**: Code changes link to features; PR merge triggers DB status update
- **Actions**: CI/CD pipeline runs tests; results feed back to DB
- **Projects**: GitHub Project board visualizes feature status from DB

**Sync Mechanism**:
```python
# Two-way sync
class GitHubSync:
    def db_to_github(self, feature_id):
        # Update issue title, body, labels based on DB state
        pass
    
    def github_to_db(self, issue_number):
        # Update DB status when issue is closed/commented
        pass
    
    def sync_all(self):
        # Resolve conflicts: DB wins for status, GitHub wins for comments
        pass
```

## Workflow: Three-Tool Collaboration

### Phase 0: Init
```
1. User: "启动开发流水线"
2. Hermes loads multi-agent-development-pipeline skill
3. pipeline.py init <project_name> --base-dir <path>
4. Creates: project dir, SOUL.md, AGENTS.md, features.json template
5. DB: inserts project record
```

### Phase 1: Design
```
1. Hermes-Research generates architecture.md
2. Human approves (or blanket authorizes)
3. DB: updates project.current_phase = "design"
4. GitHub: creates design discussion issue
```

### Phase 2: Decompose
```
1. Hermes-Research decomposes PRD into features.json
2. Validates: no circular deps, all AC present, schema valid
3. DB: imports features.json into features table
4. GitHub: creates issues for each feature
5. DB: inserts github_sync records
```

### Phase 3: Develop (Wave-based, not enforced)
```
1. Hermes analyzes dependencies, selects next feature
2. bridge_cli.py route code <feature_id> → returns target_agent
3. Hermes delegates to Claude Code (via delegate_task or fallback)
4. Claude Code codes, git commits
5. DB: inserts trace, updates feature.status = "completed"
6. GitHub: updates issue, links commit
7. checkpoint: auto-saved
```

### Phase 4: Review
```
1. bridge_cli.py route review <feature_id> → CodeWhale
2. CodeWhale reviews (via delegate_task or fallback)
3. DB: inserts audit_log, updates feature.status = "reviewed"
4. If P0: returns to Phase 3; if passed: proceeds
```

### Phase 5: Test
```
1. bridge_cli.py route test <feature_id> → Qwen Code
2. Qwen Code tests (via delegate_task or fallback)
3. DB: inserts trace, updates feature.status = "tested"
4. If tests fail: returns to Phase 3
```

### Phase 6: Accept
```
1. Hermes verifies: all AC met, tests pass, no P0
2. If blanket authorized: auto-accept
3. If granular: request human approval
4. DB: updates feature.status = "passed"
5. GitHub: closes issue, links PR
```

### Phase 7: Deploy
```
1. Generate setup.ps1, start.ps1, verify-runtime.ps1
2. Run verify-runtime.ps1
3. DB: updates project.current_phase = "deploy"
4. GitHub: creates release
```

## Agent Communication: Resilient Multi-Channel

### Primary Channel: delegate_task
- **Pros**: Native integration, rich context, tool access
- **Cons**: 600s timeout, 50 iteration limit, Windows path issues

### Fallback Channel 1: File-Based Communication
```python
# When delegate_task times out:
# 1. Hermes writes task spec to .logs/task_<feature_id>.json
# 2. Agent reads spec, executes, writes result to .logs/result_<feature_id>.json
# 3. Hermes polls file for completion

class FileBasedChannel:
    def dispatch(self, agent, task_spec):
        task_path = self.project_dir / ".logs" / f"task_{task_spec['id']}.json"
        task_path.write_text(json.dumps(task_spec))
        return {"channel": "file", "status": "dispatched"}
    
    def collect(self, feature_id, timeout=600):
        result_path = self.project_dir / ".logs" / f"result_{feature_id}.json"
        # Poll with exponential backoff
        for attempt in range(60):  # 10 minutes
            if result_path.exists():
                return json.loads(result_path.read_text())
            time.sleep(10)
        raise TimeoutError(f"Agent did not write result for {feature_id}")
```

### Fallback Channel 2: MCP-Based Messaging
```python
# If MCP server available:
# 1. Hermes sends task via MCP message queue
# 2. Agent receives, processes, replies via MCP
# 3. No timeout (async), but requires MCP infrastructure

class MCPChannel:
    def dispatch(self, agent, task_spec):
        self.mcp_client.send_message(
            recipient=agent,
            message_type="TASK",
            payload=task_spec
        )
    
    def collect(self, feature_id):
        messages = self.mcp_client.poll_messages(
            filter={"feature_id": feature_id, "type": "RESULT"}
        )
        return messages[-1] if messages else None
```

### Channel Selection Strategy
```python
class AgentDispatcher:
    def dispatch(self, agent, task_spec):
        # Try primary
        try:
            return delegate_task(goal=task_spec, toolsets=[...])
        except (TimeoutError, IterationLimitError):
            # Log failure
            self.db.insert_trace(agent, task_spec, status="timeout")
        
        # Try fallback 1
        try:
            return FileBasedChannel().dispatch(agent, task_spec)
        except Exception:
            pass
        
        # Try fallback 2 (if available)
        if self.mcp_available:
            return MCPChannel().dispatch(agent, task_spec)
        
        # Final fallback: Hermes direct execution (with warning)
        raise NoAgentAvailableError("All channels failed. Consider splitting task or manual execution.")
```

## Approval System: Flexible Authorization

### Mode 1: Granular L1/L2/L3 (Default)
```python
class GranularApproval:
    def request_approval(self, artifact, level="L2"):
        # L1: Quick scan (<20 lines)
        # L2: Standard review (20-50 lines)
        # L3: Deep review (>50 lines or high risk)
        return ApprovalRequest(level=level, artifact=artifact)
```

### Mode 2: Blanket Authorization
```python
class BlanketApproval:
    def __init__(self, authorized_by, scope, expires_at):
        self.authorized_by = authorized_by  # User who authorized
        self.scope = scope                # Which phases/features
        self.expires_at = expires_at      # When authorization expires
    
    def is_authorized(self, phase, feature_id):
        if datetime.now() > self.expires_at:
            return False
        if phase not in self.scope.get("phases", []):
            return False
        return True
```

### Integration with Pipeline
```python
# pipeline.py
class Pipeline:
    def __init__(self, approval_mode="granular"):
        if approval_mode == "blanket":
            self.approval = BlanketApproval.load_from_config()
        else:
            self.approval = GranularApproval()
    
    def advance(self, phase):
        if phase in ["design", "accept"]:
            if not self.approval.is_authorized(phase):
                raise ApprovalRequiredError(f"Phase {phase} requires approval")
        # proceed
```

## Exception Hierarchy

```python
class PipelineException(Exception):
    """Base exception for all pipeline errors"""
    pass

class ConfigurationError(PipelineException):
    """Invalid configuration or setup"""
    pass

class ConstraintViolation(PipelineException):
    """Agent role or permission violation"""
    def __init__(self, message, task_type=None, attempted_agent=None, required_agent=None):
        super().__init__(message)
        self.task_type = task_type
        self.attempted_agent = attempted_agent
        self.required_agent = required_agent

class HermesPermissionDenied(ConstraintViolation):
    """Hermes attempted non-orchestration task"""
    pass

class PhaseCheckFailed(PipelineException):
    """Phase check() did not pass"""
    def __init__(self, message, phase=None, blockers=None):
        super().__init__(message)
        self.phase = phase
        self.blockers = blockers or []

class StateStoreError(PipelineException):
    """Database or state persistence error"""
    pass

class AgentUnavailableError(PipelineException):
    """All agent communication channels failed"""
    pass

class ApprovalRequiredError(PipelineException):
    """Human approval needed before proceeding"""
    pass
```

## Configuration Management

```python
# config.py
from pydantic import BaseSettings, Field
from pathlib import Path

class PipelineConfig(BaseSettings):
    # Paths
    base_dir: Path = Field(default=Path.home() / "agent-workspace")
    db_filename: str = "pipeline_state.db"
    
    # Models
    default_model: str = "kimi-k2.6"
    review_model: str = "deepseek-v4-pro"
    test_model: str = "qwen3.7-max"
    
    # Agent Communication
    primary_channel: str = "delegate_task"
    fallback_channels: list = ["file_based", "mcp"]
    task_timeout_seconds: int = 600
    max_retries: int = 3
    
    # Approval
    approval_mode: str = "granular"  # or "blanket"
    blanket_auth_file: Path = Field(default=Path(".blanket_auth"))
    
    # GitHub
    github_token: str = ""
    github_repo: str = ""
    sync_to_github: bool = True
    
    # Limits
    max_token_budget_per_feature: int = 150000
    total_budget_usd: float = 100.0
    
    class Config:
        env_prefix = "PIPELINE_"
        env_file = ".env"
```

## Module Dependency Graph (Post-Refactor)

```
models.py          # Shared data models (Phase, ProjectState, Feature)
    ↑
config.py          # Configuration (no internal deps)
    ↑
state_store.py     # DB operations (depends: models, config)
    ↑
observability.py   # Dashboard/metrics (depends: state_store, models)
    ↑
system_constraint.py  # Role routing (depends: models, config)
    ↑
suggestion_engine.py    # Next phase suggestion (depends: state_store, system_constraint, models)
    ↑
phase_checks.py         # Phase validation (depends: state_store, models)
    ↑
phase_flow.py           # Phase transitions (depends: phase_checks, state_store, models)
    ↑
pipeline.py             # Main orchestrator (depends: phase_flow, state_store, models, config)
    ↑
entry.py                # Auto-load, dashboard (depends: state_store, pipeline, observability, models)
    ↑
bridge_cli.py           # CLI (depends: entry, system_constraint, suggestion_engine, models)
    ↑
adapters.py             # Agent communication (depends: models, config)
    ↑
approval.py             # Approval system (depends: models, config)
    ↑
github_sync.py          # GitHub integration (depends: state_store, models, config)
```

## Testing Strategy

| Module | Test Type | Priority |
|--------|-----------|----------|
| models.py | Unit | P0 |
| config.py | Unit | P0 |
| state_store.py | Unit + Integration | P0 |
| system_constraint.py | Unit | P0 |
| phase_checks.py | Unit | P0 |
| phase_flow.py | Unit | P0 |
| pipeline.py | Integration + E2E | P0 |
| bridge_cli.py | Integration | P0 |
| adapters.py | Unit + Integration | P1 |
| approval.py | Unit | P1 |
| github_sync.py | Integration (mock GitHub API) | P1 |
| observability.py | Unit | P2 |
| suggestion_engine.py | Unit | P2 |

## Migration Plan

### Step 1: Extract models.py
- Move Phase, ProjectState, Feature dataclasses from pipeline.py and state_store.py
- Update all imports
- Verify no circular imports

### Step 2: Add config.py
- Create PipelineConfig with pydantic-settings
- Replace hardcoded constants
- Add environment variable support

### Step 3: Enhance DB Schema
- Add migration script: v1 → v2
- Add columns: wave, dependencies, acceptance_criteria, token_cost
- Create github_sync table
- Import existing features.json into DB

### Step 4: Refactor Agent Communication
- Add FileBasedChannel and MCPChannel classes
- Update adapters.py with channel selection logic
- Add timeout recovery tests

### Step 5: Integrate Approval
- Remove duplicate approval logic from pipeline.py
- Integrate approval.py into phase_flow
- Support both granular and blanket modes

### Step 6: Add GitHub Sync
- Create github_sync.py
- Implement bidirectional sync
- Add webhook handlers

### Step 7: Clean Up
- Remove Chinese comments from core modules
- Add English docstrings
- Update README and DEPLOY

### Step 8: Self-Hosting
- Use pipeline to manage its own development
- Create features.json for v2.0 improvements
- Track progress through DB

## Acceptance Criteria

1. **DB Sync**: `pipeline.py init` imports features.json into DB; `pipeline.py export` exports DB to features.json
2. **GitHub Sync**: Feature status changes in DB automatically update GitHub Issue labels
3. **Agent Fallback**: delegate_task timeout automatically falls back to file-based channel
4. **Approval Flexibility**: User can switch between granular and blanket approval modes
5. **No Circular Imports**: `python -c "import pipeline; import state_store; import models"` succeeds
6. **Config Override**: `PIPELINE_BASE_DIR=/tmp/test pipeline.py init` uses custom path
7. **Tool Self-Hosting**: This document's implementation is tracked through the pipeline itself
