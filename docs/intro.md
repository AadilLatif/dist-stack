# Dist-Stack: Model Registry & Provenance Library

```{versionadded} 0.1.0
```

`dist-stack-model-registry` (import name `dist_stack`, version `0.1.0`) is the
**shared library** behind the NREL distribution suites: one implementation of
the model-reference (`model_ref`) resolution contract that previously lived,
copy-pasted, in `grid-data-models`, `gdm-flow`, and `erad` — plus four
stdlib-only SQLite stores that track models, artifact provenance, run state,
and a queryable knowledge graph.

**Zero runtime dependencies.** The library is pure Python >= 3.10 using only
the standard library (`sqlite3`, `json`, `os`, `pathlib`, `uuid`,
`dataclasses`, `datetime`, `contextlib`). It requires SQLite >= 3.24 (shipped
with Python >= 3.10). There is no optional `mcp` dependency — MCP servers live
in the ecosystem repos and import tiny helpers from `dist_stack.mcp`.

## The four stores

| Module | Purpose | Primary identity |
|---|---|---|
| `dist_stack.registry` | Versioned **model registry** (`register` / `lookup` / `delete` / `list_models`) | `(model_id, version)` |
| `dist_stack.manifest` | **Provenance sidecar** reader/writer next to every artifact | `{artifact_path}.manifest.json` |
| `dist_stack.runstore` | **Run-state + artifact store** (`create_run` / `attach_artifact`) | `run:<run_id>`, `art_<hex12>` |
| `dist_stack.kg` | **Knowledge graph** store + ingester (`upsert_node` / `get_provenance_chain` / `ingest`) | `run:`/`artifact:`/`model:` node ids |

`dist_stack.mcp` is not a store — it is the **conventions home** for the
ecosystem MCP servers (see {doc}`conventions`) plus two JSON-serialization
helpers.

## Environment variables

Each store resolves its database path **lazily per call** — never at import —
with the explicit keyword argument always winning over the env var:

| Env var | Store | Resolution order |
|---|---|---|
| `DIST_STACK_MODEL_REGISTRY_DB` | `registry` | `registry_db` arg > `model_ref["registry_db"]` > env var |
| `DIST_STACK_RUNSTORE_DB` | `runstore` | `runstore_db` arg > env var |
| `DIST_STACK_KG_DB` | `kg` | `kg_db` arg > env var |

If no path can be resolved, the store raises its `*UnavailableError`
(`RegistryUnavailableError`, `RunstoreUnavailableError`, `KGUnavailableError`)
**at call time only**. The env var may be set *after* `import dist_stack` and
is honored on the next call.

## Design rules that apply everywhere

- **Stateless-per-call connections.** Every functional call opens its own
  `sqlite3` connection (WAL, `busy_timeout=5000`, `foreign_keys=ON`),
  context-managed and closed on return. This is safe for concurrent asyncio
  MCP tool calls with no locks. Do *not* add shared-connection caching without
  an internal `threading.Lock`.
- **`PRAGMA user_version` migration.** Each store's `migrate(conn)` is
  idempotent and safe on every open; future additive changes ship as guarded
  `ALTER TABLE` statements.
- **ValueError-based error hierarchy.** Every store raises a base error that
  subclasses `ValueError`, with precise subclasses for callers
  (`ModelNotFoundError`, `RunNotFoundError`, `NodeNotFoundError`, ...).
- **Upserts are idempotent.** Registry models, KG nodes, and KG edges are all
  upserts that preserve `created_at_utc` and resurrect soft-deleted rows.
- **Soft delete by default.** `delete*` stamps `deleted_at_utc` unless
  `soft=False`.

```{note}
The functional API is the public surface. Re-exports live at the top level:
`from dist_stack import register, lookup, create_run, attach_artifact, upsert_node, ingest`.
```

## The dist-stack monorepo

This library is the shared core of a larger **monorepo** (`dist-stack`) that
also ships the orchestration-plane MCP servers and the visibility UI, all as
one `uv` workspace:

| Member | Role | Docs |
|---|---|---|
| `packages/dist-stack-model-registry` | the shared library (this book) | this page + {doc}`quickstart` |
| `packages/dist-workflow-runner` | MCP workflow orchestrator | {doc}`runner` |
| `packages/dist-kg` | MCP knowledge-graph server | {doc}`kg-server` |
| `apps/dist-dashboard` | read-only Streamlit visibility UI | {doc}`dashboard` |
| `docs/` | this Jupyter Book + the architecture-assessment archive | {doc}`ecosystem`, {doc}`mcp-wiring` |

The five domain repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`,
`shift`) stay external; {doc}`ecosystem` shows how everything ties together.

## Install

```bash
# dev (editable, PEP 660) — the monorepo workspace
uv sync
uv run python -c "import dist_stack; print(dist_stack.__version__)"

# prod / CI (from PyPI or the git URL)
pip install "dist-stack-model-registry>=0.1,<1"
```

See {doc}`quickstart` for the five-minute tour, {doc}`ecosystem` for how the
shared contracts tie the ecosystem repos together in practice, and
{doc}`references` for the full API surface and the design specs
(`docs/architecture-assessment/` 09–12).
