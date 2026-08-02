"""dist-kg MCP server — main application wiring.

``create_server()`` builds the :class:`MCPServer` and registers the tool,
resource and prompt modules. The server is stateless (no lifespan): the KG DB
path is resolved lazily per call from ``DIST_STACK_KG_DB`` (or an explicit
``kg_db`` argument), so ``create_server()`` is cheap and side-effect free.
"""

from __future__ import annotations

from mcp.server import MCPServer

from kg_server import __version__

INSTRUCTIONS = (
    "dist-kg exposes the knowledge graph of the distribution suite: runs, "
    "artifacts and models linked by provenance edges (has_artifact, "
    "generated_by, derived_from, references). Use get_node/get_neighbors to "
    "traverse, query_provenance/get_provenance_chain for provenance questions, "
    "search_nodes/graph_stats for discovery, kg://stats and kg://graph/{node_id} "
    "as read-only resources, ingest to (re)build the graph from the shared "
    "runstore + model registry + sidecar manifests, and ingest_components to add "
    "component nodes (component:<system_model_id>:<uuid>) via the gdm MCP server."
)


def create_server() -> MCPServer:
    """Create and configure the MCPServer instance."""
    mcp = MCPServer(
        "dist-kg",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    # -- Register tool modules -------------------------------------------------
    from kg_server.tools import components, ingest, provenance, queries

    queries.register(mcp)
    provenance.register(mcp)
    ingest.register(mcp)
    components.register(mcp)

    # -- Register resources ----------------------------------------------------
    from kg_server.resources import index

    index.register(mcp)

    # -- Register prompts ------------------------------------------------------
    from kg_server.prompts import provenance as provenance_prompts

    provenance_prompts.register(mcp)

    return mcp
