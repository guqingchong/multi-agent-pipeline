"""src/audit_trail.py — Event Audit Trail (W5-Q03)

Records every agent action as an AuditEvent and persists to SQLite via
the StateStore audit_logs table. Supports querying by agent, phase, and
time range for compliance auditing and debugging.

Design per PRD section 4.3:
  AuditEvent dataclass:
    agent_id, phase, action, input_summary, output_summary,
    timestamp, duration_ms, result

Persistence: extends the existing audit_logs table with additional
columns (phase, input_summary, output_summary, duration_ms, result)
while maintaining backward compatibility with StateStore's existing
AuditLogRecord-based write_audit_log / list_audit_logs methods.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from state_store import StateStore, AuditLogRecord
except ModuleNotFoundError:
    from src.state_store import StateStore, AuditLogRecord


# ───────────────────────────────────────────────────────────────
# Data Model
# ───────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Records every agent action for compliance auditing.

    Attributes:
        agent_id:  Identifier of the agent that performed the action.
        phase:     Pipeline phase (init/design/decompose/develop/test/accept/deploy).
        action:    Action type, e.g. "code_write", "test_run", "review_submit".
        input_summary:  Brief summary of what was given to the agent.
        output_summary: Brief summary of what the agent produced.
        timestamp: When the action occurred (UTC).
        duration_ms: Duration of the action in milliseconds.
        result:    Outcome: "pass", "fail", or "timeout".
        project_id: Optional project identifier.
        id:        Database-assigned ID (set after persistence).
    """
    agent_id: str
    phase: str
    action: str
    input_summary: str = ""
    output_summary: str = ""
    timestamp: Optional[datetime] = None
    duration_ms: int = 0
    result: str = "pass"          # "pass" / "fail" / "timeout"
    project_id: str = ""
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


# ───────────────────────────────────────────────────────────────
# Extended audit_logs schema (new columns for richer audit data)
# ───────────────────────────────────────────────────────────────

_AUDIT_TRAIL_EXTEND_COLUMNS = [
    ("phase",           "TEXT DEFAULT ''"),
    ("input_summary",   "TEXT DEFAULT ''"),
    ("output_summary",  "TEXT DEFAULT ''"),
    ("duration_ms",     "INTEGER DEFAULT 0"),
    ("event_result",    "TEXT DEFAULT 'pass'"),  # "pass" / "fail" / "timeout"
]


# ───────────────────────────────────────────────────────────────
# AuditTrail — high-level audit recording and query API
# ───────────────────────────────────────────────────────────────

class AuditTrail:
    """Event audit trail backed by the StateStore audit_logs table.

    Usage::

        store = StateStore(db_path)
        trail = AuditTrail(store)

        event = AuditEvent(
            agent_id="claude-code",
            phase="develop",
            action="code_write",
            input_summary="Implement feature X",
            output_summary="Wrote 200 lines in src/foo.py",
            duration_ms=3500,
            result="pass",
            project_id="my_project",
        )
        trail.record_event(event)

        # Query
        events = trail.query(agent_id="claude-code", phase="develop")
        events = trail.query_by_timerange(start_time, end_time)
    """

    def __init__(self, store: StateStore) -> None:
        """Initialize AuditTrail with a StateStore instance.

        Automatically extends the audit_logs table schema if needed.
        """
        self._store = store
        self._ensure_schema()

    # ── Schema management ──────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Open a connection to the same database used by the StateStore."""
        conn = sqlite3.connect(str(self._store.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Extend the audit_logs table with audit trail columns if missing."""
        with self._conn() as conn:
            existing = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()
            }
            for col_name, col_def in _AUDIT_TRAIL_EXTEND_COLUMNS:
                if col_name not in existing:
                    conn.execute(
                        f"ALTER TABLE audit_logs ADD COLUMN {col_name} {col_def}"
                    )
            conn.commit()

    # ── Recording ──────────────────────────────────────────────

    def record_event(self, event: AuditEvent) -> int:
        """Persist an AuditEvent to the database.

        Returns the assigned database row ID.
        """
        timestamp_str = (
            event.timestamp.isoformat() if event.timestamp else None
        )
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_logs
                (project_id, agent, command, allowed, created_at,
                 phase, input_summary, output_summary, duration_ms, event_result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.project_id or "",
                    event.agent_id,
                    event.action,
                    1 if event.result == "pass" else 0,
                    timestamp_str,
                    event.phase,
                    event.input_summary,
                    event.output_summary,
                    event.duration_ms,
                    event.result,
                ),
            )
            conn.commit()
            event.id = cur.lastrowid or 0
            return event.id

    def record(
        self,
        agent_id: str,
        phase: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: int = 0,
        result: str = "pass",
        project_id: str = "",
        timestamp: Optional[datetime] = None,
    ) -> int:
        """Convenience method: create and persist an AuditEvent in one call.

        Returns the assigned database row ID.
        """
        event = AuditEvent(
            agent_id=agent_id,
            phase=phase,
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            duration_ms=duration_ms,
            result=result,
            project_id=project_id,
            timestamp=timestamp,
        )
        return self.record_event(event)

    # ── Querying ───────────────────────────────────────────────

    def _row_to_event(self, row: sqlite3.Row) -> AuditEvent:
        """Convert a database row to an AuditEvent."""
        created = row["created_at"]
        timestamp = None
        if created:
            try:
                timestamp = datetime.fromisoformat(created)
            except (ValueError, TypeError):
                pass

        return AuditEvent(
            id=row["id"],
            project_id=row["project_id"] or "",
            agent_id=row["agent"] or "",
            phase=row["phase"] if "phase" in row.keys() else "",
            action=row["command"] or "",
            input_summary=row["input_summary"] if "input_summary" in row.keys() else "",
            output_summary=row["output_summary"] if "output_summary" in row.keys() else "",
            timestamp=timestamp,
            duration_ms=row["duration_ms"] if "duration_ms" in row.keys() else 0,
            result=row["event_result"] if "event_result" in row.keys() else "pass",
        )

    def _build_query(
        self,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        phase: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        result: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Tuple[str, List[Any]]:
        """Build a parameterized SQL query for filtering audit events."""
        conditions: List[str] = []
        params: List[Any] = []

        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)

        if agent_id is not None:
            conditions.append("agent = ?")
            params.append(agent_id)

        if phase is not None:
            # Handle case where phase column may not exist yet (pre-migration rows)
            conditions.append("phase = ?")
            params.append(phase)

        if start_time is not None:
            conditions.append("created_at >= ?")
            params.append(start_time.isoformat())

        if end_time is not None:
            conditions.append("created_at <= ?")
            params.append(end_time.isoformat())

        if result is not None:
            conditions.append("event_result = ?")
            params.append(result)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT * FROM audit_logs
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        return sql, params

    def query(
        self,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        phase: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        result: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[AuditEvent]:
        """Query audit events by any combination of filters.

        All parameters are optional; combine them to narrow results.
        Results are ordered by most recent first.

        Args:
            project_id: Filter by project.
            agent_id:   Filter by agent identifier.
            phase:      Filter by pipeline phase.
            start_time: Inclusive lower bound on timestamp.
            end_time:   Inclusive upper bound on timestamp.
            result:     Filter by outcome ("pass" / "fail" / "timeout").
            limit:      Maximum number of events to return.
            offset:     Pagination offset.

        Returns:
            List of matching AuditEvent objects.
        """
        sql, params = self._build_query(
            project_id=project_id,
            agent_id=agent_id,
            phase=phase,
            start_time=start_time,
            end_time=end_time,
            result=result,
            limit=limit,
            offset=offset,
        )
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_by_agent(
        self,
        agent_id: str,
        limit: int = 200,
    ) -> List[AuditEvent]:
        """Get all audit events for a specific agent."""
        return self.query(agent_id=agent_id, limit=limit)

    def query_by_phase(
        self,
        phase: str,
        limit: int = 200,
    ) -> List[AuditEvent]:
        """Get all audit events for a specific pipeline phase."""
        return self.query(phase=phase, limit=limit)

    def query_by_timerange(
        self,
        start_time: datetime,
        end_time: datetime,
        limit: int = 200,
    ) -> List[AuditEvent]:
        """Get all audit events within a time range."""
        return self.query(start_time=start_time, end_time=end_time, limit=limit)

    def query_by_result(
        self,
        result: str,
        limit: int = 200,
    ) -> List[AuditEvent]:
        """Get all audit events with a specific result (pass/fail/timeout)."""
        return self.query(result=result, limit=limit)

    def list_events(
        self,
        project_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[AuditEvent]:
        """List the most recent audit events, optionally filtered by project."""
        return self.query(project_id=project_id, limit=limit)

    def count(
        self,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        phase: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        result: Optional[str] = None,
    ) -> int:
        """Count audit events matching the given filters."""
        conditions: List[str] = []
        params: List[Any] = []

        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if agent_id is not None:
            conditions.append("agent = ?")
            params.append(agent_id)
        if phase is not None:
            conditions.append("phase = ?")
            params.append(phase)
        if start_time is not None:
            conditions.append("created_at >= ?")
            params.append(start_time.isoformat())
        if end_time is not None:
            conditions.append("created_at <= ?")
            params.append(end_time.isoformat())
        if result is not None:
            conditions.append("event_result = ?")
            params.append(result)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sql = f"SELECT COUNT(*) FROM audit_logs {where}"
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def get_event(self, event_id: int) -> Optional[AuditEvent]:
        """Retrieve a single audit event by its database ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM audit_logs WHERE id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    # ── Aggregation / Dashboard helpers ────────────────────────

    def summary(
        self,
        project_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Return a summary dict suitable for dashboard display.

        Includes counts by result, phase, and agent.
        """
        base_where = ""
        params: List[Any] = []
        conditions: List[str] = []

        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if since is not None:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())

        if conditions:
            base_where = "WHERE " + " AND ".join(conditions)

        with self._conn() as conn:
            # Total count
            total_row = conn.execute(
                f"SELECT COUNT(*) FROM audit_logs {base_where}", params
            ).fetchone()
            total = total_row[0] if total_row else 0

            # By result
            by_result: Dict[str, int] = {}
            for row in conn.execute(
                f"SELECT event_result, COUNT(*) as cnt FROM audit_logs {base_where} GROUP BY event_result",
                params,
            ).fetchall():
                key = row["event_result"] or "unknown"
                by_result[key] = row["cnt"]

            # By phase
            by_phase: Dict[str, int] = {}
            for row in conn.execute(
                f"SELECT phase, COUNT(*) as cnt FROM audit_logs {base_where} GROUP BY phase",
                params,
            ).fetchall():
                key = row["phase"] or "unknown"
                by_phase[key] = row["cnt"]

            # By agent (top 20)
            by_agent: Dict[str, int] = {}
            for row in conn.execute(
                f"SELECT agent, COUNT(*) as cnt FROM audit_logs {base_where} GROUP BY agent ORDER BY cnt DESC LIMIT 20",
                params,
            ).fetchall():
                key = row["agent"] or "unknown"
                by_agent[key] = row["cnt"]

            # Average duration
            avg_dur_row = conn.execute(
                f"SELECT AVG(duration_ms) FROM audit_logs {base_where}", params
            ).fetchone()
            avg_duration_ms = avg_dur_row[0] if avg_dur_row and avg_dur_row[0] else 0.0

            # Failure rate
            fail_where = base_where
            fail_params = list(params)
            if fail_where.strip():
                fail_where += " AND event_result != 'pass'"
            else:
                fail_where = "WHERE event_result != 'pass'"
            fail_row = conn.execute(
                f"SELECT COUNT(*) FROM audit_logs {fail_where}",
                fail_params,
            ).fetchone()
            fail_count = fail_row[0] if fail_row else 0
            failure_rate = fail_count / total if total > 0 else 0.0

        return {
            "total_events": total,
            "by_result": by_result,
            "by_phase": by_phase,
            "by_agent": by_agent,
            "avg_duration_ms": round(avg_duration_ms, 1),
            "failure_rate": round(failure_rate, 4),
        }


# ───────────────────────────────────────────────────────────────
# Legacy compatibility helpers
# ───────────────────────────────────────────────────────────────

def audit_event_from_legacy(record: AuditLogRecord) -> AuditEvent:
    """Convert a legacy AuditLogRecord to a richer AuditEvent.

    This is a best-effort conversion; fields not present in the legacy
    record (phase, summaries, duration, result) are left at defaults.
    """
    timestamp = None
    if record.created_at:
        try:
            timestamp = datetime.fromisoformat(record.created_at)
        except (ValueError, TypeError):
            pass

    result = "pass" if record.allowed else "fail"

    return AuditEvent(
        id=record.id,
        project_id=record.project_id or "",
        agent_id=record.agent or "",
        phase="",
        action=record.command or "",
        input_summary="",
        output_summary="",
        timestamp=timestamp,
        duration_ms=0,
        result=result,
    )


def audit_event_to_legacy(event: AuditEvent) -> AuditLogRecord:
    """Convert an AuditEvent to a legacy AuditLogRecord.

    Only the fields present in AuditLogRecord are preserved.
    """
    return AuditLogRecord(
        id=event.id,
        project_id=event.project_id,
        agent=event.agent_id,
        command=event.action,
        allowed=(event.result == "pass"),
        created_at=event.timestamp.isoformat() if event.timestamp else None,
    )
