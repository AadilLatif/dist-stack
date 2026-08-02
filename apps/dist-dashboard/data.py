"""Read-only data access layer for the dist-stack visibility app.

Every function here goes through the dist-stack Python APIs (runstore, kg,
registry). It never opens the SQLite files directly and never writes to them.

Design rules
------------
- ``Config`` holds the three resolved DB paths. The UI builds it from env
  vars / ~/.cache defaults / sidebar overrides.
- A missing or unreadable DB file raises :class:`DataError`; the UI catches
  that per call and renders an empty state instead of crashing.
- Functions return either pandas DataFrames (for tables) or the plain records
  the dist-stack API already returns (for detail views / provenance).

The public functions here are the seam the smoke test exercises.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dist_stack.kg.api import (
    VALID_NODE_TYPES,
    get_neighbors as _kg_get_neighbors,
    get_node as _kg_get_node,
    get_provenance_chain as _kg_provenance_chain,
    graph_stats as _kg_graph_stats,
    search_nodes as _kg_search_nodes,
)
from dist_stack.kg.errors import KGError
from dist_stack.registry.api import list_models as _registry_list_models
from dist_stack.registry.errors import RegistryError
from dist_stack.runstore.api import (
    get_run as _runstore_get_run,
    list_artifacts as _runstore_list_artifacts,
    list_runs as _runstore_list_runs,
)
from dist_stack.runstore.errors import RunstoreError

ENV_RUNSTORE = "DIST_STACK_RUNSTORE_DB"
ENV_KG = "DIST_STACK_KG_DB"
ENV_REGISTRY = "DIST_STACK_MODEL_REGISTRY_DB"

# Default file names inside ~/.cache/dist-stack when no env var is set.
DEFAULT_FILENAMES = {
    "runstore": "runstore.db",
    "kg": "kg.db",
    "registry": "model_registry.db",
}

# Page size used by the run-history table.
RUN_PAGE_SIZE = 25

# Stable reference lists for dropdowns / filters.
NODE_TYPES = sorted(VALID_NODE_TYPES)
RELATIONS = [
    "has_artifact",
    "generated_by",
    "derived_from",
    "references",
    "modifies",
    "visualizes",
    "consumes",
    "produces",
    "validates",
    "has_component",
    "parent_of",
]
STATUSES = ["succeeded", "failed", "running", "pending", "cancelled"]


class DataError(Exception):
    """A store could not be read (missing file, corrupt DB, unknown row)."""


@dataclass(frozen=True)
class Config:
    runstore_db: str
    kg_db: str
    registry_db: str


def cache_dir() -> Path:
    return Path.home() / ".cache" / "dist-stack"


def resolve_db_path(store: str, override: str | None) -> str:
    """Resolve one store's DB path: sidebar override > env var > ~/.cache default."""
    env = os.getenv(
        {"runstore": ENV_RUNSTORE, "kg": ENV_KG, "registry": ENV_REGISTRY}[store]
    )
    if override and override.strip():
        candidate = override.strip()
    elif env:
        candidate = env
    else:
        candidate = str(cache_dir() / DEFAULT_FILENAMES[store])
    return os.path.expanduser(candidate)


def resolve_paths(runstore_override=None, kg_override=None, registry_override=None) -> Config:
    return Config(
        runstore_db=resolve_db_path("runstore", runstore_override),
        kg_db=resolve_db_path("kg", kg_override),
        registry_db=resolve_db_path("registry", registry_override),
    )


def db_available(db_path: str) -> bool:
    """A store counts as available only if its file actually exists."""
    return bool(db_path) and os.path.isfile(db_path)


def _guard(db_path: str, fn, *args, **kwargs):
    """Run ``fn`` against a store, translating failures into DataError."""
    if not db_available(db_path):
        raise DataError(f"no database file at {db_path or '(unset)'}")
    try:
        return fn(*args, **kwargs)
    except (RunstoreError, KGError, RegistryError, sqlite3.DatabaseError, OSError) as exc:
        raise DataError(f"{db_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Runstore
# ---------------------------------------------------------------------------


def _runs_frame(records) -> pd.DataFrame:
    rows = [
        {
            "run_id": r.run_id,
            "tool": r.tool,
            "run_type": r.run_type,
            "status": r.status,
            "implementation": r.implementation,
            "session_id": r.session_id,
            "message": r.message,
            "model_id": r.model_id,
            "model_version": r.model_version,
            "created_at_utc": r.created_at_utc,
            "updated_at_utc": r.updated_at_utc,
        }
        for r in records
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("created_at_utc", ascending=False)
    return df.reset_index(drop=True)


def load_runs(
    cfg: Config,
    *,
    tool=None,
    run_type=None,
    status=None,
    session_id=None,
    offset: int = 0,
    limit: int = RUN_PAGE_SIZE,
) -> tuple[pd.DataFrame, bool]:
    """One page of runs plus a ``has_more`` flag.

    Fetches ``limit + 1`` rows so the UI can render a Next button without a
    separate count query (the runstore API exposes no count).
    """
    records = _guard(
        cfg.runstore_db,
        _runstore_list_runs,
        tool=tool,
        run_type=run_type,
        status=status,
        session_id=session_id,
        include_deleted=False,
        limit=limit + 1,
        offset=offset,
        runstore_db=cfg.runstore_db,
    )
    has_more = len(records) > limit
    return _runs_frame(records[:limit]), has_more


def get_run(cfg: Config, run_id: str):
    return _guard(cfg.runstore_db, _runstore_get_run, run_id, runstore_db=cfg.runstore_db)


def load_artifacts(cfg: Config, run_id: str) -> pd.DataFrame:
    records = _guard(
        cfg.runstore_db,
        _runstore_list_artifacts,
        run_id,
        include_deleted=False,
        runstore_db=cfg.runstore_db,
    )
    rows = [
        {
            "artifact_id": a.artifact_id,
            "artifact_path": a.artifact_path,
            "artifact_type": a.artifact_type,
            "tool": a.tool,
            "model_id": a.model_id,
            "model_version": a.model_version,
            "created_at_utc": a.created_at_utc,
        }
        for a in records
    ]
    return pd.DataFrame(rows).reset_index(drop=True)


def run_filter_options(cfg: Config, cap: int = 20_000) -> dict[str, list[str]]:
    """Distinct values for the run-history filter dropdowns.

    Pulled from the top ``cap`` newest runs; with more than ``cap`` runs the
    lists may be incomplete, which is acceptable for a visibility tool.
    """
    records = _guard(
        cfg.runstore_db,
        _runstore_list_runs,
        include_deleted=False,
        limit=cap,
        offset=0,
        runstore_db=cfg.runstore_db,
    )
    out = {"tool": set(), "run_type": set(), "status": set(), "session_id": set()}
    for r in records:
        out["tool"].add(r.tool)
        out["run_type"].add(r.run_type)
        out["status"].add(r.status)
        if r.session_id:
            out["session_id"].add(r.session_id)
    return {k: sorted(v) for k, v in out.items()}


def load_runs_all(cfg: Config, cap: int = 20_000) -> pd.DataFrame:
    """All runs (up to ``cap``) for dashboard counts and focus navigation."""
    records = _guard(
        cfg.runstore_db,
        _runstore_list_runs,
        include_deleted=False,
        limit=cap,
        offset=0,
        runstore_db=cfg.runstore_db,
    )
    return _runs_frame(records)


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------


def find_nodes(cfg: Config, term: str, limit: int = 25) -> list:
    """Resolve a free-text term to node(s).

    Exact ``node_id`` first; then a label search (exact / prefix / substring,
    case-insensitive), which is how run_id / artifact path / model_id surface
    in practice since node labels usually carry those identifiers.
    """
    term = term.strip()
    if not term:
        return []
    try:
        return [_kg_get_node(term, kg_db=cfg.kg_db)]
    except KGError:
        pass
    try:
        return _guard(cfg.kg_db, _kg_search_nodes, label=term, limit=limit, kg_db=cfg.kg_db)
    except DataError:
        return []


def load_chain(cfg: Config, node_id: str, direction: str, max_depth: int) -> list[list]:
    """Provenance chain: ``list[list[KGNode]]`` by depth, root at depth 0."""
    return _guard(
        cfg.kg_db,
        _kg_provenance_chain,
        node_id,
        direction=direction,
        max_depth=max_depth,
        kg_db=cfg.kg_db,
    )


def load_neighbors(cfg: Config, node_id: str, *, relation=None, direction: str = "both", depth: int = 1) -> list:
    return _guard(
        cfg.kg_db,
        _kg_get_neighbors,
        node_id,
        relation=relation,
        direction=direction,
        depth=depth,
        limit=100,
        kg_db=cfg.kg_db,
    )


def get_node(cfg: Config, node_id: str):
    return _guard(cfg.kg_db, _kg_get_node, node_id, kg_db=cfg.kg_db)


def search_nodes_df(cfg: Config, *, node_type=None, label=None, limit: int = 100) -> pd.DataFrame:
    records = _guard(
        cfg.kg_db,
        _kg_search_nodes,
        node_type=node_type,
        label=label,
        limit=limit,
        kg_db=cfg.kg_db,
    )
    rows = [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "label": n.label,
            "run_id": n.run_id,
            "artifact_path": n.artifact_path,
            "model_id": n.model_id,
            "tool": n.tool,
            "created_at_utc": n.created_at_utc,
        }
        for n in records
    ]
    return pd.DataFrame(rows).reset_index(drop=True)


def load_graph_stats(cfg: Config):
    return _guard(cfg.kg_db, _kg_graph_stats, kg_db=cfg.kg_db)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def load_models(cfg: Config) -> pd.DataFrame:
    records = _guard(
        cfg.registry_db,
        _registry_list_models,
        include_deleted=False,
        registry_db=cfg.registry_db,
    )
    rows = [
        {
            "model_id": m.model_id,
            "version": m.version,
            "stored_path": m.stored_path,
            "model_hash": m.model_hash,
            "created_at_utc": m.created_at_utc,
            "metadata": json.dumps(m.metadata, default=str) if m.metadata else None,
        }
        for m in records
    ]
    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Convenience for the dashboard
# ---------------------------------------------------------------------------


def counts_by_status(df: pd.DataFrame) -> dict[str, int]:
    statuses = ["succeeded", "failed", "running", "pending", "cancelled"]
    counts = {s: 0 for s in statuses}
    if not df.empty:
        counts.update(df["status"].value_counts().to_dict())
    return counts
