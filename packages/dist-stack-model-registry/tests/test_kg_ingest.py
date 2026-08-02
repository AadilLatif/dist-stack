"""Ingest (Phase 3b): runstore + registry + sidecar manifests → KG.

Covers the full §B.4 contract: node/edge mapping, derived_from resolution
(artifact / run-fallback / URI-skip / unresolved), model_id references +
stub-creation, idempotency (double-ingest → zero creates), soft-deleted source
skipping, prune (mirror mode), the manifest_dir sweep, per-row error collection,
and `limit`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from dist_stack import (
    IngestReport,
    NodeNotFoundError,
    delete_run,
    get_node,
    search_nodes,
)
from dist_stack.kg import get_neighbors, ingest
from dist_stack.manifest import write_manifest
from dist_stack.registry import register
from dist_stack.runstore import attach_artifact, create_run, ensure_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_ids(kg_db) -> set[str]:
    return {n.node_id for n in search_nodes(kg_db=kg_db, limit=10000)}


def _edge_triples(kg_db) -> set[tuple[str, str, str]]:
    triples = set()
    for nid in _node_ids(kg_db):
        for e in get_neighbors(nid, depth=5, kg_db=kg_db):
            triples.add((e.source_node, e.target_node, e.relation))
    return triples


def _seed_registry(tmp_path, model_id="m1"):
    reg = tmp_path / "registry.sqlite"
    model_file = tmp_path / "model.raw"
    model_file.write_text("MODEL")
    register(
        model_id,
        stored_path=model_file,
        model_hash="abc",
        metadata={"arch": "x"},
        registry_db=reg,
    )
    return reg


def _seed_main(tmp_path):
    """registry model m1 + runstore: r1 (4 artifacts), r2 (no artifacts)."""
    reg = _seed_registry(tmp_path)
    rs = tmp_path / "runstore.sqlite"
    r1 = "ac_000000000001"
    r2 = "sim_000000000002"
    create_run("run_ac_opf", run_type="gdm_flow_run", run_id=r1, model_id="m1", runstore_db=rs)
    create_run("run_simulation", run_type="erad_simulation", run_id=r2, model_id="m1", runstore_db=rs)

    base = tmp_path / "base.json"
    base.write_text("{}")
    write_manifest(
        base, artifact_type="gdm_flow_run", tool="run_ac_opf", tool_version="1.0.0",
        model_id="m1", derived_from=[r1],
    )
    out = tmp_path / "out.json"
    out.write_text("{}")
    write_manifest(
        out, artifact_type="gdm_flow_run", tool="run_ac_opf", tool_version="1.0.0",
        model_id="m1", derived_from=[str(base)],
    )
    uri = tmp_path / "uri.json"
    uri.write_text("{}")
    write_manifest(
        uri, artifact_type="gdm_flow_run", tool="run_ac_opf", tool_version="1.0.0",
        model_id="m1", derived_from=["s3://bucket/key.raw"],
    )
    missing = tmp_path / "missing.json"
    missing.write_text("{}")

    attach_artifact(r1, out, runstore_db=rs)
    attach_artifact(r1, base, runstore_db=rs)
    attach_artifact(r1, uri, runstore_db=rs)
    attach_artifact(r1, missing, runstore_db=rs)  # auto-writes a sidecar...
    (tmp_path / "missing.json.manifest.json").unlink()  # ...which we remove

    kg = tmp_path / "kg.sqlite"
    return kg, rs, reg, r1, r2


def test_ingest_full_fixture(tmp_path):
    kg, rs, reg, r1, r2 = _seed_main(tmp_path)
    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg)

    assert isinstance(rep, IngestReport)
    assert rep.kg_db == str(kg)
    assert rep.nodes_created == 7
    assert rep.nodes_updated == 0
    assert rep.edges_created == 15
    assert rep.edges_updated == 0
    assert rep.derived_from_unresolved == []
    assert rep.derived_from_uri_skipped == 1
    assert rep.sidecar_missing == 1
    assert rep.errors == []

    art_ids = {
        f"artifact:{tmp_path / p}" for p in ("base.json", "out.json", "uri.json", "missing.json")
    }
    assert _node_ids(kg) == {"model:m1", f"run:{r1}", f"run:{r2}"} | art_ids

    expected_edges = set()
    for p in ("base.json", "out.json", "uri.json", "missing.json"):
        a = f"artifact:{tmp_path / p}"
        expected_edges.add((f"run:{r1}", a, "has_artifact"))
        expected_edges.add((a, f"run:{r1}", "generated_by"))
    expected_edges.add((f"artifact:{tmp_path / 'out.json'}", f"artifact:{tmp_path / 'base.json'}", "derived_from"))
    expected_edges.add((f"artifact:{tmp_path / 'base.json'}", f"run:{r1}", "derived_from"))
    expected_edges.add((f"run:{r1}", "model:m1", "references"))
    expected_edges.add((f"run:{r2}", "model:m1", "references"))
    for p in ("base.json", "out.json", "uri.json"):
        expected_edges.add((f"artifact:{tmp_path / p}", "model:m1", "references"))
    assert _edge_triples(kg) == expected_edges
    assert len(expected_edges) == 15

    # model node carries registry metadata
    model = get_node("model:m1", kg_db=kg)
    assert model.node_type == "model"
    assert model.label == "m1"
    assert model.metadata["version"] == 1
    assert model.metadata["stored_path"] == str(tmp_path / "model.raw")
    assert model.metadata["model_hash"] == "abc"
    assert model.metadata["metadata"] == {"arch": "x"}
    assert "stub" not in model.metadata

    # run node
    run_node = get_node(f"run:{r1}", kg_db=kg)
    assert run_node.node_type == "gdm_flow_run"
    assert run_node.label == f"run_ac_opf {r1}"
    assert run_node.metadata["status"] == "succeeded"

    # artifact node with sidecar fields (manifest wins)
    out_node = get_node(f"artifact:{tmp_path / 'out.json'}", kg_db=kg)
    assert out_node.node_type == "gdm_flow_run"
    assert out_node.label == "out.json"
    assert out_node.metadata["run_id"] == r1
    assert out_node.metadata["derived_from_raw"] == [str(tmp_path / "base.json")]
    assert "package" not in out_node.metadata

    # edge metadata carries {tool, tool_version, model_id, config}
    edges = get_neighbors(f"run:{r1}", kg_db=kg)
    meta = {e.metadata.get("tool") for e in edges}
    assert "run_ac_opf" in meta


def test_double_ingest_zero_changes(tmp_path):
    kg, rs, reg, *_ = _seed_main(tmp_path)
    rep1 = ingest(kg_db=kg, runstore_db=rs, registry_db=reg)
    assert rep1.nodes_created == 7
    assert rep1.edges_created == 15
    nodes_before = _node_ids(kg)
    edges_before = _edge_triples(kg)

    rep2 = ingest(kg_db=kg, runstore_db=rs, registry_db=reg)
    assert rep2.nodes_created == 0
    assert rep2.edges_created == 0
    assert rep2.nodes_updated == 7
    assert rep2.edges_updated == 15
    assert rep2.derived_from_uri_skipped == 1
    assert rep2.sidecar_missing == 1
    assert rep2.errors == []
    assert _node_ids(kg) == nodes_before
    assert _edge_triples(kg) == edges_before


def test_soft_deleted_sources_skipped(tmp_path):
    kg, rs, reg, r1, r2 = _seed_main(tmp_path)
    delete_run(r2, runstore_db=rs)  # soft-delete a run (no artifacts)
    gone = tmp_path / "gone.json"
    gone.write_text("{}")
    attach_artifact(r1, gone, runstore_db=rs)
    with sqlite3.connect(str(rs)) as conn:
        conn.execute(
            "UPDATE artifacts SET deleted_at_utc = ? WHERE artifact_path = ?",
            (_now(), str(gone)),
        )

    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg)
    assert rep.errors == []
    nodes = _node_ids(kg)
    assert f"run:{r2}" not in nodes          # soft-deleted run skipped
    assert f"artifact:{gone}" not in nodes   # soft-deleted artifact row skipped
    assert "model:m1" in nodes and f"run:{r1}" in nodes


def test_prune_sweeps_stale_nodes(tmp_path):
    reg = _seed_registry(tmp_path)
    rs = tmp_path / "runstore.sqlite"
    create_run("run_ac_opf", run_type="gdm_flow_run", run_id="r1", model_id="m1", runstore_db=rs)
    create_run("run_simulation", run_type="erad_simulation", run_id="r2", model_id="m1", runstore_db=rs)
    a1 = tmp_path / "a1.json"
    a1.write_text("{}")
    attach_artifact("r1", a1, runstore_db=rs)

    kg = tmp_path / "kg.sqlite"
    ingest(kg_db=kg, runstore_db=rs, registry_db=reg)

    # sources change: r2 deleted, r3 + a3 added
    delete_run("r2", runstore_db=rs)
    create_run("run_ac_pf", run_type="gdm_flow_run", run_id="r3", runstore_db=rs)
    a3 = tmp_path / "a3.json"
    a3.write_text("{}")
    attach_artifact("r3", a3, runstore_db=rs)

    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg, prune=True)
    assert rep.errors == []
    nodes = _node_ids(kg)
    assert "run:r1" in nodes and "run:r3" in nodes
    assert f"artifact:{a1}" in nodes and f"artifact:{a3}" in nodes
    assert "run:r2" not in nodes  # stale → soft-deleted
    with pytest.raises(NodeNotFoundError):
        get_node("run:r2", kg_db=kg)


def test_manifest_dir_sweep_unattached_sidecars(tmp_path):
    reg = _seed_registry(tmp_path)
    rs = tmp_path / "runstore.sqlite"
    ensure_schema(rs)  # empty runstore — no error entries

    sweep = tmp_path / "sweep"
    sweep.mkdir()
    sb = sweep / "base.json"
    sb.write_text("{}")
    write_manifest(sb, artifact_type="artifact", tool="sim", tool_version="1.0", model_id="m1")
    so = sweep / "out.json"
    so.write_text("{}")
    write_manifest(
        so, artifact_type="gdm_system", tool="sim", tool_version="1.0",
        model_id="m1", derived_from=[str(sb)],
    )

    kg = tmp_path / "kg.sqlite"
    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg, manifest_dir=sweep)
    assert rep.errors == []
    assert f"artifact:{sb}" in _node_ids(kg)
    assert f"artifact:{so}" in _node_ids(kg)
    assert "model:m1" in _node_ids(kg)
    assert get_node(f"artifact:{sb}", kg_db=kg).node_type == "artifact"
    assert get_node(f"artifact:{so}", kg_db=kg).node_type == "gdm_system"

    triples = _edge_triples(kg)
    assert (f"artifact:{so}", f"artifact:{sb}", "derived_from") in triples
    assert (f"artifact:{so}", "model:m1", "references") in triples
    assert (f"artifact:{sb}", "model:m1", "references") in triples
    assert all(rel not in ("has_artifact", "generated_by") for _, _, rel in triples)


def test_per_row_errors_collected_never_raised(tmp_path):
    reg = _seed_registry(tmp_path)
    rs = tmp_path / "runstore.sqlite"
    rid = "r1"
    create_run("run_ac_opf", run_type="gdm_flow_run", run_id=rid, model_id="m1", runstore_db=rs)
    a1 = tmp_path / "a1.json"
    a1.write_text("{}")
    attach_artifact(rid, a1, runstore_db=rs)

    # Artifact whose sidecar is a directory (unreadable) → per-row error only.
    bad_dir = tmp_path / "bad_dir"
    bad_dir.mkdir()
    (tmp_path / "bad_dir.manifest.json").write_text("{not valid json")
    with sqlite3.connect(str(rs)) as conn:
        conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, run_id, artifact_path, artifact_type, created_at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            ("art_bad", rid, str(bad_dir), "artifact", _now()),
        )

    kg = tmp_path / "kg.sqlite"
    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg)  # must not raise
    assert rep.errors
    assert any("manifest read failed" in e for e in rep.errors)
    nodes = _node_ids(kg)
    assert "model:m1" in nodes
    assert f"run:{rid}" in nodes
    assert f"artifact:{a1}" in nodes
    assert f"artifact:{bad_dir}" in nodes  # node still created from row data


def test_limit_respected(tmp_path):
    reg = _seed_registry(tmp_path)
    rs = tmp_path / "runstore.sqlite"
    for i in range(3):
        create_run("tool", run_type="gdm_flow_run", run_id=f"r{i}", runstore_db=rs)
        p = tmp_path / f"art{i}.json"
        p.write_text("{}")
        attach_artifact(f"r{i}", p, runstore_db=rs)

    kg = tmp_path / "kg.sqlite"
    rep = ingest(kg_db=kg, runstore_db=rs, registry_db=reg, limit=2)
    assert rep.errors == []
    artifact_nodes = {n for n in _node_ids(kg) if n.startswith("artifact:")}
    assert len(artifact_nodes) == 2
    assert rep.nodes_created == 6  # model + 3 runs + 2 artifacts
