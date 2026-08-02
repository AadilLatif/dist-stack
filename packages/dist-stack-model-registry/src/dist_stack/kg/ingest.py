"""Ingestion: runstore + registry + sidecar manifests → knowledge graph.

Phase 3b — spec doc 12 §B. Stateless, mirroring the runstore/registry
conventions. Every write goes through the KG public API (``upsert_node`` /
``upsert_edge``), so the pass is idempotent: re-running against unchanged
sources yields zero creates.

Sources are resolved per call (explicit arg > env var). An unavailable
*optional* source (runstore or registry) is skipped and reported in
``report.errors`` rather than aborting; the KG path is required.

Pass order (§B.4): (1) registry models → ``model:`` nodes; (2) ``runs`` →
``run:`` nodes; (3) ``artifacts`` + sidecars → ``artifact:`` nodes; (4) edges
(``has_artifact``/``generated_by`` from rows, ``derived_from`` from sidecars,
``references`` from model_id fields); (5) optional ``manifest_dir`` sweep
(unattached sidecars — artifact nodes + derived_from/references edges, no run
edges).

Prune (mirror mode): after the pass, nodes whose ``updated_at_utc`` predates
the pass start are soft-deleted. Edges carry no ``updated_at_utc`` (schema
§A.2), so live edges are matched against the set of ``(source, target,
relation)`` triples upserted during this pass and everything else is
soft-deleted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ..manifest import MANIFEST_SUFFIX, has_manifest, read_manifest
from ..registry import get_registry_path, list_models
from ..registry.api import DEFAULT_ENV_VAR as REGISTRY_DEFAULT_ENV
from ..registry.errors import RegistryUnavailableError
from ..runstore import get_runstore_path
from ..runstore.api import DEFAULT_ENV_VAR as RUNSTORE_DEFAULT_ENV
from ..runstore.errors import RunstoreUnavailableError
from ..runstore.sqlite import _connect as _runstore_connect
from .api import (
    DEFAULT_ENV_VAR,
    ensure_schema,
    get_kg_path,
    upsert_edge,
    upsert_node,
)
from .model import IngestReport
from .sqlite import _connect as _kg_connect

__all__ = ["ingest", "DEFAULT_ENV_VAR"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_payload(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class _Reporter:
    """Mutable counters; the frozen :class:`IngestReport` is built at the end."""

    def __init__(self, kg_db: str, pass_started_at_utc: str) -> None:
        self.kg_db = kg_db
        self.pass_started_at_utc = pass_started_at_utc
        self.nodes_created = 0
        self.nodes_updated = 0
        self.edges_created = 0
        self.edges_updated = 0
        self.derived_from_unresolved: list[str] = []
        self.derived_from_uri_skipped = 0
        self.sidecar_missing = 0
        self.errors: list[str] = []

    def report(self) -> IngestReport:
        return IngestReport(
            kg_db=self.kg_db,
            pass_started_at_utc=self.pass_started_at_utc,
            nodes_created=self.nodes_created,
            nodes_updated=self.nodes_updated,
            edges_created=self.edges_created,
            edges_updated=self.edges_updated,
            derived_from_unresolved=list(self.derived_from_unresolved),
            derived_from_uri_skipped=self.derived_from_uri_skipped,
            sidecar_missing=self.sidecar_missing,
            errors=list(self.errors),
        )


# --- KG existence probes (created vs updated counting) ----------------------


def _node_exists(kg_db: str, node_id: str) -> bool:
    with _kg_connect(kg_db) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            is not None
        )


def _edge_exists(kg_db: str, source: str, target: str, relation: str) -> bool:
    with _kg_connect(kg_db) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM edges WHERE source_node = ? AND target_node = ? "
                "AND relation = ?",
                (source, target, relation),
            ).fetchone()
            is not None
        )


def _upsert_node(reporter: _Reporter, kg_db: str, node_id, node_type, **kwargs):
    if _node_exists(kg_db, node_id):
        reporter.nodes_updated += 1
    else:
        reporter.nodes_created += 1
    return upsert_node(node_id, node_type, kg_db=kg_db, **kwargs)


def _upsert_edge(reporter: _Reporter, kg_db: str, source, target, relation, *, metadata=None):
    if _edge_exists(kg_db, source, target, relation):
        reporter.edges_updated += 1
    else:
        reporter.edges_created += 1
    return upsert_edge(source, target, relation, metadata=metadata, kg_db=kg_db)


# --- source readers ----------------------------------------------------------


def _read_runs(runstore_db: str) -> list:
    with _runstore_connect(runstore_db) as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE deleted_at_utc IS NULL "
            "ORDER BY created_at_utc, run_id"
        ).fetchall()


def _read_artifacts(runstore_db: str, limit: int | None) -> list:
    with _runstore_connect(runstore_db) as conn:
        sql = (
            "SELECT a.* FROM artifacts a JOIN runs r ON r.run_id = a.run_id "
            "WHERE a.deleted_at_utc IS NULL AND r.deleted_at_utc IS NULL "
            "ORDER BY a.run_id, a.artifact_id"
        )
        params: list = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        return conn.execute(sql, params).fetchall()


# --- §B.3 derived_from resolution -------------------------------------------


def _resolve_derived_from(entry, source_node_id: str, kg_db: str):
    """Resolve one ``derived_from`` entry per §B.3.

    Returns ``("artifact"|"run", node_id)`` on a hit, else ``"self"`` (skip),
    ``"uri"`` (counted), or ``None`` (unresolved).
    """
    entry = str(entry)
    if entry == source_node_id:
        return "self"
    if entry.startswith("artifact:"):
        return ("artifact", entry) if _node_exists(kg_db, entry) else None
    if entry.startswith("run:"):
        return ("run", entry) if _node_exists(kg_db, entry) else None

    candidates = (os.path.normpath(entry), os.path.normpath(os.path.abspath(entry)))
    for candidate in candidates:
        nid = "artifact:" + candidate
        if nid == source_node_id:
            return "self"
        if _node_exists(kg_db, nid):
            return ("artifact", nid)

    run_nid = "run:" + entry
    if _node_exists(kg_db, run_nid):
        return ("run", run_nid)
    if "://" in entry:
        return "uri"
    return None


def _add_derived_from_edges(reporter: _Reporter, kg_db: str, node_id: str, manifest, edge_meta: dict, touched_edges: set):
    for entry in manifest.derived_from:
        resolved = _resolve_derived_from(entry, node_id, kg_db)
        if resolved == "self":
            continue
        if resolved == "uri":
            reporter.derived_from_uri_skipped += 1
            continue
        if resolved is None:
            reporter.derived_from_unresolved.append(str(entry))
            continue
        _target = resolved[1]
        _upsert_edge(reporter, kg_db, node_id, _target, "derived_from", metadata=edge_meta)
        touched_edges.add((node_id, _target, "derived_from"))


def _ensure_model(reporter: _Reporter, kg_db: str, model_id: str) -> None:
    """Stub-create a ``model:<model_id>`` node when the registry pass missed it."""
    nid = f"model:{model_id}"
    if _node_exists(kg_db, nid):
        return
    _upsert_node(
        reporter, kg_db, nid, "model",
        label=model_id, model_id=model_id, metadata={"stub": True},
    )


def _add_references_edge(reporter: _Reporter, kg_db: str, source_nid: str, model_id, edge_meta: dict, touched_edges: set) -> None:
    if not model_id:
        return
    _ensure_model(reporter, kg_db, model_id)
    model_nid = f"model:{model_id}"
    _upsert_edge(reporter, kg_db, source_nid, model_nid, "references", metadata=edge_meta)
    touched_edges.add((source_nid, model_nid, "references"))


# --- metadata builders -------------------------------------------------------


def _artifact_node_metadata(art, manifest) -> dict:
    """Artifact node metadata: runstore row fields first, sidecar fields win."""
    meta: dict = {}
    if art is not None:
        for key in (
            "artifact_id", "run_id", "tool", "tool_version",
            "model_id", "model_version", "model_hash",
        ):
            value = art[key]
            if value is not None:
                meta[key] = value
    if manifest is not None:
        for key in (
            "tool", "tool_version", "model_id", "model_version", "model_hash",
            "package", "package_version", "config", "created_at_utc",
        ):
            value = getattr(manifest, key)
            if value is not None:
                meta[key] = value
        meta["derived_from_raw"] = list(manifest.derived_from)
    return meta


def _artifact_node_type(manifest, art) -> str:
    if manifest is not None and manifest.artifact_type:
        return manifest.artifact_type
    if art is not None and art["artifact_type"]:
        return art["artifact_type"]
    return "artifact"


def _edge_metadata(manifest, art) -> dict:
    """Edge metadata ``{tool, tool_version, model_id, model_hash, config}``."""
    meta: dict = {}
    if manifest is not None:
        for key in ("tool", "tool_version", "model_id", "model_hash"):
            value = getattr(manifest, key)
            if value is not None:
                meta[key] = value
        if manifest.config:
            meta["config"] = manifest.config
    elif art is not None:
        for key in ("tool", "tool_version", "model_id", "model_hash"):
            value = art[key]
            if value is not None:
                meta[key] = value
    return meta


# --- prune (mirror mode) -----------------------------------------------------


def _prune(reporter: _Reporter, kg_db: str, pass_started_at_utc: str, touched_edges: set) -> None:
    """Soft-delete nodes/edges not re-affirmed by this pass (prune=True)."""
    stamp = _now()
    with _kg_connect(kg_db) as conn:
        conn.execute(
            "UPDATE nodes SET deleted_at_utc = ? "
            "WHERE deleted_at_utc IS NULL AND updated_at_utc < ?",
            (stamp, pass_started_at_utc),
        )
        # Edges have no updated_at_utc (§A.2): mirror against the touched set.
        conn.execute(
            "UPDATE edges SET deleted_at_utc = ? WHERE deleted_at_utc IS NULL",
            (stamp,),
        )
        for source, target, relation in touched_edges:
            conn.execute(
                "UPDATE edges SET deleted_at_utc = NULL "
                "WHERE source_node = ? AND target_node = ? AND relation = ? "
                "AND deleted_at_utc IS NOT NULL",
                (source, target, relation),
            )


# --- the pass -----------------------------------------------------------------


def ingest(
    *,
    kg_db=None,
    runstore_db=None,
    registry_db=None,
    manifest_dir=None,
    kg_env: str = DEFAULT_ENV_VAR,
    runstore_env: str = RUNSTORE_DEFAULT_ENV,
    registry_env: str = REGISTRY_DEFAULT_ENV,
    prune: bool = False,
    limit: int | None = None,
) -> IngestReport:
    """Ingest runstore + registry + sidecars into the KG (spec doc 12 §B).

    Idempotent: every write is an upsert, so re-running against unchanged
    sources yields ``nodes_created == edges_created == 0``. Soft-deleted source
    rows are skipped and never resurrected. ``prune=True`` soft-deletes KG
    nodes/edges not re-affirmed this pass. ``limit`` caps the number of artifact
    rows ingested (the primary source index, §B.1). Never fails mid-pass:
    per-row errors are collected in ``report.errors``.
    """
    pass_started_at_utc = _now()
    kg_path = get_kg_path(kg_db, env_var=kg_env)
    ensure_schema(kg_path)  # the existence probes below need the tables
    reporter = _Reporter(kg_path, pass_started_at_utc)
    touched_edges: set[tuple[str, str, str]] = set()

    # Optional sources resolve lazily; a missing source is skipped + reported.
    runstore_path: str | None = None
    try:
        runstore_path = get_runstore_path(runstore_db, env_var=runstore_env)
    except RunstoreUnavailableError as exc:
        reporter.errors.append(f"runstore skipped: {exc}")
    registry_path: str | None = None
    try:
        registry_path = get_registry_path(registry_db, env_var=registry_env)
    except RegistryUnavailableError as exc:
        reporter.errors.append(f"registry skipped: {exc}")

    # -- Pass 1: registry models → model nodes -------------------------------
    if registry_path is not None:
        try:
            for record in list_models(registry_db=registry_path):
                _upsert_node(
                    reporter, kg_path,
                    f"model:{record.model_id}", "model",
                    label=record.model_id,
                    model_id=record.model_id,
                    metadata={
                        "version": record.version,
                        "stored_path": record.stored_path,
                        "model_hash": record.model_hash,
                        "metadata": record.metadata,
                        "created_at_utc": record.created_at_utc,
                    },
                )
        except Exception as exc:  # noqa: BLE001 — never fail mid-pass
            reporter.errors.append(f"registry models pass failed: {exc!r}")

    # -- Pass 2: runs → run nodes -------------------------------------------
    runs: list = []
    if runstore_path is not None:
        try:
            runs = _read_runs(runstore_path)
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"runstore runs read failed: {exc!r}")
            runs = []
    for run in runs:
        try:
            run_id = run["run_id"]
            meta = {
                "tool": run["tool"],
                "tool_version": run["tool_version"],
                "status": run["status"],
                "implementation": run["implementation"],
                "session_id": run["session_id"],
                "message": run["message"],
                "payload": _parse_payload(run["payload"]),
                "created_at_utc": run["created_at_utc"],
            }
            meta = {k: v for k, v in meta.items() if v is not None}
            _upsert_node(
                reporter, kg_path,
                f"run:{run_id}", run["run_type"],
                label=f"{run['tool']} {run_id}",
                run_id=run_id,
                tool=run["tool"],
                tool_version=run["tool_version"],
                metadata=meta,
            )
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"run row {run['run_id']!r}: {exc!r}")

    # -- Pass 3: artifacts + sidecars → artifact nodes -----------------------
    artifact_rows: list = []
    if runstore_path is not None:
        try:
            artifact_rows = _read_artifacts(runstore_path, limit)
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"runstore artifacts read failed: {exc!r}")
            artifact_rows = []

    artifact_info: list = []  # (art row, manifest-or-None, artifact node_id)
    for art in artifact_rows:
        try:
            artifact_path = os.fspath(art["artifact_path"])
            node_id = "artifact:" + os.path.normpath(artifact_path)
            manifest = None
            if has_manifest(artifact_path):
                try:
                    manifest = read_manifest(artifact_path)
                except Exception as exc:  # noqa: BLE001
                    reporter.errors.append(
                        f"manifest read failed for {artifact_path!r}: {exc!r}"
                    )
            else:
                reporter.sidecar_missing += 1
            meta = _artifact_node_metadata(art, manifest)
            _upsert_node(
                reporter, kg_path, node_id,
                _artifact_node_type(manifest, art),
                label=os.path.basename(artifact_path),
                artifact_path=artifact_path,
                run_id=art["run_id"],
                model_id=meta.get("model_id"),
                tool=meta.get("tool"),
                tool_version=meta.get("tool_version"),
                metadata=meta,
            )
            artifact_info.append((art, manifest, node_id))
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"artifact row {art['artifact_id']!r}: {exc!r}")

    # -- Pass 4: edges -------------------------------------------------------
    for art, manifest, node_id in artifact_info:
        try:
            run_nid = f"run:{art['run_id']}"
            edge_meta = _edge_metadata(manifest, art)
            if _node_exists(kg_path, run_nid):
                _upsert_edge(reporter, kg_path, run_nid, node_id, "has_artifact", metadata=edge_meta)
                touched_edges.add((run_nid, node_id, "has_artifact"))
                _upsert_edge(reporter, kg_path, node_id, run_nid, "generated_by", metadata=edge_meta)
                touched_edges.add((node_id, run_nid, "generated_by"))
            if manifest is not None:
                _add_derived_from_edges(reporter, kg_path, node_id, manifest, edge_meta, touched_edges)
            model_id = (manifest.model_id if manifest is not None else None) or art["model_id"]
            _add_references_edge(reporter, kg_path, node_id, model_id, edge_meta, touched_edges)
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"artifact edges {art['artifact_id']!r}: {exc!r}")

    for run in runs:
        try:
            run_meta = {"tool": run["tool"]} if run["tool"] else {}
            _add_references_edge(
                reporter, kg_path, f"run:{run['run_id']}",
                run["model_id"], run_meta, touched_edges,
            )
        except Exception as exc:  # noqa: BLE001
            reporter.errors.append(f"run references {run['run_id']!r}: {exc!r}")

    # -- Pass 5: manifest_dir sweep (unattached sidecars) ---------------------
    # Two phases: create every swept artifact node first, then resolve
    # derived_from/references — os.walk order must not affect resolution.
    sweep_records: list = []  # (manifest, node_id)
    if manifest_dir:
        for root, _dirs, files in os.walk(os.fspath(manifest_dir)):
            for fname in files:
                if not fname.endswith(MANIFEST_SUFFIX):
                    continue
                sidecar = os.path.join(root, fname)
                artifact_path = sidecar[: -len(MANIFEST_SUFFIX)]
                try:
                    manifest = read_manifest(artifact_path)
                except Exception as exc:  # noqa: BLE001
                    reporter.errors.append(f"manifest_dir sweep {sidecar!r}: {exc!r}")
                    continue
                node_id = "artifact:" + os.path.normpath(artifact_path)
                meta = _artifact_node_metadata(None, manifest)
                _upsert_node(
                    reporter, kg_path, node_id,
                    _artifact_node_type(manifest, None),
                    label=os.path.basename(artifact_path),
                    artifact_path=artifact_path,
                    model_id=manifest.model_id,
                    tool=manifest.tool,
                    tool_version=manifest.tool_version,
                    metadata=meta,
                )
                sweep_records.append((manifest, node_id))
        for manifest, node_id in sweep_records:
            try:
                edge_meta = _edge_metadata(manifest, None)
                _add_derived_from_edges(reporter, kg_path, node_id, manifest, edge_meta, touched_edges)
                _add_references_edge(reporter, kg_path, node_id, manifest.model_id, edge_meta, touched_edges)
            except Exception as exc:  # noqa: BLE001
                reporter.errors.append(f"manifest_dir edges {node_id!r}: {exc!r}")

    # -- prune (mirror mode) ---------------------------------------------------
    if prune:
        _prune(reporter, kg_path, pass_started_at_utc, touched_edges)

    return reporter.report()
