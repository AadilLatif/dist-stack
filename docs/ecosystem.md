# The Ecosystem at a Glance

Dist-stack is a **monorepo** that consolidates five former repositories —
`gdm-stack` (docs), `dist-stack`, `dist-workflow-runner`, `dist-kg`, and
`dist-dashboard` — into five packages under one `uv` workspace. The five domain
repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`, `shift`) stay external.
This chapter is the **map**: what exists, and how the shared contracts — the
registry, the provenance sidecars, the runstore, and the knowledge graph — tie
the domain servers together.

```{note}
**This page vs the wiring guide.** {doc}`ecosystem` is about **what exists and
how data flows** between the servers. {doc}`mcp-wiring` is the operational
companion — how to *configure your client* (the launcher paths, env vars, and
servers) to actually talk to them.
```

For intent-first walk-throughs (run a study, trace results, run a reusable
workflow), see {doc}`usage-scenarios`.

## The ecosystem at a glance

The ecosystem has eight MCP servers: five in the external domain repos and two
orchestration-plane servers that now live in this monorepo
(`packages/dist-workflow-runner`, `packages/dist-kg`). Tool counts reflect the
**current live MCP registrations** (verified by `list_tools` handshakes during
MCP wiring); the pre-unification audit documented that READMEs drifted (gdm
20→24, erad 26→27, shift 33→36).

| Repo / package | Entry point / role | Tools | Resources | Prompts | Notes |
|---|---|---|---|---|---|
| **grid-data-models** *(external)* | `gdm-mcp-server` — model authoring / validation / inspection | 28 | — | — | `system_path` **or** `model_ref` → registry |
| **gdm-flow** *(external)* | `gdm-flow-mcp-server` — power-flow solvers | 15 | — | — | solvers + docs/API introspection |
| **erad** *(external)* | `erad-mcp` — hazard simulation | 33 | 3 namespaces | — | stateful; `run_simulation` / `generate_scenarios` |
| **ditto** *(external)* | `ditto_mcp` — model conversion | 14 | 2 | 2 | FastMCP; readers/writers |
| **shift** *(external)* | `shift-mcp-server` — feeder synthesis | 36 | 3 | 3 | `MCPServer` + per-module `register(mcp)` |
| **dist-workflow-runner** (this monorepo, `packages/`) | `workflow_runner` — MCP *client* orchestrator | 8 | 2 | 1 | runstore-backed; per-server subprocesses |
| **dist-kg** (this monorepo, `packages/`) | `kg-server` — knowledge-graph server | 8 | 2 | 1 | stateless; env-lazy `dist_stack.kg` |
| **dist-stack-model-registry** (this monorepo, `packages/`) | shared contracts (the library) | — | — | — | no MCP server; conventions + helpers (`dist_stack.mcp`) |

(`erad_plugins` is a ninth repo but ships **no** MCP server — 0 tools — so it
is excluded from the server count. The monorepo also contains
`apps/dist-dashboard`, the read-only visibility UI, and `docs/` — the Jupyter
Book that includes this chapter and the architecture-assessment archive.)

Each monorepo member has its own dedicated page: the runner's tool surface,
workflow format, and runstore lifecycle live in {doc}`runner`; the dist-kg
server's tools, node-id schemes, and provenance semantics live in
{doc}`kg-server` (distinct from {doc}`kg`, which documents the `dist_stack.kg`
library); and the dashboard's views and read-only contract live in
{doc}`dashboard`.

The three stores at the center — `models(model_id, version, stored_path)`,
runstore `runs`/`artifacts`, and the KG `nodes`/`edges` — are the **shared
contracts** every repo writes to or reads from. The model-registry contract is
already implicit in gdm, gdm-flow, and erad, which all resolve `model_ref`
against `DIST_STACK_MODEL_REGISTRY_DB` (audit finding 5).

## The provenance spine

Every artifact produced by any domain server flows through the same spine:

```
   MCP tool call (domain server)           attach_artifact(run_id, path)
        |                                         |
        v                                         v
   artifact file  -- writes -->  *.manifest.json   +---->  runstore
   (out.json)                    sidecar           |       runs + artifacts
                                 (derived_from,    |       run:<run_id>, art_<hex12>
                                  config,          |
                                  model_id, tool,  |
                                  created_at_utc)  |
        |                                         |
        +---------------- ingest reads all three  |
                                                   v
                              knowledge graph (dist_stack.kg)
                              run:<run_id> / artifact:<path> / model:<model_id>
                              nodes + has_artifact / generated_by /
                              derived_from / references edges
                                                   |
                                                   v
                              dist-kg queries: get_provenance_chain,
                              get_neighbors, search_nodes, graph_stats
```

In words: a domain tool writes an **artifact file**; the library (or the tool's
best-effort hook) writes a **manifest sidecar** recording `derived_from`,
`config`, and `model_id`; `runstore.attach_artifact` indexes the artifact
against a **run row**; `dist_stack.kg.ingest` reads the registry, the runstore,
and the sidecars into **KG nodes and edges**; and the `dist-kg` server makes the
result **queryable** through provenance-chain and neighbor tools. Because
`derived_from` is resolved against existing artifact/run nodes, a chain of runs
built this way is fully walkable — see {doc}`kg`.

## Where the shared databases live

Each store resolves its path **lazily per call** — explicit argument first, env
var second — and raises its `*UnavailableError` when neither is set (see
{doc}`library`). The orchestration convention (doc 11, `servers.yaml` example)
places the shared DBs in **`~/.cache/dist-stack/`**:

| Env var | Store | Conventional path |
|---|---|---|
| `DIST_STACK_MODEL_REGISTRY_DB` | `dist_stack.registry` | `~/.cache/dist-stack/registry.db` |
| `DIST_STACK_RUNSTORE_DB` | `dist_stack.runstore` | `~/.cache/dist-stack/runstore.db` |
| `DIST_STACK_KG_DB` | `dist_stack.kg` | `~/.cache/dist-stack/kg.db` |

**Resetting the shared state** is just file deletion: each store recreates its
schema idempotently on the next open (`migrate` is safe on every call), and the
KG is a derived index — `dist_stack.kg.ingest` rebuilds it from the registry,
the runstore, and the sidecars. Remove the `.db` files (plus any `.db-wal` /
`.db-shm` siblings) and point the env vars at fresh paths or re-ingest.

For the step-by-step journeys that exercise this spine, see
{doc}`usage-scenarios`; for the API surface of each `dist_stack` module see
{doc}`references`.
