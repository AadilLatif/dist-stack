"""Component-ingestion tool tests (Phase 3f).

Spawns a real MCPServer fake gdm (``tests/fake_gdm.py``) through the production
stdio path (``kg_server.gdm_client``) and asserts the KG side effects: component
nodes with the frozen ``component:<system_model_id>:<uuid>`` key space, correct
metadata, ``has_component`` + ``parent_of`` edges, XOR enforcement, registry
resolution, and error payloads.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from dist_stack.kg import get_neighbors, get_node, graph_stats
from dist_stack.registry import register

from kg_server.server import create_server

FAKE_GDM = Path(__file__).resolve().parent / "fake_gdm.py"

SYS_PATH = "/data/systems/ieee13.json"
SYS_NODE_ID = "artifact:/data/systems/ieee13.json"


def _fn(name: str):
    return create_server()._tool_manager._tools[name].fn


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_gdm_env(monkeypatch):
    """Point KG_GDM_COMMAND/KG_GDM_ARGS at the fake gdm server."""
    monkeypatch.setenv("KG_GDM_COMMAND", sys.executable)
    monkeypatch.setenv("KG_GDM_ARGS", str(FAKE_GDM))
    return FAKE_GDM


@pytest.fixture
def registry_env(registry_db, monkeypatch):
    """Wire DIST_STACK_MODEL_REGISTRY_DB to the tmp registry DB."""
    monkeypatch.setenv("DIST_STACK_MODEL_REGISTRY_DB", str(registry_db))
    return registry_db


class TestIngestComponentsSurface:
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


class TestXor:
    def test_both_given(self, seed_kg, fake_gdm_env):
        result = json.loads(
            run(_fn("ingest_components")(system_path=SYS_PATH, model_id="m1"))
        )
        assert result["success"] is False
        assert "exactly one" in result["error"]

    def test_neither_given(self, seed_kg, fake_gdm_env):
        result = json.loads(run(_fn("ingest_components")()))
        assert result["success"] is False
        assert "exactly one" in result["error"]


class TestIngestBySystemPath:
    def test_full_ingest_default_depth(self, seed_kg, fake_gdm_env):
        result = json.loads(run(_fn("ingest_components")(system_path=SYS_PATH)))
        assert result["success"] is True
        assert result["system_node_id"] == SYS_NODE_ID
        assert result["system_model_id"] == "ieee13"
        assert result["components_ingested"] == 3
        # 3 has_component + 2 parent_of (bus-1 -> load-1, bus-1 -> solar-1)
        assert result["edges_added"] == 5
        assert result["errors"] == []

    def test_component_nodes_and_metadata(self, seed_kg, fake_gdm_env):
        run(_fn("ingest_components")(system_path=SYS_PATH))
        node = get_node("component:ieee13:load-1", kg_db=seed_kg)
        assert node.node_type == "component"
        assert node.label == "Load 1"
        assert node.model_id == "ieee13"
        assert node.metadata["component_type"] == "DistributionLoad"
        assert node.metadata["feeder"] == "Feeder 1"
        assert node.metadata["substation"] == "Sub A"
        assert node.metadata["phases"] == ["A"]
        assert node.metadata["in_service"] is True

    def test_has_component_edges(self, seed_kg, fake_gdm_env):
        run(_fn("ingest_components")(system_path=SYS_PATH))
        edges = get_neighbors(SYS_NODE_ID, direction="out", relation="has_component", kg_db=seed_kg)
        assert {e.target_node for e in edges} == {
            "component:ieee13:bus-1",
            "component:ieee13:load-1",
            "component:ieee13:solar-1",
        }

    def test_parent_of_edges(self, seed_kg, fake_gdm_env):
        run(_fn("ingest_components")(system_path=SYS_PATH))
        edges = get_neighbors(
            "component:ieee13:bus-1", direction="out", relation="parent_of", kg_db=seed_kg
        )
        assert {e.target_node for e in edges} == {
            "component:ieee13:load-1",
            "component:ieee13:solar-1",
        }

    def test_depth_one_skips_relationship_pass(self, seed_kg, fake_gdm_env):
        result = json.loads(run(_fn("ingest_components")(system_path=SYS_PATH, depth=1)))
        assert result["success"] is True
        assert result["components_ingested"] == 3
        assert result["edges_added"] == 3  # has_component only
        parents = get_neighbors(
            "component:ieee13:bus-1", direction="out", relation="parent_of", kg_db=seed_kg
        )
        assert parents == []

    def test_reingest_is_idempotent(self, seed_kg, fake_gdm_env):
        run(_fn("ingest_components")(system_path=SYS_PATH))
        second = json.loads(run(_fn("ingest_components")(system_path=SYS_PATH)))
        assert second["success"] is True
        assert second["components_ingested"] == 3
        # upserts are idempotent — no duplicate rows
        stats = graph_stats(kg_db=seed_kg)
        assert stats.node_counts["component"] == 3
        assert stats.edge_counts["has_component"] == 3
        assert stats.edge_counts["parent_of"] == 2


class TestRegistryResolution:
    def test_model_id_resolves_to_system_path(self, seed_kg, fake_gdm_env, registry_env, tmp_path):
        sys_file = tmp_path / "ieee13.json"
        sys_file.write_text("{}")
        register("sys13", stored_path=str(sys_file))
        result = json.loads(run(_fn("ingest_components")(model_id="sys13")))
        assert result["success"] is True
        assert result["system_model_id"] == "sys13"
        assert result["components_ingested"] == 3
        # node ids use the resolved model_id, not the path slug
        assert get_node("component:sys13:bus-1", kg_db=seed_kg).node_type == "component"
        # system anchor is the registered stored path
        assert result["system_node_id"] == "artifact:" + str(sys_file)

    def test_system_path_reverse_lookup_model_id(self, seed_kg, fake_gdm_env, registry_env, tmp_path):
        sys_file = tmp_path / "ieee13.json"
        sys_file.write_text("{}")
        register("sys13", stored_path=str(sys_file))
        result = json.loads(run(_fn("ingest_components")(system_path=str(sys_file))))
        assert result["success"] is True
        assert result["system_model_id"] == "sys13"
        assert get_node(f"component:sys13:bus-1", kg_db=seed_kg).node_type == "component"

    def test_model_id_not_found(self, seed_kg, fake_gdm_env, registry_env):
        result = json.loads(run(_fn("ingest_components")(model_id="nope")))
        assert result["success"] is False
        assert "nope" in result["error"]


class TestErrorPayloads:
    def test_kg_unavailable(self, fake_gdm_env, monkeypatch):
        monkeypatch.delenv("DIST_STACK_KG_DB", raising=False)
        result = json.loads(run(_fn("ingest_components")(system_path=SYS_PATH)))
        assert result["success"] is False
        assert "DIST_STACK_KG_DB" in result["error"]

    def test_gdm_spawn_failure(self, seed_kg, monkeypatch):
        monkeypatch.setenv("KG_GDM_COMMAND", "/nonexistent/python")
        result = json.loads(run(_fn("ingest_components")(system_path=SYS_PATH)))
        assert result["success"] is False
        assert "gdm" in result["error"]
