"""Schema: fresh DB → PRAGMA user_version == 1; reopen idempotent (migrate on
every open); nodes + edges tables, all 8 spec indexes; guarded-ALTER path.
"""
from __future__ import annotations

import sqlite3

from dist_stack.kg import ensure_schema
from dist_stack.kg.schema import SCHEMA_VERSION, migrate

NODES_COLUMNS = {
    "node_id", "node_type", "label", "artifact_path", "run_id", "model_id",
    "tool", "tool_version", "metadata", "created_at_utc", "updated_at_utc",
    "deleted_at_utc",
}
EDGES_COLUMNS = {
    "edge_id", "source_node", "target_node", "relation", "metadata",
    "created_at_utc", "deleted_at_utc",
}
NODES_INDEXES = {
    "idx_nodes_type", "idx_nodes_label", "idx_nodes_run", "idx_nodes_model",
    "idx_nodes_artifact",
}
EDGES_INDEXES = {
    "idx_edges_unique", "idx_edges_target", "idx_edges_relation",
}


def _columns(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_fresh_db_user_version_and_shape(tmp_path):
    db = tmp_path / "kg.sqlite"
    ensure_schema(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert NODES_COLUMNS <= _columns(conn, "nodes")
        assert EDGES_COLUMNS <= _columns(conn, "edges")
        assert NODES_INDEXES <= _indexes(conn, "nodes")
        assert EDGES_INDEXES <= _indexes(conn, "edges")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"nodes", "edges"} <= tables


def test_reopen_idempotent(tmp_path):
    db = tmp_path / "kg.sqlite"
    ensure_schema(db)
    ensure_schema(db)
    ensure_schema(db)  # migrate on every open is safe
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert NODES_COLUMNS <= _columns(conn, "nodes")
        assert EDGES_COLUMNS <= _columns(conn, "edges")


def test_migrate_direct(tmp_path):
    db = tmp_path / "kg.sqlite"
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_future_v2_additive_alter(tmp_path, monkeypatch):
    import dist_stack.kg.schema as schema_mod

    db = tmp_path / "kg.sqlite"
    ensure_schema(db)
    # Simulate the v2 release: a bumped SCHEMA_VERSION + one additive ALTER.
    monkeypatch.setattr(schema_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        schema_mod,
        "DDL_ALTER_NODES",
        ("ALTER TABLE nodes ADD COLUMN v2_flag TEXT",),
    )
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "v2_flag" in _columns(conn, "nodes")
    conn.close()


def test_guarded_alter_when_column_already_exists(tmp_path, monkeypatch):
    import dist_stack.kg.schema as schema_mod

    db = tmp_path / "kg.sqlite"
    ensure_schema(db)
    monkeypatch.setattr(schema_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        schema_mod,
        "DDL_ALTER_NODES",
        ("ALTER TABLE nodes ADD COLUMN v2_flag TEXT",),
    )
    conn = sqlite3.connect(str(db))
    migrate(conn)  # v1 → v2: adds the column
    assert "v2_flag" in _columns(conn, "nodes")

    # Simulate a v2 DB whose user_version regressed (e.g. interrupted write):
    # migrate() must swallow the duplicate-column ALTER and restore the version.
    conn.execute("PRAGMA user_version = 1")
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "v2_flag" in _columns(conn, "nodes")
    conn.close()
