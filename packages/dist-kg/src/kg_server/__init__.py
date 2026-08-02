"""dist-kg: knowledge-graph MCP server for the NREL distribution suite.

Serves the ``dist_stack.kg`` store (a sibling package in the dist-stack
monorepo — `packages/dist-stack-model-registry`)
over MCP: node/edge queries, provenance traversal, graph stats, and ingestion
from the shared runstore + model registry + sidecar manifests. Stateless
server: every tool resolves the KG DB path lazily per call from
``DIST_STACK_KG_DB`` (or an explicit ``kg_db`` argument).
"""

__version__ = "0.1.0"
