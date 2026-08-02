"""Tests for the tool catalog: mangle/demangle + build_catalog (spec 15 §G).

Run from apps/dist-dashboard:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import unittest

from assistant import build_catalog, demangle, mangle
from fake_pool import FakePool, build_assistant_pool


class TestMangleDemangle(unittest.TestCase):
    def test_round_trip(self):
        name = mangle("gdm", "get_system_summary")
        self.assertEqual(name, "gdm__get_system_summary")
        self.assertEqual(demangle(name), ("gdm", "get_system_summary"))

    def test_multi_underscore_tool_names_survive(self):
        # Only the FIRST "__" is the separator; tool names may contain "__".
        self.assertEqual(demangle("kg_server__get_provenance_chain"), ("kg_server", "get_provenance_chain"))

    def test_rejects_names_without_separator(self):
        with self.assertRaises(ValueError):
            demangle("get_system_summary")

    def test_rejects_empty_parts(self):
        with self.assertRaises(ValueError):
            demangle("gdm__")
        with self.assertRaises(ValueError):
            demangle("__get_system_summary")
        with self.assertRaises(ValueError):
            demangle("__")


def run(coro):
    return asyncio.run(coro)


class TestBuildCatalog(unittest.TestCase):
    def _tools(self, catalog, name):
        return [t for t in catalog if t["function"]["name"] == name]

    def test_read_only_filters_write_tools(self):
        pool = build_assistant_pool()
        catalog = run(build_catalog(pool, pool.names, allow_write=False))
        names = {t["function"]["name"] for t in catalog}
        # read-only surface present
        self.assertIn("kg_server__search_nodes", names)
        self.assertIn("workflow_runner__list_runs", names)
        # write tools absent
        self.assertNotIn("kg_server__ingest", names)
        self.assertNotIn("workflow_runner__run_workflow", names)

    def test_allow_write_admits_everything(self):
        pool = build_assistant_pool()
        catalog = run(build_catalog(pool, pool.names, allow_write=True))
        names = {t["function"]["name"] for t in catalog}
        self.assertIn("kg_server__ingest", names)
        self.assertIn("workflow_runner__run_workflow", names)
        self.assertIn("kg_server__search_nodes", names)

    def test_input_schema_passthrough(self):
        pool = build_assistant_pool()
        catalog = run(build_catalog(pool, pool.names, allow_write=True))
        search = self._tools(catalog, "kg_server__search_nodes")[0]
        self.assertEqual(search["type"], "function")
        params = search["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("properties", params)

    def test_description_prefixed_and_capped(self):
        pool = FakePool()
        pool.add_server("kg_server", {"search_nodes": lambda node_type=None: {"success": True}})
        catalog = run(build_catalog(pool, pool.names, allow_write=True))
        entry = catalog[0]
        self.assertTrue(entry["function"]["description"].startswith("[kg_server] "))
        self.assertLessEqual(len(entry["function"]["description"]), 1024)

    def test_connect_failure_skips_server(self):
        pool = build_assistant_pool()
        pool.connect_errors.add("kg_server")
        catalog = run(build_catalog(pool, pool.names, allow_write=True))
        names = {t["function"]["name"] for t in catalog}
        self.assertNotIn("kg_server__search_nodes", names)
        self.assertIn("workflow_runner__list_runs", names)
        self.assertEqual(pool.statuses["kg_server"], "error")

    def test_unknown_tools_blocked_by_default(self):
        # A server whose tools are NOT in the read-only allowlist yields an
        # empty read-only catalog (allowlist, not denylist).
        pool = FakePool()
        pool.add_server("mystery", {"anything": lambda: {"success": True}})
        catalog = run(build_catalog(pool, pool.names, allow_write=False))
        self.assertEqual(catalog, [])


if __name__ == "__main__":
    unittest.main()
