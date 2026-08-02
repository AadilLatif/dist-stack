# Knowledge Graph Server (dist-kg)

`packages/dist-kg` (import name `kg_server`, dist name `dist-kg`) is the
**knowledge-graph MCP server** of the distribution suite. It exposes the
`dist_stack.kg` store — the shared knowledge graph of runs, artifacts, and
models linked by provenance edges — as MCP tools, resources, and a prompt.

This page documents the **server**. The `dist_stack.kg` **library** it serves
(store schema, upserts, the ingester, node-id conventions) is covered in
{doc}`kg`; the two are deliberately separate: the library is a pure SQLite
store, and `dist-kg` is the stateless MCP server that queries it.

The server is stateless (no lifespan): every tool resolves the KG DB path
**lazily per call** from `DIST_STACK_KG_DB` (or an explicit `kg_db`
argument), so `create_server()` is cheap and side-effect free.

## The eight MCP tools

| Tool | Module | Purpose |
|---|---|---|
| `get_node(node_id)` | `tools/queries.py` | Get a single knowledge-graph node by its stable id. |
| `get_neighbors(node_id, relation=None, direction="both", depth=1, limit=50)` | `tools/queries.py` | Get the neighbors of a node within `depth` hops, optionally restricted to one relation and/or direction (`in` / `out` / `both`). |
| `search_nodes(node_type=None, label=None, limit=50)` | `tools/queries.py` | Search nodes by exact `node_type` and/or a case-insensitive `label` match. |
| `graph_stats()` | `tools/queries.py` | Aggregate graph statistics: node counts by type, edge counts by relation, top-degree nodes, and a UTC snapshot timestamp. |
| `query_provenance(artifact_path=None, run_id=None, model_id=None, depth=1)` | `tools/provenance.py` | Resolve **exactly one** subject (runtime XOR) to its KG node and fetch its neighbors. |
| `get_provenance_chain(node_id, direction="up", max_depth=10)` | `tools/provenance.py` | Provenance ancestry/descendancy of a node, one list per depth (see [Provenance semantics](#provenance-semantics)). |
| `ingest(runstore_db=None, registry_db=None, manifest_dir=None, prune=False)` | `tools/ingest.py` | (Re)build the knowledge graph from the runstore, the model registry, and sidecar manifests (wraps `dist_stack.kg.ingest`, imported lazily). |
| `ingest_components(system_path=None, model_id=None, depth=2)` | `tools/components.py` | Ingest component nodes for a distribution system via the gdm MCP server (see [Component ingestion](#component-ingestion)). |

All tools return JSON strings. Errors return `{"success": false, "error": ...}`
payloads and never raise, following the ecosystem convention from
{doc}`conventions`.

## Node-id schemes

The knowledge graph identifies every node with a stable, namespaced id:

| Scheme | Meaning |
|---|---|
| `run:<run_id>` | A runstore run (e.g. `run:sim_1a2b3c4d5e6f`). |
| `artifact:<normpath>` | An artifact file, keyed by its normalized absolute path (e.g. `artifact:/data/outputs/out.json`). |
| `model:<model_id>` | A registered model. |
| `component:<system_model_id>:<uuid>` | A component ingested from gdm (e.g. `component:ieee13:load-1`). |

`query_provenance` maps its XOR subject to this scheme before traversing:
`artifact_path` -> `artifact:<normpath>`, `run_id` -> `run:<run_id>`, `model_id`
-> `model:<model_id>`.

The four provenance relations between these nodes are `has_artifact`,
`generated_by`, `derived_from`, and `references` (plus `has_component` /
`parent_of` from component ingestion).

## Resources and prompt

| Kind | Name | Purpose |
|---|---|---|
| Resource | `kg://stats` | Static; node counts by type, edge counts by relation, and the UTC snapshot timestamp. |
| Resource | `kg://graph/{node_id}` | Templated; the node plus its 1-hop neighbors (in and out) with edge metadata. |
| Prompt | `trace_provenance(subject)` | Instructions for answering provenance questions: resolve the subject, read its neighborhood, walk chains up/down, and cite concrete node ids and relations. |

## Provenance semantics

`get_provenance_chain(node_id, direction, max_depth)` walks the graph one
relation-set at a time and returns the chain as a list of depth-levels, each
level a list of node records (trailing empty depths are trimmed):

- **`direction="up"`** (ancestors) walks **incoming** edges with relations
  `derived_from` / `generated_by` / `references`.
- **`direction="down"`** (descendants) walks **outgoing** edges with relations
  `derived_from` / `has_artifact`.

`query_provenance` (XOR subject resolver) and `kg://graph/{node_id}` both
return the 1-hop neighborhood in both directions; use the chain tool when you
need the full ancestry or descendancy beyond one hop.

## Component ingestion

`ingest_components` resolves the distribution system (exactly one of
`system_path` / `model_id`; a `model_id` is resolved through the model
registry to its stored path), then:

1. spawns the **gdm MCP server** through the shared low-level client
   (`dist_stack.mcp.client.session()`, via `kg_server.gdm_client`) — the gdm
   launch command comes from `KG_GDM_COMMAND` (default `python`) and
   `KG_GDM_ARGS` (default `-m gdm.mcp.server`), and the child inherits the
   parent env so `DIST_STACK_MODEL_REGISTRY_DB` etc. flow through;
2. calls gdm `query_components` for the system and upserts one `component`
   node per component;
3. anchors a system node (`artifact:<normpath>`, node_type `gdm_system`) and
   adds `has_component` edges (system -> component);
4. for `depth >= 2`, calls gdm `get_component_relationships` per component and
   adds `parent_of` edges (parent component -> child component).

`system_model_id` is the resolved registry model_id when given, else a slug of
the system-path basename (with a best-effort reverse registry lookup first).
Re-ingestion is idempotent: node/edge upserts preserve `created_at_utc` and
never duplicate rows.

## Environment variables

| Variable | Purpose |
|---|---|
| `DIST_STACK_KG_DB` | Path to the KG SQLite database. When unset, KG-backed tools return a clean `KGUnavailableError` payload. |
| `KG_GDM_COMMAND` / `KG_GDM_ARGS` | Optional overrides for the gdm launch used by `ingest_components` (defaults `python` / `-m gdm.mcp.server`). |

## Running it

```bash
export DIST_STACK_KG_DB=/path/to/kg.db
uv run --project packages/dist-kg python -m kg_server
# or via the entry point:
uv run --project packages/dist-kg kg-server
```

See {doc}`mcp-wiring` for how `dist-kg` is wired into an LLM client, and
{doc}`ecosystem` for the end-to-end scenarios that query it.
