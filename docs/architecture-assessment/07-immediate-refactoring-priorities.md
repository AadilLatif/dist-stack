# 7. Immediate Refactoring Priorities

Top 10 quick wins, each with a concrete target. All are mechanical, low-risk, and independently shippable. Ordered roughly by leverage-per-effort.

| # | Priority | Repo | Target | Why it matters |
|---|---|---|---|---|
| 1 | Derive tool manifest + README table from live MCP registration | gdm, gdm-flow, erad, shift | MCP server module (list_tools source) + README badge/count | Kills the 20-vs-24 / 26-vs-27 / 33-vs-36 drift — LLM planners need an honest capability inventory (Gap #1) |
| 2 | Replace hardcoded `COMPONENT_MAP` with pydantic introspection at startup | gdm | `src/gdm/mcp/knowledge/documentation.py` | Knowledge tools currently report 14 of ~20 component classes (Gap #2) |
| 3 | Delete dead CLI flags `--host`/`--port`/`--allow-auto-fix` | gdm | `src/gdm/mcp/server.py:1061` | Dead params mislead agent-driven invocations; `allow_auto_fix` never reaches `apply_fixes` (Gap #20) |
| 4 | Point MCP validation/suggest/apply_fixes at `System.validate()` + `hashing_utils` | gdm | `src/gdm/mcp/validation/diagnostics.py` | Removes the second validation philosophy; makes MCP diagnostics consistent with library semantics (Gap #16) |
| 5 | Fix `DEFAULT_FRAGILTY_CURVES` typo; align `mcp.__version__` with package 0.1.14 | erad | `src/erad/default_fragility_curves/default_fragility_curves.py:16`, `src/erad/mcp/__init__.py:41` | Version negotiation + naming trust (Gap #12) |
| 6 | Unify run_id prefixes and `violation_kind` strings behind single enums | gdm-flow | `src/gdm_flow/sqlite_export.py` (run ids :26-27, violation kinds :337/:553) | Artifact references and future KG edges must match exactly (Gap #11) |
| 7 | Remove phantom `[sparse]`/`[optimization]` extras from README; serve docs via `importlib.resources` | gdm-flow | README.md:31,37,49; `src/gdm_flow/mcp/server.py:36` | Doc tools currently break in wheel installs because they resolve a repo-relative `docs/` folder (Gap #20) |
| 8 | Fix stale ARCHITECTURE.md/API.md module references; implement lifespan or drop the `_SYNC_STATE` docstring claim | ditto | `ARCHITECTURE.md`, `API.md`, `src/ditto/mcp/state.py:3-4` vs `server.py:505` | Doc/code drift erodes agent trust in docs-grounded planning (Gap #20) |
| 9 | Repair `_DOC_FILES` (CHANGELOG.md/IMPROVEMENTS.md refs); sync docs 33→36 | shift | `src/shift/mcp_server/server.py:19-55`, `docs/MCP_SERVER.md` | `list_docs`/`read_doc` silently never serve two advertised docs (Gap #20) |
| 10 | Relax `==2.3.7` → `~=2.3.7`; bump plugin `register()` to 0.1.2 | erad, ditto, shift, erad_plugins | pyproject.toml pins; `plugins/*/src/*/plugin.py` | Unblocks the ecosystem from lockstep versioning; aligns plugin metadata with package version (Gap #13, #12) |

**Why these ten first:** every item is contained in a single module, needs no cross-repo coordination, is verifiable by existing tests, and removes an active source of agent error (wrong inventory, wrong versions, dead params, broken doc tools). Together they form Phase 0 of the roadmap (see Deliverable 6).
