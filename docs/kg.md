# Knowledge Graph (`dist_stack.kg`)

The knowledge graph is a stdlib-only SQLite graph store (nodes + edges +
recursive CTEs) that surfaces cross-artifact provenance: which run produced
which artifact, what an artifact was derived from, and which model a run or
artifact references.

```{note}
KG errors subclass `KGError(ValueError)`: `KGUnavailableError`,
`NodeNotFoundError`.
```

## Schema

### `nodes`

```sql
node_id        TEXT PRIMARY KEY,   -- run:<run_id>, artifact:<path>, model:<model_id>
node_type      TEXT NOT NULL,      -- gdm_system|component|gdm_flow_run|erad_simulation|
                                   -- ditto_conversion|shift_feeder|workflow_execution|artifact|model
label          TEXT,
artifact_path  TEXT,  run_id TEXT, model_id TEXT,
tool TEXT, tool_version TEXT,
metadata       TEXT,               -- JSON object
created_at_utc TEXT NOT NULL, updated_at_utc TEXT, deleted_at_utc TEXT
```

### `edges`

```sql
edge_id        TEXT PRIMARY KEY,   -- minted "e_<uuid4().hex[:12]>"
source_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
target_node    TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
relation       TEXT NOT NULL,      -- vocabulary below; NO CHECK
metadata       TEXT,               -- JSON: {tool, tool_version, model_id, model_hash, config}
created_at_utc TEXT NOT NULL, deleted_at_utc TEXT
```

A unique index on `(source_node, target_node, relation)` is the idempotency
anchor — edge upserts conflict on that triple.

## Node ID schemes (stable, idempotent)

| Node | `node_id` | Uniqueness |
|---|---|---|
| run | `run:<run_id>` | run_id PK in runstore |
| artifact / gdm_system | `artifact:<normpath(abs_path)>` | one node per artifact file |
| model | `model:<model_id>` | registry identity; stub-created from manifests if missing (`stub: true`) |
| component (v2) | `component:<system_model_id>:<uuid>` | key space frozen now |

Soft-deleted nodes keep their key — re-ingest **resurrects** them.

## Relation vocabulary

`has_artifact` (run → artifact), `generated_by` (artifact → run),
`derived_from` (artifact → artifact/run), `references` (artifact/run → model),
plus declared-but-not-yet-ingested `modifies`, `visualizes`, `consumes`,
`produces`, `validates`; v2 adds `has_component`, `parent_of`.

## Upsert semantics (idempotency contract)

- **Nodes**: `INSERT ... ON CONFLICT(node_id) DO UPDATE SET` all mutable fields
  + `deleted_at_utc=NULL` (resurrect); `created_at_utc` is **preserved**.
  `metadata` is **merged**, not replaced (new keys overwrite, existing keys
  kept).
- **Edges**: `ON CONFLICT(source_node, target_node, relation)`; `edge_id` and
  `created_at_utc` preserved; `metadata` replaced, `deleted_at_utc` cleared.

## Public API

### Nodes

```{eval-rst}
.. function:: upsert_node(node_id, node_type, *, label=None, artifact_path=None, run_id=None, model_id=None, tool=None, tool_version=None, metadata=None, kg_db=None, env_var="DIST_STACK_KG_DB") -> KGNode

   Upsert keyed on ``node_id``. ``node_type`` is validated against the
   Literal. ``metadata`` is merged with any existing value.

.. function:: get_node(node_id, *, kg_db=None, env_var=...) -> KGNode

   Fetch a non-deleted node; :class:`NodeNotFoundError` on miss.

.. function:: search_nodes(*, node_type=None, label=None, limit=50, kg_db=None, env_var=...) -> list[KGNode]

   ``node_type`` matches exactly; ``label`` matches case-insensitively —
   exact/prefix first, then a ``LIKE '%..%'`` substring fallback. Soft-deleted
   excluded.

.. function:: delete_node(node_id, *, soft=True, kg_db=None, env_var=...) -> None

   ``soft=True`` stamps ``deleted_at_utc``; ``soft=False`` hard-deletes —
   incident edges cascade via the FK.
```

```python
from dist_stack import upsert_node, get_node, search_nodes

upsert_node("model:my-model", "model", label="my-model", kg_db=kg_db)
node = get_node("model:my-model", kg_db=kg_db)

results = search_nodes(node_type="artifact", label="result", kg_db=kg_db)
```

### Edges

```{eval-rst}
.. function:: upsert_edge(source_node, target_node, relation, *, metadata=None, kg_db=None, env_var=...) -> KGEdge

   Mints ``e_<uuid4().hex[:12]>``; upserts on the unique
   ``(source_node, target_node, relation)`` triple. Raises
   :class:`NodeNotFoundError` if either endpoint is missing or soft-deleted.
```

```python
from dist_stack import upsert_edge

upsert_edge("run:sim_000000000001", "artifact:/data/result.json",
            "has_artifact", kg_db=kg_db)
```

### Queries

```{eval-rst}
.. function:: get_neighbors(node_id, *, relation=None, direction="both", depth=1, limit=50, kg_db=None, env_var=...) -> list[KGEdge]

   Edges reachable within ``depth`` hops (bounded BFS). ``direction`` ∈
   ``in``/``out``/``both``; ``depth`` > 1 uses a recursive CTE with a cycle
   guard (``instr(path, node_id) = 0``) and is hard-capped at 5. Edges are
   ordered by BFS depth. Soft-deleted nodes/edges excluded.

.. function:: get_provenance_chain(node_id, *, direction="up", max_depth=10, kg_db=None, env_var=...) -> list[list[KGNode]]

   Provenance ancestry/descendancy as a list of lists grouped by depth.
   ``up`` walks incoming edges with ``('derived_from','generated_by','references')``;
   ``down`` walks outgoing edges with ``('derived_from','has_artifact')``.
   Cycle-safe. Trailing empty depths trimmed.
```

```python
from dist_stack import get_neighbors, get_provenance_chain

# what produced this artifact?
chain = get_provenance_chain("artifact:/data/result.json", direction="up", kg_db=kg_db)
for depth, level in enumerate(chain):
    print(depth, [n.node_id for n in level])

# both directions, two hops, only derived_from edges
neighbors = get_neighbors(
    "artifact:/data/result.json",
    relation="derived_from",
    direction="both",
    depth=2,
    kg_db=kg_db,
)
```

### Stats

```{eval-rst}
.. function:: graph_stats(*, kg_db=None, env_var=...) -> KGStats

   ``node_counts`` by type, ``edge_counts`` by relation, top-10 ``top_degree``
   list, and ``updated_at_utc`` — over non-deleted rows.
```

```python
from dist_stack import graph_stats

stats = graph_stats(kg_db=kg_db)
stats.node_counts    # {'model': 1, 'run': 1, 'artifact': 2}
stats.edge_counts    # {'has_artifact': 1, 'references': 3}
```

## Ingestion (`dist_stack.kg.ingest`)

The ingester derives the graph from the registry, the runstore, and the
sidecars on disk. It is **idempotent** — re-running against unchanged sources
yields `nodes_created == 0` and `edges_created == 0`.

```{eval-rst}
.. function:: ingest(*, kg_db=None, runstore_db=None, registry_db=None, manifest_dir=None, kg_env="DIST_STACK_KG_DB", runstore_env="DIST_STACK_RUNSTORE_DB", registry_env="DIST_STACK_MODEL_REGISTRY_DB", prune=False, limit=None) -> IngestReport
```

**Pass order:**

1. **Registry models** → `model:<model_id>` nodes
   (metadata `{version, stored_path, model_hash, metadata, created_at_utc}`).
2. **Runs** → `run:<run_id>` nodes (`node_type=runs.run_type`,
   `label=f"{tool} {run_id}"`).
3. **Artifacts + sidecars** → `artifact:<normpath(path)>` nodes
   (`node_type=manifest.artifact_type` else `"artifact"`; sidecar fields win
   over the runstore row).
4. **Edges** — `has_artifact`/`generated_by` from the artifact rows,
   `derived_from` from sidecars (§B.3 resolution), `references` from `model_id`
   fields (stub-creating missing `model:` nodes with `{"stub": True}`).
5. **Optional `manifest_dir` sweep** — unattached sidecars become artifact
   nodes with `derived_from`/`references` edges but **no run edges**.
6. **`prune=True`** (mirror mode) — soft-deletes nodes whose `updated_at_utc`
   predates the pass start, and any edge not re-affirmed this pass.

**Semantics:** soft-deleted source rows (runs/artifacts with
`deleted_at_utc NOT NULL`) are skipped and never resurrected; an unavailable
optional source (runstore/registry) is skipped and reported in
`report.errors` rather than aborting; per-row failures are collected in
`report.errors` — the pass never raises mid-way. `limit` caps the number of
artifact rows ingested.

```python
from dist_stack import ingest

report = ingest(
    kg_db=kg_db,
    runstore_db=run_db,
    registry_db=reg_db,
)
print(report.nodes_created, report.edges_created)  # e.g. 3, 5
print(report.derived_from_unresolved)              # [] on a clean source
print(report.errors)                               # [] on success
```

## The `IngestReport` dataclass

```python
@dataclass(frozen=True)
class IngestReport:
    kg_db: str
    pass_started_at_utc: str
    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_updated: int
    derived_from_unresolved: list[str]
    derived_from_uri_skipped: int
    sidecar_missing: int
    errors: list[str]
```

## Environment variable

`DIST_STACK_KG_DB` — read lazily per call; `kg_db` argument wins. Raising
`KGUnavailableError` when unset.
