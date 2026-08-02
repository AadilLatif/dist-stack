# 6. Recommended Roadmap

How the ecosystem should evolve toward AI-first workflows, multi-agent orchestration, dynamic workflow planning, MCP-native execution, reusable workflow components, and future knowledge-graph integration.

**Guiding principle:** keep the six repos as domain servers; move all *shared contracts* (registry, manifest, naming) into one small owned package or the missing dist-stack service. No redesigns — incremental contract + surface work.

---

## Phase 0 — "Do this week" (trust & truth, 0–2 weeks)

Make the current surface *honest* before adding to it:

- **All repos:** generate tool inventories from live MCP registration; fix README counts (gdm 20→24, erad 26→27, shift 33→36). *(now 28/33/36 — further tools added in Phases 1–3)*
- **gdm:** introspect `COMPONENT_MAP` from the pydantic component registry at startup; delete dead CLI params (`--host`/`--port`/`--allow-auto-fix`); point MCP diagnostics at `System.validate()` + `hashing_utils`.
- **gdm-flow:** unify run_id prefixes and `violation_kind` strings behind single enums; remove phantom `[sparse]`/`[optimization]` extras from README; package docs via `importlib.resources`.
- **erad:** fix `DEFAULT_FRAGILTY_CURVES` typo; align `mcp.__version__` with package version.
- **ditto:** fix stale ARCHITECTURE.md/API.md references; implement the documented lifespan or drop the `_SYNC_STATE` claim.
- **shift:** repair `_DOC_FILES` (CHANGELOG.md/IMPROVEMENTS.md); sync docs 33→36.
- **erad/ditto/shift:** relax `==2.3.7` → `~=2.3.7`; bump erad_plugins `register()` to 0.1.2.

## Phase 1 — MCP surface & identity (weeks 2–6)

The orchestration-critical layer:

- **SDK unification:** pick one style (recommended: shift's `MCPServer` + per-module `register()` — it already scales to 36 tools; migrate gdm/gdm-flow/erad off hand-rolled `mcp.server.Server` handlers). Even without migration, adopt a shared tool-naming/param convention doc.
- **Registry contract ships:** implement the `models(model_id, version, stored_path)` + `DIST_STACK_MODEL_REGISTRY_DB` contract as a shared library; migrate ditto/shift onto it. Single highest-leverage interoperability fix.
- **Provenance manifest v1:** sidecar JSON per run/artifact (`model_ref`, `model_hash`, tool+version, config snapshot, `schema_version`, `derived_from`); stamp into gdm-flow runs, erad sim metadata, ditto conversions, shift builds; stamp `schema_version` in all exporters.
- **First tranche of workflow-completing MCP tools:**
  - gdm: `get_time_series_values`, `apply_tracked_changes`, `plot_system`/`to_geojson`
  - gdm-flow: `run_ac_pf`, `run_qsts`, `run_multiperiod`, `plot_ts`
  - erad: `export_parquet`, `export_csv`, `apply_scenario_to_system`, `get_failed_assets`
  - ditto: `read_cyme`, CIM writer kwargs (package mode)
- **Resources/prompts for gdm & gdm-flow:** registry/system-catalog resources; canonical-workflow prompts.

## Phase 2 — Execution, plugins, workflows (months 2–4)

- **Plugin system real:** `importlib.metadata` discovery of `erad.plugins` in ERAD core; shared plugin SDK (eliminates quadruplicated `_require_engine` guards); plugin results serialized (HazardSystem → GDM JSON + manifest); plugin metadata exposed via MCP.
- **Execution state / `dist_stack.runstore` (doc 11 §1):** the shared run-state + artifact-store contract is now `dist_stack.runstore` — a new SQLite run-state + artifact store in dist-stack, keyed by `run_id`, adopted **best-effort/write-only** by all five domain repos; local `ServerState`/`_SYNC_STATE`/`AppContext` keep their session-working-state role.
- **Reusable workflow components:** versioned MCP prompts per repo; plus a thin **workflow-runner MCP server** — the new sibling repo **`dist-workflow-runner`** (planning/routing only; it calls the other servers as an MCP *client*, holds zero domain logic) that persists execution graphs via `dist_stack.runstore` as JSON artifacts (doc 11 §2).
- **Identity preservation:** mRID sidecar for CIM round-trips; artifact `derived_from` linking everywhere.

## Phase 3 — Knowledge graph & agent memory (months 4+)

- **KG ingestion service** over manifests (never over repo internals): nodes from gdm `get_components`/`get_component_relationships` (feeders/buses/lines/transformers/DERs), gdm-flow runs, erad hazard systems/scenarios, ditto conversions, shift synthetic feeders; edges from manifest `derived_from`/`generated_by`/`visualizes` + TrackedChange `modifies`.
- **KG MCP server:** provenance-chain queries ("what produced this artifact?", nearest neighbors).
- **Execution graphs as KG nodes** → dynamic workflow planning reuses/rewrites prior graphs instead of rebuilding.

---

## Mapping to the future agent roster

| Future agent (brief) | Existing base | Roadmap dependency |
|---|---|---|
| Tool Discovery Agent | MCP `list_tools`/resources across all servers | Phase 0 inventory accuracy + Phase 1 naming convention |
| Workflow Planning Agent | ditto `convert_guide` / shift `build_feeder_from_location` prompts | Phase 1 versioned prompts + Phase 2 workflow-runner |
| Data Validation Agent | gdm `diagnose_system`/`suggest_fixes`/`apply_fixes` | Phase 0 delegate to core validation |
| DistributionSystem Reasoning Agent | gdm inspection tools + registry | Phase 1 registry contract + manifest |
| Simulation Agent | gdm-flow solver tools + erad simulation tools | Phase 1 missing-solver tools |
| Optimization Agent | gdm-flow AC/DC OPF + multiperiod | Phase 1 `run_multiperiod` exposure |
| Visualization Agent | gdm `plot`/gdm-flow dashboards/erad plots (all library-only) | Phase 1 plot/export tools |
| Knowledge Graph Agent | manifests + `get_component_relationships` | Phase 2 manifest linking + Phase 3 KG service |
| Report Generation Agent | SQLite exports, dashboards, tracked_changes archives | Phase 1 artifact manifest + Phase 3 provenance queries |
