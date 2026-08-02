# Reference

## API surface summary

Every signature below matches the source at `src/dist_stack/`. Explicit `*_db=`
and `env_var=` kwargs are omitted from the "purpose" column for brevity — see
each module page for full signatures.

### `dist_stack.registry`

| Function | Purpose |
|---|---|
| `register(model_id, version=None, stored_path=..., *, model_hash=None, hash_fn=None, metadata=None, check_exists=True, store_relative_to_db=False)` | Upsert a model row; `version=None` → `next_version` |
| `lookup(model_id, version=None, *, resolve_path=True, expected_hash=None)` | Latest (or pinned) non-deleted version as `ModelRecord` |
| `lookup_path(model_id, version=None)` | Convenience stored-path string |
| `delete(model_id, version=None, *, soft=True)` | Soft (default) or hard delete; `version=None` targets all versions |
| `list_models(*, include_deleted=False)` | All records ordered by `model_id, version` |
| `resolve_model_ref(model_ref, *, registry_db_env_var=...)` | `model_ref` → stored path; drop-in for the legacy resolvers |
| `next_version(model_id)` | `max(version)+1`, else 1 |
| `make_model_id(source, *, namespace="dist-stack.models")` | Deterministic uuid5-based id |
| `get_registry_path(registry_db=None, *, env_var=...)` | Resolve DB path (arg > env var) |
| `ensure_schema(db_path)` | Idempotent create/migrate |

Data model: `ModelRecord(model_id, version, stored_path, model_hash, metadata, created_at_utc, deleted_at_utc)`.

### `dist_stack.manifest`

| Function | Purpose |
|---|---|
| `write_manifest(artifact_path, **kwargs)` | Create + write a frozen `Manifest` sidecar |
| `read_manifest(artifact_path)` | Read the sidecar (`FileNotFoundError` if absent) |
| `has_manifest(artifact_path)` | Sidecar existence check |
| `get_manifest_path(artifact_path)` | Expected sidecar path (`{artifact_path}.manifest.json`) |

Constants: `MANIFEST_SUFFIX = ".manifest.json"`, `MANIFEST_SCHEMA_VERSION = 1`.
Data model: `Manifest(artifact_path, artifact_type, tool, tool_version, schema_version, model_id, model_version, model_hash, package, package_version, config, derived_from, created_at_utc)`.

### `dist_stack.runstore`

| Function | Purpose |
|---|---|
| `create_run(tool, *, run_type, run_id=None, status=None, success=None, ...)` | Insert a run (NOT an upsert); mints `run_id` when None |
| `get_run(run_id)` | Fetch a non-deleted run |
| `list_runs(*, tool=None, run_type=None, status=None, ..., include_deleted=False, limit=100, offset=0)` | Filtered, newest-first |
| `update_run(run_id, *, status=None, ..., payload=None)` | Update provided kwargs; stamps `updated_at_utc` |
| `delete_run(run_id, *, soft=True)` | Soft (default) or hard delete (FK cascade) |
| `attach_artifact(run_id, artifact_path)` | Attach an artifact file + manifest sidecar to a run |
| `list_artifacts(run_id, *, include_deleted=False)` | Artifacts of a run, newest first |
| `make_run_id(prefix)` | Mint `"{prefix}_{hex12}"` (16 chars) |

Data models: `RunRecord` (with `success` property), `ArtifactRecord`.

### `dist_stack.kg`

| Function | Purpose |
|---|---|
| `upsert_node(node_id, node_type, *, label=None, ..., metadata=None)` | Upsert a node; metadata merged; resurrects soft-deleted |
| `get_node(node_id)` | Fetch a non-deleted node |
| `search_nodes(*, node_type=None, label=None, limit=50)` | Exact type + case-insensitive label match |
| `delete_node(node_id, *, soft=True)` | Soft (default) or hard delete (FK cascade on edges) |
| `upsert_edge(source_node, target_node, relation, *, metadata=None)` | Upsert on the unique `(source, target, relation)` triple |
| `get_neighbors(node_id, *, relation=None, direction="both", depth=1, limit=50)` | Bounded BFS; depth capped at 5; cycle-safe |
| `get_provenance_chain(node_id, *, direction="up", max_depth=10)` | Ancestry/descendancy by depth; cycle-safe |
| `graph_stats(*, ...)` | `node_counts`, `edge_counts`, `top_degree`, `updated_at_utc` |
| `ensure_schema(db_path)` | Idempotent create/migrate |
| `ingest(*, kg_db=None, runstore_db=None, registry_db=None, manifest_dir=None, prune=False, limit=None)` | Derive the graph from runstore + registry + sidecars |

Data models: `KGNode`, `KGEdge`, `KGStats`, `IngestReport`.

### `dist_stack.mcp`

| Function | Purpose |
|---|---|
| `json_safe(obj, **kwargs)` | JSON-encode with non-JSON coercion |
| `error_payload(message, **extra)` | `{"success": False, "error": ...}` string |

Plus `CONVENTIONS.md` — the ecosystem MCP server conventions ({doc}`conventions`).

### Top-level re-exports

`from dist_stack import` exposes the whole surface: `register`, `lookup`,
`lookup_path`, `delete`, `list_models`, `resolve_model_ref`, `next_version`,
`make_model_id`, `ensure_schema`, `get_registry_path`, all manifest functions
and `Manifest`, all runstore functions and `RunRecord`/`ArtifactRecord`, all KG
functions plus `KGNode`/`KGEdge`/`KGStats`/`IngestReport`/`ingest`, all error
classes, and `__version__`.

## Error hierarchy

All stores raise `ValueError`-derived errors so legacy `try/except ValueError`
catch sites keep working:

| Store | Base | Subclasses |
|---|---|---|
| registry | `RegistryError` | `InvalidModelRefError`, `ModelNotFoundError`, `ModelPathNotFoundError`, `RegistryUnavailableError`, `HashMismatchError` |
| runstore | `RunstoreError` | `RunstoreUnavailableError`, `RunNotFoundError`, `RunExistsError`, `ArtifactPathNotFoundError` |
| kg | `KGError` | `KGUnavailableError`, `NodeNotFoundError` |

## Design specs

The authoritative design documents live in this repo under
`docs/architecture-assessment/` (the former gdm-stack archive, now part of the
dist-stack monorepo):

| Doc | Topic | Relevant module |
|---|---|---|
| `09-model-registry-spec.md` | Model registry spec | `registry`, `manifest` |
| `10-mcp-sdk-unification-plan.md` | MCP SDK unification | `mcp` conventions |
| `11-runstore-and-workflow-runner-spec.md` | Runstore + workflow runner | `runstore` |
| `12-knowledge-graph-spec.md` | Knowledge graph (store + ingest) | `kg` |
| `13-monorepo-restructure-spec.md` | Monorepo restructure | workspace layout |

The docs resolve at `docs/architecture-assessment/<file>`.
