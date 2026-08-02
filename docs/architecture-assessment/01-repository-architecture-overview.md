# 1. Repository Architecture Overview

**Ecosystem:** Grid Data Models (GDM) distribution-system modeling toolchain
**Assessment date:** 2026-07-31
**Scope:** grid-data-models, gdm-flow, erad, erad_plugins, ditto, shift, dist-workflow-runner, dist-kg (all local checkouts under `/home/aadillatif/Documents/GitHub/`)
**Method:** read-only source inspection with file:line citations; no code modified.

> Note on repo identity: the task brief listed "Shift" with the path of `erad`. Both are distinct local repos:
> `shift` (`/shift`, package `nrel-shift`) is the synthetic-model generator; `erad` (`/erad`, package `NREL-erad`) is the resilience simulator.

---

## 1.1 The interoperability contract: DistributionSystem

All six **domain** repositories are bound together by the **`DistributionSystem`** class in `grid-data-models` (`src/gdm/distribution/distribution_system.py:68`), which subclasses `infrasys.System`. It is simultaneously:

- an **in-memory container** of pydantic component models (buses, branches, transformers, DERs, controllers, equipment) with inherited `add_component`/`get_components`/`get_component_by_uuid` etc.;
- a **serialization format** — a JSON document plus a sibling `_time_series/` folder of Arrow files and `time_series_metadata.db` (infrasys-managed);
- a **versioned artifact** — `data_format_version` is stamped at construction from the installed package version (`distribution_system.py:73-74`), and a 14-step upgrade-handler chain migrates raw JSON between schema versions `2.0.1` → `2.3.7` (`src/gdm/distribution/upgrade_handler/upgrade_handler.py:74-145`);
- the **input contract** consumed by every downstream domain tool (gdm-flow, erad, ditto, shift all depend on `grid-data-models`, either `>=2.3.7` or pinned `==2.3.7`).

---

## 1.2 grid-data-models (GDM core)

| Aspect | Finding |
|---|---|
| **Purpose** | Standard pydantic/infrasys data models for distribution-system assets and datasets; the ecosystem's interoperability layer (README.md:5-28) |
| **Package / version** | `grid-data-models` 2.3.7; Python ≥3.11; BSD-3-Clause; deps include `infrasys~=1.2`, `pydantic`, `semver`, `networkx`, `geopandas`, `plotly`, `typer` (pyproject.toml:23-33) |
| **Layout** | `src/gdm/`: `distribution/` (core), `structural/`, `dataset/`, `mcp/`, `cli/` |
| **Major modules** | `distribution/distribution_system.py` (DistributionSystem), `distribution/components/` (~20 asset classes), `distribution/components/base/` (abstract bases), `distribution/equipment/` (equipment specs), `distribution/controllers/` (DER/switch controllers), `distribution/common/` (limitsets, curves), `distribution/market/` (DER aggregators, tariffs), `distribution/upgrade_handler/` (schema migrations), `distribution/model_reduction/` (reducers), `distribution/sys_functools.py` (aggregated time series), `distribution/catalog_system.py`, `distribution/distribution_graph.py` (legacy), `dataset/` (DatasetSystem, CostModel), `structural/` (poles, buildings, segments — note typo `SructuralSystem` in structural_system.py:6), `tracked_changes.py` (scenario/time-based change playback), `hashing_utils.py` (`hash_model`) |
| **Public API** | `DistributionSystem` methods: `get_bus_connected_components` (:76), `get_source_bus` (:154), `get_undirected_graph` (:183), `get_directed_graph` (:376), `get_subsystem` (:279), `get_split_phase_mapping` (:496), `to_gdf` (:636), `to_geojson` (:675), `plot` (:708), `deepcopy` (:906), `convert_geometry_to_matrix_representation` (:923), `kron_reduce` (:977); inherited infrasys: `to_json`/`from_json`/`validate`/time-series APIs |
| **Serialization** | JSON (`{name, description, uuid, data_format_version, components[]}` with `__metadata__` field tags) + sibling `_time_series/` Arrow folder + `time_series_metadata.db`; `from_json(path, upgrade_handler=...)` applies the version chain |
| **CLI** | `gdm` → `gdm.cli.cli:app` — single command `reduce` (typer; cli/cli.py:4-5, cli/reducer.py:17-53) |
| **MCP server** | `gdm-mcp-server` → `gdm.mcp.server:main`; 28 tools; see Deliverable 2 |
| **Plugin/extension points** | No formal entry-point mechanism; extension via infrasys component registry, the upgrade-handler chain, and a hardcoded `COMPONENT_MAP` knowledge registry (mcp/knowledge/documentation.py:35-50) |

---

## 1.3 gdm-flow (power flow)

| Aspect | Finding |
|---|---|
| **Purpose** | "Power flow utilities for grid-data-models distribution systems" — Y-bus construction + four solvers + QSTS/multi-period + export (pyproject.toml:9) |
| **Package / version** | `gdm-flow` 0.3.1; Python ≥3.11; MIT; deps `numpy`, `grid-data-models>=2.3.7`, `typer`, `rich`, `scipy` (pyproject.toml:16-22); extras `plotting`/`mcp`/`opendss`/`dev` |
| **Layout** | `src/gdm_flow/`: `ybus.py`, `ac_opf.py`, `ac_pf.py`, `dc_opf.py`, `lindistflow.py`, `time_series.py`, `multiperiod.py`, `sqlite_export.py`, `dashboard.py`, `cli.py`, `export_cli.py`, `mcp/server.py` |
| **Major modules** | `ybus` (`calculate_ybus` :365, `YBusResult` :33); `ac_opf` (`optimize_ac_power_flow` :491, `PowerFlowOptimizationResult` :31); `ac_pf` (Newton–Raphson, `solve_ac_power_flow` :48); `dc_opf` (HiGHS LP, `solve_dc_opf` :162, `DCOPFResult` :40); `lindistflow` (`solve_lindistflow` :248); `time_series` (`run_qsts` :610, `QSTSSummary` :596); `multiperiod` (scipy milp, `solve_multiperiod_dc_opf` :123); `sqlite_export` (run_id-keyed schema); `dashboard` (Plotly dashboards) |
| **Public API** | `calculate_ybus`, `solve_*`/`optimize_*` and `*_from_components` conveniences, `run_qsts`, `export_*_result_to_sqlite`, `export_all_results_to_sqlite` (44-symbol `__all__`, __init__.py:3-102) |
| **Serialization** | Input: GDM JSON only (`DistributionSystem.from_json`). Output: SQLite (tables `runs`, `ac_opf_*`, `dc_opf_*`, `lindistflow_*`, `voltage_violations`, `loading_violations`, `losses`), JSON result templates, Plotly HTML dashboards |
| **CLI** | `gdm-flow` (typer, 12 commands: `info`, `run`, `compare`, `plot`, `export`, `report-overvoltage`, `report-overload`, `db-schema`, `ts-info`, `qsts`, `multiperiod`, `plot-ts`); `gdm-flow-export` (JSON→SQLite) |
| **MCP server** | `gdm-flow-mcp-server` → `gdm_flow.mcp.server:main`; 15 tools; see Deliverable 2 |
| **Plugin/extension points** | None; solvers are the extension surface |

---

## 1.4 erad (resilience analysis)

| Aspect | Finding |
|---|---|
| **Purpose** | Energy resilience analysis for distribution systems under hazards (earthquake, flood, hurricane/wind, wildfire) using fragility curves (README.md:13-17) |
| **Package / version** | `NREL-erad` 0.1.14; Python ≥3.10; deps `grid-data-models==2.3.7`, `gdmloader`, `geopandas`, `duckdb`, `sqlmodel`, `mcp>=1.0`, `typer` (pyproject.toml:42-60) |
| **Layout** | `src/erad/`: `engine/` (DuckDB vectorized), `models/` (Asset, hazard models, fragility), `systems/` (AssetSystem, HazardSystem), `runner.py` (HazardSimulator/HazardScenarioGenerator), `gdm_mapping.py`, `probability_builder.py`, `tables.py`, `cli.py`, `mcp/`, `default_fragility_curves/` |
| **Major modules** | `runner.py` (`HazardSimulator` :22, `HazardScenarioGenerator` :207); `engine/core.py` (`SimulationEngine` :21); `systems/asset_system.py` (`AssetSystem.from_gdm` :74); `systems/hazard_system.py` (`HazardSystem`); `models/hazard/*` (Wind/Earthquake/Flood/Fire models); `gdm_mapping.py` (GDM class → AssetType mapping); `mcp/` (stateful server) |
| **Public API** | `AssetSystem.from_gdm(dist_system)`; `HazardSimulator.from_gdm`/`run`; `HazardScenarioGenerator.samples` → list of GDM `TrackedChange`; `AssetSystem.export_results(db_path)`; `SimulationEngine.export_to_parquet/sqlite/csv/arrow/dataframe`; `AssetSystem.to_gdf/to_geojson/plot` |
| **Serialization** | SQLite `AssetStateTable` (tables.py:8); Parquet (ZSTD)/CSV/Arrow; GeoJSON; plotly figures; `tracked_changes.json` ZIP archives (cli.py:356-407); cached model JSONs + `models_metadata.json` in platform cache |
| **CLI** | `erad` (typer) with 5 sub-apps: `models` (list/add/remove/show/export), `hazards`, `cache`, `server` (incl. `server mcp` → `erad.mcp.main`), `engine` (run/convert/query/info); root `simulate`, `generate`, `version`, `info` |
| **MCP server** | `erad-mcp` → `erad.mcp:main`; 33 tools + resources; stateful; see Deliverable 2 |
| **Plugin/extension points** | **None declared.** No `[project.entry-points]`. The only plugin coupling is a hardcoded optional import of `erad_plugin_forefire` in `mcp/simulation.py:313` (graceful ImportError). Despite `erad_plugins` providing `erad.plugins` entry points, ERAD does not discover them |

---

## 1.5 erad_plugins (hazard-engine integrations)

| Aspect | Finding |
|---|---|
| **Purpose** | Monorepo of standalone packages wrapping external hazard engines and converting their outputs into ERAD hazard models (README.md:1-17) |
| **Packages / version** | Workspace root is config-only (pyproject.toml:1-49). Four plugins, each `0.1.2`: `erad-plugin-climada` (wind), `erad-plugin-egsim` (earthquake), `erad-plugin-forefire` (wildfire), `erad-plugin-pywatershed` (flood). All depend on `NREL-erad>=0.1.14`, `pydantic>=2`, `shapely>=2`; engines are optional extras (`[climada]`, `[egsim]`, `[forefire]`, `[pywatershed]`) |
| **Layout** | `plugins/erad-plugin-<name>/src/erad_plugin_<name>/` with uniform modules: `plugin.py`, `config.py`, `simulator.py`, `converter.py`, `scenario.py`; empty `artifacts/` dir; docs; Makefile (`install-all`, `test`, `clean`) |
| **Plugin system** | **De-facto interface, not an ABC.** Each plugin exposes `register()` → metadata dict `{name, version, description, hazard_types, requires}` (e.g. `climada/plugin.py:8-24`). Discovery contract is entry points: `[project.entry-points."erad.plugins"] <name> = "erad_plugin_<name>.plugin:register"` (each pyproject.toml:40-43). Lifecycle: install (`pip install -e`) → ERAD scans entry points → user imports package → `run_<name>_scenario(config) -> HazardSystem` orchestrates Simulator.run → converter.to_<hazard>_models → to_hazard_system. Engine availability guarded by `HAS_X` + `_require_engine()` |
| **Public API** | Per plugin: `register()`; pydantic `Config`/`Result` classes; `Simulator(config).run()`; `to_<hazard>_models()`/`to_hazard_system()`; optional `run_<name>_scenario()` |
| **Serialization** | **None.** Results are in-memory Python objects (DataFrames, Hazard objects, polygon lists, xarray dicts). No to_json/to_csv/to_parquet anywhere; `artifacts/` never written |
| **CLI** | None (library-only) |
| **MCP server** | **Absent** (verified: no `mcp` matches, no web/server framework in any plugin) |
| **DistributionSystem touchpoints** | Consumers of ERAD/GDM *hazard-side* types only (`HazardSystem`, `erad.models.hazard.*`, `gdm.quantities` for Distance/Angle); never produce/mutate grid assets |

---

## 1.6 ditto (model conversion)

| Aspect | Finding |
|---|---|
| **Purpose** | "Many to one to many" distribution-system model converter via a GDM `DistributionSystem` intermediate (README.md:12-16, ARCHITECTURE.md) |
| **Package / version** | `NREL-ditto` 0.1.5; Python ≥3.11; MIT; deps `opendssdirect.py`, `grid-data-models==2.3.7` (pinned), `rdflib`, `NREL-altdss-schema==0.0.3`, `typer`, `mcp[cli]` (pyproject.toml:23-30) |
| **Layout** | `src/ditto/`: `readers/` (opendss, cim_iec_61968_13, cyme), `writers/` (opendss, cim_iec_61968_13), `mcp/`, `cli.py`, core `AbstractReader`/`AbstractWriter` |
| **Major modules** | `readers/reader.py` (`AbstractReader` :25, `get_system()` → DistributionSystem); `readers/opendss` (element parsers + `graph_utils.update_split_phase_nodes`); `readers/cim_iec_61968_13` (12 SPARQL queries, CimMapper); `readers/cyme` (text-table parser, BFS voltage assignment, parallel-branch serialization); `writers/opendss` (reflective `OpenDSSMapper`, validated via altdss_schema); `writers/cim_iec_61968_13` (single/package + manifest.xml); `mcp/server.py` (FastMCP) |
| **Public API** | `AbstractReader.get_system()` / `to_json()`; `AbstractWriter(system).write(output_path, **kwargs)`; reader constructors take source paths + options |
| **Serialization** | Readers: OpenDSS `.dss`, CIM RDF/XML, CYME text tables. Writers: OpenDSS `.dss` tree (Master.dss + per-type files), CIM XML (single or package with `manifest.xml`). GDM JSON via inherited `to_json` |
| **CLI** | `ditto_cli` (typer): `list-readers`, `list-writers`, `convert --reader --writer --input --output [--save-gdm]` (dynamic import, cli.py:22-136) |
| **MCP server** | `ditto_mcp` → `ditto.mcp.server:main`; FastMCP; 14 tools + 2 resources + 2 prompts; see Deliverable 2 |
| **Plugin/extension points** | Reader/writer sub-package convention discovered via `pkgutil` (cli.py:34-63); mappers named `<Type>Mapper` and reflectively resolved by writers (opendss/write.py:221-232) |

---

## 1.7 shift (synthetic feeder generation)

| Aspect | Finding |
|---|---|
| **Purpose** | Build synthetic distribution feeder models from open-source geospatial data (OSM parcels + roads), exported as GDM `DistributionSystem` JSON (README.md:3-16) |
| **Package / version** | `nrel-shift` 0.8.0; Python ≥3.10; BSD-3-Clause; deps `osmnx`, `scikit-learn`, `plotly`, `grid-data-models==2.3.7` (pinned), `importlib-metadata`, `loguru` (pyproject.toml:27-36); extras `mcp` (mcp>=2.0), `ui` (fastapi/uvicorn), `flow` (gdm-flow>=0.3.2) |
| **Layout** | `src/shift/`: `parcel.py`, `openstreet_roads.py`, `data_model.py`, `system_builder.py`, `graph/`, `mapper/`, `utils/`, `plots.py`/`plot_manager.py`, `mcp_server/`, `ui_api/` (FastAPI) |
| **Major modules** | `DistributionGraph` (graph/distribution_graph.py:21); `OpenStreetGraphBuilder` (:27) + `PRSG` (prsgb.py:34, primary+secondary grid); `ExistingGraphRouter` (existing_graph_router.py:226, embeds PG-DiGress abstract graphs); routing/secondary strategy registries; mappers (`BalancedPhaseMapper`, `TransformerVoltageMapper`, `EdgeEquipmentMapper`/`DefaultLoadEquipmentMapper`); `DistributionSystemBuilder` (system_builder.py:47) |
| **Public API** | ~60 exported symbols: parcel/road fetching, clustering utils, graph builders + routing strategies, mappers, `DistributionSystemBuilder`, `PlotManager`, exceptions (__init__.py) |
| **Serialization** | Inputs: OSM (Overpass/PBF), CSV/GeoDataFrame parcels, DiGress JSON. Output: GDM `DistributionSystem.to_json` (system/export.py:53, README.md:161-170) |
| **CLI** | `shift-mcp-server` (stdio MCP) and `shift-ui-server` (uvicorn FastAPI UI Studio, 25 endpoints duplicating much of the MCP surface) |
| **MCP server** | 36 tools + 3 resources + 3 prompts; `MCPServer` (newer SDK) with per-module `register(mcp)` pattern; stateful `AppContext`; see Deliverable 2 |
| **Plugin/extension points** | Mapper/strategy ABCs + `STRATEGY_REGISTRIES`; GDM catalog systems as equipment source (`DatasetSystem.from_json` with `UpgradeHandler`, mcp_server/tools/mapper/equipment.py:150-153) |

---

## 1.8 dist-workflow-runner (workflow orchestration MCP client)

| Aspect | Finding |
|---|---|
| **Purpose** | Workflow orchestration MCP server — an MCP *client* to the domain servers: lists servers/tools, runs versioned workflow templates (sequential in v1), persists execution graphs via the runstore; **zero domain logic** (doc 11 §2) |
| **Package / version** | New sibling repo `dist-workflow-runner` (Phase 2, doc 11 §2.1); deps `mcp>=2.0`, `dist-stack-model-registry` |
| **Layout** | `src/workflow_runner/`: `server.py` (MCPServer + lifespan), `client.py` (ServerPool — lazy stdio spawn per domain server), `config.py`, `models.py`, `templates.py`, `executor.py`, `tools/` (servers, workflows, runs), `resources/`, `prompts/`; checked-in `workflows/` templates (doc 11 §2.2) |
| **MCP server** | `workflow_runner` (stdio); tools `list_servers`, `list_tools`, `create_workflow`, `run_workflow`, `get_run`, `list_runs`; resources `workflow-runner://workflows`, `workflow-runner://servers` (doc 11 §2.2, §2.6) |
| **CLI** | None — stdio MCP server via `python -m workflow_runner` (doc 11 §2.2) |
| **DistributionSystem touchpoints** | **None** — orchestrates domain servers over MCP stdio; consumes their JSON outputs, never imports domain models (doc 11 §2.1) |

---

## 1.9 Ecosystem summary

- **Seven repos in the ecosystem** — the six domain repos plus **`dist-workflow-runner`**, a new zero-domain-logic workflow-orchestration MCP client (deps `mcp>=2.0`, `dist-stack-model-registry`; doc 11 §2).
- **One shared object** — `DistributionSystem` — is produced (shift), converted (ditto), simulated (gdm-flow), and analyzed (erad) across the ecosystem; all six domain repos pin or floor on GDM 2.3.7.
- **Six MCP servers in five different styles** (see Deliverable 2), ranging from full (shift 36 tools) to none (erad_plugins).
- **Three repos (gdm, gdm-flow, erad)** share a `model_ref`/`DIST_STACK_MODEL_REGISTRY_DB` registry convention pointing at a sibling model-registry schema that does not exist in any of the seven repos — an implicit, unowned contract.
- **Three repos have no CLI story** (erad_plugins library-only; shift and dist-workflow-runner are server-only); **one repo is pure plugin infrastructure** (erad_plugins) whose discovery consumer is missing from its host (erad).
