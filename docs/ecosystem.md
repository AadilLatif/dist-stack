# Working Across the Ecosystem

dist-stack is now a **monorepo** that consolidates five former repositories —
`gdm-stack` (docs), `dist-stack`, `dist-workflow-runner`, `dist-kg`, and
`dist-dashboard` — into five packages under one `uv` workspace. The five domain
repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`, `shift`) stay external.
This chapter shows how the shared contracts — the registry, the provenance
sidecars, the runstore, and the knowledge graph — tie the domain servers
together in practice, and how to trace a result back to the model, run, and
parent artifacts that produced it.

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

## Worked scenario 1: synthetic feeder to hazard simulation

The classic study flow: synthesize a feeder with shift, run power flow with
gdm-flow, simulate hazards with erad, and trace the whole chain through the KG.

1. **Build the feeder (shift).** `build_system` synthesizes a system from
   parcel/graph state; `export_system_json` writes the `system.json` artifact.
2. **Register it (shared registry contract).** `registry.register(model_id=...,
   stored_path=<exported json>)` so gdm-flow and erad can resolve it via
   `model_ref`. (Write a manifest sidecar with `write_manifest` if the tool did
   not.)
3. **Power flow (gdm-flow).** `opf_run_ac` (or `opf_run_dc` / `opf_run_lindistflow`)
   solves from `system_path | model_ref`; `opf_export_sqlite` runs the solvers
   and writes a SQLite results artifact, returning `run_ids`. (QSTS and
   multi-period runs exist in the `gdm_flow` Python API but are not yet MCP
   tools — the audit lists them under "should be MCP".)
4. **Hazard simulation (erad).** `load_distribution_model` (by `model_ref`),
   `run_simulation`, then `generate_scenarios` (Monte Carlo); export with
   `export_to_sqlite` / `export_to_json`.
5. **Index (runstore + KG).** Each step best-effort `create_run` /
   `attach_artifact` (sidecar present), then
   `dist-kg ingest` (or `dist_stack.kg.ingest`) once at the end.
6. **Trace.** `get_provenance_chain(artifact:<simulation result>, direction="up")`
   walks `generated_by` / `derived_from` / `references` back to the hazard run,
   the power-flow run, the registered model, and the synthesized feeder —
   including `model:<model_id>` and every `run:<run_id>` in between.

## Worked scenario 2: model conversion round-trip

CIM ↔ GDM conversion with identity — the ditto path.

1. **Read (ditto).** `read_cim_model` loads a CIM file into the in-memory system
   state (`name="default"`).
2. **Export (ditto).** `export_gdm_json` writes the GDM JSON artifact.
3. **Convert back (ditto).** `convert_model` (`reader_type`/`writer_type`) — or
   `write_opendss` for the OpenDSS writer. Caveat from the audit: the CIM
   writer **synthesizes `uuid5` mRIDs**, so a CIM→GDM→CIM round-trip is lossy —
   original identifiers are not preserved. A dedicated CIM writer (and a CYME
   reader) are on the roadmap; treat identity as the thing to verify after
   conversion, not something the writer guarantees.
4. **Index.** `write_manifest` on each output (GDM JSON, CIM output) with
   `derived_from=[<input path>]`, plus runstore rows, then `dist-kg ingest`.
5. **Trace.** `get_provenance_chain(artifact:<converted.cim>, direction="up")`
   shows the input CIM and the conversion run; `get_neighbors(..., relation="derived_from")`
   shows both directions of the round-trip.

## Worked scenario 3: reusable workflow execution

Run a workflow once, then re-run it from a previous execution graph.

1. **Define (dist-workflow-runner).** `create_workflow` records the step
   template (server + tool + args per step).
2. **Execute.** `run_workflow` spawns the domain servers over MCP stdio, writes
   an execution-graph artifact (`artifact_type="workflow_execution"`) plus a
   manifest sidecar, and persists the run in the runstore.
3. **Reuse.** `run_workflow(reuse_run_id=...)` loads the prior execution graph,
   validates `prior_graph.workflow_id == workflow_id` (and that the prior run is
   a `workflow_execution` with `status == "succeeded"`), executes normally, and
   returns the prior graph in the response as `prior_graph` — with step results
   omitted as a size guard. Reuse is read-only: no automatic rewriting.
4. **Find reusable runs (dist-kg).** `search_nodes(node_type="workflow_execution")`
   lists the stored execution-graph nodes; `get_node("run:<run_id>")` and
   `get_provenance_chain("run:<run_id>", direction="down")` (via
   `has_artifact` / `derived_from`) reveal what each run produced.

## Interacting with results

Beyond the domain and orchestration servers, three ways to inspect what the
ecosystem produced:

- **Supporting reference servers.** The runner/dashboard setups typically pair
  the domain servers with generic **SQLite**, **filesystem**, and **Python**
  reference MCP servers to read `runstore.db`, browse manifest sidecars, and
  run ad-hoc queries. dist-stack adds no dependency on these — they are
  optional companions.
- **dist-dashboard** (in this monorepo, `apps/dist-dashboard`). A **read-only
  browser** over the runstore, the KG, and the registry: list runs and
  artifacts, walk provenance trees, and inspect registered model versions —
  without ever writing to the shared DBs.
- **Direct queries.** The stores are plain SQLite; you can always open them
  with the `sqlite3` CLI or a Python connection:

```python
import sqlite3

conn = sqlite3.connect("~/.cache/dist-stack/runstore.db")
conn.execute(
    "SELECT run_id, tool, run_type, status FROM runs "
    "WHERE deleted_at_utc IS NULL ORDER BY created_at_utc DESC LIMIT 10"
).fetchall()
```

## Where the shared databases live

Each store resolves its path **lazily per call** — explicit argument first, env
var second — and raises its `*UnavailableError` when neither is set (see
{doc}`intro`). The orchestration convention (doc 11, `servers.yaml` example)
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

## Design specs

The contracts this chapter relies on are pinned in the architecture-assessment
docs of this monorepo (`docs/architecture-assessment/`):

| Doc | Topic |
|---|---|
| `09-model-registry-spec.md` | model registry + manifest sidecar |
| `10-mcp-sdk-unification-plan.md` | ecosystem MCP server conventions |
| `11-runstore-and-workflow-runner-spec.md` | runstore + runner orchestration (`servers.yaml`, `reuse_run_id`) |
| `12-knowledge-graph-spec.md` | KG store, ingester, and `dist-kg` server |

See {doc}`references` for the API surface of each `dist_stack` module.
