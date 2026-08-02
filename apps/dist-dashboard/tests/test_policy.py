"""Tests for the write-tool policy (spec 15 §E/§G).

Covers the ``catalog_allowed`` matrix, the read-only ⊆ real-tool-list drift
guard (verified against this repo's actual workflow_runner / kg_server tool
surfaces), write-tool classification, and the unknown-defaults-blocked rule.
"""

from __future__ import annotations

import unittest

from assistant import (
    KNOWN_TOOLS,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    catalog_allowed,
    drift_report,
)

# The REAL tool surfaces of the two in-repo MCP servers, read from their
# source (workflow_runner.tools.* and kg_server.tools.*). The drift guard must
# hold against these, not just against the curated KNOWN_TOOLS.
REAL_WORKFLOW_RUNNER_TOOLS = {
    "run_workflow",
    "create_workflow",
    "get_workflow",
    "list_workflows",
    "list_servers",
    "list_tools",
    "get_run",
    "list_runs",
}
REAL_KG_SERVER_TOOLS = {
    "ingest",
    "ingest_components",
    "get_node",
    "get_neighbors",
    "search_nodes",
    "graph_stats",
    "query_provenance",
    "get_provenance_chain",
}
REAL_TOOL_LISTS = {
    "workflow_runner": REAL_WORKFLOW_RUNNER_TOOLS,
    "kg_server": REAL_KG_SERVER_TOOLS,
}


class TestCatalogAllowed(unittest.TestCase):
    def test_read_only_tool_allowed_when_writes_off(self):
        self.assertTrue(catalog_allowed("kg_server", "search_nodes", allow_write=False))
        self.assertTrue(catalog_allowed("workflow_runner", "list_runs", allow_write=False))

    def test_write_tool_blocked_when_writes_off(self):
        self.assertFalse(catalog_allowed("kg_server", "ingest", allow_write=False))
        self.assertFalse(catalog_allowed("workflow_runner", "run_workflow", allow_write=False))

    def test_allow_write_admits_everything(self):
        self.assertTrue(catalog_allowed("kg_server", "ingest", allow_write=True))
        self.assertTrue(catalog_allowed("mystery_server", "anything", allow_write=True))

    def test_unknown_tool_defaults_blocked(self):
        # Not in the allowlist, regardless of server knowledge.
        self.assertFalse(catalog_allowed("kg_server", "brand_new_tool", allow_write=False))

    def test_unknown_server_defaults_blocked(self):
        self.assertFalse(catalog_allowed("mystery_server", "anything", allow_write=False))


class TestDriftGuard(unittest.TestCase):
    def test_policy_is_internally_consistent(self):
        self.assertEqual(drift_report(), [])

    def test_read_only_subset_of_real_tool_lists(self):
        """README_OF tools must actually exist on the servers (no phantoms)."""
        for server, real in REAL_TOOL_LISTS.items():
            with self.subTest(server=server):
                self.assertTrue(
                    READ_ONLY_TOOLS[server] <= real,
                    f"{server} read-only tools contain phantoms: "
                    f"{READ_ONLY_TOOLS[server] - real}",
                )

    def test_write_tools_subset_of_real_tool_lists(self):
        for server, real in REAL_TOOL_LISTS.items():
            with self.subTest(server=server):
                self.assertTrue(
                    WRITE_TOOLS[server] <= real,
                    f"{server} write tools contain phantoms: "
                    f"{WRITE_TOOLS[server] - real}",
                )

    def test_read_and_write_are_disjoint(self):
        for server in READ_ONLY_TOOLS:
            with self.subTest(server=server):
                self.assertFalse(
                    READ_ONLY_TOOLS[server] & WRITE_TOOLS.get(server, set()),
                    f"{server} classifies the same tool as read-only AND write",
                )

    def test_write_tools_are_always_blocked_in_read_only_mode(self):
        for server, tools in WRITE_TOOLS.items():
            for tool in tools:
                with self.subTest(server=server, tool=tool):
                    self.assertFalse(catalog_allowed(server, tool, allow_write=False))

    def test_drift_is_detected(self):
        # Dropping a tool from the known surface must be flagged.
        broken = {s: frozenset(t) for s, t in KNOWN_TOOLS.items()}
        broken["kg_server"] = frozenset(t for t in KNOWN_TOOLS["kg_server"] if t != "ingest")
        problems = drift_report(known_tools=broken)
        self.assertTrue(any("ingest" in p for p in problems))

    def test_overlap_is_detected(self):
        # A tool classified as both read-only and write must be flagged.
        overlap_read = {s: frozenset(t) for s, t in READ_ONLY_TOOLS.items()}
        overlap_write = {s: frozenset(t) for s, t in WRITE_TOOLS.items()}
        overlap_write["kg_server"] = frozenset(overlap_write["kg_server"]) | {"search_nodes"}
        problems = drift_report(read_only=overlap_read, write=overlap_write)
        self.assertTrue(any("both read-only and write" in p for p in problems))

    def test_all_servers_have_a_known_surface(self):
        for server in READ_ONLY_TOOLS:
            with self.subTest(server=server):
                self.assertIn(server, KNOWN_TOOLS)
        for server in WRITE_TOOLS:
            with self.subTest(server=server):
                self.assertIn(server, KNOWN_TOOLS)


if __name__ == "__main__":
    unittest.main()
