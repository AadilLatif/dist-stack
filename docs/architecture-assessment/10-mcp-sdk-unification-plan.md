# 10. MCP SDK Unification Plan

**Status:** Implementation-ready design (oracle-verified against all 5 repos' source).
**Date:** 2026-07-31

---

# Unified MCP SDK Implementation Plan: MCPServer Pattern + MCP 2.0

## 0. Current state inventory

| Repo | MCP version (pyproject) | API style | Tools | Resources | Prompts | Server file | Entry point |
|---|---|---|---|---|---|---|---|
| shift | `mcp>=2.0` | **`MCPServer` + `register()`** (target) | 24 | 4 | 3 | `src/shift/mcp_server/server.py` (197 L) | `shift.mcp_server.__main__:main` |
| gdm (grid-data-models) | `mcp>=2.0` | low-level `Server` ctor callbacks | 28 | 3 | 3 | `src/gdm/mcp/server.py` (1599 L) | `gdm.mcp.server:main` (typer) |
| gdm-flow | `mcp>=1.0.0,<2` | low-level `Server` decorators | 15 | 2 | 3 | `src/gdm_flow/mcp/server.py` (1231 L) | `gdm_flow.mcp.server:main` (typer) |
| erad | `mcp>=1.0.0` (venv already has **2.0.0**) | low-level `Server` + `add_request_handler` | 27 | dynamic | 0 | `src/erad/mcp/server.py` (739 L) | `erad.mcp:main` |
| ditto | `mcp[cli]>=1.0,<2` | **`FastMCP`** (removed in mcp 2.0) | 14 | 2 | 2 | `src/ditto/mcp/server.py` (665 L) | `ditto.mcp.server:main` |

Key verified facts that drive the plan:

- **`FastMCP` does not exist in mcp 2.0.0** (`mcp/server/__init__.py` exports only `MCPServer`, `Server`, `ServerRequestContext`, etc.). Ditto cannot stay as-is if it upgrades.
- **mcp 2.0 keeps the 1.x low-level `Server` API working** (`add_request_handler`, `mcp.types`, `mcp.server.stdio` all still present). This means erad is *already running on mcp 2.0.0 binaries* through backward compat — silently. The `mcp>=1.0.0` pin is a landmine.
- The `mcp[cli]` extra still exists in 2.0.0 (ditto's extra is fine, just bump the bound).
- All 5 repos already import `dist_stack` (gdm, gdm-flow, erad, ditto declare it; **shift imports `dist_stack.manifest` but does not declare the dependency** — fix while here).
- gdm already has domain subpackages (`inspection/`, `operations/`, `utilities/`, `validation/`, `knowledge/`) but they hold *implementation* logic; all 28 tool schemas + dispatch live in server.py.
- erad already has per-domain handler modules; only the schema list (`_get_tools`) and dispatch map (`_TOOL_HANDLERS`) are centralized in server.py.
- Existing naming: verb-first snake_case in 4/5 repos; **gdm-flow is the outlier** with an `opf_` prefix.

---

## 1. Target pattern (canonical spec, from shift)

Every repo gets this exact shape under its `<pkg>/mcp/` (or `mcp_server/`) package:

```
<package>/mcp/
├── server.py          # create_server(): MCPServer + register() calls (<= ~80 lines)
├── __init__.py        # version + re-exports
├── __main__.py        # optional thin main → create_server().run(transport="stdio")
├── common.py          # shared helpers (path/model_ref resolution, serializers) [per-repo]
├── tools/
│   ├── __init__.py
│   ├── <domain_a>.py  # each: def register(mcp: MCPServer) -> None: @mcp.tool() ...
│   └── <domain_b>.py
├── resources/
│   ├── __init__.py
│   └── <domain>.py    # def register(mcp): @mcp.resource("scheme://...") ...
└── prompts/
    ├── __init__.py
    └── workflows.py   # def register(mcp): @mcp.prompt() ...
```

Module contract (copy shift's conventions exactly):

```python
# tools/graph/nodes.py  (shift pattern)
from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def add_node(ctx: Context[AppContext], graph_id: str, node_name: str,
                 longitude: float, latitude: float, assets: list[str] | None = None) -> str:
        """<description>

        Args:
            graph_id: ...
        Returns:
            JSON confirmation...
        """
        ...
```

Rules:
- **Schema comes from type annotations + Google-style docstrings.** `inputSchema` `properties` → typed params; `default` → Python defaults; `required` → params without defaults; `enum` → `Literal[...]`; `anyOf` (system_path XOR model_ref) → `X | None = None` + the existing runtime check (`_get_system_path_arg`) — keep that helper, it already enforces the XOR.
- `ctx: Context[AppContext]` first param **only if** the tool needs session state. gdm/gdm-flow/erad/ditto tools are stateless-per-call (they hold module-level state singletons); omit `ctx` there.
- Return values: `str` (JSON) like shift, or typed objects (mcp 2.x auto-serializes dicts/lists). Keep the current JSON-string convention to minimize client-visible change.
- Errors: keep returning `{"success": False, "error": ...}` payloads rather than raising.
- Resources: `@mcp.resource("scheme://static")` or `@mcp.resource("scheme://{param}")` (templates); static URIs must take **no** function params (mcp 2.0 raises otherwise). `name=`/`mime_type=` kwargs map to the old `Resource(...)` fields.
- Prompts: `@mcp.prompt()` returning `str` (shift's `workflows.py`). Prompt args become function params.
- Entry point: `create_server()` → `mcp.run(transport="stdio")`. Keep the existing typer CLI shells (they just set globals before `create_server().run(...)`).
- `_TOOL_CALLS_ENABLED` control flag (gdm): with MCPServer there is no central `call_tool` to intercept. Keep the flag in `tools/control.py`; wrap every non-control tool at registration with a guard decorator defined there (`@_guard`). One helper, applied uniformly.

---

## 2. Decisions

### 2.1 Shared MCPServer class (dist-stack) vs per-repo — **per-repo, no wrapper**

**Do not create a shared `MCPServer` subclass in dist-stack.** Rationale:

1. `MCPServer` from the `mcp` package *is* the shared abstraction — all five repos import the same class from the same pinned version.
2. The pattern is a *convention* (`register(mcp)` + decorators + `create_server()`), not a class hierarchy.
3. State management diverges per repo (shift: lifespan `AppContext`; erad: `ServerState` singleton; ditto: `_SYNC_STATE`).
4. dist-stack's job is the model registry (`dist_stack.registry` / `dist_stack.manifest`). Keep it that way.

**What should go in dist-stack instead** (small, optional, non-blocking): repurpose the empty `src/dist_stack/mcp/__init__.py` placeholder into a conventions home:
- `dist_stack/mcp/CONVENTIONS.md` — this pattern spec so future servers copy one doc.
- Optionally `dist_stack/mcp/serialization.py` with `json_safe()` / `error_payload()` shared by all repos.
- Delete the stale "FUTURE (Phase 2): registry MCP server" docstring.

### 2.2 Ditto: FastMCP → **migrate to MCPServer (forced)**

FastMCP is removed in mcp 2.0. The migration is the *smallest* of the five:

- `FastMCP("DiTTo", instructions=...)` → `MCPServer("DiTTo", instructions=...)`
- `mcp.run()` → `mcp.run(transport="stdio")`
- `@mcp.tool()` (sync fns — supported by `add_tool`), `@mcp.resource(...)`, `@mcp.prompt(description=...)` — all unchanged syntax
- `_SYNC_STATE` singleton — unchanged
- pyproject: `mcp[cli]>=1.0,<2` → `mcp[cli]>=2.0,<3` (both occurrences)

### 2.3 Version pinning — **`mcp>=2.0,<3` everywhere**

| Repo | Current | Change to |
|---|---|---|
| grid-data-models | `mcp>=2.0` (extra) | `mcp>=2.0,<3` |
| gdm-flow | `mcp>=1.0.0,<2` (extra) | `mcp>=2.0,<3` |
| erad | `mcp>=1.0.0` (main deps) | `mcp>=2.0,<3` |
| ditto | `mcp[cli]>=1.0,<2` (main + extra) | `mcp[cli]>=2.0,<3` |
| shift | `mcp>=2.0` (extra) | `mcp>=2.0,<3` |

Also: add `dist-stack-model-registry` to shift's pyproject `mcp` extra (undeclared import today).

### 2.4 Tool naming convention — **verb-first snake_case, no prefix**

Adopt across all repos: `<verb>_<noun>[_<qualifier>]`. Matches 4/5 repos already.

**gdm-flow rename table** (drop the `opf_` prefix; verb-first):

| Current | New |
|---|---|
| `opf_calculate_ybus` | `calculate_ybus` |
| `opf_run_ac` | `run_ac_opf` |
| `opf_run_dc` | `run_dc_opf` |
| `opf_run_lindistflow` | `run_lindistflow` |
| `opf_compare_solvers` | `compare_solvers` |
| `opf_export_sqlite` | `export_sqlite` |
| `opf_run_ac_pf` | `run_ac_pf` |
| `opf_run_qsts` | `run_qsts` |
| `opf_run_multiperiod` | `run_multiperiod` |
| `opf_plot_ts` | `plot_ts` |
| `list_opf_documentation` | `list_documentation` |
| `search_opf_documentation` | `search_documentation` |
| `get_opf_documentation_page` | `get_documentation_page` |
| `list_opf_api_symbols` | `list_api_symbols` |
| `get_opf_api_reference` | `get_api_reference` |

Cross-repo collisions (`get_system_summary` in gdm+ditto, etc.) are **acceptable**: each repo runs as its own MCP server and clients namespace tools by server name. Resource URI schemes stay per-repo (`gdm://`, `gdm-flow://`, `erad://`, `ditto://`, `shift://`).

---

## 3. mcp 1.x → 2.x upgrade notes (applies to gdm-flow, erad, ditto)

| mcp 1.x | mcp 2.x |
|---|---|
| `from mcp.server import Server; app = Server("name")` | `from mcp.server import MCPServer; mcp = MCPServer("name", instructions=...)` |
| `@app.list_tools()` / `@app.call_tool()` returning `list[Tool]` / `CallToolResult` | `@mcp.tool()` per function; schema from annotations + docstring |
| `@app.list_resources()` / `@app.read_resource(uri) -> list[TextResourceContents]` | `@mcp.resource("uri")` returning `str`/`bytes`/dict directly |
| `@app.list_prompts()` / `@app.get_prompt(name, args) -> GetPromptResult` | `@mcp.prompt()` returning `str`; args become params |
| `app.add_request_handler("tools/list", ...)` (erad) | removed concept — decorators register directly |
| `app.run(read_stream, write_stream, app.create_initialization_options())` inside `asyncio.run` | `mcp.run(transport="stdio")` (sync) |
| `mcp.types.AnyUrl`, `ReadResourceContents` import dance (gdm-flow lines 33–36) | delete — not needed |
| `mcp.server.fastmcp.FastMCP` (ditto) | **gone** → `MCPServer` |
| `Context` = `mcp.server.fastmcp.Context` (1.x) | `mcp.server.mcpserver.context.Context` |

The 1.x low-level `Server` still works on 2.0.0 (backward compat, verified) — the safety net: **bump the pin first, run the suite, then migrate code** (build-then-flip).

---

## 4. Per-repo migration plans

All follow the same strategy: **build-then-flip** — (a) bump/confirm mcp 2.x, (b) create the new modules and migrate code while the old server.py still runs, (c) flip server.py to `MCPServer` + `create_server()` in one commit, (d) update tests. Each repo is independent.

### 4.1 gdm — largest file, zero version-change risk

```
src/gdm/mcp/
├── server.py            # REWRITE → create_server() (~50 L) + typer CLI
├── common.py            # NEW: _load_system_with_fallback_name, _get_system_path_arg,
│                        #      _get_system_paths_arg, _json_safe (moved from server.py)
├── tools/
│   ├── __init__.py      # NEW
│   ├── validation.py    # diagnose_system, suggest_fixes, apply_fixes
│   ├── operations.py    # merge_systems, split_by_substation, split_by_feeder, reduce_system, save_system
│   ├── inspection.py    # get_system_summary, query_components, analyze_topology, validate_connectivity,
│   │                    # get_component_details, find_orphaned_components, get_component_relationships,
│   │                    # get_time_series_summary, get_time_series_values
│   ├── export.py        # export_subsystem_by_buses, to_geojson, plot_system
│   ├── tracked_changes.py  # apply_tracked_changes
│   ├── knowledge.py     # search_gdm_documentation, get_api_reference, get_code_examples,
│   │                    # list_available_components, get_component_fields
│   └── control.py       # set_tool_calls_enabled, get_tool_calls_enabled + _TOOL_CALLS_ENABLED + _guard
├── resources/
│   ├── __init__.py      # NEW
│   └── index.py         # register(): @mcp.resource("gdm://components"), ("gdm://tools"), ("gdm://workflows")
├── prompts/
│   ├── __init__.py      # NEW
│   └── workflows.py     # register(): validate_and_fix(system_path), reduce_and_export(system_path, export_path),
│                        #             analyze_system(system_path)
├── schemas.py           # unchanged
├── exceptions.py        # unchanged
└── version.py           # unchanged
```

Steps: (1) pin `mcp>=2.0,<3`; (2) create `common.py`; (3) create `tools/` 7 modules; (4) create `resources/index.py` + `prompts/workflows.py`; (5) flip `server.py` to `create_server()`; (6) update tests.

Test impact: replace `_call_tool` helper with direct function calls (or a `tests/mcp_tool_calls.py` name→function map); `_handle_*` dict-arg calls become kwargs; `_TOOL_CALLS_ENABLED` import from `tools/control`. Recommended: golden-schema snapshot test before flip.

### 4.2 gdm-flow — mcp 1.x→2.x + rename, medium risk

```
src/gdm_flow/mcp/
├── server.py            # REWRITE → create_server() (~35 L) + typer CLI
├── common.py            # NEW: _load_system, _resolve_model_ref_to_path, _get_system_path_arg,
│                        #      all 8 _serialize_* functions, docs helpers
├── tools/
│   ├── __init__.py      # NEW
│   ├── solvers.py       # calculate_ybus, run_ac_opf, run_dc_opf, run_lindistflow, compare_solvers,
│   │                    # export_sqlite, run_ac_pf, run_qsts, run_multiperiod, plot_ts
│   └── knowledge.py     # list_documentation, search_documentation, get_documentation_page,
│                        # list_api_symbols, get_api_reference
├── resources/
│   ├── __init__.py      # NEW
│   └── index.py         # @mcp.resource("gdm-flow://solvers"), ("gdm-flow://workflows")
└── prompts/
    ├── __init__.py      # NEW
    └── workflows.py     # run_ac_pf_workflow, run_qsts_workflow, run_opf_workflow
```

Steps: (1) pin `mcp>=2.0,<3` + run suite (compat holds); (2) create `common.py`; (3) create tools with renamed functions; (4) resources + prompts; (5) flip server.py; (6) update tests + 12 doc files referencing `opf_*` names.

### 4.3 erad — version bump + pattern flip; keep flat layout (no `tools/` churn)

Keep existing flat modules; add `register(mcp)` to each:

```
src/erad/mcp/
├── server.py            # REWRITE → create_server() (~40 L) + main()/serve()
├── simulation.py        # ADD register(mcp): 7 tools
├── assets.py            # ADD register(mcp): 4 tools
├── hazards.py           # ADD register(mcp): 6 tools
├── fragility.py         # ADD register(mcp): 2 tools
├── export.py            # ADD register(mcp): 6 tools
├── cache.py             # ADD register(mcp): 2 tools
├── documentation.py     # ADD register(mcp): search_documentation
├── utilities.py         # ADD register(mcp): 3 tools
├── resources.py         # ADD register(mcp): template resources + catalog
├── prompts/
│   ├── __init__.py      # NEW
│   └── workflows.py     # NEW: 3 prompts
├── state.py             # unchanged
├── helpers.py           # unchanged
└── __init__.py          # keep re-exports
```

Tool function names: drop the `_tool` suffix inside `register()` (schema name = function name). Resources redesign: use templates (`erad://docs/{doc_path}` with `security=` path-traversal policy, `erad://cached-model/{model_name}`, `erad://asset-system/{system_id}`) + static `erad://catalog`.

### 4.4 ditto — smallest diff; FastMCP→MCPServer forced

- `FastMCP("DiTTo", instructions=...)` → `MCPServer("DiTTo", instructions=...)`
- `mcp.run()` → `mcp.run(transport="stdio")`
- pyproject: `mcp[cli]>=1.0,<2` → `mcp[cli]>=2.0,<3`
- Optional: split into `tools/readers.py`, `tools/writers.py`, `tools/inspection.py`, `resources/docs.py`, `prompts/workflows.py`

---

## 5. dist-stack changes (optional, low priority)

1. `src/dist_stack/mcp/__init__.py`: replace the "FUTURE (Phase 2)" placeholder with a pointer to `CONVENTIONS.md`.
2. Add `src/dist_stack/mcp/CONVENTIONS.md` (the §1 spec) + optional `serialization.py`.
3. No dependency changes (do **not** add `mcp` to dist-stack).
4. shift's pyproject: declare `dist-stack-model-registry` in the `mcp` extra.

---

## 6. Global migration order

Each repo is independent; recommended sequence by risk:

1. **gdm** — validates the pattern at the largest scale with zero version risk. (Also snapshot golden schemas; reuse script for others.)
2. **erad** — version pin is a latent bug; flip pattern; no tool renames; largest schema surface but cleanest existing split.
3. **gdm-flow** — real 1.x→2.x decorator migration + 15 renames + docs ripple; do once the pattern is proven.
4. **ditto** — smallest diff; FastMCP→MCPServer forced.
5. **dist-stack** — conventions doc any time.

Per-repo commit shape: (1) pyproject pin bump + test run, (2..n) additive module creations, (n+1) server.py flip + deletions + test updates + docs, (n+2) golden-schema test. Never commit a half-flipped server.

---

## 7. Execution checklist for fixers

- Copy the pattern from `shift/src/shift/mcp_server/` — read `server.py`, `tools/graph/nodes.py`, `resources/docs.py`, `prompts/workflows.py`, `__main__.py`, `state.py` before starting.
- mcp 2.0.0: `MCPServer` (`mcp/server/mcpserver/server.py`), `Context` (`mcp/server/mcpserver/context.py`); tool/resource/prompt decorators support sync + async; static resources reject params; template resources require exact param match.
- **FastMCP is removed in mcp 2.0** — ditto's migration is not optional.
- Tool names must not change for gdm/erad/ditto; gdm-flow changes per §2.4 table (longest-match-first when sed-ing).
- Keep `system_path`/`model_ref` XOR semantics via the existing `_get_system_path_arg` helpers.
- Everything else (state singletons, typer CLIs, loguru setup, dist_stack registry integration, entry-point names) stays as-is.
