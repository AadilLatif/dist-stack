"""Tool-level tests: direct function calls (CONVENTIONS pattern) on all 7 tools.

Uses the ``mcp._tool_manager._tools[name].fn`` accessor (doc-10 direct-call
pattern) with the monkeypatched ``DIST_STACK_KG_DB`` env var from ``kg_db``.
Every tool returns a JSON string — the contract is asserted end-to-end.
"""

from __future__ import annotations

import json
import os
import sys

from dist_stack.registry import register
from dist_stack.runstore import attach_artifact, create_run

from kg_server.server import create_server

A1 = "/data/outputs/a1.json"
A2 = "/data/outputs/a2.json"
A3 = "/data/outputs/a3.json"


def _fn(name: str):
    return create_server()._tool_manager._tools[name].fn


class TestToolSurface:
    def test_exactly_eight_tools(self):
        mcp = create_server()
        assert sorted(mcp._tool_manager._tools) == [
            "get_neighbors",
            "get_node",
            "get_provenance_chain",
            "graph_stats",
            "ingest",
            "ingest_components",
            "query_provenance",
            "search_nodes",
        ]


class TestGetNode:
    def test_happy_path(self, seed_kg):
        result = json.loads(_fn("get_node")("run:r1"))
        assert result["success"] is True
        node = result["node"]
        assert node["node_id"] == "run:r1"
        assert node["node_type"] == "gdm_flow_run"
        assert node["label"] == "gdm_flow r1"
        assert node["run_id"] == "r1"
        assert node["tool"] == "gdm_flow"
        assert node["tool_version"] == "1.0.0"
        assert node["metadata"] == {}
        assert node["created_at_utc"]

    def test_missing_node(self, seed_kg):
        result = json.loads(_fn("get_node")("run:nope"))
        assert result["success"] is False
        assert "no node found" in result["error"]

    def test_no_kg_db(self, monkeypatch):
        monkeypatch.delenv("DIST_STACK_KG_DB", raising=False)
        result = json.loads(_fn("get_node")("run:r1"))
        assert result["success"] is False
        assert "DIST_STACK_KG_DB" in result["error"]


class TestGetNeighbors:
    def test_out_direction(self, seed_kg):
        result = json.loads(_fn("get_neighbors")("run:r1", direction="out"))
        assert result["success"] is True
        assert result["node"]["node_id"] == "run:r1"
        rels = {(n["edge"]["relation"], n["node"]["node_id"]) for n in result["neighbors"]}
        assert rels == {
            ("has_artifact", f"artifact:{A1}"),
            ("has_artifact", f"artifact:{A2}"),
            ("has_artifact", f"artifact:{A3}"),
        }

    def test_in_direction(self, seed_kg):
        result = json.loads(_fn("get_neighbors")(f"artifact:{A1}", direction="in"))
        assert result["success"] is True
        rels = {(n["edge"]["relation"], n["node"]["node_id"]) for n in result["neighbors"]}
        assert rels == {
            ("derived_from", f"artifact:{A2}"),
            ("has_artifact", "run:r1"),
        }

    def test_relation_filter(self, seed_kg):
        result = json.loads(
            _fn("get_neighbors")(
                f"artifact:{A1}", direction="both", relation="references"
            )
        )
        assert result["success"] is True
        assert {n["edge"]["relation"] for n in result["neighbors"]} == {"references"}
        assert {n["node"]["node_id"] for n in result["neighbors"]} == {"model:m1"}

    def test_edge_metadata_present(self, seed_kg):
        result = json.loads(_fn("get_neighbors")("run:r1", direction="out"))
        edge = result["neighbors"][0]["edge"]
        assert edge["edge_id"].startswith("e_")
        assert edge["metadata"] == {"tool": "gdm_flow", "tool_version": "1.0.0"}

    def test_missing_node(self, seed_kg):
        result = json.loads(_fn("get_neighbors")("run:nope"))
        assert result["success"] is False


class TestSearchNodes:
    def test_search_all(self, seed_kg):
        result = json.loads(_fn("search_nodes")())
        assert result["success"] is True
        assert result["count"] == 6
        assert {n["node_id"] for n in result["nodes"]} == {
            "run:r1",
            "run:r2",
            f"artifact:{A1}",
            f"artifact:{A2}",
            f"artifact:{A3}",
            "model:m1",
        }

    def test_search_by_type(self, seed_kg):
        result = json.loads(_fn("search_nodes")(node_type="artifact"))
        assert result["success"] is True
        assert result["count"] == 3
        assert all(n["node_type"] == "artifact" for n in result["nodes"])

    def test_search_by_label(self, seed_kg):
        result = json.loads(_fn("search_nodes")(label="a1.json"))
        assert result["success"] is True
        assert result["count"] == 1
        assert result["nodes"][0]["node_id"] == f"artifact:{A1}"

    def test_search_invalid_type(self, seed_kg):
        result = json.loads(_fn("search_nodes")(node_type="bogus"))
        assert result["success"] is False


class TestGraphStats:
    def test_stats(self, seed_kg):
        result = json.loads(_fn("graph_stats")())
        assert result["success"] is True
        stats = result["stats"]
        assert stats["nodes"]["artifact"] == 3
        assert stats["nodes"]["gdm_flow_run"] == 1
        assert stats["nodes"]["workflow_execution"] == 1
        assert stats["nodes"]["model"] == 1
        assert stats["edges"]["has_artifact"] == 3
        assert stats["edges"]["derived_from"] == 2
        assert stats["edges"]["generated_by"] == 1
        assert stats["edges"]["references"] == 1
        assert stats["top_degree"][0] == [f"artifact:{A1}", 4]
        assert stats["updated_at_utc"]

    def test_no_kg_db(self, monkeypatch):
        monkeypatch.delenv("DIST_STACK_KG_DB", raising=False)
        result = json.loads(_fn("graph_stats")())
        assert result["success"] is False


class TestQueryProvenance:
    def test_by_run_id(self, seed_kg):
        result = json.loads(_fn("query_provenance")(run_id="r1"))
        assert result["success"] is True
        assert result["node"]["node_id"] == "run:r1"
        assert {n["edge"]["relation"] for n in result["neighbors"]} == {
            "has_artifact",
            "generated_by",
        }

    def test_by_artifact_path(self, seed_kg):
        result = json.loads(_fn("query_provenance")(artifact_path=A1))
        assert result["success"] is True
        assert result["node"]["node_id"] == f"artifact:{A1}"
        assert result["node"]["artifact_path"] == A1

    def test_by_model_id(self, seed_kg):
        result = json.loads(_fn("query_provenance")(model_id="m1"))
        assert result["success"] is True
        assert result["node"]["node_id"] == "model:m1"

    def test_xor_none_given(self, seed_kg):
        result = json.loads(_fn("query_provenance")())
        assert result["success"] is False
        assert "exactly one" in result["error"]

    def test_xor_multiple_given(self, seed_kg):
        result = json.loads(_fn("query_provenance")(run_id="r1", model_id="m1"))
        assert result["success"] is False
        assert "exactly one" in result["error"]

    def test_empty_string_counts_as_missing(self, seed_kg):
        result = json.loads(_fn("query_provenance")(artifact_path=""))
        assert result["success"] is False
        assert "exactly one" in result["error"]

    def test_unresolvable_subject(self, seed_kg):
        result = json.loads(_fn("query_provenance")(run_id="nope"))
        assert result["success"] is False
        assert "no node found" in result["error"]


class TestGetProvenanceChain:
    def test_up_chain(self, seed_kg):
        result = json.loads(_fn("get_provenance_chain")(f"artifact:{A1}"))
        assert result["success"] is True
        assert result["node_id"] == f"artifact:{A1}"
        assert result["direction"] == "up"
        assert [n["node_id"] for n in result["chain"][0]] == [f"artifact:{A1}"]
        assert [n["node_id"] for n in result["chain"][1]] == [f"artifact:{A2}"]
        assert [n["node_id"] for n in result["chain"][2]] == [f"artifact:{A3}"]

    def test_down_chain(self, seed_kg):
        result = json.loads(
            _fn("get_provenance_chain")(f"artifact:{A3}", direction="down")
        )
        assert result["success"] is True
        assert result["direction"] == "down"
        assert [n["node_id"] for n in result["chain"][0]] == [f"artifact:{A3}"]
        assert [n["node_id"] for n in result["chain"][1]] == [f"artifact:{A2}"]
        assert [n["node_id"] for n in result["chain"][2]] == [f"artifact:{A1}"]

    def test_up_from_run(self, seed_kg):
        result = json.loads(_fn("get_provenance_chain")("run:r1", direction="up"))
        assert result["success"] is True
        assert [n["node_id"] for n in result["chain"][1]] == [f"artifact:{A1}"]
        assert [n["node_id"] for n in result["chain"][2]] == [f"artifact:{A2}"]

    def test_chain_missing_node(self, seed_kg):
        result = json.loads(_fn("get_provenance_chain")("run:nope"))
        assert result["success"] is False


class TestIngest:
    def test_defensive_when_module_missing(self, kg_db, monkeypatch):
        # ``sys.modules[..] = None`` makes the lazy import raise ImportError,
        # exercising the defensive path used before dist_stack.kg.ingest lands.
        monkeypatch.setitem(sys.modules, "dist_stack.kg.ingest", None)
        result = json.loads(_fn("ingest")())
        assert result["success"] is False
        assert result["error"] == "ingest module not available yet"

    def test_ingest_round_trip(self, kg_db, runstore_db, registry_db, tmp_path):
        # -- seed runstore + registry (the ingest sources) -------------------
        create_run(
            "gdm_flow", run_type="gdm_flow_run", run_id="rt1", model_id="m1",
            runstore_db=runstore_db,
        )
        art = tmp_path / "out.json"
        art.write_text("{}")
        attach_artifact("rt1", str(art), runstore_db=runstore_db)
        model_path = tmp_path / "m1.model"
        model_path.write_text("model")
        register("m1", stored_path=str(model_path), registry_db=registry_db)

        # -- ingest via the tool ---------------------------------------------
        result = json.loads(
            _fn("ingest")(
                runstore_db=str(runstore_db), registry_db=str(registry_db)
            )
        )
        assert result["success"] is True
        report = result["report"]
        assert report["nodes_created"] >= 3  # run + artifact + model
        assert report["edges_created"] >= 1
        assert report["errors"] == []

        # -- the graph is queryable through the tools ------------------------
        node = json.loads(_fn("get_node")("run:rt1"))
        assert node["success"] is True
        assert node["node"]["run_id"] == "rt1"

        model = json.loads(_fn("get_node")("model:m1"))
        assert model["success"] is True

        prov = json.loads(_fn("query_provenance")(artifact_path=str(art)))
        assert prov["success"] is True
        assert prov["node"]["node_id"] == f"artifact:{os.path.normpath(str(art))}"
        assert {n["edge"]["relation"] for n in prov["neighbors"]} >= {
            "has_artifact",
            "generated_by",
        }

        stats = json.loads(_fn("graph_stats")())
        assert stats["stats"]["nodes"]["model"] == 1
