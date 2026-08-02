# dist-kg

Knowledge-graph MCP server for the NREL distribution suite. Serves the
`dist_stack.kg` store — a sibling package (`packages/dist-stack-model-registry`
in the dist-stack monorepo) — over MCP: node /
edge queries, provenance traversal, graph statistics, read-only resources, a
provenance prompt, and ingestion from the shared runstore + model registry +
sidecar manifests.

## Layout

```
src/kg_server/
├── __init__.py           # __version__
├── __main__.py           # kg-server entry: create_server().run(transport="stdio")
├── server.py             # create_server(): MCPServer("dist-kg") + register() calls
├── tools/
│   ├── queries.py        # get_node, get_neighbors, search_nodes, graph_stats
│   ├── provenance.py     # query_provenance, get_provenance_chain
│   └── ingest.py         # ingest (lazy import of dist_stack.kg.ingest)
├── resources/
│   └── index.py          # kg://stats, kg://graph/{node_id}
└── prompts/
    └── provenance.py     # trace_provenance(subject)
```

## Install

```bash
# from the dist-stack monorepo root (uv workspace)
uv sync                      # installs dist-kg + dist-stack-model-registry editable
```

## Run

```bash
export DIST_STACK_KG_DB=/path/to/kg.db   # required (KGUnavailableError when unset)
kg-server                                # or: python -m kg_server
```

Stateless server: no lifespan. Every tool/resource resolves the KG DB path
lazily per call from `DIST_STACK_KG_DB`; an explicit `kg_db` argument to the
`dist_stack.kg` API always wins.

## Tool surface (7)

| Tool | Signature |
|---|---|
| `get_node` | `(node_id: str)` |
| `get_neighbors` | `(node_id, relation=None, direction="both", depth=1, limit=50)` |
| `query_provenance` | `(artifact_path=None, run_id=None, model_id=None, depth=1)` — runtime XOR |
| `get_provenance_chain` | `(node_id, direction="up", max_depth=10)` |
| `search_nodes` | `(node_type=None, label=None, limit=50)` |
| `graph_stats` | `()` |
| `ingest` | `(runstore_db=None, registry_db=None, manifest_dir=None, prune=False)` |

All tools return JSON strings (`{"success", ...}` payloads); errors return
`{"success": False, "error": ...}` and never raise. `ingest` imports
`dist_stack.kg.ingest` lazily — when that module is unavailable (a parallel
lane) it returns a clean "ingest module not available yet" error, leaving the
query tools fully standalone.

## Resources & prompt

- `kg://stats` — static; node counts by type, edge counts by relation, `updated_at_utc`.
- `kg://graph/{node_id}` — templated; the node + 1-hop neighbors (in and out) with edge metadata.
- `trace_provenance(subject)` — instructions for answering provenance questions against the KG.

## Test

```bash
python -m pytest tests/ -q
```

Tests use the CONVENTIONS direct-call pattern (`mcp._tool_manager._tools[name].fn`)
with tmp KG/runstore/registry DB fixtures and a seeded fixture graph.
