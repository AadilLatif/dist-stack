"""Public functional API of the knowledge graph store.

Stateless: every call opens its own ``sqlite3`` connection via
:func:`dist_stack.kg.sqlite._connect` (context-managed, closed on return). Safe
for concurrent asyncio MCP tool calls with no locks.

``DIST_STACK_KG_DB`` is read lazily per call — never at import. An explicit
``kg_db`` argument always wins over the env var.

Upsert contract (spec §A.5): node upserts keep ``created_at_utc`` and resurrect
soft-deleted rows; ``metadata`` is MERGED (new keys overwrite, existing keys
kept), never replaced. Edge upserts conflict on the unique
``(source_node, target_node, relation)`` triple and preserve ``edge_id`` +
``created_at_utc``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from .errors import (
    KGError,
    KGUnavailableError,
    NodeNotFoundError,
)
from .model import KGEdge, KGNode, KGStats
from .schema import migrate
from .sqlite import _connect

DEFAULT_ENV_VAR = "DIST_STACK_KG_DB"

# The API enforces the node_type Literal (the schema deliberately has no CHECK).
VALID_NODE_TYPES = frozenset(
    {
        "gdm_system",
        "component",
        "gdm_flow_run",
        "erad_simulation",
        "ditto_conversion",
        "shift_feeder",
        "workflow_execution",
        "artifact",
        "model",
    }
)

__all__ = [
    "DEFAULT_ENV_VAR",
    "VALID_NODE_TYPES",
    "get_kg_path",
    "ensure_schema",
    "upsert_node",
    "get_node",
    "search_nodes",
    "delete_node",
    "upsert_edge",
    "get_neighbors",
    "get_provenance_chain",
    "graph_stats",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_node_id(node_id) -> str:
    """Accept any caller-supplied node_id: non-empty string.

    Whitespace is allowed — artifact paths legitimately contain spaces
    (``artifact:<normpath(abs_path)>``).
    """
    if not isinstance(node_id, str) or not node_id.strip():
        raise KGError("node_id must be a non-empty string")
    return node_id


def _validate_relation(relation) -> str:
    if not isinstance(relation, str) or not relation.strip():
        raise KGError("relation must be a non-empty string")
    return relation


def _parse_metadata(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _merge_metadata(existing: dict, new) -> dict:
    """Merge metadata: new keys overwrite, existing keys kept."""
    merged = dict(existing)
    if new is not None:
        merged.update(new)
    return merged


def _row_to_node(row) -> KGNode:
    return KGNode(
        node_id=row["node_id"],
        node_type=row["node_type"],
        label=row["label"],
        artifact_path=row["artifact_path"],
        run_id=row["run_id"],
        model_id=row["model_id"],
        tool=row["tool"],
        tool_version=row["tool_version"],
        metadata=_parse_metadata(row["metadata"]),
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def _row_to_edge(row) -> KGEdge:
    return KGEdge(
        edge_id=row["edge_id"],
        source_node=row["source_node"],
        target_node=row["target_node"],
        relation=row["relation"],
        metadata=_parse_metadata(row["metadata"]),
        created_at_utc=row["created_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_kg_path(
    kg_db: str | os.PathLike | None = None,
    *,
    env_var: str = DEFAULT_ENV_VAR,
) -> str:
    """Resolve the KG DB path: explicit arg > env var.

    Read lazily per call — never at import. Raises
    :class:`KGUnavailableError` when unset.
    """
    db_path = str(kg_db) if kg_db is not None else None
    if not db_path:
        db_path = os.getenv(env_var)
    if not db_path:
        raise KGUnavailableError(
            f"no KG DB path available: pass kg_db or set {env_var}"
        )
    return db_path


def ensure_schema(db_path: str | os.PathLike) -> None:
    """Idempotent create/migrate; safe to call on every open."""
    with _connect(db_path) as conn:
        migrate(conn)


def upsert_node(
    node_id,
    node_type,
    *,
    label=None,
    artifact_path=None,
    run_id=None,
    model_id=None,
    tool=None,
    tool_version=None,
    metadata=None,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> KGNode:
    """Upsert a node keyed on ``node_id`` (spec §A.5).

    On conflict, every mutable field is overwritten, ``deleted_at_utc`` is
    cleared (resurrect), and ``created_at_utc`` is preserved. ``metadata`` is
    MERGED with any existing value (new keys overwrite, existing keys kept).
    ``node_type`` is validated against the Literal (the schema has no CHECK).
    """
    node_id = _validate_node_id(node_id)
    if node_type not in VALID_NODE_TYPES:
        raise KGError(
            f"invalid node_type {node_type!r}: must be one of "
            f"{sorted(VALID_NODE_TYPES)}"
        )
    stamp = _now()

    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        existing = conn.execute(
            "SELECT metadata FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        merged = _merge_metadata(
            _parse_metadata(existing["metadata"]) if existing else {}, metadata
        )
        conn.execute(
            """
            INSERT INTO nodes
                (node_id, node_type, label, artifact_path, run_id, model_id,
                 tool, tool_version, metadata, created_at_utc, updated_at_utc,
                 deleted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(node_id) DO UPDATE SET
                node_type     = excluded.node_type,
                label         = excluded.label,
                artifact_path = excluded.artifact_path,
                run_id        = excluded.run_id,
                model_id      = excluded.model_id,
                tool          = excluded.tool,
                tool_version  = excluded.tool_version,
                metadata      = excluded.metadata,
                updated_at_utc = excluded.updated_at_utc,
                deleted_at_utc = NULL
            """,
            (
                node_id,
                node_type,
                label,
                artifact_path,
                run_id,
                model_id,
                tool,
                tool_version,
                json.dumps(merged, default=str) if merged else None,
                stamp,
                stamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()

    return _row_to_node(row)


def get_node(
    node_id,
    *,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> KGNode:
    """Fetch a non-deleted node. Raises :class:`NodeNotFoundError` on miss."""
    node_id = _validate_node_id(node_id)
    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        row = conn.execute(
            "SELECT * FROM nodes WHERE node_id = ? AND deleted_at_utc IS NULL",
            (node_id,),
        ).fetchone()
    if row is None:
        raise NodeNotFoundError(f"no node found for node_id={node_id}")
    return _row_to_node(row)


def search_nodes(
    *,
    node_type=None,
    label=None,
    limit: int = 50,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[KGNode]:
    """Nodes matching every provided filter, ordered by ``node_id``.

    ``node_type`` matches exactly (validated against the Literal). ``label``
    matches case-insensitively: exact or prefix first, falling back to a
    ``LIKE '%..%'`` substring scan when the first stage is empty. Soft-deleted
    nodes are excluded.
    """
    if node_type is not None and node_type not in VALID_NODE_TYPES:
        raise KGError(
            f"invalid node_type {node_type!r}: must be one of "
            f"{sorted(VALID_NODE_TYPES)}"
        )
    limit = 50 if limit is None else max(0, int(limit))

    clauses = ["deleted_at_utc IS NULL"]
    params: list = []
    if node_type is not None:
        clauses.append("node_type = ?")
        params.append(node_type)

    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if label is not None:
            label = str(label)
            esc = _escape_like(label)
            rows = conn.execute(
                f"SELECT * FROM nodes WHERE {' AND '.join(clauses)} "
                "AND (lower(label) = lower(?) OR lower(label) LIKE lower(?) "
                "ESCAPE '\\') ORDER BY node_id LIMIT ?",
                (*params, label, esc + "%", limit),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    f"SELECT * FROM nodes WHERE {' AND '.join(clauses)} "
                    "AND lower(label) LIKE lower(?) ESCAPE '\\' "
                    "ORDER BY node_id LIMIT ?",
                    (*params, "%" + esc + "%", limit),
                ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM nodes WHERE {' AND '.join(clauses)} "
                "ORDER BY node_id LIMIT ?",
                (*params, limit),
            ).fetchall()

    return [_row_to_node(r) for r in rows]


def delete_node(
    node_id,
    *,
    soft: bool = True,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> None:
    """``soft=True``: stamp ``deleted_at_utc`` (re-delete re-stamps).

    ``soft=False``: hard DELETE — incident edges cascade via the FK
    ``ON DELETE CASCADE`` constraints. Raises :class:`NodeNotFoundError` when no
    row matches ``node_id``.
    """
    node_id = _validate_node_id(node_id)
    stamp = _now()
    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        if soft:
            cur = conn.execute(
                "UPDATE nodes SET deleted_at_utc = ? WHERE node_id = ?",
                (stamp, node_id),
            )
        else:
            cur = conn.execute(
                "DELETE FROM nodes WHERE node_id = ?", (node_id,)
            )
    if cur.rowcount == 0:
        raise NodeNotFoundError(f"no node found for node_id={node_id}")


def upsert_edge(
    source_node,
    target_node,
    relation,
    *,
    metadata=None,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> KGEdge:
    """Upsert an edge keyed on the unique ``(source_node, target_node, relation)`` triple.

    Mints ``e_<uuid4().hex[:12]>`` for the ``edge_id`` on insert; on conflict the
    existing ``edge_id`` and ``created_at_utc`` are preserved and only
    ``metadata`` is replaced and ``deleted_at_utc`` cleared. Raises
    :class:`NodeNotFoundError` when either endpoint is missing or soft-deleted
    (a soft-deleted endpoint is treated as missing).
    """
    source_node = _validate_node_id(source_node)
    target_node = _validate_node_id(target_node)
    relation = _validate_relation(relation)
    edge_id = f"e_{uuid.uuid4().hex[:12]}"
    stamp = _now()
    metadata_json = json.dumps(metadata or {}, default=str)

    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            "SELECT node_id FROM nodes WHERE node_id IN (?, ?) "
            "AND deleted_at_utc IS NULL",
            (source_node, target_node),
        ).fetchall()
        found = {r["node_id"] for r in rows}
        missing = [
            n for n in (source_node, target_node) if n not in found
        ]
        if missing:
            raise NodeNotFoundError(
                f"edge endpoint node(s) not found (or soft-deleted): {missing}"
            )
        conn.execute(
            """
            INSERT INTO edges
                (edge_id, source_node, target_node, relation, metadata,
                 created_at_utc, deleted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(source_node, target_node, relation) DO UPDATE SET
                metadata      = excluded.metadata,
                deleted_at_utc = NULL
            """,
            (
                edge_id,
                source_node,
                target_node,
                relation,
                metadata_json,
                stamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM edges WHERE source_node = ? AND target_node = ? "
            "AND relation = ?",
            (source_node, target_node, relation),
        ).fetchone()

    return _row_to_edge(row)


def get_neighbors(
    node_id,
    *,
    relation=None,
    direction: str = "both",
    depth: int = 1,
    limit: int = 50,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[KGEdge]:
    """Edges reachable from ``node_id`` within ``depth`` hops (bounded BFS).

    ``direction`` ∈ {``in``, ``out``, ``both``}. ``depth`` > 1 uses a recursive
    CTE with a cycle guard (``instr(b.path, n.node_id) = 0``); ``depth`` is
    hard-capped at 5. ``relation`` restricts traversal to one relation.
    Soft-deleted nodes/edges are excluded. Returns ``list[KGEdge]`` ordered by
    BFS depth.
    """
    node_id = _validate_node_id(node_id)
    if direction not in ("in", "out", "both"):
        raise KGError(
            f"direction must be 'in', 'out', or 'both', got {direction!r}"
        )
    try:
        depth = max(1, int(depth))
    except (TypeError, ValueError):
        raise KGError(f"depth must be a positive integer, got {depth!r}") from None
    depth = min(depth, 5)  # hard cap (spec §A.5)
    limit = 50 if limit is None else max(0, int(limit))
    if relation is not None:
        relation = _validate_relation(relation)

    if direction == "out":
        edge_cond = "e.source_node = b.cur"
        other = "e.target_node"
    elif direction == "in":
        edge_cond = "e.target_node = b.cur"
        other = "e.source_node"
    else:  # both
        edge_cond = "(e.source_node = b.cur OR e.target_node = b.cur)"
        other = (
            "CASE WHEN e.source_node = b.cur THEN e.target_node ELSE e.source_node END"
        )

    rel_clause = "AND e.relation = ?" if relation is not None else ""
    params: list = [node_id, node_id]
    if relation is not None:
        params.append(relation)
    params.append(depth)
    params.append(limit)

    sql = f"""
    WITH RECURSIVE bfs(cur, depth, path, edge_id, source_node, target_node,
                       relation, metadata, created_at_utc, deleted_at_utc) AS (
        SELECT ?, 0, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT n.node_id, b.depth + 1, b.path || '>' || n.node_id,
               e.edge_id, e.source_node, e.target_node, e.relation,
               e.metadata, e.created_at_utc, e.deleted_at_utc
        FROM bfs b
        JOIN edges e ON e.deleted_at_utc IS NULL AND {edge_cond} {rel_clause}
        JOIN nodes n ON n.node_id = {other}
            AND n.deleted_at_utc IS NULL
            AND instr(b.path, n.node_id) = 0
        WHERE b.depth < ?
    )
    SELECT DISTINCT edge_id, source_node, target_node, relation,
                    metadata, created_at_utc, deleted_at_utc, depth
    FROM bfs WHERE edge_id IS NOT NULL
    ORDER BY depth, source_node, target_node, relation
    LIMIT ?
    """

    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        start = conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ? AND deleted_at_utc IS NULL",
            (node_id,),
        ).fetchone()
        if start is None:
            raise NodeNotFoundError(f"no node found for node_id={node_id}")
        rows = conn.execute(sql, params).fetchall()

    return [_row_to_edge(r) for r in rows]


def get_provenance_chain(
    node_id,
    *,
    direction: str = "up",
    max_depth: int = 10,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> list[list[KGNode]]:
    """Provenance ancestry/descendancy as ``list[list[KGNode]]`` by depth.

    ``direction="up"`` walks incoming edges with relations
    ``('derived_from','generated_by','references')``; ``direction="down"`` walks
    outgoing edges with relations ``('derived_from','has_artifact')``. The
    recursive CTE is cycle-safe (``instr(c.path, n.node_id) = 0``). Trailing
    empty depths are trimmed.
    """
    node_id = _validate_node_id(node_id)
    if direction not in ("up", "down"):
        raise KGError(
            f"direction must be 'up' or 'down', got {direction!r}"
        )
    try:
        max_depth = max(0, int(max_depth))
    except (TypeError, ValueError):
        raise KGError(
            f"max_depth must be a non-negative integer, got {max_depth!r}"
        ) from None

    if direction == "up":
        relations = ("derived_from", "generated_by", "references")
        edge_cond = "e.target_node = c.cur"
        other = "e.source_node"
    else:  # down
        relations = ("derived_from", "has_artifact")
        edge_cond = "e.source_node = c.cur"
        other = "e.target_node"

    placeholders = ",".join("?" * len(relations))
    sql = f"""
    WITH RECURSIVE chain(cur, depth, path) AS (
        SELECT ?, 0, ?
        UNION ALL
        SELECT n.node_id, c.depth + 1, c.path || '>' || n.node_id
        FROM chain c
        JOIN edges e ON e.deleted_at_utc IS NULL
            AND e.relation IN ({placeholders})
            AND {edge_cond}
        JOIN nodes n ON n.node_id = {other}
            AND n.deleted_at_utc IS NULL
            AND instr(c.path, n.node_id) = 0
        WHERE c.depth < ?
    )
    SELECT cur, depth FROM chain ORDER BY depth, cur
    """

    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        start = conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ? AND deleted_at_utc IS NULL",
            (node_id,),
        ).fetchone()
        if start is None:
            raise NodeNotFoundError(f"no node found for node_id={node_id}")
        rows = conn.execute(
            sql, (node_id, node_id, *relations, max_depth)
        ).fetchall()
        node_ids = [r["cur"] for r in rows]
        nodes_by_id: dict[str, KGNode] = {}
        if node_ids:
            ph = ",".join("?" * len(node_ids))
            for r in conn.execute(
                f"SELECT * FROM nodes WHERE node_id IN ({ph})", node_ids
            ).fetchall():
                nodes_by_id[r["node_id"]] = _row_to_node(r)

    chains: list[list[KGNode]] = []
    for d in range(max_depth + 1):
        chains.append(
            [nodes_by_id[r["cur"]] for r in rows if r["depth"] == d]
        )
    while chains and not chains[-1]:
        chains.pop()  # trim trailing empty depths
    return chains


def graph_stats(
    *,
    kg_db=None,
    env_var: str = DEFAULT_ENV_VAR,
) -> KGStats:
    """Aggregate stats over non-deleted nodes/edges: counts by type/relation,
    top-10 nodes by degree, and the snapshot timestamp.
    """
    db_path = get_kg_path(kg_db, env_var=env_var)
    with _connect(db_path) as conn:
        migrate(conn)
        node_rows = conn.execute(
            "SELECT node_type, COUNT(*) AS n FROM nodes "
            "WHERE deleted_at_utc IS NULL GROUP BY node_type"
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT relation, COUNT(*) AS n FROM edges "
            "WHERE deleted_at_utc IS NULL GROUP BY relation"
        ).fetchall()
        degree_rows = conn.execute(
            "SELECT node_id, COUNT(*) AS n FROM ("
            "    SELECT source_node AS node_id FROM edges WHERE deleted_at_utc IS NULL"
            "    UNION ALL"
            "    SELECT target_node AS node_id FROM edges WHERE deleted_at_utc IS NULL"
            ") GROUP BY node_id ORDER BY n DESC, node_id LIMIT 10"
        ).fetchall()

    return KGStats(
        node_counts={r["node_type"]: r["n"] for r in node_rows},
        edge_counts={r["relation"]: r["n"] for r in edge_rows},
        top_degree=[(r["node_id"], r["n"]) for r in degree_rows],
        updated_at_utc=_now(),
    )
