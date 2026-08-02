"""SQLite schema: DDL constants, migration, and PRAGMA user_version handling.

``PRAGMA user_version`` is the single schema-version authority. Migration runs
only when ``user_version < SCHEMA_VERSION`` (one PRAGMA read per call
afterwards). Future additive schema changes ship as guarded ALTER statements in
``DDL_ALTER_RUNS`` / ``DDL_ALTER_ARTIFACTS``; each is wrapped in
``except sqlite3.OperationalError: pass`` so re-running against a schema that
already has the column is harmless.
"""

from __future__ import annotations

from .sqlite import OperationalError

__all__ = [
    "SCHEMA_VERSION",
    "DDL_CREATE_RUNS",
    "DDL_CREATE_ARTIFACTS",
    "DDL_INDEX_RUNS",
    "DDL_ALTER_RUNS",
    "DDL_ALTER_ARTIFACTS",
    "migrate",
]

SCHEMA_VERSION = 1

DDL_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    tool            TEXT    NOT NULL,            -- tool name, e.g. 'run_ac_opf', 'run_simulation', 'convert_model'
    tool_version    TEXT,                        -- repo __version__ at write time
    run_type        TEXT    NOT NULL,            -- manifest artifact_type vocabulary: 'gdm_flow_run'|'erad_simulation'|'ditto_conversion'|'shift_feeder'|'workflow_execution'|...
    implementation  TEXT,                        -- gdm-flow solver: 'ac_opf'|'ac_pf'|'dc_opf'|'lindistflow'|'qsts'|'multiperiod'; NULL otherwise
    status          TEXT    NOT NULL DEFAULT 'succeeded',  -- 'pending'|'running'|'succeeded'|'failed'|'cancelled'
    message         TEXT,                        -- gdm-flow result.message / failure detail
    session_id      TEXT,                        -- shift session id, ditto name, runner session; NULL otherwise
    model_id        TEXT,                        -- registry provenance (fills the gdm-flow gap)
    model_version   INTEGER,
    model_hash      TEXT,
    payload         TEXT,                        -- JSON: erad {asset_system_id, hazard_system_id, curve_set, ...}, ditto {reader_type, source}, shift {graph_id, ...}, runner {workflow_id, inputs, ...}
    created_at_utc  TEXT NOT NULL,               -- ISO-8601 UTC, same convention as registry + gdm-flow
    updated_at_utc  TEXT,                        -- stamped on create and every update_run
    deleted_at_utc  TEXT                         -- soft delete
)
"""

DDL_CREATE_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id    TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    artifact_path  TEXT NOT NULL,                -- absolute path
    artifact_type  TEXT,                         -- from the sidecar manifest
    tool           TEXT,
    tool_version   TEXT,
    model_id       TEXT,
    model_version  INTEGER,
    model_hash     TEXT,
    created_at_utc TEXT,
    deleted_at_utc TEXT
)
"""

# 4 indexes (spec §1.2); created with IF NOT EXISTS so migrate is safe on
# every open.
DDL_INDEX_RUNS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_runs_tool        ON runs(tool)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status      ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_session     ON runs(session_id)",
)

# Additive ALTERs for future versions, each guarded:
#   except sqlite3.OperationalError: pass  # column already exists
DDL_ALTER_RUNS: tuple[str, ...] = ()
DDL_ALTER_ARTIFACTS: tuple[str, ...] = ()


def migrate(conn) -> None:
    """Idempotent create/migrate to ``SCHEMA_VERSION``; safe to call on every open."""
    conn.execute(DDL_CREATE_RUNS)
    conn.execute(DDL_CREATE_ARTIFACTS)
    for stmt in DDL_INDEX_RUNS:
        conn.execute(stmt)
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is not None and row[0] >= SCHEMA_VERSION:
        return
    for stmt in (*DDL_ALTER_RUNS, *DDL_ALTER_ARTIFACTS):
        try:
            conn.execute(stmt)
        except OperationalError:
            pass  # column already exists
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
