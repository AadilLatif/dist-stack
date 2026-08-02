"""Query tools over the knowledge-graph store: ``get_node``, ``get_neighbors``,
``search_nodes``, ``graph_stats``.

Stateless-per-call tools (no ``ctx``): the KG DB path is resolved lazily from
``DIST_STACK_KG_DB`` on every call. Errors return ``{"success": False,
"error": ...}`` payloads, never raise.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from dist_stack.kg import (
    KGEdge,
    KGUnavailableError,
    NodeNotFoundError,
    get_neighbors as kg_get_neighbors,
    get_node as kg_get_node,
    graph_stats as kg_graph_stats,
    search_nodes as kg_search_nodes,
)
from dist_stack.mcp.serialization import error_payload, json_safe

from mcp.server import MCPServer


def _other_endpoint(edge: KGEdge, node_id: str, direction: str) -> str:
    """Id of the neighbor node on the far side of ``edge``.

    For depth=1 the other endpoint is unambiguous (whichever of
    ``source_node``/``target_node`` is not ``node_id``). For multi-hop edges
    neither endpoint equals ``node_id``; fall back to the BFS-reachable
    endpoint (the source for ``in`` traversal, the target for ``out``/``both``).
    """
    if edge.source_node == node_id:
        return edge.target_node
    if edge.target_node == node_id:
        return edge.source_node
    if direction == "in":
        return edge.source_node
    return edge.target_node


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def get_node(node_id: str) -> str:
        """Get a single knowledge-graph node by its stable id.

        Args:
            node_id: Node id (``run:<run_id>``, ``artifact:<path>``,
                ``model:<model_id>``).

        Returns:
            JSON ``{"success", "node"}`` with the node record (node_id,
            node_type, label, artifact_path, run_id, model_id, tool,
            tool_version, metadata, created_at_utc).
        """
        try:
            node = kg_get_node(node_id)
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        return json_safe({"success": True, "node": asdict(node)})

    @mcp.tool()
    def get_neighbors(
        node_id: str,
        relation: str | None = None,
        direction: Literal["in", "out", "both"] = "both",
        depth: int = 1,
        limit: int = 50,
    ) -> str:
        """Get the neighbors of a node within ``depth`` hops.

        Args:
            node_id: Node id to start from.
            relation: Restrict traversal to a single relation.
            direction: ``in``, ``out``, or ``both``.
            depth: Maximum number of hops (the store caps this at 5).
            limit: Maximum number of edges to return (default 50).

        Returns:
            JSON ``{"success", "node", "neighbors"}`` where each neighbor is
            ``{"edge": {...}, "node": {...}}``.
        """
        try:
            node = kg_get_node(node_id)
            edges = kg_get_neighbors(
                node_id,
                relation=relation,
                direction=direction,
                depth=depth,
                limit=limit,
            )
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        neighbors = []
        for edge in edges:
            other_id = _other_endpoint(edge, node_id, direction)
            try:
                other = kg_get_node(other_id)
            except NodeNotFoundError:
                continue
            neighbors.append({"edge": asdict(edge), "node": asdict(other)})
        return json_safe(
            {"success": True, "node": asdict(node), "neighbors": neighbors}
        )

    @mcp.tool()
    def search_nodes(
        node_type: str | None = None,
        label: str | None = None,
        limit: int = 50,
    ) -> str:
        """Search nodes by type and/or label.

        Args:
            node_type: Exact node_type filter (one of the KG Literal types).
            label: Case-insensitive label match (exact/prefix, then substring).
            limit: Maximum number of nodes to return (default 50).

        Returns:
            JSON ``{"success", "count", "nodes"}``.
        """
        try:
            nodes = kg_search_nodes(node_type=node_type, label=label, limit=limit)
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        return json_safe(
            {
                "success": True,
                "count": len(nodes),
                "nodes": [asdict(n) for n in nodes],
            }
        )

    @mcp.tool()
    def graph_stats() -> str:
        """Aggregate knowledge-graph statistics.

        Returns:
            JSON ``{"success", "stats"}`` with node counts by type, edge counts
            by relation, the top-degree nodes, and a UTC snapshot timestamp.
        """
        try:
            stats = kg_graph_stats()
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        return json_safe(
            {
                "success": True,
                "stats": {
                    "nodes": stats.node_counts,
                    "edges": stats.edge_counts,
                    "top_degree": [
                        [node_id, degree] for node_id, degree in stats.top_degree
                    ],
                    "updated_at_utc": stats.updated_at_utc,
                },
            }
        )
