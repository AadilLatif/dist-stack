# 3. DistributionSystem Lifecycle Analysis

**Question:** how does the `DistributionSystem` move through the ecosystem, and where does the ecosystem fail to capture provenance, versioning, and interoperability metadata along the way?

The lifecycle is traced across the six repositories. Every stage names the concrete modules/classes that implement it, then lists what metadata is (and is not) carried.

---

## 3.1 Lifecycle map

```
CREATION ─► VALIDATION ─► SERIALIZATION ─► DESERIALIZATION ─► MODIFICATION ─► ENRICHMENT ─► EXECUTION ─► REPORTING
   shift          gdm             gdm                gdm            gdm            gdm            gdm-flow      gdm-flow
   (builder)   (pydantic)     (to_json)         (from_json +     (infrasys      (reducers,       (solvers,     (SQLite/HTML/
                 ditto          sidecar TS      upgrade chain)     edits)        subsystems,      QSTS,         GeoJSON/
                 erad           folder)                                              TS agg)        multiperiod)   tracked_changes
                                                                                       erad
```

---

## 3.2 Stage-by-stage trace

### 1. Creation
- **shift** — `DistributionSystemBuilder.build()` instantiates `DistributionSystem(name=..., auto_add_composed_components=True)` (system_builder.py:97) and populates buses/assets/branches/transformers from graph + mappers (`_add_bus` :139, `_add_asset` :128, `_add_branch` :150, `_add_transformer` :235).
- **ditto** — all three readers construct `DistributionSystem(auto_add_composed_components=True)` (opendss/reader.py:49, cim/reader.py:61, cyme/reader.py:58) and return it via `AbstractReader.get_system()`.
- **erad** — consumes existing systems; builds `AssetSystem`/`HazardSystem` (separate infrasys types), not DistributionSystems.
- **gdm** — programmatic construction in tests/examples; composed components auto-added.

**Metadata captured:** `name`, `uuid`, `description`, `data_format_version` (stamped from installed package version at `__init__`, distribution_system.py:73-74).
**Metadata NOT captured:** creator tool, creation timestamp, source dataset, input hashes. A system born in shift carries no record that shift built it; a system born in ditto carries no record of its source format/file.

### 2. Validation
- **gdm (library):** per-component pydantic `model_validator`s (`validate_fields`/`validate_fields_base`); graph-level warnings (`_warn_uncovered_bus_phases`, distribution_system.py:233-260); `MultipleOrEmptyVsourceFound` (:178-180); infrasys `System.validate()`.
- **gdm (MCP):** `diagnose_system` re-implements a heuristic subset (phase consistency, matrix dims, array lengths; diagnostics.py:50-54) instead of calling `System.validate()` — two validation philosophies coexist.
- **ditto:** readers re-validate via pydantic; OpenDSS reader *raises* on any error (opendss/reader.py:155-172) while CYME reader only *warns* unless `raise_on_validation_error=True` (cyme/reader.py:331-338) — inconsistent validation semantics across converters.
- **erad:** `AssetSystem.from_gdm` validates through its own `gdm_mapping` filtering; no schema-level re-validation of the source system.

**Metadata NOT captured:** validation reports are not attached to the model; no validation history, no validator version.

### 3. Serialization
- **gdm:** `to_json` (infrasys) writes the JSON document; time series go to a sibling `<stem>_time_series/` folder of Arrow files + `time_series_metadata.db`. JSON carries `__metadata__` field tags per component; quantities as `{value, units}`; composed components referenced by UUID.
- **ditto:** GDM JSON via inherited `to_json` (`--save-gdm`, MCP `export_gdm_json`); also emits OpenDSS `.dss` trees and CIM XML (single/package + `manifest.xml`).
- **erad:** serializes *results* (SQLite `AssetStateTable`, Parquet/CSV/Arrow, GeoJSON) and *cached models* (JSON + `models_metadata.json` with only `{description, created_at, file_path}`); HazardSystem/AssetSystem have their own `to_json`.
- **shift:** GDM JSON via `system.to_json` (MCP `export_system_json`); catalog via `DatasetSystem.from_json(path, upgrade_handler=UpgradeHandler().upgrade)` (mcp/mapper/equipment.py:150-153).

**Metadata captured:** `data_format_version`; per-component type tags.
**Metadata NOT captured:** schema version of the *emitting tool* vs GDM; writer/reader toolchain; source-provenance fields; time-series folder↔JSON atomicity guarantees (a JSON without its TS folder silently loses data).

### 4. Deserialization
- **gdm:** `DistributionSystem.from_json(path, upgrade_handler=...)` — the **upgrade chain** (2.0.1→…→2.3.7, 14 raw-dict handlers; upgrade_handler.py:74-145) is the ecosystem's only schema-versioning machinery. Chain integrity is validated (`_get_upgrade_handlers` :160-191); broken chains raise.
- **ditto/shift/erad:** call `from_json` with the default handler (shift passes `UpgradeHandler().upgrade` explicitly).

**Metadata captured:** none beyond the JSON itself — the upgrade path taken is not recorded; a model migrated 2.0.1→2.3.7 is indistinguishable from a natively-2.3.7 model after the fact.

### 5. Modification
- **gdm:** infrasys `add_component`/`remove_component`; `convert_geometry_to_matrix_representation` (:923); `kron_reduce` (:977); MCP `apply_fixes` (deep-copy edits with change log); MCP `save_system` (name override).
- **erad:** `TrackedChange`/`PropertyEdit` playback (`tracked_changes.py`; scenarios generated by `HazardScenarioGenerator.samples` can be applied back to a GDM model, runner.py:237-248) — but **no MCP tool applies them**, and no tool records which base model a change-set applies to.
- **ditto:** read-only; no mutation API.

**Metadata NOT captured:** no `modified_at`/`modified_by`/revision counter on the system; change history only exists as an *optional* `TrackedChange` list that nothing in the ecosystem populates automatically.

### 6. Enrichment
- **gdm:** `get_subsystem` (:279), model reduction (`reduce_to_three_phase_system`/`reduce_to_primary_system`), aggregated time series (`sys_functools.py`), TS attach via `add_time_series`.
- **erad:** `AssetSystem.from_gdm` enriches assets with `distribution_asset: UUID` back-references and `connections` (models/asset.py:283-284).

**Metadata NOT captured:** enrichment operations are not recorded (a reduced system doesn't say "derived from X by reduce_to_three_phase_system"); the `distribution_asset` UUID back-reference in ERAD is the closest thing the ecosystem has to a provenance edge.

### 7. Execution
- **gdm-flow:** `calculate_ybus`/solvers take a `DistributionSystem` object; results are dataclasses; persisted to SQLite with **run IDs** (`ac_<uuid>`/`pf_<uuid>`/`dc_<uuid>`/`lindistflow_<uuid>`, sqlite_export.py:26-27).
- **erad:** `HazardSimulator.from_gdm/run`; `SimulationEngine` loads assets into DuckDB; Monte Carlo `HazardScenarioGenerator.samples`.

**Metadata NOT captured (critical):** the gdm-flow `runs` table stores only `{run_id, implementation, success, message, created_at_utc}` — **no model reference, no model hash, no GDM version, no config snapshot**. Run IDs are random and untraceable to inputs. ERAD's simulation metadata similarly lacks ERAD version, curve-set hash, and input-model hashes → results are not reproducible or linkable. (gdm-flow and erad can *resolve* models via the registry, but results never record which `model_ref` produced them.)

### 8. Reporting
- **gdm:** `to_gdf`/`to_geojson`/`plot` (Plotly HTML).
- **gdm-flow:** SQLite result DBs, Plotly HTML dashboards (`generate_dashboard`/`generate_ts_dashboard`), Rich terminal reports, JSON result templates.
- **erad:** SQLite/Parquet/CSV/Arrow exports, GeoJSON, Plotly figures, `tracked_changes.json` + ZIP archives.
- **ditto:** converted format trees; **ditto/shift** emit GDM JSON as their "report."

**Metadata NOT captured:** no artifact manifests; plots/HTML/reports carry no link back to the model, run, or config that produced them.

---

## 3.3 Cross-cutting issues

| # | Issue | Evidence | Impact |
|---|---|---|---|
| L1 | **No provenance** on systems or results | No created-by/modified-by/source fields anywhere; gdm-flow `runs` lacks model ref/hash; erad sim metadata lacks version/hashes | Cannot reconstruct *what produced what* — fatal for the knowledge-graph/agent-memory vision |
| L2 | **Upgrade path not recorded** | `from_json(upgrade_handler=...)` doesn't record the migration | A migrated model is indistinguishable from a native one; audit/replay impossible |
| L3 | **Version coupling is fragile** | ditto/erad/shift pin `grid-data-models==2.3.7`; gdm-flow uses `>=2.3.7`; upgrade chain stops at 2.3.7 | A GDM 2.4 JSON cannot be consumed until every consumer bumps; ecosystem evolves in lockstep or not at all |
| L4 | **MCP sub-versions decoupled** | gdm mcp 0.1.0 vs package 2.3.7; erad mcp 1.0.0 vs 0.1.14; shift ServerConfig 0.1.0 vs 0.8.0; erad_plugins register() 0.1.0 vs 0.1.2 | Agents can't infer server capability from package version; drift hides |
| L5 | **Time-series folder is a sidecar with no integrity contract** | JSON + `<stem>_time_series/` Arrow + `time_series_metadata.db`; reducer `--force` deletes folder | Copies/moves that drop the folder silently lose TS data; no manifest or checksum |
| L6 | **Dual validation philosophies** | Library uses pydantic validators + `System.validate()`; MCP `diagnose_system` re-implements heuristics | Divergent diagnostics; MCP may accept models the library rejects and vice versa |
| L7 | **Change tracking exists but is disconnected** | `TrackedChange`/`PropertyEdit`/`apply_updates_to_system` in gdm; ERAD generates them; **no MCP tool applies them** | Scenario playback requires direct Python imports — exactly the class of op the AI-native vision needs as MCP |
| L8 | **Lossy conversions erase identity** | ditto CIM writer synthesizes `uuid5` mRIDs; OpenDSS name sanitization (`get_opendss_safe_name`) | Round-trips break identity links; provenance to source lost |
| L9 | **Model registry is an unowned contract** | `models(model_id, version, stored_path)` schema referenced by gdm/gdm-flow/erad via env `DIST_STACK_MODEL_REGISTRY_DB` | Three servers depend on a registry none of them implement |
| L10 | **No artifact management** | Results/plots/reports have no registry, manifest, or lifecycle | Artifacts are orphaned files; an orchestrator cannot enumerate what a workflow produced |

---

## 3.4 Opportunities for richer interoperability

1. **A provenance header** on the DistributionSystem JSON (`produced_by`, `source`, `created_at`, `input_hashes`, `upgrade_path`). Backward-compatible: additive JSON keys, no schema bump required if kept as metadata.
2. **Model-identity hashing:** `hash_model` already exists (hashing_utils.py:30) — emit a system-level content hash on `to_json` so runs/artifacts can reference it (gdm-flow `runs`, erad sim metadata, ditto CIM output).
3. **Record the upgrade path** in the deserialized system metadata (list of applied handler versions) for auditability.
4. **Standardize validation:** make MCP `diagnose_system` delegate to `System.validate()` and surface both; attach validation summary to artifacts.
5. **Expose scenario application via MCP:** `apply_tracked_changes(system, changes)` tool in gdm — unblocks the "run ERAD scenarios → apply to model → re-run powerflow" loop as a pure-MCP workflow.
6. **Artifact manifest convention** per run (model hash + GDM version + tool version + config snapshot + output file list) — the seed of the future knowledge graph.
