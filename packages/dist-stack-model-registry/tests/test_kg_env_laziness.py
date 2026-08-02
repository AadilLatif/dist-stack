"""Env var read lazily per call, never at import.

``DIST_STACK_KG_DB`` is set *after* the library has already been imported and
must be honored on the next call; unset → KGUnavailableError at call time only.
"""
from __future__ import annotations

import pytest

import dist_stack  # noqa: F401  (imported BEFORE env is set — the point)
from dist_stack import (
    KGUnavailableError,
    NodeNotFoundError,
    delete_node,
    get_kg_path,
    get_neighbors,
    get_node,
    get_provenance_chain,
    graph_stats,
    search_nodes,
    upsert_edge,
    upsert_node,
)


def test_env_set_after_import_resolves(tmp_path, monkeypatch):
    db = tmp_path / "kg.sqlite"
    monkeypatch.setenv("DIST_STACK_KG_DB", str(db))
    n = upsert_node("artifact:/a.json", "artifact")
    assert n.node_id == "artifact:/a.json"
    assert get_node("artifact:/a.json").node_id == "artifact:/a.json"
    assert get_kg_path() == str(db)


def test_env_unset_raises_at_call_time(tmp_path, monkeypatch):
    monkeypatch.delenv("DIST_STACK_KG_DB", raising=False)
    with pytest.raises(KGUnavailableError):
        get_kg_path()
    with pytest.raises(KGUnavailableError):
        upsert_node("n1", "artifact")
    with pytest.raises(KGUnavailableError):
        get_node("anything")
    with pytest.raises(KGUnavailableError):
        search_nodes()
    with pytest.raises(KGUnavailableError):
        delete_node("anything")
    with pytest.raises(KGUnavailableError):
        upsert_edge("a", "b", "references")
    with pytest.raises(KGUnavailableError):
        get_neighbors("anything")
    with pytest.raises(KGUnavailableError):
        get_provenance_chain("anything")
    with pytest.raises(KGUnavailableError):
        graph_stats()


def test_env_read_lazily_per_call(tmp_path, monkeypatch):
    db1 = tmp_path / "one.sqlite"
    db2 = tmp_path / "two.sqlite"
    upsert_node("a", "artifact", kg_db=db1)
    upsert_node("b", "artifact", kg_db=db2)

    monkeypatch.setenv("DIST_STACK_KG_DB", str(db1))
    assert get_node("a").node_id == "a"
    with pytest.raises(NodeNotFoundError):
        get_node("b")

    # Switching the env var between calls must take effect immediately.
    monkeypatch.setenv("DIST_STACK_KG_DB", str(db2))
    assert get_node("b").node_id == "b"
    with pytest.raises(NodeNotFoundError):
        get_node("a")

    monkeypatch.delenv("DIST_STACK_KG_DB")
    with pytest.raises(KGUnavailableError):
        get_node("a")


def test_empty_env_var_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("DIST_STACK_KG_DB", "")
    with pytest.raises(KGUnavailableError):
        get_kg_path()
