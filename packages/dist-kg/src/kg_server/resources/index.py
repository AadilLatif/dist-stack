"""Knowledge-graph resources: ``kg://stats`` (static) and
``kg://graph/{node_id}`` (templated).

Both are read-only views over the store and are side-effect free; the KG DB
path is resolved lazily from ``DIST_STACK_KG_DB`` on every read. In mcp 2.0 a
static resource takes no params, and a templated resource takes exactly the
templated params.
"""

from __future__ import annotations

from dataclasses import asdict

from dist_stack.kg import (
    KGUnavailableError,
    NodeNotFoundError,
    get_neighbors as kg_get_neighbors,
    get_node as kg_get_node,
    graph_stats as kg_graph_stats,
)
from dist_stack.mcp.serialization import error_payload, json_safe

from mcp.server import MCPServer

from kg_server.tools.queries import _other_endpoint


def register(mcp: MCPServer) -> None:
    @mcp.resource("kg://stats")
    def kg_stats_resource() -> str:
        """Knowledge-graph statistics: node counts by type, edge counts by
        relation, and the snapshot UTC timestamp."""
        try:
            stats = kg_graph_stats()
        except KGUnavailableError as exc:
            return error_payload(str(exc))
        return json_safe(
            {
                "nodes": stats.node_counts,
                "edges": stats.edge_counts,
                "updated_at_utc": stats.updated_at_utc,
            }
        )

    @mcp.resource("kg://graph/{node_id}")
    def kg_graph_resource(node_id: str) -> str:
        """The node and its 1-hop neighbors (in and out) with edge metadata.

        Args:
            node_id: Node id (``run:<run_id>``, ``artifact:<path>``,
                ``model:<model_id>``).
        """
        try:
            node = kg_get_node(node_id)
            edges = kg_get_neighbors(
                node_id, direction="both", depth=1, limit=50
            )
        except (KGUnavailableError, NodeNotFoundError) as exc:
            return error_payload(str(exc))
        neighbors = []
        for edge in edges:
            other_id = _other_endpoint(edge, node_id, "both")
            try:
                other = kg_get_node(other_id)
            except NodeNotFoundError:
                continue
            neighbors.append({"edge": asdict(edge), "node": asdict(other)})
        return json_safe({"node": asdict(node), "neighbors": neighbors})
