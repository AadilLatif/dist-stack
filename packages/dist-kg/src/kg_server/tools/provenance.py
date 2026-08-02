"""Provenance tools: ``query_provenance`` and ``get_provenance_chain``.

``query_provenance`` is a runtime-XOR subject resolver: exactly one of
``artifact_path``/``run_id``/``model_id`` must be given, and it is mapped to the
KG node-id scheme (``artifact:<normpath>`` / ``run:<run_id>`` /
``model:<model_id>``) before a depth-limited neighbor traversal.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Literal

from dist_stack.kg import (
    KGUnavailableError,
    NodeNotFoundError,
    get_neighbors as kg_get_neighbors,
    get_node as kg_get_node,
    get_provenance_chain as kg_get_provenance_chain,
)
from dist_stack.mcp.serialization import error_payload, json_safe

from mcp.server import MCPServer

from kg_server.tools.queries import _other_endpoint

XOR_ERROR = (
    "query_provenance requires exactly one of artifact_path, run_id, model_id"
)


def _artifact_node_id(artifact_path: str) -> str:
    """``artifact:<normpath(abs_path)>`` — matches the ingest node-id scheme."""
    expanded = os.path.expanduser(str(artifact_path))
    return "artifact:" + os.path.normpath(os.path.abspath(expanded))


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def query_provenance(
        artifact_path: str | None = None,
        run_id: str | None = None,
        model_id: str | None = None,
        depth: int = 1,
    ) -> str:
        """Resolve exactly one subject to its KG node and fetch its neighbors.

        Exactly one of ``artifact_path``/``run_id``/``model_id`` must be given
        (runtime XOR). Resolution follows the node-id scheme: artifact_path ->
        ``artifact:<normpath>``, run_id -> ``run:<run_id>``, model_id ->
        ``model:<model_id>``.

        Args:
            artifact_path: Artifact file path (absolute or relative).
            run_id: Run id from the runstore.
            model_id: Model id from the model registry.
            depth: Number of hops to traverse (default 1).

        Returns:
            JSON ``{"success", "node", "neighbors"}`` where each neighbor is
            ``{"edge": {...}, "node": {...}}``.
        """
        given = sum(
            1 for v in (artifact_path, run_id, model_id) if v is not None and v != ""
        )
        if given != 1:
            return error_payload(XOR_ERROR)
        if artifact_path is not None:
            node_id = _artifact_node_id(artifact_path)
        elif run_id is not None:
            node_id = f"run:{run_id}"
        else:
            node_id = f"model:{model_id}"

        try:
            node = kg_get_node(node_id)
            edges = kg_get_neighbors(
                node_id, direction="both", depth=depth, limit=50
            )
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        neighbors = []
        for edge in edges:
            other_id = _other_endpoint(edge, node_id, "both")
            try:
                other = kg_get_node(other_id)
            except NodeNotFoundError:
                continue
            neighbors.append({"edge": asdict(edge), "node": asdict(other)})
        return json_safe(
            {"success": True, "node": asdict(node), "neighbors": neighbors}
        )

    @mcp.tool()
    def get_provenance_chain(
        node_id: str,
        direction: Literal["up", "down"] = "up",
        max_depth: int = 10,
    ) -> str:
        """Provenance ancestry/descendancy of a node, one list per depth.

        ``up`` walks incoming edges with relations derived_from/generated_by/
        references; ``down`` walks outgoing edges with relations derived_from/
        has_artifact. Trailing empty depths are trimmed.

        Args:
            node_id: Node id to start from.
            direction: ``up`` (ancestors) or ``down`` (descendants).
            max_depth: Maximum number of levels (default 10).

        Returns:
            JSON ``{"success", "node_id", "direction", "chain"}`` where chain
            is ``[[depth 0], [depth 1], ...]`` of node records.
        """
        try:
            chains = kg_get_provenance_chain(
                node_id, direction=direction, max_depth=max_depth
            )
        except (KGUnavailableError, NodeNotFoundError, ValueError) as exc:
            return error_payload(str(exc))
        return json_safe(
            {
                "success": True,
                "node_id": node_id,
                "direction": direction,
                "chain": [
                    [asdict(node) for node in depth_nodes] for depth_nodes in chains
                ],
            }
        )
