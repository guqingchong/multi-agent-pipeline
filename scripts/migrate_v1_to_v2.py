"""scripts/migrate_v1_to_v2.py — One-time migration for legacy v1 state databases.

Usage:
    python scripts/migrate_v1_to_v2.py <path/to/pipeline_state.db>

This script performs the schema changes that older versions of
``state_store.py`` applied automatically.  Run it once per legacy project
before opening the database with the simplified v2 ``StateStore``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


V2_FEATURES_COLUMNS = {
    "owner_agent": "TEXT",
    "token_cost": "INTEGER DEFAULT 0",
    "wave": "INTEGER DEFAULT 0",
    "dependencies_json": "TEXT DEFAULT '[]'",
    "acceptance_criteria_json": "TEXT DEFAULT '[]'",
    "github_issue_number": "INTEGER",
    "sync_status": "TEXT DEFAULT 'unsynced'",
    "created_at": "TEXT DEFAULT '1970-01-01T00:00:00Z'",
    "updated_at": "TEXT DEFAULT '1970-01-01T00:00:00Z'",
}


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("PRAGMA table_info(features)")
        columns = {row[1] for row in cursor.fetchall()}

        for col_name, col_def in V2_FEATURES_COLUMNS.items():
            if col_name not in columns:
                conn.execute(f"ALTER TABLE features ADD COLUMN {col_name} {col_def}")
                print(f"Added features.{col_name}")

        # Add CHECK triggers for sync_status now that the column is guaranteed to exist.
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS features_sync_status_check_insert
            BEFORE INSERT ON features
            BEGIN
                SELECT CASE
                    WHEN NEW.sync_status NOT IN ('unsynced','syncing','synced','failed')
                    THEN RAISE(ABORT, 'Invalid sync_status')
                END;
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS features_sync_status_check_update
            BEFORE UPDATE ON features
            BEGIN
                SELECT CASE
                    WHEN NEW.sync_status NOT IN ('unsynced','syncing','synced','failed')
                    THEN RAISE(ABORT, 'Invalid sync_status')
                END;
            END;
        """)
        print("Ensured features.sync_status CHECK triggers")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'")
        if cursor.fetchone() is not None:
            cursor = conn.execute("PRAGMA table_info(audit_logs)")
            audit_columns = {row[1] for row in cursor.fetchall()}
            if "phase" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN phase TEXT")
                print("Added audit_logs.phase")
            if "event" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN event TEXT")
                print("Added audit_logs.event")
            if "details_json" not in audit_columns:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN details_json TEXT DEFAULT '{}'")
                print("Added audit_logs.details_json")
        else:
            print("audit_logs table not present; skipping audit_logs migration")

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_history'")
        if cursor.fetchone() is not None:
            cursor = conn.execute("PRAGMA table_info(dispatch_history)")
            dispatch_columns = {row[1] for row in cursor.fetchall()}
            if "project_id" not in dispatch_columns:
                conn.execute("ALTER TABLE dispatch_history ADD COLUMN project_id TEXT")
                print("Added dispatch_history.project_id")
        else:
            print("dispatch_history table not present; skipping dispatch_history migration")

        conn.commit()
        print(f"Migration complete: {db_path}")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate a v1 pipeline_state.db to v2 schema.")
    parser.add_argument("db_path", help="Path to pipeline_state.db")
    args = parser.parse_args(argv)

    try:
        migrate(Path(args.db_path))
        return 0
    except Exception as exc:  # pragma: no cover - CLI error reporting
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
