"""Resource-level tests: registration + read (CONVENTIONS pattern).

In mcp 2.0 a static resource takes no params and a templated resource takes
exactly the templated params — matched via ``ResourceTemplate.matches`` and
invoked through the resource ``fn``.
"""

from __future__ import annotations

import inspect
import json

from kg_server.server import create_server

A1 = "/data/outputs/a1.json"


def _mcp():
    return create_server()


def _resource_fn(uri: str):
    return _mcp()._resource_manager._resources[uri].fn


def _template(uri: str):
    return _mcp()._resource_manager._templates[uri]


class TestResourceRegistration:
    def test_resources_registered(self):
        mcp = _mcp()
        assert "kg://stats" in mcp._resource_manager._resources
        assert "kg://graph/{node_id}" in mcp._resource_manager._templates

    def test_static_resource_takes_no_params(self):
        fn = _resource_fn("kg://stats")
        assert list(inspect.signature(fn).parameters) == []

    def test_list_resources(self):
        import asyncio

        mcp = _mcp()
        uris = {r.uri for r in asyncio.run(mcp.list_resources())}
        assert uris == {"kg://stats"}
        templates = {t.uri_template for t in asyncio.run(mcp.list_resource_templates())}
        assert templates == {"kg://graph/{node_id}"}


class TestStatsResource:
    def test_read(self, seed_kg):
        result = json.loads(_resource_fn("kg://stats")())
        assert result["nodes"]["artifact"] == 3
        assert result["nodes"]["model"] == 1
        assert result["edges"]["has_artifact"] == 3
        assert result["edges"]["derived_from"] == 2
        assert result["updated_at_utc"]

    def test_read_no_kg(self, monkeypatch):
        monkeypatch.delenv("DIST_STACK_KG_DB", raising=False)
        result = json.loads(_resource_fn("kg://stats")())
        assert result["success"] is False
        assert "DIST_STACK_KG_DB" in result["error"]


class TestGraphResource:
    def test_template_matches(self, seed_kg):
        t = _template("kg://graph/{node_id}")
        assert t.matches("kg://graph/run:r1") == {"node_id": "run:r1"}
        assert t.matches("kg://graph/other") == {"node_id": "other"}

    def test_read_returns_node_and_1hop_neighbors(self, seed_kg):
        t = _template("kg://graph/{node_id}")
        result = json.loads(t.fn(node_id="run:r1"))
        assert result["node"]["node_id"] == "run:r1"
        assert {n["edge"]["relation"] for n in result["neighbors"]} == {
            "has_artifact",
            "generated_by",
        }
        assert all("edge" in n and "node" in n for n in result["neighbors"])

    def test_read_with_edge_metadata(self, seed_kg):
        t = _template("kg://graph/{node_id}")
        result = json.loads(t.fn(node_id=f"artifact:{A1}"))
        by_rel = {n["edge"]["relation"]: n for n in result["neighbors"]}
        assert by_rel["references"]["edge"]["metadata"] == {"model_id": "m1"}

    def test_read_missing_node(self, seed_kg):
        t = _template("kg://graph/{node_id}")
        result = json.loads(t.fn(node_id="run:nope"))
        assert result["success"] is False
        assert "no node found" in result["error"]
