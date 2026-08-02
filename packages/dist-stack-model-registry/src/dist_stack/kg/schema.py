"""SQLite schema: DDL constants, migration, and PRAGMA user_version handling.

Verbatim runstore migration pattern. ``PRAGMA user_version`` is the single
schema-version authority. Migration runs only when
``user_version < SCHEMA_VERSION`` (one PRAGMA read per call afterwards). Future
additive schema changes ship as guarded ALTER statements in ``DDL_ALTER_NODES``
/ ``DDL_ALTER_EDGES``; each is wrapped in ``except sqlite3.OperationalError:
pass`` so re-running against a schema that already has the column is harmless.
"""

from __future__ import annotations

from .sqlite import OperationalError

__all__ = [
    "SCHEMA_VERSION",
    "DDL_CREATE_NODES",
    "DDL_CREATE_EDGES",
    "DDL_INDEX_NODES",
    "DDL_INDEX_EDGES",
    "DDL_ALTER_NODES",
    "DDL_ALTER_EDGES",
    "migrate",
]

SCHEMA_VERSION = 1

DDL_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,      -- stable identity: run:<run_id>, artifact:<path>, model:<model_id>
    node_type      TEXT NOT NULL,         -- gdm_system|component|gdm_flow_run|erad_simulation|
                                         -- ditto_conversion|shift_feeder|workflow_execution|artifact|model
    label          TEXT,
    artifact_path  TEXT,
    run_id         TEXT,
    model_id       TEXT,
    tool           TEXT,
    tool_version   TEXT,
    metadata       TEXT,                  -- JSON object
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
)
"""

DDL_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    edge_id        TEXT PRIMARY KEY,      -- minted "e_<uuid4().hex[:12]>"
    source_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    target_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    relation       TEXT NOT NULL,         -- vocabulary in §B; NO CHECK
    metadata       TEXT,                  -- JSON: {tool, tool_version, model_id, model_hash, config}
    created_at_utc TEXT NOT NULL,
    deleted_at_utc TEXT
)
"""

# 8 indexes (spec §A.2); created with IF NOT EXISTS so migrate is safe on
# every open. idx_edges_unique is the idempotency anchor (upsert conflict
# target) and the source-node lookup index.
DDL_INDEX_NODES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_nodes_type     ON nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_label    ON nodes(label)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_run      ON nodes(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_model    ON nodes(model_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_artifact ON nodes(artifact_path)",
)

DDL_INDEX_EDGES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique   ON edges(source_node, target_node, relation)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target_node)",
    "CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation)",
)

# Additive ALTERs for future versions, each guarded:
#   except sqlite3.OperationalError: pass  # column already exists
DDL_ALTER_NODES: tuple[str, ...] = ()
DDL_ALTER_EDGES: tuple[str, ...] = ()


def migrate(conn) -> None:
    """Idempotent create/migrate to ``SCHEMA_VERSION``; safe to call on every open."""
    conn.execute(DDL_CREATE_NODES)
    conn.execute(DDL_CREATE_EDGES)
    for stmt in (*DDL_INDEX_NODES, *DDL_INDEX_EDGES):
        conn.execute(stmt)
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is not None and row[0] >= SCHEMA_VERSION:
        return
    for stmt in (*DDL_ALTER_NODES, *DDL_ALTER_EDGES):
        try:
            conn.execute(stmt)
        except OperationalError:
            pass  # column already exists
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
