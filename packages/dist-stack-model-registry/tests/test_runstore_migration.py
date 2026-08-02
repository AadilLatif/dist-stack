"""Migration path: fresh DB → user_version == 1; reopen idempotent; simulate a
future v1→v2 additive ALTER proving the guarded-ALTER path.
"""
from __future__ import annotations

import sqlite3

import dist_stack.runstore.schema as schema_mod
from dist_stack.runstore import ensure_schema
from dist_stack.runstore.schema import migrate


def _columns(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_db_migrate_sets_user_version(tmp_path):
    db = tmp_path / "runstore.sqlite"
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_reopen_idempotent(tmp_path):
    db = tmp_path / "runstore.sqlite"
    ensure_schema(db)
    ensure_schema(db)
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_future_v2_additive_alter(tmp_path, monkeypatch):
    db = tmp_path / "runstore.sqlite"
    ensure_schema(db)
    # Simulate the v2 release: a bumped SCHEMA_VERSION + one additive ALTER.
    monkeypatch.setattr(schema_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        schema_mod,
        "DDL_ALTER_RUNS",
        ("ALTER TABLE runs ADD COLUMN v2_flag TEXT",),
    )
    conn = sqlite3.connect(str(db))
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "v2_flag" in _columns(conn, "runs")
    conn.close()


def test_guarded_alter_when_column_already_exists(tmp_path, monkeypatch):
    db = tmp_path / "runstore.sqlite"
    ensure_schema(db)
    monkeypatch.setattr(schema_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        schema_mod,
        "DDL_ALTER_RUNS",
        ("ALTER TABLE runs ADD COLUMN v2_flag TEXT",),
    )
    conn = sqlite3.connect(str(db))
    migrate(conn)  # v1 → v2: adds the column
    assert "v2_flag" in _columns(conn, "runs")

    # Simulate a v2 DB whose user_version regressed (e.g. interrupted write):
    # migrate() must swallow the duplicate-column ALTER and restore the version.
    conn.execute("PRAGMA user_version = 1")
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "v2_flag" in _columns(conn, "runs")
    conn.close()
