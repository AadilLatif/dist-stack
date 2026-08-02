# 8. Future AI Architecture Notes

Sketch of how the knowledge graph, agent memory, MCP-native execution, and workflow planning should layer onto this ecosystem. Deliberately a sketch, not a design spec. Everything below is grounded in artifacts/objects found in the six repos.

---

## 8.1 Identity layer (the spine)

- **`model_id`** in the registry (`models(model_id, version, stored_path)` — the `DIST_STACK_MODEL_REGISTRY_DB` contract already referenced by gdm/gdm-flow/erad) becomes the canonical node key for every DistributionSystem. Agents address everything by `model_id`/`run_id`, never by filesystem path.
- **Manifest sidecar** (`model_id`, `model_hash`, tool+version, config snapshot, `schema_version`, `derived_from`) is the universal edge carrier. Modeled on the existing pattern of "JSON + sibling sidecar folder" (DistributionSystem JSON + `_time_series/`).
- **mRID sidecar** preserves identity across format boundaries (ditto CIM currently synthesizes `uuid5` mRIDs, losing originals).
- `gdm.hashing_utils.hash_model` already provides the hashing primitive — wire it into manifests.

## 8.2 Node inventory (all exist today)

| Node type | Source in the ecosystem |
|---|---|
| DistributionSystem | gdm load/save; shift `DistributionSystemBuilder`; ditto `convert_model` → GDM |
| Feeders, buses, lines, transformers, DERs | gdm `get_components` / `get_component_relationships` (parents/children) |
| Studies / runs | gdm-flow runs (ac_opf, ac_pf, dc_opf, lindistflow, qsts, multiperiod) — currently untraceable, becomes traceable via manifest |
| Hazard systems & scenarios | erad `create_hazard_system`, `generate_scenarios` → `TrackedChange` lists |
| Artifacts | JSON, `_time_series/` Arrow, SQLite exports, GeoJSON, Plotly HTML, tracked_changes ZIP, converted format trees (ditto) |
| Plugins | erad_plugins `register()` metadata (wind/earthquake/wildfire/flood) — once ERAD actually discovers them |

## 8.3 Edge inventory (maps to the brief's relationship vocabulary)

- `generated_by` — run/artifact → tool + version + config (from manifest)
- `derived_from` — conversions (ditto), scenario outputs, exports (from manifest)
- `modifies` — `TrackedChange` playback via gdm `apply_updates_to_system`
- `validates` — gdm validation tools
- `visualizes` — future plot tools → Plotly HTML
- `consumes` / `produces` — OPF consumes model + time series, produces SQLite run
- `references` — `model_ref` registry lookups

## 8.4 Layering

```
┌─────────────────────────────────────────────────────────────┐
│ Orchestration plane (new, thin)                             │
│  • dist-workflow-runner — the MCP-client workflow runtime   │
│    runtime: discovers tools, builds execution graphs from   │
│    versioned prompts, executes via MCP client calls,        │
│    persists each graph as a JSON artifact                   │
│  • dist-kg — the knowledge-graph server: provenance-chain   │
│    queries over dist_stack.kg nodes/edges                   │
│  • Registry service — model identity (contract exists)      │
├─────────────────────────────────────────────────────────────┤
│ Domain plane (the six repos, unchanged role)                │
│  gdm (models) · gdm-flow (power flow) · erad (resilience)   │
│  erad_plugins (hazard engines) · ditto (conversion)         │
│  shift (synthetic generation) — each exposed via MCP        │
└─────────────────────────────────────────────────────────────┘
```

The six repos remain **dumb domain servers**. The **dist-kg** server, **dist-workflow-runner** MCP server, and registry are the orchestration plane. dist-workflow-runner holds zero domain logic — it is an MCP client that calls the domain servers, exactly the "communicate through MCP rather than importing libraries" principle from the brief. dist-kg reads only stored artifacts (runstore, manifests, registry) — never repo internals.

## 8.5 Dynamic workflow planning

- Planning starts from **versioned MCP prompts** (extend ditto's `convert_guide` / shift's `build_feeder_from_location` patterns to all servers).
- Each executed workflow graph is persisted as a JSON artifact; the KG ingests it, making **every past plan a reusable node** — planning reuses/rewrites prior graphs instead of rebuilding (the brief's "self-organizing" property).
- Because tool inventories become live/accurate (Phase 0) and tools carry consistent names (Phase 1), the planner can construct graphs like:
  `shift_generate_feeder → gdm_validate → flow_run_ac_opf → erad_simulate → gdm_apply_scenario → flow_run_qsts → export + plot`

## 8.6 Agent memory mapping

- **Episodic memory** = run_id-keyed manifests (what was done, with what inputs/config)
- **Procedural memory** = versioned prompts + persisted execution graphs (how to do things)
- **Semantic memory** = the knowledge graph itself (what exists and how things relate)

All three are **stored artifacts, not agent-internal state** — memory survives sessions and is shareable across agents.

## 8.7 Ordering constraint

None of this works before Phase 1 lands. The **manifest + registry are the load-bearing contracts**; everything in this section is a consumer of them. Until then, provenance chains cannot be built and cross-server model handoff is path-based guesswork.

## 8.8 Assumptions

1. The missing "dist-stack" service is intended as the registry/KG host — if so, Phase 1's shared library should be its client SDK.
2. The seventh component is **dist-workflow-runner** (the MCP-client workflow runtime, doc 11); the eighth is **dist-kg** (the knowledge-graph server over `dist_stack.kg`, doc 12) — both kept separate rather than grafted onto gdm, preserving the incremental constraint.
3. ditto/shift adopting the registry contract is assumed acceptable despite their current path-based isolation.
4. MCP SDK unification toward one idiom (recommended: `MCPServer` + per-module `register()`, as in shift) is assumed preferable to maintaining four integration styles.
