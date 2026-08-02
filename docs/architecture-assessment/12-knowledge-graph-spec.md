# 12. Knowledge Graph Spec (Phase 3)

**Status:** Implementation-ready design (oracle-verified against dist-stack + dist-workflow-runner source).
**Date:** 2026-08-01

---

# Phase-3 Design — Knowledge Graph (store + ingester + MCP server + runner bridge)

## Decision summary

| Deliverable | Decision | Why |
|---|---|---|
| **A. Store** | `dist_stack.kg` package in **dist-stack** (stdlib SQLite, mirror of `registry`/`runstore`) | dist-stack is dependency-free and already hosts two SQLite stores with an exact pattern. Graph store is stdlib-only (nodes + edges + recursive CTEs). Rejected: networkx (non-persistent), neo4j (a service — over-built for thousands of rows). |
| **B. Ingestion** | `dist_stack.kg.ingest` module; reads **runstore `artifacts` table + sidecar files on disk** (+ `runs`, + registry `models`) | Artifacts table is the index; sidecar is the authority (`derived_from`, `config`). No repo internals, no MCP client for v1. |
| **C. MCP server** | **New sibling repo `dist-kg`** | dist-stack can't host `mcp`; the runner's charter is execution, not memory. KG is the eighth component. |
| **D. Runner bridge** | `run_workflow(..., reuse_run_id=None)` returns prior execution graph **without auto-rewriting** | ~30 lines, zero new deps; auto-rewrite stays v2. |
| **E. Memory mapping** | 2 resources + 1 prompt on the KG server | §E below. |

---

## A. KG store — `dist_stack.kg`

### A.1 Module layout (exact mirror of `runstore/`)

```
src/dist_stack/kg/
├── __init__.py     # re-exports public API
├── model.py        # KGNode, KGEdge, KGStats, IngestReport (frozen dataclasses)
├── schema.py       # SCHEMA_VERSION, DDL_CREATE_NODES, DDL_CREATE_EDGES, DDL_INDEX_*, DDL_ALTER_*, migrate()
├── sqlite.py       # _connect() — verbatim clone of runstore/sqlite.py (WAL, busy_timeout=5000, foreign_keys=ON)
├── errors.py       # KGError hierarchy
├── api.py          # stateless functional API (node/edge CRUD + queries)
└── ingest.py       # ingestion from runstore + registry + sidecars
```

`dist-stack/pyproject.toml` unchanged — zero new dependencies.

### A.2 Schema (exact DDL)

```sql
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,      -- stable identity: run:<run_id>, artifact:<path>, model:<model_id>
    node_type      TEXT NOT NULL,         -- gdm_system|component|gdm_flow_run|erad_simulation|
                                         -- ditto_conversion|shift_feeder|workflow_execution|artifact|model
    label          TEXT,
    artifact_path  TEXT,
    run_id         TEXT,
    model_id       TEXT,
    tool           TEXT,
    tool_version   TEXT,
    metadata       TEXT,                  -- JSON object
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id        TEXT PRIMARY KEY,      -- minted "e_<uuid4().hex[:12]>"
    source_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    target_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    relation       TEXT NOT NULL,         -- vocabulary in §B; NO CHECK
    metadata       TEXT,                  -- JSON: {tool, tool_version, model_id, model_hash, config}
    created_at_utc TEXT NOT NULL,
    deleted_at_utc TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique   ON edges(source_node, target_node, relation);
CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target_node);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
CREATE INDEX IF NOT EXISTS idx_nodes_type     ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_label    ON nodes(label);
CREATE INDEX IF NOT EXISTS idx_nodes_run      ON nodes(run_id);
CREATE INDEX IF NOT EXISTS idx_nodes_model    ON nodes(model_id);
CREATE INDEX IF NOT EXISTS idx_nodes_artifact ON nodes(artifact_path);
```

`idx_edges_unique` is the idempotency anchor (upsert conflict target) and the source-node lookup index. `label`/`node_type` get no CHECK constraints — the API enforces the `Literal`.

### A.3 Migration — verbatim runstore pattern

```python
SCHEMA_VERSION = 1
DDL_ALTER_NODES: tuple[str, ...] = ()
DDL_ALTER_EDGES: tuple[str, ...] = ()

def migrate(conn) -> None:
    conn.execute(DDL_CREATE_NODES)
    conn.execute(DDL_CREATE_EDGES)
    for stmt in DDL_INDEX_*:            # all IF NOT EXISTS
        conn.execute(stmt)
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is not None and row[0] >= SCHEMA_VERSION:
        return
    for stmt in (*DDL_ALTER_NODES, *DDL_ALTER_EDGES):
        try:
            conn.execute(stmt)
        except OperationalError:
            pass
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

### A.4 Node ID scheme — stable, idempotent keys

| Node | node_id | Uniqueness |
|---|---|---|
| run | `run:<run_id>` | run_id PK in runstore |
| artifact / gdm_system | `artifact:<normpath(abs_path)>` | one node per artifact file |
| model | `model:<model_id>` | registry identity; stub-created from manifests if missing (`stub: true`) |
| component (v2) | `component:<system_model_id>:<component_uuid>` | key space frozen now |

Soft-deleted nodes keep their key — re-ingest resurrects.

### A.5 Public API — exact signatures

```python
DEFAULT_ENV_VAR = "DIST_STACK_KG_DB"

def get_kg_path(kg_db=None, *, env_var=DEFAULT_ENV_VAR) -> str
def ensure_schema(db_path) -> None
def upsert_node(node_id, node_type, *, label=None, artifact_path=None, run_id=None,
                model_id=None, tool=None, tool_version=None, metadata=None,
                kg_db=None, env_var=DEFAULT_ENV_VAR) -> KGNode
def get_node(node_id, *, kg_db=None, env_var=DEFAULT_ENV_VAR) -> KGNode       # NodeNotFoundError
def search_nodes(*, node_type=None, label=None, limit=50, kg_db=None, env_var=DEFAULT_ENV_VAR) -> list[KGNode]
def delete_node(node_id, *, soft=True, kg_db=None, env_var=DEFAULT_ENV_VAR) -> None
def upsert_edge(source_node, target_node, relation, *, metadata=None,
                kg_db=None, env_var=DEFAULT_ENV_VAR) -> KGEdge
def get_neighbors(node_id, *, relation=None, direction="both", depth=1, limit=50,
                  kg_db=None, env_var=DEFAULT_ENV_VAR) -> list[KGEdge]
def get_provenance_chain(node_id, *, direction="up", max_depth=10,
                         kg_db=None, env_var=DEFAULT_ENV_VAR) -> list[list[KGNode]]
def graph_stats(*, kg_db=None, env_var=DEFAULT_ENV_VAR) -> KGStats
```

**Upsert semantics (idempotency contract):**
- `INSERT ... ON CONFLICT(node_id) DO UPDATE SET` all mutable fields + `deleted_at_utc=NULL` (resurrect); `created_at_utc` preserved.
- Edges: `ON CONFLICT(source_node, target_node, relation) DO UPDATE SET metadata=..., deleted_at_utc=NULL`; edge_id preserved.
- `metadata` is **merged, not replaced**, precedence **registry > manifest > runstore**.

**Chain traversal** — recursive CTE with cycle guard (`instr(c.path, e.source_node) = 0`), both directions:
- `up`: walk incoming edges, relations `('derived_from','generated_by','references')`.
- `down`: walk outgoing edges, relations `('derived_from','has_artifact')`.
- `get_neighbors` depth>1 uses same CTE; `max_depth` hard-capped at 5.

---

## B. Ingestion — `dist_stack.kg.ingest`

### B.1 Source decision

Primary: iterate runstore `artifacts` table (join `runs`), read each sidecar via `manifest.read_manifest(artifact_path)`. Plus `runs` with no artifacts + registry `models`. Optional `manifest_dir` sweep for unattached sidecars (no run edges).

### B.2 Node mapping

| Source | node_id | node_type | label | metadata |
|---|---|---|---|---|
| registry `models` | `model:<model_id>` | `model` | model_id | `{version, stored_path, model_hash, metadata, created_at_utc}` |
| runstore `runs` | `run:<run_id>` | `runs.run_type` | `f"{tool} {run_id}"` | `{tool, tool_version, status, implementation, session_id, message, payload, created_at_utc}` |
| artifacts + sidecar | `artifact:<path>` | `manifest.artifact_type` else `"artifact"` | basename | `{artifact_id, run_id, tool, tool_version, model_id, model_version, model_hash, package, package_version, config, derived_from_raw, created_at_utc}` |
| sidecar `gdm_system` | same | `gdm_system` | basename | same (+ model_id reference edge) |
| workflow execution graph | same | `workflow_execution` | `wf_<hex12>.execution.json` | `{workflow_id, workflow_version, status, source_prompt, artifact_path, step_count, step_tools}` |
| gdm `get_components` (v2) | `component:<system_model_id>:<uuid>` | `component` | name | `{component_type, feeder, substation, phases, in_service}` |

Component nodes have **no v1 source** — extraction requires gdm schema knowledge behind MCP calls. Key space frozen; v2 module in dist-kg reuses the runner's ServerPool pattern.

### B.3 Edge mapping

| relation | direction | derived from |
|---|---|---|
| `has_artifact` | run → artifact | `artifacts.run_id` |
| `generated_by` | artifact → run | same row, inverse |
| `derived_from` | artifact → artifact/run | `manifest.derived_from`: (1) matches artifact node; (2) matches run_id (attach fallback); (3) `://` URI → no edge, recorded in metadata; (4) unresolvable → no edge, counted. Self-loops skipped. |
| `references` | artifact/run → model | `manifest.model_id` / `runs.model_id` → `model:<model_id>` (stub-created if absent) |
| `modifies`, `visualizes`, `consumes`, `produces`, `validates` | — | **Declared vocabulary, no v1 source** |
| `has_component`, `parent_of` | — | v2 gdm MCP-client pass |

Edge `metadata`: `{tool, tool_version, model_id, model_hash, config}` from the sidecar.

### B.4 Ingester API — exact signature

```python
@dataclass(frozen=True)
class IngestReport:
    kg_db: str
    pass_started_at_utc: str
    nodes_created: int; nodes_updated: int
    edges_created: int; edges_updated: int
    derived_from_unresolved: list[str]
    derived_from_uri_skipped: int
    sidecar_missing: int
    errors: list[str]

def ingest(*, kg_db=None, runstore_db=None, registry_db=None, manifest_dir=None,
           kg_env=DEFAULT_ENV_VAR, runstore_env=runstore.DEFAULT_ENV_VAR,
           registry_env=registry.DEFAULT_ENV_VAR,
           prune: bool = False, limit: int | None = None) -> IngestReport
```

**Pass order:** (1) registry models → model nodes; (2) `runs` → run nodes; (3) `artifacts` + sidecars → artifact/system nodes; (4) edges (has_artifact/generated_by from rows, derived_from from sidecars, references from model_id); (5) optional manifest_dir sweep.

**Semantics:** idempotent (re-run → zero changes); soft-deleted source rows skipped; `prune=False` default (accumulate); `prune=True` soft-deletes nodes/edges with `updated_at_utc < pass_started_at_utc` (mirror mode); never fails mid-pass (errors collected in `report.errors`). KG is a derived index; rebuild-from-source is the repair story.

---

## C. KG MCP server — new repo `dist-kg`

### C.1 Home + deps

New sibling repo `dist-kg`. deps: `mcp>=2.0,<3`, `dist-stack-model-registry`. Entry point `kg-server = "kg_server.__main__:main"`.

### C.2 Package layout (CONVENTIONS.md shape)

```
dist-kg/
├── pyproject.toml
├── README.md
├── src/kg_server/
│   ├── __init__.py           # __version__
│   ├── __main__.py           # main → create_server().run(transport="stdio")
│   ├── server.py             # create_server(): MCPServer + register() calls (stateless — no lifespan)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── queries.py        # register(mcp): get_node, get_neighbors, search_nodes, graph_stats
│   │   ├── provenance.py     # register(mcp): query_provenance, get_provenance_chain
│   │   └── ingest.py         # register(mcp): ingest
│   ├── resources/
│   │   ├── __init__.py
│   │   └── index.py          # register(mcp): kg://stats, kg://graph/{node_id}
│   └── prompts/
│       ├── __init__.py
│       └── provenance.py     # register(mcp): trace_provenance(subject)
└── tests/                    # conftest, test_tools, test_resources, test_prompts, test_integration
```

Stateless server; `KGUnavailableError` caught in each tool → `{"success": False, "error": ...}` (JSON-string contract; `dist_stack.mcp.serialization` imported directly).

### C.3 Tool surface — exact signatures

- `get_node(node_id)` — `{"success", "node": {...}}`
- `get_neighbors(node_id, relation=None, direction="in"/"out"/"both"=both, depth=1, limit=50)` — `{"success", "node", "neighbors": [{"edge": {...}, "node": {...}}]}`
- `query_provenance(artifact_path=None, run_id=None, model_id=None, depth=1)` — XOR at runtime — `{"success", "node", "neighbors": [...]}`
- `get_provenance_chain(node_id, direction="up"/"down"=up, max_depth=10)` — `{"success", "node_id", "direction", "chain": [[depth 0], [depth 1], ...]}`
- `search_nodes(node_type=None, label=None, limit=50)` — `{"success", "count", "nodes": [...]}`
- `graph_stats()` — `{"success", "stats": {"nodes": {...by type}, "edges": {...by relation}, "top_degree": [...], "updated_at_utc": ...}}`
- `ingest(runstore_db=None, registry_db=None, manifest_dir=None, prune=False)` — returns IngestReport JSON
- `ingest_components(system_model_id=None, component_type=None, name=None, ...)` — ingests v2 `component:` nodes from a GDM system model / component catalog

---

## D. Dynamic workflow planning bridge — minimal v1 hook

```python
# dist-workflow-runner/src/workflow_runner/tools/runs.py
async def run_workflow(ctx, workflow_id: str, inputs: dict | None = None,
                       run_id: str | None = None,
                       reuse_run_id: str | None = None) -> str:
```

When `reuse_run_id` given: (1) `runstore.get_run` — must exist, `run_type=="workflow_execution"`, `status=="succeeded"`; (2) load execution-graph artifact; (3) validate `prior_graph.workflow_id == workflow_id`; (4) execute normally; response gains `"prior_graph"` (steps with results **omitted** — size guard). **No automatic rewriting** — the agent does reuse/rewrite via KG `search_nodes`/`get_node`. Auto-rewrite stays v2.

---

## E. Agent memory mapping (doc 08 §8.6)

| Memory | Stored in | Surfaced by KG server |
|---|---|---|
| **Episodic** | runstore runs/artifacts + sidecars | `query_provenance(run_id=...)`, `get_provenance_chain("run:<run_id>")`, `kg://graph/run:<run_id>` |
| **Procedural** | workflow templates + execution graphs | `search_nodes(node_type="workflow_execution")`, `run_workflow(reuse_run_id=...)` |
| **Semantic** | the KG itself | `graph_stats`, `search_nodes`, `get_neighbors`, `kg://stats` |

**Resources (2):** `kg://stats` (static, node/edge counts), `kg://graph/{node_id}` (templated, node + 1-hop neighbors).
**Prompt (1):** `trace_provenance(subject)` — instructions for provenance questions against the KG.

---

## F. Config

| Env var | Owner | Meaning |
|---|---|---|
| `DIST_STACK_KG_DB` | new | KG SQLite path; lazy per call; `KGUnavailableError` when unset |
| `DIST_STACK_RUNSTORE_DB` | existing | read by `ingest` |
| `DIST_STACK_MODEL_REGISTRY_DB` | existing | read by `ingest` |

---

## G. Test strategy

- **dist-stack:** test_kg_schema (user_version, guarded-ALTER), test_kg_api (upsert idempotency, metadata merge precedence, FK cascade, resurrect), test_kg_queries (filters, BFS, cycle safety, chains, caps), test_kg_ingest (seeded runstore/registry + hand-crafted sidecars; double-ingest zero-change; prune; manifest_dir; error collection), test_kg_env_laziness, test_kg_thread_safety.
- **dist-kg:** test_tools (JSON-string contract, query_provenance XOR, error payloads), test_resources/test_prompts, test_integration (ingest → query round-trip).
- **dist-workflow-runner:** reuse_run_id happy path (results stripped), wrong workflow_id → error, non-execution run → error, unknown run → error, unset reuse → unchanged.

---

## H. Implementation order

```
Phase 3a  dist_stack.kg store        (schema → sqlite → errors → model → api)   FOUNDATION
Phase 3b  dist_stack.kg.ingest       (depends on 3a + stable runstore/registry/manifest APIs)
Phase 3c  dist-kg repo: MCP server   (depends on 3a only — can start once api.py lands)
Phase 3d  runner reuse_run_id hook   (depends only on runstore — no KG dependency)
Phase 3e  docs: new gdm-stack doc 12, update doc 02 inventory + doc 08 §8.4 diagram
Phase 3f  v2: component ingestion via gdm MCP client (key space already frozen)
```

**Parallelizable:** 3b ∥ 3d; 3c can proceed against the 3a API while 3b is in flight. 3a is the only strict prerequisite. **Sequencing rule:** never ship 3c/3d tool surfaces before 3a is merged.

**Charter check:** dist-stack gains zero dependencies; no existing repo's behavior changes; all five domain repos untouched in Phase 3.
