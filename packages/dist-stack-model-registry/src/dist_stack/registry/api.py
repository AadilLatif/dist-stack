"""Public functional API of the model registry.

Stateless: every call opens its own ``sqlite3`` connection via
:func:`dist_stack.registry.sqlite._connect` (context-managed, closed on
return). Safe for concurrent asyncio MCP tool calls with no locks.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .errors import (
    HashMismatchError,
    InvalidModelRefError,
    ModelNotFoundError,
    ModelPathNotFoundError,
    RegistryUnavailableError,
)
from .model import ModelRecord
from .schema import SCHEMA_VERSION, migrate
from .sqlite import _connect

DEFAULT_ENV_VAR = "DIST_STACK_MODEL_REGISTRY_DB"

__all__ = [
    "DEFAULT_ENV_VAR",
    "SCHEMA_VERSION",
    "get_registry_path",
    "ensure_schema",
    "register",
    "lookup",
    "lookup_path",
    "delete",
    "list_models",
    "resolve_model_ref",
    "next_version",
    "make_model_id",
]


def get_registry_path(
    registry_db: str | os.PathLike | None = None,
    *,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Resolve the registry DB path: explicit arg > env var.

    (In :func:`resolve_model_ref`, ``model_ref["registry_db"]`` slots in
    between those two.) Read lazily per call — never at import.
    Raises :class:`RegistryUnavailableError` when unset.
    """
    db_path = str(registry_db) if registry_db is not None else None
    if not db_path:
        db_path = os.getenv(env_var)
    if not db_path:
        raise RegistryUnavailableError(
            f"no registry DB path available: pass registry_db or set {env_var}"
        )
    return db_path


def ensure_schema(db_path: str | os.PathLike) -> None:
    """Idempotent create/migrate; safe to call on every open. See §4."""
    with _connect(db_path) as conn:
        migrate(conn)


def _row_to_record(row) -> ModelRecord:
    raw = row["metadata"]
    metadata: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            metadata = parsed
    return ModelRecord(
        model_id=row["model_id"],
        version=row["version"],
        stored_path=row["stored_path"],
        model_hash=row["model_hash"],
        metadata=metadata,
        created_at_utc=row["created_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def _resolve_record_path(record: ModelRecord, db_path: str) -> ModelRecord:
    """Resolve a relative stored_path against the DB file's parent directory."""
    if record.stored_path and not os.path.isabs(record.stored_path):
        return ModelRecord(
            model_id=record.model_id,
            version=record.version,
            stored_path=str(Path(db_path).parent / record.stored_path),
            model_hash=record.model_hash,
            metadata=record.metadata,
            created_at_utc=record.created_at_utc,
            deleted_at_utc=record.deleted_at_utc,
        )
    return record


def register(
    model_id: str,
    version: int | None = None,
    stored_path: str | os.PathLike = ...,
    *,
    model_hash: str | None = None,
    hash_fn: Callable[[str], str] | None = None,
    metadata: dict | None = None,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
    check_exists: bool = True,
    store_relative_to_db: bool = False,
) -> ModelRecord:
    """Upsert. ``version=None`` → ``next_version(model_id)`` (max+1, else 1).

    Idempotent: identical (model_id, version, stored_path, model_hash) is a
    no-op that preserves the original ``created_at_utc``. Re-register with a
    changed path/hash/metadata updates those columns, clears
    ``deleted_at_utc`` (resurrecting a soft-deleted row), and preserves
    ``created_at_utc``.

    ``check_exists=True`` → :class:`ModelPathNotFoundError` if ``stored_path``
    is missing. ``hash_fn`` (if given and ``model_hash`` is None) is called
    with the stored path string; its return value is stored. Returns the
    stored :class:`ModelRecord`.
    """
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must be a non-empty string")

    path_value = os.fspath(stored_path)

    # Default stores absolute (lexical normalization, no symlink resolution,
    # no FS access); store_relative_to_db stores relative to the DB parent.
    db_path = get_registry_path(registry_db, env_var=env_var)
    if store_relative_to_db:
        stored = os.path.relpath(path_value, Path(db_path).parent)
    else:
        stored = os.path.abspath(path_value)

    if check_exists and not os.path.exists(os.path.abspath(path_value)):
        raise ModelPathNotFoundError(f"stored_path does not exist: {path_value}")

    if version is None:
        version = next_version(model_id, registry_db=db_path, env_var=env_var)
    else:
        version = int(version)

    if model_hash is None and hash_fn is not None:
        model_hash = str(hash_fn(stored))

    metadata_json = json.dumps(dict(metadata) if metadata else {})
    created_at = datetime.now(timezone.utc).isoformat()

    with _connect(db_path) as conn:
        migrate(conn)
        conn.execute(
            """
            INSERT INTO models
                (model_id, version, stored_path, model_hash, metadata,
                 created_at_utc, deleted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(model_id, version) DO UPDATE SET
                stored_path  = excluded.stored_path,
                model_hash   = excluded.model_hash,
                metadata     = excluded.metadata,
                deleted_at_utc = NULL
            """,
            (model_id, version, stored, model_hash, metadata_json, created_at),
        )
        row = conn.execute(
            "SELECT * FROM models WHERE model_id = ? AND version = ?",
            (model_id, version),
        ).fetchone()

    return _row_to_record(row)


def lookup(
    model_id: str,
    version: int | str | None = None,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
    resolve_path: bool = True,
    expected_hash: str | None = None,
) -> ModelRecord:
    """``version=None`` → highest version among non-deleted rows.

    ``version`` is normalized with ``int(version)``; a non-numeric version
    raises ``ValueError`` exactly as the legacy code did.
    ``resolve_path=True`` resolves a relative ``stored_path`` against the DB
    file's parent directory. ``expected_hash`` set → :class:`HashMismatchError`
    if the stored ``model_hash`` differs (no verification by default).
    Raises :class:`ModelNotFoundError` on miss.
    """
    db_path = get_registry_path(registry_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if version is None:
            row = conn.execute(
                """
                SELECT * FROM models
                WHERE model_id = ? AND deleted_at_utc IS NULL
                ORDER BY version DESC
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
        else:
            version = int(version)
            row = conn.execute(
                """
                SELECT * FROM models
                WHERE model_id = ? AND version = ? AND deleted_at_utc IS NULL
                LIMIT 1
                """,
                (model_id, version),
            ).fetchone()

    if row is None:
        if version is None:
            raise ModelNotFoundError(f"no model found for model_id={model_id}")
        raise ModelNotFoundError(
            f"no model found for model_id={model_id}, version={version}"
        )

    record = _row_to_record(row)
    if expected_hash is not None and record.model_hash != expected_hash:
        raise HashMismatchError(
            f"model_hash mismatch for model_id={model_id}, version={record.version}: "
            f"expected {expected_hash!r}, stored {record.model_hash!r}"
        )
    if resolve_path:
        record = _resolve_record_path(record, db_path)
    return record


def lookup_path(
    model_id: str,
    version: int | str | None = None,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Convenience: ``str(lookup(..., resolve_path=False).stored_path)``."""
    return str(
        lookup(
            model_id,
            version=version,
            registry_db=registry_db,
            env_var=env_var,
            resolve_path=False,
        ).stored_path
    )


def delete(
    model_id: str,
    version: int | None = None,
    *,
    soft: bool = True,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> None:
    """``soft=True``: set ``deleted_at_utc`` (re-delete re-stamps; idempotent).

    ``soft=False``: hard DELETE. ``version=None`` targets all versions of
    ``model_id``. Raises :class:`ModelNotFoundError` if no rows matched.
    """
    db_path = get_registry_path(registry_db, env_var=env_var)
    stamp = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        migrate(conn)
        if version is None:
            if soft:
                cur = conn.execute(
                    "UPDATE models SET deleted_at_utc = ? WHERE model_id = ?",
                    (stamp, model_id),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM models WHERE model_id = ?", (model_id,)
                )
        else:
            version = int(version)
            if soft:
                cur = conn.execute(
                    "UPDATE models SET deleted_at_utc = ? "
                    "WHERE model_id = ? AND version = ?",
                    (stamp, model_id, version),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM models WHERE model_id = ? AND version = ?",
                    (model_id, version),
                )
    if cur.rowcount == 0:
        raise ModelNotFoundError(
            f"no model found for model_id={model_id}, version={version}"
        )


def list_models(
    *,
    include_deleted: bool = False,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[ModelRecord]:
    """All records ordered by ``model_id, version``.

    Excludes soft-deleted rows unless ``include_deleted=True``.
    """
    db_path = get_registry_path(registry_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if include_deleted:
            rows = conn.execute(
                "SELECT * FROM models ORDER BY model_id, version"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM models WHERE deleted_at_utc IS NULL "
                "ORDER BY model_id, version"
            ).fetchall()
    return [_row_to_record(r) for r in rows]


def resolve_model_ref(
    model_ref: dict,
    *,
    registry_db_env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """DROP-IN replacement for the three copy-pasted ``_resolve_model_ref_to_path``
    functions. Exact behavioral clone — see §5.1 of the spec.

    1. Path-key passthrough for ``("stored_path", "path", "source_path")`` in
       order: a ``str`` with a truthy ``strip()`` is returned verbatim (no
       normalization, no existence check). A ``Path`` object in these keys is
       *ignored* and falls through to the ``model_id`` branch.
    2. Missing/non-str ``model_id`` → ``InvalidModelRefError``.
    3. ``model_ref["registry_db"]`` first, then env var; missing →
       ``RegistryUnavailableError``.
    4. ``version`` present → exact version (``int`` normalization); absent →
       latest. Miss → ``ModelNotFoundError`` interpolating the *raw* version.
    5. Returns the stored path verbatim (``resolve_path=False`` internally).
    """
    for key in ("stored_path", "path", "source_path"):
        value = model_ref.get(key)
        if isinstance(value, str) and value.strip():
            return value

    model_id = model_ref.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise InvalidModelRefError("model_ref must include a path or model_id")

    version = model_ref.get("version")
    db_path = model_ref.get("registry_db") or os.getenv(registry_db_env_var)
    if not db_path:
        raise RegistryUnavailableError(
            "model_ref requires DIST_STACK_MODEL_REGISTRY_DB (or model_ref.registry_db) "
            "when path fields are not provided"
        )

    try:
        record = lookup(
            model_id, version=version, registry_db=db_path, resolve_path=False
        )
    except ModelNotFoundError:
        suffix = "latest" if version is None else f"version={version}"
        raise ModelNotFoundError(
            f"model_ref not found for model_id={model_id}, {suffix}"
        ) from None
    return record.stored_path


def next_version(
    model_id: str,
    *,
    registry_db: str | os.PathLike | None = None,
    env_var: str = DEFAULT_ENV_VAR,
) -> int:
    """``max(version)+1`` for ``model_id`` (including soft-deleted), else 1."""
    db_path = get_registry_path(registry_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        row = conn.execute(
            "SELECT MAX(version) AS max_version FROM models WHERE model_id = ?",
            (model_id,),
        ).fetchone()
    max_version = row["max_version"]
    return (max_version + 1) if max_version is not None else 1


def make_model_id(
    source: str | os.PathLike, *, namespace: str = "dist-stack.models"
) -> str:
    """Deterministic id: ``str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{source}"))``.

    Same source → same model_id across sessions.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{source}"))
