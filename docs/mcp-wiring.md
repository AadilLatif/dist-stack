# LLM Client Wiring (opencode)

```{note}
**Operational companion to {doc}`ecosystem`.** This page is the *how to
configure your client* guide — the exact launcher paths, env vars, and
restart steps for hooking the ecosystem into opencode. {doc}`ecosystem` is the
map of *what exists and how data flows*; {doc}`usage-scenarios` has the
intent-first journeys.
```

This monorepo (`dist-stack`) is the wiring point for the whole
distribution-suite ecosystem: it consolidates the former `gdm-stack`,
`dist-stack`, `dist-workflow-runner`, `dist-kg`, and `dist-dashboard`
repositories. This guide explains what was configured, how to talk to the
system, and how to inspect the results afterwards.

---

## 1. What was configured and why it matters

Your LLM client (opencode) reads `opencode.json` at the root of this repo.
Its `mcp` section registers **10 servers**. Seven are the ecosystem repos,
three are "supporting" servers that let you query and read the data those
repos produce.

### The 7 ecosystem servers

| Server | Repo / package | What it contributes |
|---|---|---|
| `gdm` | grid-data-models (`gdm.mcp.server`) | Build, validate, inspect, split/merge/reduce distribution systems; export GeoJSON/plots. 28 tools. |
| `gdm-flow` | gdm-flow (`gdm_flow.mcp.server`) | Power-flow studies: AC/DC OPF, LinDistFlow, AC power flow, QSTS, multi-period; exports results to SQLite. 15 tools. |
| `erad` | erad (`erad.mcp`) | Energy resilience: load asset/hazard models, run hazard simulations, generate failure scenarios, fragility curves, export failures. 33 tools. |
| `ditto` | ditto (`ditto.mcp.server`) | Model conversion: read/write OpenDSS, CIME, CYME; convert to/from GDM JSON. 14 tools. |
| `shift` | shift (`shift.mcp_server`) | Spatial system building: parcels, road networks, clustering, graph/mesh creation, routing, voltage/phase mapping. 36 tools. |
| `dist-workflow-runner` | dist-stack monorepo (`workflow_runner.__main__`, `packages/dist-workflow-runner`) | Multi-step workflow orchestration: `run_workflow`, `list_workflows`, `list_runs`, `get_run`. Spawns the 5 domain servers (gdm, gdm_flow, erad, ditto, shift) from `servers.yaml`. 8 tools. |
| `dist-kg` | dist-stack monorepo (`kg_server.__main__`, `packages/dist-kg`) | Knowledge graph over your data: `ingest`, `search_nodes`, `get_neighbors`, `query_provenance`, `get_provenance_chain`, `graph_stats`. 8 tools. |

### The 3 supporting servers

| Server | Status | What it does |
|---|---|---|
| `sqlite` | **enabled** | Read-only-ish SQL access to the shared runstore DB (where runs and artifacts are recorded). Tools: `query`, `list-tables`, `describe-table`, `execute`, `transaction`, etc. |
| `filesystem` | **enabled** | File access to the shared DB dir (`~/.cache/dist-stack`) and the ERAD artifact dirs (`~/.cache/erad/hazard_models`, `~/.cache/erad/distribution_models`), so the agent can read simulation JSONs and `.manifest.json` files directly. |
| `python` | **enabled** | Python code execution via `uvx python-mcp-server` (a Jupyter-kernel interpreter MCP server). Tools: `run_python_code`, `install_dependencies`, `notebook`, `read_file`/`write_file`, `list_files`, `delete_file`, `restart_kernel`. Installed `uv` (provides `uvx`) with `pipx install uv`. |

### Launchers used (why not `gdm-mcp-server` etc.)

None of the entry-point binaries (`gdm-mcp-server`, `erad-mcp`, …) are on
`PATH`. Each ecosystem server is therefore launched with
the interpreter of the venv where its package is installed, via
`python -m <module>` — the module path matching the `[project.scripts]` entry
point. The full command arrays are listed in §6.

Two servers need special handling:

- **erad**: its repo entry point (`erad.mcp:main`) previously crashed at
  startup — `main()` wrapped the async `serve()` in `asyncio.run()`, and
  `serve()` called the synchronous `create_server().run()` which starts its own
  anyio loop, producing a nested-loop
  `RuntimeError: Already running asyncio in this thread`. **Fixed in the erad
  repo**: `serve()`/`main()` are now plain synchronous functions (mcp 2.x
  `MCPServer.run()` manages its own loop) and `python -m erad.mcp` works via a
  new `src/erad/mcp/__main__.py`. The old `/tmp/opencode/erad-mcp-launcher.py`
  shim was deleted.
- **gdm**: the installed `gdm` copies in the venvs are stale and incompatible
  with the installed `mcp 2.0.0` SDK. The server is therefore run from the
  grid-data-models repo source via `PYTHONPATH` (set per-server in the
  environment block).

---

## 2. How to talk to the system

The servers are plain MCP tool providers, so you just ask the agent to do the
work. The agent routes each step to the right server's tools. Example prompt
that exercises the whole chain:

> "Build a synthetic feeder, run AC power flow, simulate a wind hazard, export
> the failures, then tell me what every artifact derived from."

What happens, step by step:

1. **gdm** — `query_components` / `split_by_substation` / `split_by_feeder`
   to obtain or build a feeder system model.
2. **gdm-flow** — `run_ac_pf` (AC power flow) on that system; results can be
   persisted with `export_sqlite`.
3. **erad** — `load_distribution_model`, then `load_hazard_model` (or
   `list_historic_hurricanes`/`list_historic_wildfires` +
   `load_historic_*`), then `run_simulation` for the wind hazard and
   `get_failed_assets` / `generate_scenarios` for the failures.
4. **erad** — `export_to_sqlite` / `export_csv` / `export_parquet` to write
   the failures out; the runstore records the run and artifacts.
5. **dist-kg** — `ingest_components` / `query_provenance` /
   `get_provenance_chain` to trace exactly which output artifacts derive from
   which inputs ("tell me what every artifact derived from").
6. **sqlite + filesystem** — see §3 for inspecting the persisted results.

For other ready-made task shapes — model conversion with ditto, the packaged
workflows, graph queries, or mesh routing with shift — follow the journeys in
{doc}`usage-scenarios`.

---

## 3. Querying results directly

All persisted state lives in `~/.cache/dist-stack/`. The three data servers
give you direct, non-destructive access to it.

**sqlite (runstore.db)** — the runstore records every run and artifact. Ask
the agent (or use the tools yourself):

- `list-tables` on runstore.db to see `runs` / `artifacts` (and any other
  tables the suite creates).
- `query` — e.g. `SELECT run_id, status, started_at_utc FROM runs ORDER BY started_at_utc DESC;`
- `describe-table` for the schema before writing complex queries.
- The same server can also open the registry (`registry.db`) and KG
  (`kg.db`) DBs — point `--db` at the file you want.

**filesystem** — for anything the DB doesn't cover:

- `~/.cache/dist-stack/` — the three SQLite files.
- `~/.cache/erad/hazard_models/` — one `simulation_<id>.json` + a
  `.manifest.json` per hazard run, plus `simulation_<id>_time_series/` dirs.
- `~/.cache/erad/distribution_models/` — cached distribution models.

**python** — Jupyter-kernel code execution for analysis over the exported
CSVs/Parquet. Ask the agent to `run_python_code` with a snippet (results come
back with stdout, figures and any new files), or point it at the artifact
files you read with the filesystem server.

---

## 4. The environment variables and the shared DB location

Every ecosystem server receives these in its `environment` block (absolute
paths are used in `opencode.json`; `servers.yaml` uses `~` which the
workflow-runner expands):

| Variable | Default location |
|---|---|
| `DIST_STACK_MODEL_REGISTRY_DB` | `~/.cache/dist-stack/registry.db` |
| `DIST_STACK_RUNSTORE_DB` | `~/.cache/dist-stack/runstore.db` |
| `DIST_STACK_KG_DB` | `~/.cache/dist-stack/kg.db` |

The dist-stack APIs **lazy-create** these on first use (`ensure_schema` on
open), so there is no init step. The `~/.cache/dist-stack/` directory itself
is created by the wiring setup.

**To reset** — stop opencode, then delete the files (they are recreated
lazily):

```bash
rm -f ~/.cache/dist-stack/registry.db ~/.cache/dist-stack/runstore.db ~/.cache/dist-stack/kg.db
```

To also clear ERAD's cached simulation artifacts (same as a fresh hazard
environment):

```bash
rm -rf ~/.cache/erad/hazard_models ~/.cache/erad/distribution_models
```

> Note: explicit `--config`/args win over the env vars, and the workflow
> runner's `runstore_db:` setting in `servers.yaml` points at the same
> runstore file, so everything stays consistent.

---

## 5. Restart your client

opencode loads `opencode.json` **at startup**. After editing it you must
**restart opencode** for the `mcp` section to take effect.

Verify from a terminal after restart:

```bash
cd /home/aadillatif/Documents/GitHub/dist-stack
opencode mcp list
```

All ecosystem and supporting servers should show `connected`.

---

## 6. Launcher commands (and manual full-stack launch)

### Command arrays registered in `opencode.json`

| Server | `command` |
|---|---|
| `gdm` | `["/tmp/gdmflow-venv/bin/python", "-m", "gdm.mcp.server"]` |
| `gdm-flow` | `["/tmp/gdmflow-venv/bin/python", "-m", "gdm_flow.mcp.server"]` |
| `erad` | `["/home/aadillatif/Documents/GitHub/erad/.venv/bin/python", "-m", "erad.mcp"]` |
| `ditto` | `["/home/aadillatif/Documents/GitHub/ditto/.venv/bin/ditto_mcp"]` |
| `shift` | `["/tmp/opencode/shift-venv/bin/python", "-m", "shift.mcp_server.__main__"]` |
| `dist-workflow-runner` | `["/home/aadillatif/Documents/GitHub/dist-stack/.venv/bin/python", "-m", "workflow_runner.__main__"]` |
| `dist-kg` | `["/home/aadillatif/Documents/GitHub/dist-stack/.venv/bin/python", "-m", "kg_server.__main__"]` |
| `sqlite` | `["npx", "-y", "mcp-server-sqlite", "--db", "/home/aadillatif/.cache/dist-stack/runstore.db"]` |
| `python` | `["uvx", "python-mcp-server"]` |
| `filesystem` | `["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home/aadillatif/.cache/dist-stack", "/home/aadillatif/.cache/erad/hazard_models", "/home/aadillatif/.cache/erad/distribution_models"]` |

Each ecosystem server also gets the three `DIST_STACK_*` env vars from §4;
`gdm` additionally gets `PYTHONPATH=/home/aadillatif/Documents/GitHub/grid-data-models/src`
and `dist-workflow-runner` gets `WORKFLOW_RUNNER_CONFIG=/home/aadillatif/Documents/GitHub/dist-stack/servers.yaml`.

### Two manual commands to launch the full stack (without the client's MCP manager)

The workflow-runner keeps the five domain servers alive once a client
connects; the KG server is standalone. Run these two in separate terminals if
you are not using opencode's MCP manager:

```bash
# 1. Workflow runner — spawns gdm, gdm_flow, erad, ditto, shift
WORKFLOW_RUNNER_CONFIG=/home/aadillatif/Documents/GitHub/dist-stack/servers.yaml \
  /home/aadillatif/Documents/GitHub/dist-stack/.venv/bin/python -m workflow_runner.__main__

# 2. Knowledge-graph server
/home/aadillatif/Documents/GitHub/dist-stack/.venv/bin/python -m kg_server.__main__
```

Supporting servers, if needed standalone:

```bash
npx -y mcp-server-sqlite --db ~/.cache/dist-stack/runstore.db
npx -y @modelcontextprotocol/server-filesystem ~/.cache/dist-stack ~/.cache/erad/hazard_models ~/.cache/erad/distribution_models
uvx python-mcp-server
```

### Files touched by this wiring

- `opencode.json` (this monorepo) — client MCP config for all 10 servers.
- `servers.yaml` (this monorepo root, gitignored) — real config (copied from
  `servers.yaml.example`), the 5 domain servers the runner spawns.
- `docs/mcp-wiring.md` — this guide.
