"""Legacy 3-column table migration (§8 item 2).

Reproduces the exact legacy DDL from
``grid-data-models/tests/test_mcp_server.py:86-94`` (the gdm-flow and erad
copies are byte-identical) and verifies the library migrates the table in
place, preserving rows.
"""
from __future__ import annotations

import sqlite3

import pytest

from dist_stack import (
    ModelRecord,
    ensure_schema,
    lookup,
    register,
    resolve_model_ref,
)

# Byte-identical DDL from grid-data-models/tests/test_mcp_server.py:86-94
# (also gdm-flow/tests/test_mcp_server.py:78-86 and
#  erad/tests/test_mcp_server.py:106-114).
LEGACY_DDL = """
CREATE TABLE models (
    model_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    stored_path TEXT NOT NULL
)
"""

FULL_COLUMNS = [
    "model_id",
    "version",
    "stored_path",
    "model_hash",
    "metadata",
    "created_at_utc",
    "deleted_at_utc",
]


def build_legacy_db(tmp_path, rows=None):
    db_path = tmp_path / "registry.sqlite"
    rows = rows if rows is not None else [("abc123", 1, str(tmp_path / "legacy.json"))]
    with sqlite3.connect(db_path) as conn:
        conn.execute(LEGACY_DDL)
        for model_id, version, stored_path in rows:
            conn.execute(
                "INSERT INTO models (model_id, version, stored_path) VALUES (?, ?, ?)",
                (model_id, version, stored_path),
            )
    return db_path


def test_user_version_becomes_1(tmp_path):
    db = build_legacy_db(tmp_path)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    lookup("abc123", registry_db=db, resolve_path=False)  # migrates on open
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_columns_added(tmp_path):
    db = build_legacy_db(tmp_path)
    ensure_schema(db)
    with sqlite3.connect(db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(models)")]
    assert cols == FULL_COLUMNS


def test_unique_index_created(tmp_path):
    db = build_legacy_db(tmp_path)
    ensure_schema(db)
    with sqlite3.connect(db) as conn:
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_models_model_id_version",),
        ).fetchone()
    assert idx is not None
    # The unique index enforces (model_id, version) uniqueness in place
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO models (model_id, version, stored_path) VALUES (?, ?, ?)",
                ("abc123", 1, str(tmp_path / "dup.json")),
            )


def test_lookup_returns_legacy_row_verbatim(tmp_path):
    db = build_legacy_db(tmp_path)
    rec = lookup("abc123", registry_db=db, resolve_path=False)
    assert isinstance(rec, ModelRecord)
    assert rec.model_id == "abc123"
    assert rec.version == 1
    assert rec.stored_path == str(tmp_path / "legacy.json")  # verbatim
    assert rec.model_hash is None
    assert rec.metadata == {}
    assert rec.created_at_utc is None
    assert rec.deleted_at_utc is None


def test_migration_preserves_rows(tmp_path):
    db = build_legacy_db(
        tmp_path, rows=[("a", 1, "/p/a.json"), ("b", 2, "/p/b.json")]
    )
    ensure_schema(db)
    ensure_schema(db)  # idempotent
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert lookup("a", version=1, registry_db=db).stored_path == "/p/a.json"
    assert lookup("b", registry_db=db).stored_path == "/p/b.json"


def test_register_works_on_migrated_legacy_table(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text("{}")
    db = build_legacy_db(tmp_path, rows=[("abc123", 1, str(path))])
    # Upsert into the migrated legacy table, same (model_id, version).
    register("abc123", version=1, stored_path=path, registry_db=db, model_hash="h")
    got = lookup("abc123", registry_db=db)
    assert got.version == 1
    assert got.model_hash == "h"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 1


def test_resolve_model_ref_against_legacy_table(tmp_path, monkeypatch):
    # Mirrors gdm/tests/test_mcp_server.py:79-108 and erad:103-126: a raw
    # 3-column table, direct INSERT, env var set after table creation.
    path = tmp_path / "system.json"
    path.write_text("{}")
    db = build_legacy_db(tmp_path, rows=[("abc123", 1, str(path))])
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    assert resolve_model_ref({"model_id": "abc123", "version": 1}) == str(path)


def test_legacy_latest_version_lookup(tmp_path, monkeypatch):
    db = build_legacy_db(
        tmp_path, rows=[("m", 1, "/v1.json"), ("m", 5, "/v5.json")]
    )
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(db))
    assert resolve_model_ref({"model_id": "m"}) == "/v5.json"
