# 2. MCP Capability Audit

**Scope:** all MCP servers in the ecosystem (gdm, gdm-flow, erad, ditto, shift; erad_plugins has none; dist-workflow-runner planned, doc 11).
**Method:** tool-by-tool inventory from source; comparisons of SDK style, state model, model-passing convention, docs/prompts/resources, and coverage vs each repo's Python API.

---

## 2.1 Server inventory at a glance

> Tool counts below reflect **current live MCP registrations** (verified by
> `list_tools` handshakes during MCP wiring). The per-server tool tables in
> §2.2+ remain snapshots from the pre-unification assessment era.

| Repo | Entry point | SDK style | Tools | Resources | Prompts | State | Model-passing |
|---|---|---|---|---|---|---|---|
| grid-data-models | `gdm-mcp-server` | `mcp.server.Server` (SDK 2.0), manual `list_tools`/`call_tool` | 28 | — | — | global `_TOOL_CALLS_ENABLED` flag only | `system_path` path **or** `model_ref` dict → SQLite registry |
| gdm-flow | `gdm-flow-mcp-server` | `mcp.server.Server`, manual handlers | 15 | — | — | none | `system_path` **or** `model_ref` → registry |
| erad | `erad-mcp` | `mcp.server.Server` (SDK 2.0), `add_request_handler` | 33 | 3 namespaces | — | `ServerState` (in-memory singleton) | `source`/`file_path` paths, cached names, `model_ref` → registry |
| ditto | `ditto_mcp` | `mcp.server.fastmcp.FastMCP` (high-level) | 14 | 2 | 2 | module-global `_SYNC_STATE` | file paths + in-memory system `name` keys |
| shift | `shift-mcp-server` | `mcp.server.MCPServer` (newer SDK) + per-module `register(mcp)` | 36 | 3 | 3 | `AppContext` per-session | file paths (PBF/catalog), in-memory `graph_id`/`system_name` keys |
| dist-workflow-runner (doc 11) | `workflow_runner` (MCP *client*) | `MCPServer` + lifespan; `stdio_client` + `ClientSession` | 8 | 2 | 1 | runstore-backed (`dist_stack.runstore`); per-server subprocesses | server/tool names + args over MCP stdio; runstore `run_id` |
| dist-kg (doc 12) | `kg-server` | `MCPServer` + per-module `register(mcp)` | 8 | 2 | 1 | stateless; env-lazy `dist_stack.kg` | node ids (`run:<id>`, `artifact:<path>`, `model:<id>`) |
| erad_plugins | — | none | 0 | 0 | 0 | — | — |

**Headline finding:** the ecosystem has eight MCP servers in four different SDK idioms (including **dist-workflow-runner**, the MCP-client orchestrator, doc 11, and **dist-kg**, the knowledge-graph server, doc 12). Tool semantics, state, and model-passing conventions are inconsistent enough that a single agent cannot assume common behavior across servers. This is the central MCP-readiness problem for the AI-native vision.

**Orchestration-plane servers (Phase 2–3, docs 11–12):** **dist-workflow-runner** (new sibling repo) exposes `list_servers`, `list_tools`, `create_workflow`, `get_workflow`, `list_workflows`, `run_workflow`, `get_run`, `list_runs`, plus resources `workflow-runner://workflows` and `workflow-runner://servers`. It is an MCP *client* to the domain servers (zero domain logic) and persists execution graphs via `dist_stack.runstore`. **dist-kg** (new sibling repo) exposes `get_node`, `get_neighbors`, `query_provenance`, `get_provenance_chain`, `search_nodes`, `graph_stats`, `ingest`, `ingest_components`, plus resources `kg://stats` and `kg://graph/{node_id}`. It queries the `dist_stack.kg` knowledge graph.

---

## 2.2 grid-data-models — 28 tools (`src/gdm/mcp/server.py`)

Dispatch map `_TOOL_HANDLERS` (server.py:689-714); unified `call_tool` (:717); errors → `CallToolResult(is_error=True)` (:748-768).

**Validation** (handlers :772-810)
1. `diagnose_system` — `{system_path|model_ref}` → `ValidationReport` (phase consistency, matrix dims, array lengths; re-implements checks rather than `System.validate()`)
2. `suggest_fixes` — report + `FixSuggestion[]` (strategies ALIGN_PHASES, RESIZE_MATRIX, ADJUST_ARRAY_LENGTH, SET_DEFAULT, REMOVE_INVALID)
3. `apply_fixes` — `{..., output_path, auto_approve}` → fixes a deep copy, writes JSON, returns change log (confidence gating)

**Operations** (handlers :813-881)
4. `merge_systems` — `{system_paths[]|model_refs[], output_path, name, strict}` → merged JSON + `MergeReport` (UUID/name conflict detection)
5. `split_by_substation` — `{..., output_dir, keep_timeseries, include_unassigned}` → N JSONs + `SplitReport`
6. `split_by_feeder` — same schema
7. `reduce_system` — `{..., reducer∈[three_phase, primary], name, keep_timeseries, overwrite}` → reduced JSON + summary

**Inspection** (handlers :884-950)
8. `get_system_summary` → `SystemSummary`
9. `query_components` — `{component_types[], substation, feeder, phases[], in_service, has_timeseries}` → `ComponentInfo[]`
10. `analyze_topology` — node/edge counts, cycles, islands, radiality, source
11. `validate_connectivity` — reachable/unreachable buses from source
12. `get_component_details` — full `model_dump()` by identifier
13. `find_orphaned_components` — no substation/feeder
14. `get_component_relationships` — parents (`list_parent_components`) + children

**Utilities** (handlers :953-978)
15. `export_subsystem_by_buses` — `{bus_names[], output_path, name, keep_timeseries}` → `get_subsystem`
16. `get_time_series_summary` — TS stats (not values)
17. `save_system` — re-serialize/copy with optional name override

**Documentation/Knowledge** (handlers :982-1015)
18. `search_gdm_documentation` — keyword scan of repo `docs/*.md`+`*.ipynb`
19. `get_api_reference` — from hardcoded `COMPONENT_MAP` (14 of ~20 classes)
20. `get_code_examples` — notebook code cells by topic
21. `list_available_components` — component catalog
22. `get_component_fields` — pydantic `model_fields` introspection

**Server control** (handlers :1041-1058)
23. `set_tool_calls_enabled` / 24. `get_tool_calls_enabled` — global safety toggle

**Assessment:** strong inspection/validation/operations coverage; **no resources, no prompts**. Missing: plotting/GIS, `tracked_changes` playback, kron/geometry conversion, split-phase mapping, TS value reads, aggregated TS, catalog/dataset/cost systems, `hash_model`, explicit upgrade-trigger. Knowledge tools are fragile (hardcoded paths + manually maintained `COMPONENT_MAP`). `--host/--port/--allow-auto-fix` CLI flags accepted but unused (stdio-only).

---

## 2.3 gdm-flow — 15 tools (`src/gdm_flow/mcp/server.py`)

`_TOOL_HANDLERS` (:565-577); schema block :302-562; handlers :603-818. No resources, prompts, or state.

**Computation** (all take `system_path` **or** `model_ref`)
1. `opf_calculate_ybus` — `{include_neutral, include_shunt, include_transformers, include_open_switches, convert_geometry_to_matrix, sparse, include_matrix, matrix_preview_limit}`
2. `opf_run_ac` — AC OPF from components, `vm_min/max_pu`, `max_nfev`, `include_details`
3. `opf_run_dc` — DC OPF, `slack_cost_linear`, `theta_min/max_rad`, `theta_penalty`, `maxiter`
4. `opf_run_lindistflow` — LinDistFlow, `include_open_switches`
5. `opf_compare_solvers` — AC+DC+LDF nested result
6. `opf_export_sqlite` — runs solvers → SQLite, returns `run_ids`

**Documentation/API introspection**
7. `list_opf_documentation` — repo `docs/` (parents[3]-relative, breaks in wheels)
8. `search_opf_documentation` — snippet search (radius 140 chars)
9. `get_opf_documentation_page` — page read with path-traversal guard (:790)
10. `list_opf_api_symbols` — `gdm_flow.__all__`
11. `get_opf_api_reference` — `{symbol, module, signature, doc}` via `inspect`

**Assessment:** 5 of 15 tools are computation; the rest are docs/API introspection. Missing: **AC power flow, QSTS/time series, multi-period OPF, dashboards/plotting**, fine-grained AC-OPF spec controls, JSON-template generation. No model provenance recorded in results. Only 5 smoke tests (solver/export handlers untested).

---

## 2.4 erad — 33 tools + resources (`src/erad/mcp/`)

Handlers registered via `app.add_request_handler("tools/list"|"tools/call"|"resources/list"|"resources/read")` (server.py:619-622); `_TOOL_HANDLERS` dispatch (:566-594).

**Simulation** (`simulation.py`)
1. `load_distribution_model` — path/cached name/`model_ref` → `AssetSystem`, stores in state
2. `load_hazard_model` — `HazardSystem.from_json`
3. `create_hazard_system` — empty
4. `create_forefire_hazard` — **direct optional import of `erad_plugin_forefire`** (:313), landscape+fuels+ignition → time-stepped `FireModel`
5. `run_simulation` — `HazardSimulator`, returns `simulation_id`
6. `generate_scenarios` — Monte Carlo `samples`, stores `tracked_changes`

**Asset query** (`assets.py`)
7. `query_assets` — type/bbox/survival filters
8. `get_asset_details`
9. `get_asset_statistics` — counts, survival stats
10. `get_network_topology` — node/edge lists

**Historic hazards** (`hazards.py`, SQLite over auto-downloaded ~500MB `erad_data.sqlite`)
11. `list_historic_hurricanes` / 12. `list_historic_earthquakes` / 13. `list_historic_wildfires`
14. `load_historic_hurricane` / 15. `load_historic_earthquake` / 16. `load_historic_wildfire`

**Fragility** (`fragility.py`) — 17. `list_fragility_curves` / 18. `get_fragility_curve_parameters`

**Export** (`export.py`) — 19. `export_to_sqlite` / 20. `export_to_json` / 21. `export_tracked_changes`

**Cache** (`cache.py`) — 22. `list_cached_models` / 23. `get_cache_info`

**Docs** (`documentation.py`) — 24. `search_documentation` (grep over docs/)

**Utilities** (`utilities.py`) — 25. `list_asset_types` / 26. `list_loaded_systems` / 27. `clear_system`

**Resources** (`resources.py`)
- `erad://docs/{path}` — 6 markdown docs
- `erad://cached-model/{name}` — cached distribution model JSON
- `erad://asset-system/{id}` — serialized loaded asset systems

**Assessment:** the most "agent-shaped" server — stateful, resources, end-to-end study flow (load→simulate→scenario→export). Missing: plotting/GIS, `SimulationEngine` SQL query + parquet/csv/arrow exports, `get_failed_assets`, engine/hydrate toggles, elevation raster, `EditStore`, applying `tracked_changes` back to GDM, hazard-system resources, and — critically — **plugin discovery** (only ForeFIRE, hardcoded). No prompts. Version metadata inconsistent (`mcp.__version__ == 1.0.0` vs package 0.1.14). README badge claims 26 tools (33 actual).

---

## 2.5 ditto — 14 tools + 2 resources + 2 prompts (`src/ditto/mcp/server.py`)

FastMCP `mcp = FastMCP("DiTTo", instructions=...)` (:35); stdio only.

**Tools** (`@mcp.tool`)
1. `list_readers` / 2. `list_writers` — sub-package discovery
3. `read_opendss_model` — `{master_file, name="default", crs}` → stored in `_SYNC_STATE`
4. `read_cim_model` — `{cim_file, name}`
5. `load_gdm_json` — `DistributionSystem.from_json` into state
6. `list_loaded_systems`
7. `get_system_summary` — component counts
8. `get_components` — `{component_type, name, limit=50}` (hardcoded attribute subset, no pagination)
9. `get_component_detail` — full `model_dump(mode="json")`
10. `write_opendss` — `{name, output_path, separate_substations, separate_feeders}`
11. `export_gdm_json` — `system.to_json`
12. `convert_model` — `{reader_type, writer_type, input_path, output_path, save_gdm}` (writer called with **no kwargs** → CIM `output_mode="single"` only)

**Resources** (`@mcp.resource`)
- `ditto://docs` — JSON index (9 slugs)
- `ditto://docs/{page}` — raw markdown

**Prompts** (`@mcp.prompt`)
- `convert_guide` — step-by-step conversion workflow
- `inspect_model` — guided exploration (summary → components → detail → export)

**State:** `AppState` dataclass (`mcp/state.py:17-48`) but exposed as **module-level `_SYNC_STATE` singleton** (:505); `state.py` docstring describes an unregistered lifespan pattern (doc/code drift).

**Assessment:** clean read-only conversion surface with prompts; missing CYME reader, dedicated CIM writer (only via `convert_model` with default kwargs), profile/TS inspection, component mutation, pagination, system metadata/graph queries, state persistence, provenance (CIM writer synthesizes `uuid5` mRIDs — lossy round-trips, original IDs lost).

---

## 2.6 shift — 36 tools + 3 resources + 3 prompts (`src/shift/mcp_server/`)

`MCPServer("nrel-shift", instructions=..., lifespan=app_lifespan)` (server.py:136-147); per-module `register(mcp)` pattern; stateful `AppContext`.

**Data acquisition (5)** — `set_local_pbf`, `fetch_parcels`, `fetch_parcels_in_polygon`, `fetch_road_network`, `cluster_parcels`

**Graph (13)** — `create_graph`, `delete_graph`, `list_graphs`, `add_node`, `remove_node`, `get_node`, `add_edge`, `remove_edge`, `get_edge`, `query_graph` (summary/nodes/edges/vsource/dfs_tree), `build_graph_from_groups` (full PRSG pipeline), `route_existing_graph` (PG-DiGress embedding), `layout_existing_graph`

**Mappers (6)** — `configure_phase_mapper`, `get_phase_mapping`, `configure_voltage_mapper`, `get_voltage_mapping`, `configure_equipment_mapper` (catalog path), `get_equipment_mapping`

**System (4)** — `build_system`, `get_system_summary`, `list_systems`, `export_system_json`

**Utilities (5)** — `distance_between_points`, `polygon_from_points`, `create_mesh_network`, `split_edges`, `find_nearest_points`

**Documentation (3)** — `search_docs`, `list_docs`, `read_doc`

**Resources** — `shift://docs`, `shift://docs/{doc_name}`, `shift://graphs`

**Prompts** — `build_feeder_from_location` (8-step pipeline), `inspect_network`, `explore_api`

**Assessment:** largest surface, strongest workflow-prompts story, and a per-session state model; but a *different* SDK idiom from every other server. Docs claim 33 tools (36 actual; `set_local_pbf`, `route_existing_graph`, `layout_existing_graph` undocumented). `_DOC_FILES` references non-existent `CHANGELOG.md`/`IMPROVEMENTS.md`. A FastAPI UI Studio (`ui_api/`, 25 endpoints) duplicates much of this surface outside MCP.

---

## 2.7 erad_plugins — MCP gap

**No MCP server, no serialization, no artifact persistence.** What an agent needs but cannot get today:

| Needed operation | Existing hook | Gap |
|---|---|---|
| List plugins / capabilities | `register()` metadata dict via `erad.plugins` entry points | no discovery consumer in ERAD core |
| Invoke a plugin | `run_<name>_scenario(config)` | Python-only configs, no JSON schema/CLI; engines optional extras |
| Retrieve plugin artifacts | nothing persisted | full gap (empty `artifacts/`) |

The `register()` metadata shape is a ready seed for `list_plugins`/`get_capabilities` tools; `hazard_types` vocabulary (wind/earthquake/wildfire/flood) maps cleanly to tool namespacing.

---

## 2.8 Cross-cutting audit findings

1. **SDK fragmentation (critical):** four idioms — low-level `mcp.server.Server` manual handlers (gdm, gdm-flow, erad), FastMCP decorators (ditto), `MCPServer`+register modules (shift). An orchestrator cannot rely on uniform introspection, context, or lifespan semantics. The ecosystem MCP convention now lives at `dist_stack/mcp/CONVENTIONS.md` (doc 10 §2.1).
2. **No prompts in gdm, gdm-flow, erad** — only ditto and shift ship workflow prompts; the AI-native vision needs these everywhere.
3. **Resources only in erad, ditto, shift** — gdm and gdm-flow expose tools only; no canonical resource namespaces for models/artifacts.
4. **Duplicate functionality with divergent schemas:** system summary/component query exist in gdm (`get_system_summary`/`query_components`), erad (`list_loaded_systems`/`query_assets`), ditto (`get_system_summary`/`get_components`), shift (`get_system_summary`/`list_systems`). Doc tooling is implemented 5 different ways.
5. **Implicit shared registry contract:** gdm, gdm-flow, erad all resolve `model_ref` against a SQLite `models(model_id, version, stored_path)` schema via `DIST_STACK_MODEL_REGISTRY_DB` — an unowned cross-repo contract (ditto/shift don't participate).
6. **In-memory state is ephemeral:** erad's `ServerState` and shift's `AppContext` die with the process; ditto's `_SYNC_STATE` likewise. No server persists session/artifact state.
7. **Tool-count documentation drift:** gdm 20→24→28, erad 26→27→33, shift 33→36 (unchanged; ditto badge matched at 12 before it grew to 14). Agents reading READMEs will under-utilize servers.
8. **Direct-Python-import operations with no MCP exposure** (the "should be MCP" list for Phase 5):
   - gdm: `plot`/`to_gdf`/`to_geojson`, `tracked_changes` (apply_updates_to_system), `kron_reduce`, `convert_geometry_to_matrix_representation`, `get_split_phase_mapping`, TS value reads, aggregated TS, `CatalogSystem`/`DatasetSystem`/`CostModel`, `hash_model`
   - gdm-flow: `solve_ac_power_flow`, `run_qsts`, multi-period OPF, dashboards
   - erad: engine SQL/parquet/csv/arrow exports, plotting/GIS, `get_failed_assets`, elevation raster, `EditStore`, applying scenarios to GDM
   - ditto: CYME reader, CIM package writing, profile inspection
   - erad_plugins: everything (see 2.7)
   - shift: UI Studio endpoints (replicated API outside MCP)
