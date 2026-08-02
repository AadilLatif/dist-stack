# Usage Scenarios

Intent-first journeys through the distribution-suite ecosystem: decide what to
reach for, then three walk-throughs — run a study end-to-end, see and trace
what happened, and run a reusable workflow. Each journey states **when** to
reach for it, the **steps**, and how to **verify** the result.

## Deciding what to use

| Task | Tool |
|---|---|
| Register / write / query models, runs, or graph data in code | the library — {doc}`library` |
| Run a multi-step study across the domain servers | the runner — {doc}`runner` |
| Trace provenance of a result (agent) | `dist-kg` — {doc}`kg-server` |
| Browse what happened (human) | the dashboard — {doc}`dashboard` |
| Read a store directly | `sqlite3` CLI or the supporting reference servers (see Journey 2) |

## Journey 1 — Run a study end-to-end

**When.** You want to run a classic distribution study — synthesize a feeder,
run power flow, simulate hazards, and trace the whole chain through the
knowledge graph.

**Steps.**

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
   `attach_artifact` (sidecar present), then `dist-kg ingest` (or
   `dist_stack.kg.ingest`) once at the end.
6. **Trace.** `get_provenance_chain(artifact:<simulation result>, direction="up")`
   walks `generated_by` / `derived_from` / `references` back to the hazard run,
   the power-flow run, the registered model, and the synthesized feeder —
   including `model:<model_id>` and every `run:<run_id>` in between.

**Verify.** `get_provenance_chain` resolves the simulation artifact all the way
back to the feeder; the runstore holds one `workflow_execution` (or tool) row
per step; the dashboard's **Run History** and **Provenance** views show the
same chain.

## Journey 2 — See and trace what happened

**When.** Something has already run and you want to inspect it or answer
"what did this artifact derive from?" — as an agent, from a dashboard, or
straight from SQLite.

**Steps.**

1. **Trace a conversion round-trip (ditto).** Read a CIM file with
   `read_cim_model` (into in-memory state `name="default"`), export GDM JSON
   with `export_gdm_json`, then convert back with `convert_model`
   (`reader_type`/`writer_type`) — or `write_opendss` for the OpenDSS writer.
   Caveat from the audit: the CIM writer **synthesizes `uuid5` mRIDs**, so a
   CIM→GDM→CIM round-trip is lossy — original identifiers are not preserved. A
   dedicated CIM writer (and a CYME reader) are on the roadmap; treat identity
   as the thing to verify after conversion, not something the writer
   guarantees.
2. **Index.** `write_manifest` on each output (GDM JSON, CIM output) with
   `derived_from=[<input path>]`, plus runstore rows, then `dist-kg ingest`.
3. **Trace.** `get_provenance_chain(artifact:<converted.cim>, direction="up")`
   shows the input CIM and the conversion run;
   `get_neighbors(..., relation="derived_from")` shows both directions of the
   round-trip.

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

**Verify.** The traced artifact's `get_provenance_chain` (up) reaches the input
CIM and the conversion run; `get_neighbors(..., relation="derived_from")`
shows both directions; or the dashboard's **Provenance** view renders the same
tree.

## Journey 3 — Run a reusable workflow

**When.** You want to run a workflow once, then re-run it from a previous
execution graph.

**Steps.**

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

**Verify.** The second `run_workflow` returns a valid `prior_graph` for the
same `workflow_id`; `get_run(<new run_id>)` shows the new execution-graph
artifact; the prior run's records are unchanged (reuse is read-only).

See {doc}`runner` for the runner's full tool surface, {doc}`kg-server` for the
graph queries, and {doc}`ecosystem` for how the shared contracts tie the
servers together.
