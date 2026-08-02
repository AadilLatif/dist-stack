"""End-to-end integration: seed the KG via ``dist_stack.kg`` API upserts, then
query it back through the MCP tool functions (round-trip across the store
boundary). Also verifies the server exposes the full 7-tool surface.
"""

from __future__ import annotations

import json

from dist_stack.kg import upsert_edge, upsert_node
from dist_stack.registry import register
from dist_stack.runstore import attach_artifact, create_run

from kg_server.server import create_server

OUT_ART = "/work/rt1/out.json"


def _fn(name: str):
    return create_server()._tool_manager._tools[name].fn


def test_upsert_to_query_round_trip(kg_db):
    # -- seed the KG through the store API (as the ingester would) ------------
    upsert_node(
        "run:rt1", "gdm_flow_run", label="rt1", run_id="rt1",
        tool="gdm_flow", tool_version="1.0.0", kg_db=kg_db,
    )
    upsert_node(
        f"artifact:{OUT_ART}", "artifact", label="out.json",
        artifact_path=OUT_ART, run_id="rt1", kg_db=kg_db,
    )
    upsert_edge(
        "run:rt1", f"artifact:{OUT_ART}", "has_artifact",
        metadata={"tool": "gdm_flow"}, kg_db=kg_db,
    )
    upsert_edge(f"artifact:{OUT_ART}", "run:rt1", "generated_by", kg_db=kg_db)

    # -- query back through the tools ----------------------------------------
    node = json.loads(_fn("get_node")("run:rt1"))
    assert node["success"] is True
    assert node["node"]["node_type"] == "gdm_flow_run"
    assert node["node"]["label"] == "rt1"

    prov = json.loads(_fn("query_provenance")(artifact_path=OUT_ART))
    assert prov["success"] is True
    assert prov["node"]["node_id"] == f"artifact:{OUT_ART}"
    assert {n["edge"]["relation"] for n in prov["neighbors"]} == {
        "has_artifact",
        "generated_by",
    }

    neighbors = json.loads(_fn("get_neighbors")("run:rt1", direction="out"))
    assert {n["node"]["node_id"] for n in neighbors["neighbors"]} == {
        f"artifact:{OUT_ART}"
    }

    chain = json.loads(_fn("get_provenance_chain")("run:rt1", direction="up"))
    assert chain["success"] is True
    assert chain["chain"][0][0]["node_id"] == "run:rt1"
    assert {n["node_id"] for n in chain["chain"][1]} == {f"artifact:{OUT_ART}"}

    found = json.loads(_fn("search_nodes")(node_type="artifact", label="out.json"))
    assert found["success"] is True
    assert found["count"] == 1
    assert found["nodes"][0]["node_id"] == f"artifact:{OUT_ART}"

    stats = json.loads(_fn("graph_stats")())
    assert stats["success"] is True
    assert stats["stats"]["nodes"]["artifact"] == 1
    assert stats["stats"]["edges"]["has_artifact"] == 1
    assert stats["stats"]["updated_at_utc"]


def test_ingest_to_query_round_trip(kg_db, runstore_db, registry_db, tmp_path):
    """Seed runstore + registry, run the ``ingest`` tool, then query back."""
    create_run(
        "gdm_flow", run_type="gdm_flow_run", run_id="it1", model_id="m1",
        runstore_db=runstore_db,
    )
    art = tmp_path / "it_out.json"
    art.write_text("{}")
    attach_artifact("it1", str(art), runstore_db=runstore_db)
    model_path = tmp_path / "m1.model"
    model_path.write_text("model")
    register("m1", stored_path=str(model_path), registry_db=registry_db)

    ingested = json.loads(
        _fn("ingest")(
            runstore_db=str(runstore_db), registry_db=str(registry_db)
        )
    )
    assert ingested["success"] is True
    assert ingested["report"]["nodes_created"] >= 3
    assert ingested["report"]["errors"] == []

    # the ingested graph answers the same queries as the API-seeded graph
    prov = json.loads(_fn("query_provenance")(run_id="it1"))
    assert prov["success"] is True
    assert prov["node"]["node_id"] == "run:it1"
    assert {n["node"]["node_id"] for n in prov["neighbors"]} == {
        f"artifact:{art}",
        "model:m1",  # run references its model via runs.model_id
    }

    chain = json.loads(_fn("get_provenance_chain")("run:it1", direction="up"))
    assert chain["success"] is True
    assert {n["node_id"] for n in chain["chain"][1]} == {f"artifact:{art}"}

    found = json.loads(_fn("search_nodes")(node_type="model"))
    assert found["count"] == 1
    assert found["nodes"][0]["node_id"] == "model:m1"
