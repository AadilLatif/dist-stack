"""SQLite schema: DDL constants, migration, and PRAGMA user_version handling.

``PRAGMA user_version`` is the single schema-version authority. Migration runs
only when ``user_version < SCHEMA_VERSION`` (one PRAGMA read per call
afterwards). Legacy databases shaped like the three test-suite fixtures
(``models(model_id TEXT NOT NULL, version INTEGER NOT NULL, stored_path TEXT
NOT NULL)`` with no PK) are migrated *in place* with additive ALTERs; a PK
constraint cannot be added to an existing table without a rebuild, so the
unique index enforces the same invariant.
"""

from __future__ import annotations

from collections.abc import Iterable

from .sqlite import OperationalError

__all__ = [
    "SCHEMA_VERSION",
    "DDL_CREATE_MODELS",
    "DDL_ALTER_MODELS",
    "DDL_UNIQUE_INDEX",
    "migrate",
]

SCHEMA_VERSION = 1

DDL_CREATE_MODELS = """
CREATE TABLE IF NOT EXISTS models (
    model_id       TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    stored_path    TEXT    NOT NULL,
    model_hash     TEXT,
    metadata       TEXT,              -- JSON object: tool provenance etc.
    created_at_utc TEXT,
    deleted_at_utc TEXT,
    PRIMARY KEY (model_id, version)
)
"""

# Legacy migration (additive; each guarded: except sqlite3.OperationalError: pass)
DDL_ALTER_MODELS: tuple[str, ...] = (
    "ALTER TABLE models ADD COLUMN model_hash TEXT",
    "ALTER TABLE models ADD COLUMN metadata TEXT",
    "ALTER TABLE models ADD COLUMN created_at_utc TEXT",
    "ALTER TABLE models ADD COLUMN deleted_at_utc TEXT",
)

# Uniqueness on legacy tables (new tables already have the PK index)
DDL_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_models_model_id_version "
    "ON models(model_id, version)"
)


def migrate(conn) -> None:
    """Idempotent create/migrate to ``SCHEMA_VERSION``; safe to call on every open."""
    conn.execute(DDL_CREATE_MODELS)
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is not None and row[0] >= SCHEMA_VERSION:
        return
    for stmt in DDL_ALTER_MODELS:
        try:
            conn.execute(stmt)
        except OperationalError:
            pass  # column already exists
    conn.execute(DDL_UNIQUE_INDEX)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
