"""Public functional API of the runstore.

Stateless: every call opens its own ``sqlite3`` connection via
:func:`dist_stack.runstore.sqlite._connect` (context-managed, closed on
return). Safe for concurrent asyncio MCP tool calls with no locks.

``DIST_STACK_RUNSTORE_DB`` is read lazily per call — never at import. An
explicit ``runstore_db`` argument always wins over the env var.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from dist_stack.manifest import has_manifest, read_manifest, write_manifest

from .errors import (
    ArtifactPathNotFoundError,
    RunExistsError,
    RunNotFoundError,
    RunstoreError,
    RunstoreUnavailableError,
)
from .model import ArtifactRecord, RunRecord
from .schema import SCHEMA_VERSION, migrate
from .sqlite import IntegrityError, _connect

DEFAULT_ENV_VAR = "DIST_STACK_RUNSTORE_DB"

# Registered prefixes: ac pf dc lindistflow qsts mp (gdm-flow), sim (erad),
# conv (ditto), feeder graph (shift), wf (workflow-runner).
_RUN_ID_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]*$")

# The API enforces the status Literal (the schema deliberately has no CHECK).
_VALID_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})

_STATUS_FROM_SUCCESS = {True: "succeeded", False: "failed"}

__all__ = [
    "DEFAULT_ENV_VAR",
    "SCHEMA_VERSION",
    "get_runstore_path",
    "ensure_schema",
    "make_run_id",
    "create_run",
    "get_run",
    "list_runs",
    "update_run",
    "delete_run",
    "attach_artifact",
    "list_artifacts",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_run_id(run_id) -> str:
    """Accept any caller-supplied run_id: non-empty, <=128 chars, no whitespace."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise RunstoreError("run_id must be a non-empty string")
    if len(run_id) > 128:
        raise RunstoreError("run_id must be at most 128 characters")
    if any(ch.isspace() for ch in run_id):
        raise RunstoreError("run_id must not contain whitespace")
    return run_id


def get_runstore_path(
    runstore_db: str | os.PathLike | None = None,
    *,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Resolve the runstore DB path: explicit arg > env var.

    Read lazily per call — never at import. Raises
    :class:`RunstoreUnavailableError` when unset.
    """
    db_path = str(runstore_db) if runstore_db is not None else None
    if not db_path:
        db_path = os.getenv(env_var)
    if not db_path:
        raise RunstoreUnavailableError(
            f"no runstore DB path available: pass runstore_db or set {env_var}"
        )
    return db_path


def ensure_schema(db_path: str | os.PathLike) -> None:
    """Idempotent create/migrate; safe to call on every open."""
    with _connect(db_path) as conn:
        migrate(conn)


def make_run_id(prefix: str) -> str:
    """Mint a canonical 2-part id: ``f"{prefix}_{uuid4().hex[:12]}"`` (16 chars).

    ``prefix`` must match ``^[a-z][a-z0-9]*$``; anything else raises
    :class:`RunstoreError`.
    """
    if not isinstance(prefix, str) or not _RUN_ID_PREFIX_RE.match(prefix):
        raise RunstoreError(
            f"invalid run_id prefix {prefix!r}: must match ^[a-z][a-z0-9]*$"
        )
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_run(row) -> RunRecord:
    raw = row["payload"]
    payload: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    return RunRecord(
        run_id=row["run_id"],
        tool=row["tool"],
        run_type=row["run_type"],
        status=row["status"],
        implementation=row["implementation"],
        message=row["message"],
        session_id=row["session_id"],
        tool_version=row["tool_version"],
        model_id=row["model_id"],
        model_version=row["model_version"],
        model_hash=row["model_hash"],
        payload=payload,
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def _row_to_artifact(row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row["artifact_id"],
        run_id=row["run_id"],
        artifact_path=row["artifact_path"],
        artifact_type=row["artifact_type"],
        tool=row["tool"],
        tool_version=row["tool_version"],
        model_id=row["model_id"],
        model_version=row["model_version"],
        model_hash=row["model_hash"],
        created_at_utc=row["created_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def create_run(
    tool: str,
    *,
    run_type: str,
    run_id: str | None = None,
    implementation=None,
    status=None,
    success=None,
    message=None,
    session_id=None,
    tool_version=None,
    model_id=None,
    model_version=None,
    model_hash=None,
    payload=None,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> RunRecord:
    """Insert a run record (NOT an upsert).

    ``run_id=None`` mints ``make_run_id(tool)`` when ``tool`` is a valid
    prefix, else ``make_run_id("run")``. Caller-supplied run_ids are accepted
    verbatim (any non-empty, ≤128 chars, no whitespace — including 3-part
    ``qsts_<solver>_<hex12>``); an invalid format raises :class:`RunstoreError`.

    ``status`` defaults to ``'succeeded'``. ``success=True/False`` is a
    convenience that maps to ``'succeeded'``/``'failed'``; passing both
    ``status`` and ``success`` raises :class:`RunstoreError`. Raises
    :class:`RunExistsError` when ``run_id`` already exists.
    """
    if not isinstance(tool, str) or not tool.strip():
        raise RunstoreError("tool must be a non-empty string")
    if not isinstance(run_type, str) or not run_type.strip():
        raise RunstoreError("run_type must be a non-empty string")

    if run_id is None:
        try:
            run_id = make_run_id(tool)
        except RunstoreError:
            run_id = make_run_id("run")
    else:
        run_id = _validate_run_id(run_id)

    if success is not None and status is not None:
        raise RunstoreError("pass either status= or success=, not both")
    if success is not None:
        status = _STATUS_FROM_SUCCESS[bool(success)]
    if status is None:
        status = "succeeded"
    if status not in _VALID_STATUSES:
        raise RunstoreError(
            f"invalid status {status!r}: must be one of "
            f"{sorted(_VALID_STATUSES)}"
        )

    payload_json = json.dumps(payload or {}, default=str)
    created_at = _now()

    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        try:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, tool, tool_version, run_type, implementation,
                     status, message, session_id, model_id, model_version,
                     model_hash, payload, created_at_utc, updated_at_utc,
                     deleted_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    tool,
                    tool_version,
                    run_type,
                    implementation,
                    status,
                    message,
                    session_id,
                    model_id,
                    model_version,
                    model_hash,
                    payload_json,
                    created_at,
                    created_at,
                ),
            )
        except IntegrityError:
            raise RunExistsError(f"run already exists: {run_id}") from None
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    return _row_to_run(row)


def get_run(
    run_id,
    *,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> RunRecord:
    """Fetch a non-deleted run. Raises :class:`RunNotFoundError` on miss."""
    run_id = _validate_run_id(run_id)
    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ? AND deleted_at_utc IS NULL",
            (run_id,),
        ).fetchone()
    if row is None:
        raise RunNotFoundError(f"no run found for run_id={run_id}")
    return _row_to_run(row)


def list_runs(
    *,
    tool=None,
    run_type=None,
    status=None,
    implementation=None,
    session_id=None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[RunRecord]:
    """All runs matching every provided filter, newest first.

    Order: ``created_at_utc DESC, run_id DESC`` (run_id breaks same-second
    ties). Soft-deleted rows are excluded unless ``include_deleted=True``.
    """
    db_path = get_runstore_path(runstore_db, env_var=env_var)
    clauses: list[str] = []
    params: list = []
    if not include_deleted:
        clauses.append("deleted_at_utc IS NULL")
    if tool is not None:
        clauses.append("tool = ?")
        params.append(tool)
    if run_type is not None:
        clauses.append("run_type = ?")
        params.append(run_type)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if implementation is not None:
        clauses.append("implementation = ?")
        params.append(implementation)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)

    limit = 100 if limit is None else max(0, int(limit))
    offset = 0 if offset is None else max(0, int(offset))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT * FROM runs {where} "
        "ORDER BY created_at_utc DESC, run_id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    with _connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_run(r) for r in rows]


def update_run(
    run_id,
    *,
    status=None,
    message=None,
    implementation=None,
    session_id=None,
    model_id=None,
    model_version=None,
    model_hash=None,
    payload=None,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> RunRecord:
    """Update only the provided kwargs; always stamps ``updated_at_utc``.

    ``payload`` REPLACES the stored payload wholesale. Raises
    :class:`RunNotFoundError` when no row matches ``run_id``.
    """
    run_id = _validate_run_id(run_id)

    fields: dict[str, object] = {}
    if status is not None:
        if status not in _VALID_STATUSES:
            raise RunstoreError(
                f"invalid status {status!r}: must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        fields["status"] = status
    if message is not None:
        fields["message"] = message
    if implementation is not None:
        fields["implementation"] = implementation
    if session_id is not None:
        fields["session_id"] = session_id
    if model_id is not None:
        fields["model_id"] = model_id
    if model_version is not None:
        fields["model_version"] = model_version
    if model_hash is not None:
        fields["model_hash"] = model_hash
    if payload is not None:
        fields["payload"] = json.dumps(payload or {}, default=str)

    assignments = ", ".join(
        [f"{col} = ?" for col in fields] + ["updated_at_utc = ?"]
    )
    stamp = _now()

    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        cur = conn.execute(
            f"UPDATE runs SET {assignments} WHERE run_id = ?",
            (*fields.values(), stamp, run_id),
        )
        if cur.rowcount == 0:
            raise RunNotFoundError(f"no run found for run_id={run_id}")
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    return _row_to_run(row)


def delete_run(
    run_id,
    *,
    soft: bool = True,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> None:
    """``soft=True``: stamp ``deleted_at_utc`` (re-delete re-stamps).

    ``soft=False``: hard DELETE — artifacts rows cascade via the FK
    ``ON DELETE CASCADE`` constraint. Raises :class:`RunNotFoundError` when no
    row matches ``run_id``.
    """
    run_id = _validate_run_id(run_id)
    stamp = _now()
    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if soft:
            cur = conn.execute(
                "UPDATE runs SET deleted_at_utc = ? WHERE run_id = ?",
                (stamp, run_id),
            )
        else:
            cur = conn.execute(
                "DELETE FROM runs WHERE run_id = ?", (run_id,)
            )
    if cur.rowcount == 0:
        raise RunNotFoundError(f"no run found for run_id={run_id}")


def attach_artifact(
    run_id,
    artifact_path,
    *,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> ArtifactRecord:
    """Attach an artifact file (and its manifest sidecar) to an existing run.

    1. The artifact file must exist on disk → else
       :class:`ArtifactPathNotFoundError`.
    2. If a manifest sidecar exists next to ``artifact_path`` it is read and
       its fields are copied verbatim into the artifacts row; otherwise a new
       sidecar is written via ``dist_stack.manifest.write_manifest`` with
       ``artifact_type=run.run_type``, ``tool=run.tool``,
       ``tool_version=run.tool_version``, ``config={"run_id": run_id}``,
       ``derived_from=[run_id]``.
    3. Inserts one artifacts row (index of the sidecar). The same
       ``artifact_path`` may be attached to many runs.

    Raises :class:`RunNotFoundError` when ``run_id`` does not resolve to a
    non-deleted run.
    """
    run_id = _validate_run_id(run_id)
    path = os.fspath(artifact_path)
    if not os.path.exists(path):
        raise ArtifactPathNotFoundError(f"artifact path does not exist: {path}")

    run = get_run(run_id, runstore_db=runstore_db, env_var=env_var)

    if has_manifest(path):
        manifest = read_manifest(path)
    else:
        manifest = write_manifest(
            path,
            artifact_type=run.run_type,
            tool=run.tool,
            tool_version=run.tool_version,
            config={"run_id": run_id},
            derived_from=[run_id],
        )

    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    created_at = manifest.created_at_utc or _now()

    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        conn.execute(
            """
            INSERT INTO artifacts
                (artifact_id, run_id, artifact_path, artifact_type, tool,
                 tool_version, model_id, model_version, model_hash,
                 created_at_utc, deleted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                artifact_id,
                run_id,
                manifest.artifact_path,
                manifest.artifact_type,
                manifest.tool,
                manifest.tool_version,
                manifest.model_id,
                manifest.model_version,
                manifest.model_hash,
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()

    return _row_to_artifact(row)


def list_artifacts(
    run_id,
    *,
    include_deleted: bool = False,
    runstore_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[ArtifactRecord]:
    """Artifacts attached to ``run_id``, newest first.

    Soft-deleted rows are excluded unless ``include_deleted=True``. Returns
    ``[]`` for an unknown run_id.
    """
    run_id = _validate_run_id(run_id)
    db_path = get_runstore_path(runstore_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if include_deleted:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? "
                "ORDER BY created_at_utc DESC, artifact_id DESC",
                (run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? "
                "AND deleted_at_utc IS NULL "
                "ORDER BY created_at_utc DESC, artifact_id DESC",
                (run_id,),
            ).fetchall()
    return [_row_to_artifact(r) for r in rows]
