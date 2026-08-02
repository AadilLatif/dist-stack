"""Public API semantics: node/edge upsert idempotency, created_at_utc
preservation, metadata merge, resurrect-after-soft-delete, NodeNotFoundError,
and FK cascade on hard delete.
"""
from __future__ import annotations

import sqlite3

import pytest

from dist_stack import (
    KGEdge,
    KGNode,
    KGUnavailableError,
    NodeNotFoundError,
    delete_node,
    get_kg_path,
    get_node,
    upsert_edge,
    upsert_node,
)
from dist_stack.kg import ensure_schema


def edge_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def node_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def test_upsert_create_then_update_preserves_created(tmp_path):
    db = tmp_path / "kg.sqlite"
    first = upsert_node(
        "artifact:/data/out.json", "artifact", label="first", kg_db=db
    )
    assert isinstance(first, KGNode)
    second = upsert_node(
        "artifact:/data/out.json",
        "artifact",
        label="second",
        tool="sim",
        kg_db=db,
    )
    assert second.label == "second"
    assert second.tool == "sim"
    assert second.created_at_utc == first.created_at_utc  # preserved
    assert second.updated_at_utc is not None
    assert get_node("artifact:/data/out.json", kg_db=db) == second


def test_resurrect_after_soft_delete(tmp_path):
    db = tmp_path / "kg.sqlite"
    n1 = upsert_node("model:m1", "model", label="v1", kg_db=db)
    delete_node("model:m1", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        get_node("model:m1", kg_db=db)
    n2 = upsert_node("model:m1", "model", label="v2", kg_db=db)
    assert n2.deleted_at_utc is None  # resurrected
    assert n2.created_at_utc == n1.created_at_utc
    assert get_node("model:m1", kg_db=db).label == "v2"


def test_metadata_merge_new_keys_overwrite_existing_kept(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("n1", "artifact", metadata={"a": 1, "b": 2, "registry": "x"}, kg_db=db)
    upsert_node(
        "n1",
        "artifact",
        metadata={"b": 3, "manifest": "y", "registry": "z"},
        kg_db=db,
    )
    node = get_node("n1", kg_db=db)
    assert node.metadata == {"a": 1, "b": 3, "manifest": "y", "registry": "z"}
    # metadata=None leaves the stored metadata untouched
    upsert_node("n1", "artifact", metadata=None, kg_db=db)
    assert get_node("n1", kg_db=db).metadata == {
        "a": 1,
        "b": 3,
        "manifest": "y",
        "registry": "z",
    }


def test_edge_upsert_idempotent_via_unique_triple(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("run:r1", "gdm_flow_run", kg_db=db)
    upsert_node("artifact:/data/out.json", "artifact", kg_db=db)
    e1 = upsert_edge(
        "run:r1", "artifact:/data/out.json", "has_artifact",
        metadata={"tool": "sim"}, kg_db=db,
    )
    assert isinstance(e1, KGEdge)
    assert e1.edge_id.startswith("e_")
    # Second insert on the same unique triple → same edge_id, created preserved,
    # metadata replaced.
    e2 = upsert_edge(
        "run:r1", "artifact:/data/out.json", "has_artifact",
        metadata={"tool": "sim2"}, kg_db=db,
    )
    assert e2.edge_id == e1.edge_id
    assert e2.created_at_utc == e1.created_at_utc
    assert e2.metadata == {"tool": "sim2"}
    # A different relation on the same endpoints is a new edge.
    e3 = upsert_edge(
        "run:r1", "artifact:/data/out.json", "derived_from", kg_db=db
    )
    assert e3.edge_id != e1.edge_id
    assert edge_count(db) == 2


def test_node_not_found(tmp_path):
    db = tmp_path / "kg.sqlite"
    ensure_schema(db)
    with pytest.raises(NodeNotFoundError):
        get_node("missing", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        delete_node("missing", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        delete_node("missing", soft=False, kg_db=db)


def test_edge_endpoint_missing_raises(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("n1", "artifact", kg_db=db)
    upsert_node("n2", "artifact", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        upsert_edge("missing", "n2", "references", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        upsert_edge("n1", "missing", "references", kg_db=db)
    # Soft-deleted endpoint is treated as missing.
    delete_node("n1", kg_db=db)
    with pytest.raises(NodeNotFoundError):
        upsert_edge("n1", "n2", "references", kg_db=db)


def test_hard_delete_cascades_edges(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("n1", "artifact", kg_db=db)
    upsert_node("n2", "artifact", kg_db=db)
    upsert_edge("n1", "n2", "references", kg_db=db)
    assert edge_count(db) == 1
    delete_node("n1", soft=False, kg_db=db)
    assert node_count(db) == 1
    assert edge_count(db) == 0  # FK ON DELETE CASCADE
    assert get_node("n2", kg_db=db).node_id == "n2"


def test_get_kg_path_and_schema(tmp_path):
    db = tmp_path / "kg.sqlite"
    assert get_kg_path(kg_db=db) == str(db)
    ensure_schema(db)
    assert node_count(db) == 0
