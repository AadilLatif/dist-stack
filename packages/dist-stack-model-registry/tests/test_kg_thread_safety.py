"""Concurrent access: ThreadPoolExecutor(8) x 200 mixed upsert_node /
upsert_edge / get_node against one file DB — no exceptions, correct counts.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from dist_stack import get_node, upsert_edge, upsert_node


def test_concurrent_mixed_upserts_and_reads(tmp_path):
    db = tmp_path / "kg.sqlite"
    root = "run:root"
    upsert_node(root, "gdm_flow_run", kg_db=db)

    def run(i):
        if i % 3 == 0:
            n = upsert_node(
                f"artifact:/data/n{i:04d}", "artifact", label=f"L{i:04d}",
                kg_db=db,
            )
            return ("ok", n.node_id)
        if i % 3 == 1:
            nid = f"artifact:/data/n{i:04d}"
            upsert_node(nid, "artifact", kg_db=db)  # guarantee the endpoint exists
            e = upsert_edge(root, nid, "has_artifact", kg_db=db)
            return ("ok", e.edge_id)
        n = get_node(root, kg_db=db)
        return ("ok", n.node_id)

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(run, range(200)):
            results.append(result)

    # No exceptions; all tasks completed.
    assert all(status == "ok" for status, _ in results)

    # Correct final counts (distinct node_ids + 1 root; one edge per i%3==1).
    expected_nodes = 1 + sum(1 for i in range(200) if i % 3 in (0, 1))
    expected_edges = sum(1 for i in range(200) if i % 3 == 1)
    with sqlite3.connect(str(db)) as conn:
        n_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        n_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert n_nodes == expected_nodes == 135
    assert n_edges == expected_edges == 67
    assert get_node(root, kg_db=db).node_id == root
