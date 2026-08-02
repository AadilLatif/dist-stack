"""sqlite3 connection helpers — the only place ``sqlite3`` is imported.

Verbatim clone of ``runstore.sqlite``. Every functional API call opens its own
connection via :func:`_connect` (context-managed, closed on return). This is
safe for concurrent asyncio MCP tool calls with no locks, because no connection
or cursor is shared across threads. Do *not* add shared-connection caching
without an internal ``threading.Lock``.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["_connect", "OperationalError", "IntegrityError"]

OperationalError = sqlite3.OperationalError
IntegrityError = sqlite3.IntegrityError


@contextmanager
def _connect(db_path: str | os.PathLike) -> Iterator[sqlite3.Connection]:
    """Open a fresh sqlite3 connection to ``db_path``.

    - ``PRAGMA journal_mode=WAL`` on every connect (best-effort — fails
      harmlessly on ``:memory:`` and read-only filesystems).
    - ``PRAGMA busy_timeout = 5000`` on every connect; writers serialize via
      SQLite itself. A ``sqlite3.OperationalError: database is locked`` after
      5 s indicates a pathological writer (a long transaction held open
      elsewhere) — this library never holds transactions across calls.
    - ``PRAGMA foreign_keys=ON`` so the ``edges.source_node/target_node ...
      REFERENCES nodes(node_id) ON DELETE CASCADE`` constraints are enforced on
      hard delete.

    Context-managed: committed on normal exit, rolled back on exception, and
    the connection is always closed.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError:
        pass
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
