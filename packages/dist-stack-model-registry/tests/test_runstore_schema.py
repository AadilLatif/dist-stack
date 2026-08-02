"""Schema: fresh DB → PRAGMA user_version == 1; reopen idempotent (migrate on
every open); the four runs indexes exist.
"""
from __future__ import annotations

import sqlite3

from dist_stack.runstore import ensure_schema
from dist_stack.runstore.schema import SCHEMA_VERSION, migrate

RUNS_COLUMNS = {
    "run_id", "tool", "tool_version", "run_type", "implementation", "status",
    "message", "session_id", "model_id", "model_version", "model_hash",
    "payload", "created_at_utc", "updated_at_utc", "deleted_at_utc",
}
ARTIFACTS_COLUMNS = {
    "artifact_id", "run_id", "artifact_path", "artifact_type", "tool",
    "tool_version", "model_id", "model_version", "model_hash",
    "created_at_utc", "deleted_at_utc",
}
RUNS_INDEXES = {
    "idx_runs_created_at", "idx_runs_tool", "idx_runs_status", "idx_runs_session",
}


def _columns(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_fresh_db_user_version_and_shape(tmp_path):
    db = tmp_path / "runstore.sqlite"
    ensure_schema(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert RUNS_COLUMNS <= _columns(conn, "runs")
        assert ARTIFACTS_COLUMNS <= _columns(conn, "artifacts")
        assert RUNS_INDEXES <= _indexes(conn, "runs")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"runs", "artifacts"} <= tables


def test_reopen_idempotent(tmp_path):
    db = tmp_path / "runstore.sqlite"
    ensure_schema(db)
    ensure_schema(db)
    ensure_schema(db)  # migrate on every open is safe
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert RUNS_COLUMNS <= _columns(conn, "runs")


def test_migrate_direct(tmp_path):
    db = tmp_path / "runstore.sqlite"
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()
