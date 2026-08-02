"""Public surface of the knowledge graph (`dist_stack.kg`).

Re-exports the functional API, the ``KGNode``/``KGEdge``/``KGStats``/
``IngestReport`` dataclasses, and the exception classes. Nothing else is
public. MCP exposure is explicitly deferred to the sibling `dist-kg` package
(`packages/dist-kg` in the dist-stack monorepo).
"""

from __future__ import annotations

from .api import (
    delete_node,
    ensure_schema,
    get_kg_path,
    get_neighbors,
    get_node,
    get_provenance_chain,
    graph_stats,
    search_nodes,
    upsert_edge,
    upsert_node,
)
from .errors import (
    KGError,
    KGUnavailableError,
    NodeNotFoundError,
)
from .ingest import ingest
from .model import IngestReport, KGEdge, KGNode, KGStats

__all__ = [
    "upsert_node",
    "get_node",
    "search_nodes",
    "delete_node",
    "upsert_edge",
    "get_neighbors",
    "get_provenance_chain",
    "graph_stats",
    "ensure_schema",
    "get_kg_path",
    "ingest",
    "KGNode",
    "KGEdge",
    "KGStats",
    "IngestReport",
    "KGError",
    "KGUnavailableError",
    "NodeNotFoundError",
]
