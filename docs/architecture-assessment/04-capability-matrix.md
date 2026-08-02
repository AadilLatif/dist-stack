# 4. Tool Capability Matrix

Per-repo capability tables + an ecosystem-level view. Columns: Input Types, Output Types, Operations, Generated Artifacts, Expected Side Effects, MCP Coverage, Missing MCP Coverage, Potential Agent Responsibilities.

---

## 4.1 grid-data-models (GDM core)

| Dimension | Detail |
|---|---|
| **Input types** | GDM JSON (any 2.0.1–2.3.7, auto-upgraded); programmatic component construction; equipment catalogs (`DatasetSystem`/`CatalogSystem`) |
| **Output types** | GDM JSON (+ sidecar `_time_series/` Arrow folder); GeoDataFrame/CSV; GeoJSON; Plotly HTML maps; reduced/split/merged systems; validation reports; TrackedChange logs |
| **Operations** | create/edit/remove components; validate; diagnose/suggest/apply fixes; merge/split by substation/feeder; reduce (3-phase/primary); subsystem export; topology/connectivity analysis; relationships/orphans; time-series summary; plotting/GIS export; model hashing; scenario change playback; kron reduction; geometry→matrix conversion |
| **Generated artifacts** | `{name}_plot.html`; GeoJSON/CSV; reduced/merged/split system JSONs; fix change-logs; validation reports |
| **Expected side effects** | `to_json`/`apply_fixes`/`save_system` write files to disk; `reduce --force` deletes the TS sidecar folder; upgrade chain mutates raw JSON in memory |
| **MCP coverage (28 tools)** | validation (diagnose/suggest/apply_fixes), operations (merge/split×2/reduce), inspection (summary/query/topology/connectivity/details/orphans/relationships), utilities (subsystem/TS-summary/save), knowledge (docs/api/code-examples/components/fields), control (tool-calls toggle) |
| **Missing MCP coverage** | `plot`/`to_gdf`/`to_geojson`; `tracked_changes` apply/filter; `kron_reduce`; `convert_geometry_to_matrix_representation`; `get_split_phase_mapping`; TS value reads + `list_components_with_timeseries`; aggregated TS (`sys_functools`); `CatalogSystem`/`DatasetSystem`/`CostModel`; `hash_model`; explicit upgrade trigger |
| **Potential agent responsibilities** | inspect & summarize systems; validate + auto-fix; plan merges/splits/reductions; answer "what's in this network"; explain components & API usage |

---

## 4.2 gdm-flow (power flow)

| Dimension | Detail |
|---|---|
| **Input types** | GDM JSON (`DistributionSystem.from_json`); time-series profiles embedded in GDM models |
| **Output types** | solver result dataclasses; SQLite DBs; JSON result templates; Plotly HTML dashboards; Rich console tables |
| **Operations** | Y-bus construction; AC OPF; AC power flow (NR); DC OPF (HiGHS); LinDistFlow; QSTS; multi-period OPF; export to SQLite/JSON; overvoltage/overload reporting; TS dashboards |
| **Generated artifacts** | `runs`+solver SQLite tables (`ac_opf_*`, `dc_opf_*`, `lindistflow_*`, `voltage_violations`, `loading_violations`, `losses`, `ts_*`); HTML dashboards; JSON result templates |
| **Expected side effects** | solvers run on a *loaded copy* (in-place `convert_geometry_to_matrix_representation` and `aggregate_single_phase_transformers` may mutate the passed system); `export` writes SQLite; run IDs generated per run |
| **MCP coverage (15 tools)** | `opf_calculate_ybus`, `opf_run_ac`, `opf_run_dc`, `opf_run_lindistflow`, `opf_compare_solvers`, `opf_export_sqlite`; docs (list/search/get page) + API introspection (symbols/reference) |
| **Missing MCP coverage** | AC power flow (`solve_ac_power_flow`); QSTS (`run_qsts`); multi-period OPF; dashboards (`generate_dashboard`/`generate_ts_dashboard`); fine-grained AC-OPF spec controls; JSON template generation; reading result DBs |
| **Potential agent responsibilities** | run & compare solvers; export results to SQLite; answer "what voltages/loadings under scenario X"; interpret violations; explain OPF API |

---

## 4.3 erad (resilience analysis)

| Dimension | Detail |
|---|---|
| **Input types** | GDM DistributionSystem JSON; HazardSystem JSON; hazard catalogs; historic hazard DBs (~500 MB auto-download); fragility curves; asset locations; (plugin: landscape/fuels NetCDF/CSV for ForeFIRE) |
| **Output types** | `AssetState` survival probabilities per (asset, timestamp); SQLite `AssetStateTable`; Parquet/CSV/Arrow; GeoJSON; Plotly figures; `TrackedChange` scenario lists; ZIP archives; cached model JSON |
| **Operations** | GDM→AssetSystem mapping; hazard system load/create; simulate (DuckDB or legacy); Monte Carlo scenarios; asset query/statistics/topology; historic hazard listing/loading; fragility listing/params; export (SQLite/JSON/tracked-changes); cache management; engine SQL query/format conversion |
| **Generated artifacts** | results DBs; Parquet/CSV/Arrow exports; GeoJSON; Plotly HTML/maps; `tracked_changes.json` + ZIP; cached models + `models_metadata.json` |
| **Expected side effects** | heavy compute; ~500 MB DB auto-download on first hazard use; cache writes to platform cache dir; `generate_scenarios` writes ZIP |
| **MCP coverage (33 tools)** | simulation (load dist/hazard, create hazard system, create_forefire_hazard, run, generate scenarios); asset query/detail/statistics/topology; historic hurricanes/earthquakes/wildfires (list/load); fragility; export (sqlite/json/tracked_changes); cache; docs search; utilities (asset types/loaded systems/clear) |
| **Missing MCP coverage** | `AssetSystem.plot`/`HazardSystem.plot`/fragility plot; `to_gdf`/`to_geojson`; engine `query` (SQL) + parquet/csv/arrow exports; `get_failed_assets`; engine/hydrate selection; elevation raster; `EditStore`; **applying tracked_changes back to GDM**; hazard-system resources |
| **Potential agent responsibilities** | run resilience studies end-to-end; answer "which assets fail at hazard intensity X"; generate/query scenarios; compare fragility curves; explain resilience metrics |

---

## 4.4 erad_plugins (hazard engines)

| Dimension | Detail |
|---|---|
| **Input types** | pydantic configs (constructed in Python: storm tracks, rupture/site params, fire params, watershed params); asset location lists; (engine-specific: CLIMADA data, eGSIM, pyforefire, pywatershed PRMS files) |
| **Output types** | in-memory only: pandas DataFrames; CLIMADA Hazard/TCTracks; OpenQuake ruptures; fire perimeter polygons; xarray dicts → ERAD hazard models + `HazardSystem` |
| **Operations** | `register()` metadata; `Simulator.run()`; `to_<hazard>_models()`; `to_hazard_system()`; `run_<name>_scenario()` |
| **Generated artifacts** | **none** (artifacts/ dir never written) |
| **Expected side effects** | optional engine imports (`HAS_X` guard); external engine execution; heavy compute |
| **MCP coverage** | **none** |
| **Missing MCP coverage** | list plugins; get capabilities/schemas; invoke scenarios; serialize/retrieve results; engine status |
| **Potential agent responsibilities** | discover available hazard engines; pick a plugin for a hazard type; configure & run a hazard scenario; retrieve hazard models for ERAD simulation |

---

## 4.5 ditto (model conversion)

| Dimension | Detail |
|---|---|
| **Input types** | OpenDSS `.dss` (master + linked files); CIM RDF/XML (IEC 61968-13); CYME text tables (Network/Equipment/Load); GDM JSON |
| **Output types** | GDM JSON; OpenDSS `.dss` trees (Master.dss + per-type files, optional Substation/Feeder dirs); CIM XML (single or package + manifest.xml) |
| **Operations** | read (3 formats) → GDM; write GDM → OpenDSS/CIM; GDM JSON load/save; convert (reader→GDM→writer) with optional `--save-gdm`; list readers/writers |
| **Generated artifacts** | converted model trees; intermediate GDM JSON; `manifest.xml`; OpenDSS file set |
| **Expected side effects** | OpenDSS writer deletes existing `*.dss` files in output dir (`prepare_folder`); reader validation may raise (OpenDSS) or warn (CYME); CIM writer synthesizes new UUIDs (lossy) |
| **MCP coverage (14 tools)** | list_readers/list_writers; read_opendss/read_cim/load_gdm_json; list_loaded_systems; get_system_summary/get_components/get_component_detail; write_opendss/export_gdm_json/convert_model; resources `ditto://docs`; prompts `convert_guide`, `inspect_model` |
| **Missing MCP coverage** | CYME reader; dedicated CIM writer (kwargs-limited via `convert_model`); profile/time-series inspection; component mutation; pagination; system metadata/graph queries; state persistence |
| **Potential agent responsibilities** | convert models between formats; answer "can I convert X to Y and what's lost"; inspect an intermediate GDM model; explain conversion steps |

---

## 4.6 shift (synthetic model generation)

| Dimension | Detail |
|---|---|
| **Input types** | OSM parcels/roads (Overpass/PBF, local `.osm.pbf`); CSV/GeoDataFrame parcels; PG-DiGress abstract graph JSON; equipment catalog GDM JSON |
| **Output types** | GDM `DistributionSystem` JSON; NetworkX graphs; phase/voltage/equipment mappings; cluster groups; plotly figures |
| **Operations** | fetch parcels/roads; cluster parcels; build/query/edit graphs (nodes/edges/DFS); route existing (DiGress embedding); layout; configure mappers (phase/voltage/equipment); build system; export GDM JSON; geo utilities (distance/polygon/mesh/split/nearest) |
| **Generated artifacts** | GDM system JSON (export); plots; docs resources |
| **Expected side effects** | network fetches (Overpass/PBF); local PBF extraction (osmium); catalog load with upgrade chain; session state mutation (graphs/mappers/systems in `AppContext`) |
| **MCP coverage (36 tools)** | data acquisition (5); graph (13); mappers (6); system (4); utilities (5); docs (3); resources `shift://docs`/`shift://graphs`; prompts `build_feeder_from_location`/`inspect_network`/`explore_api` |
| **Missing MCP coverage** | plot generation (plots.py/PlotManager); most UI Studio endpoints duplicated outside MCP (catalog transformers, fix-violations, build-full, quick-build, multi-feeder) |
| **Potential agent responsibilities** | generate a synthetic feeder from a location; inspect network state; configure mapping strategies; explain build steps; export models for downstream simulation |

---

## 4.7 Ecosystem-level capability matrix

| Capability | gdm | gdm-flow | erad | erad_plugins | ditto | shift |
|---|---|---|---|---|---|---|
| **Load GDM JSON** | ✅ (`from_json`) | ✅ | ✅ | — | ✅ | ✅ (catalog) |
| **Produce GDM JSON** | ✅ (`to_json`) | — | (cache/JSON) | — | ✅ (save-gdm) | ✅ (builder) |
| **Create network** | programmatic | — | — | — | ✅ (readers) | ✅ (graph+build) |
| **Inspect/summarize** | ✅ MCP | (CLI info) | ✅ MCP | — | ✅ MCP | ✅ MCP |
| **Validate/diagnose** | ✅ MCP | — | (fragility only) | — | ⚠ (reader re-validate) | ⚠ (builder checks) |
| **Mutate model** | ✅ MCP (`apply_fixes`/`save_system`) | ⚠ in-place preprocessing | — | — | ✖ | — |
| **Execute simulation** | — | ✅ MCP (OPF/PF) | ✅ MCP (resilience) | ✅ (library only) | — | — |
| **Scenario analysis** | ✅ (`tracked_changes`, no MCP) | ⚠ QSTS/multiperiod (no MCP) | ✅ MCP (`generate_scenarios`) | — | — | — |
| **Convert formats** | — | — | — | — | ✅ MCP | — |
| **Export GIS/plots** | ✅ lib, ✖ MCP | ⚠ dashboard lib, ✖ MCP | ⚠ lib, ✖ MCP | ✖ | — | ⚠ lib, ✖ MCP |
| **Docs/API access for agents** | ✅ MCP (5 tools) | ✅ MCP (5 tools) | ✅ MCP (resource+tool) | ✖ | ✅ MCP (resources+prompts) | ✅ MCP (3 tools+resources) |
| **Workflow prompts** | ✖ | ✖ | ✖ | ✖ | ✅ 2 | ✅ 3 |
| **Resources** | ✖ | ✖ | ✅ 3 | ✖ | ✅ 2 | ✅ 3 |
| **State/session** | ⚠ global flag | ✖ | ✅ `ServerState` | — | ⚠ `_SYNC_STATE` | ✅ `AppContext` |
| **MCP server** | ✅ 28 | ✅ 15 | ✅ 33 | ✖ | ✅ 14 | ✅ 36 |
| **Provenance/metadata** | ⚠ `data_format_version` only | ✖ (runs untraceable) | ⚠ minimal | ✖ | ✖ (lossy CIM) | ⚠ version_summary |
| **Plugin/extension** | ⚠ upgrade chain + COMPONENT_MAP | ✖ | ✖ (hardcoded ForeFIRE) | ✅ entry points (orphaned) | ⚠ reader/writer modules | ⚠ strategy registries |

**Reading:** ✅ exposed via MCP · ⚠ exists only as library/CLI · ✖ absent.

---

## 4.8 Key matrix takeaways

1. **The "compute" tools are MCP-covered; the "produce/visualize/report" tools are not.** Every repo can *execute and export structured results* via MCP, but plotting/GIS/dashboard generation requires direct Python imports everywhere except shift's UI.
2. **Validation is the weakest shared capability:** only gdm has MCP validation tools; ditto's readers validate inconsistently; shift/erad validate implicitly during build/mapping.
3. **Scenario and provenance capabilities exist as libraries but not as MCP** (gdm `tracked_changes`, gdm-flow QSTS/multiperiod, gdm `hash_model`, erad scenario-application) — precisely the ops an orchestration agent needs for multi-step workflows.
4. **erad_plugins is a capability black hole:** the plugins are the ecosystem's only hazard-generation engines, yet they are invisible to MCP entirely.
5. **Agent responsibilities cluster into four reusable roles** matching the brief's future agent roster: *inspection/validation* (gdm), *execution* (gdm-flow, erad), *conversion* (ditto), *generation* (shift, erad_plugins).
