"""Query semantics: get_neighbors relation/direction filters, depth-2 BFS,
cycle safety (self-loop + 2-cycle), get_provenance_chain up/down correctness,
and the max_depth cap.
"""
from __future__ import annotations

import pytest

from dist_stack import (
    KGError,
    NodeNotFoundError,
    delete_node,
    get_neighbors,
    get_provenance_chain,
    upsert_edge,
    upsert_node,
)


def _pairs(edges) -> list[tuple[str, str]]:
    return sorted((e.source_node, e.target_node) for e in edges)


def _node_ids_at(chain, depth: int) -> list[str]:
    return sorted(n.node_id for n in chain[depth])


def test_get_neighbors_relation_and_direction_filters(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("a", "artifact", kg_db=db)
    upsert_node("b", "artifact", kg_db=db)
    upsert_node("c", "artifact", kg_db=db)
    upsert_edge("a", "b", "derived_from", kg_db=db)
    upsert_edge("b", "a", "references", kg_db=db)
    upsert_edge("a", "c", "visualizes", kg_db=db)

    # both directions, no relation filter
    assert _pairs(get_neighbors("a", kg_db=db)) == [
        ("a", "b"), ("a", "c"), ("b", "a"),
    ]
    # out only
    assert _pairs(get_neighbors("a", direction="out", kg_db=db)) == [
        ("a", "b"), ("a", "c"),
    ]
    # in only
    assert _pairs(get_neighbors("a", direction="in", kg_db=db)) == [
        ("b", "a"),
    ]
    # relation filter
    assert _pairs(get_neighbors("a", relation="derived_from", kg_db=db)) == [
        ("a", "b"),
    ]
    assert get_neighbors("a", relation="nope", kg_db=db) == []
    with pytest.raises(KGError):
        get_neighbors("a", direction="sideways", kg_db=db)


def test_depth_2_bfs(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("a", "artifact", kg_db=db)
    upsert_node("b", "artifact", kg_db=db)
    upsert_node("c", "artifact", kg_db=db)
    upsert_edge("a", "b", "derived_from", kg_db=db)
    upsert_edge("b", "c", "derived_from", kg_db=db)

    assert _pairs(get_neighbors("a", depth=1, kg_db=db)) == [("a", "b")]
    assert _pairs(get_neighbors("a", depth=2, kg_db=db)) == [
        ("a", "b"), ("b", "c"),
    ]
    # depth is capped at 5, not an error
    assert _pairs(get_neighbors("a", depth=9, kg_db=db)) == [
        ("a", "b"), ("b", "c"),
    ]


def test_cycle_safety_self_loop_and_2_cycle(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("a", "artifact", kg_db=db)
    upsert_node("b", "artifact", kg_db=db)
    upsert_edge("a", "a", "derived_from", kg_db=db)  # self-loop
    upsert_edge("a", "b", "derived_from", kg_db=db)
    upsert_edge("b", "a", "derived_from", kg_db=db)  # 2-cycle

    # self-loop is excluded by the cycle guard; 2-cycle terminates at depth 1.
    assert _pairs(get_neighbors("a", depth=5, kg_db=db)) == [
        ("a", "b"), ("b", "a"),
    ]
    assert _pairs(get_neighbors("a", direction="out", depth=5, kg_db=db)) == [
        ("a", "b"),
    ]
    assert _pairs(get_neighbors("a", direction="in", depth=5, kg_db=db)) == [
        ("b", "a"),
    ]


def _provenance_fixture(db):
    run_id = "run:r1"
    art1 = "artifact:/data/a.json"
    art2 = "artifact:/data/b.json"
    model = "model:m1"
    upsert_node(run_id, "gdm_flow_run", kg_db=db)
    upsert_node(art1, "artifact", kg_db=db)
    upsert_node(art2, "artifact", kg_db=db)
    upsert_node(model, "model", kg_db=db)
    upsert_edge(run_id, art1, "has_artifact", kg_db=db)
    upsert_edge(art1, art2, "derived_from", kg_db=db)
    upsert_edge(art2, model, "references", kg_db=db)
    return run_id, art1, art2, model


def test_provenance_chain_up(tmp_path):
    db = tmp_path / "kg.sqlite"
    run_id, art1, art2, model = _provenance_fixture(db)
    chain = get_provenance_chain(model, direction="up", kg_db=db)
    assert _node_ids_at(chain, 0) == [model]
    assert _node_ids_at(chain, 1) == [art2]   # references → model
    assert _node_ids_at(chain, 2) == [art1]   # derived_from → art2
    assert len(chain) == 3                    # run unreachable via up relations


def test_provenance_chain_down(tmp_path):
    db = tmp_path / "kg.sqlite"
    run_id, art1, art2, model = _provenance_fixture(db)
    chain = get_provenance_chain(run_id, direction="down", kg_db=db)
    assert _node_ids_at(chain, 0) == [run_id]
    assert _node_ids_at(chain, 1) == [art1]   # has_artifact
    assert _node_ids_at(chain, 2) == [art2]   # derived_from
    assert len(chain) == 3                    # model unreachable (references not down)
    with pytest.raises(KGError):
        get_provenance_chain(run_id, direction="sideways", kg_db=db)


def test_provenance_chain_max_depth_cap(tmp_path):
    db = tmp_path / "kg.sqlite"
    run_id, art1, art2, model = _provenance_fixture(db)
    chain = get_provenance_chain(model, direction="up", max_depth=1, kg_db=db)
    assert _node_ids_at(chain, 0) == [model]
    assert _node_ids_at(chain, 1) == [art2]
    assert len(chain) == 2


def test_provenance_chain_cycle_safe(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("a", "artifact", kg_db=db)
    upsert_node("b", "artifact", kg_db=db)
    upsert_edge("a", "b", "derived_from", kg_db=db)
    upsert_edge("b", "a", "derived_from", kg_db=db)
    chain = get_provenance_chain("a", direction="up", kg_db=db)
    assert _node_ids_at(chain, 0) == ["a"]
    assert _node_ids_at(chain, 1) == ["b"]  # b→a incoming; a→b guarded out
    assert len(chain) == 2


def test_queries_exclude_soft_deleted(tmp_path):
    db = tmp_path / "kg.sqlite"
    upsert_node("a", "artifact", kg_db=db)
    upsert_node("b", "artifact", kg_db=db)
    upsert_edge("a", "b", "derived_from", kg_db=db)
    delete_node("b", kg_db=db)
    assert get_neighbors("a", kg_db=db) == []
    with pytest.raises(NodeNotFoundError):
        get_provenance_chain("b", direction="down", kg_db=db)
