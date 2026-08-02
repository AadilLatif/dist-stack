# 14. Jupyter Book Information Architecture (Redesign)

**Status:** Implementation-ready design (oracle-verified against all current book pages).
**Date:** 2026-08-01

---

# Dist-Stack Jupyter Book — Information Architecture Redesign

**Diagnosis of the "weird" feel:** the book reads as "library docs + two surprise server pages + a wiring memo" because (a) `intro.md` is a library page wearing a scope page's job, (b) `mcp-wiring.md` (an ops memo of machine-specific launcher paths) sits behind the quickstart, (c) `ecosystem.md` mixes three audiences (map, scenarios, ops) and overlaps `mcp-wiring.md`, (d) the KG library/server pair is only distinguished by filename, (e) `conventions.md` has no audience marker, (f) no page answers "what problem does this solve".

## A. Target book structure (exact `_toc.yml`)

```yaml
format: jb-book
root: overview
parts:
  - caption: Overview
    chapters:
      - file: architecture
      - file: quickstart
  - caption: Core Library
    chapters:
      - file: library
      - file: registry
      - file: manifest
      - file: runstore
      - file: kg
      - file: conventions
  - caption: Orchestration & Apps
    chapters:
      - file: runner
      - file: kg-server
      - file: dashboard
  - caption: Usage & Ecosystem
    chapters:
      - file: usage-scenarios
      - file: ecosystem
      - file: mcp-wiring
  - caption: Reference & Examples
    chapters:
      - file: references
      - file: examples/provenance_walkthrough
  - caption: Architecture Assessment (spec archive)
    chapters:
      - file: architecture-assessment/01-repository-architecture-overview
      # ... 02 through 13 (all specs, unchanged)
```

**Reader journey:** understand (overview → architecture → quickstart) → use the library (library + four stores + conventions-as-contributor) → run the apps (runner/kg-server/dashboard) → connect the ecosystem (usage-scenarios → ecosystem → mcp-wiring-last) → dive deep (reference + specs archive).

## B. Scope framing — new root `docs/overview.md`

States plainly: dist-stack is a monorepo = (1) a shared zero-dependency library (`dist_stack`), (2) orchestration apps (two MCP servers + a Streamlit dashboard), (3) the wiring point (opencode.json + servers.yaml.example) + this book + the spec archive.

**"What belongs where" tables:**
- In this monorepo: `packages/dist-stack-model-registry` (library), `packages/dist-workflow-runner` (MCP server), `packages/dist-kg` (MCP server), `apps/dist-dashboard` (Streamlit app), `docs/`, wiring files.
- External (NOT in this repo, reached only over MCP): grid-data-models (28 tools), gdm-flow (15), erad (33), ditto (14), shift (36), erad_plugins (library, no server).

**"When to use what" table:** register/write/query in code → library; run multi-step study → runner; ask an agent about the graph → kg-server; see what happened → dashboard; hook into an LLM client → mcp-wiring.

**Assumptions:** Python ≥3.10, SQLite ≥3.24, `uv`, domain repos not required to read the book but required to run orchestration scenarios.

`_config.yml`: `root: overview`, title → `"Dist-Stack — Model Registry & Provenance for the Distribution Suite"`.

## C. Page-by-page plan

| Page | Verdict | New title | Key change |
|---|---|---|---|
| intro.md | rename+rewrite → library.md | `Core Library at a Glance (dist_stack)` | Drop scope framing (→ overview); keep stores table (with "see page" column), env table, design rules, mcp-is-not-a-store note, install; add "What's next" |
| overview.md | **new** (root) | as drafted in B | — |
| architecture.md | **new** | `Architecture` | members table, data spine diagram, process model (runner MCP-client / kg-server stateless / dashboard read-only), shared-state DBs, repo layout |
| quickstart.md | rewrite | `Quickstart` | Keep library tour as **Track A** (renumbered); add **Track B** orchestration stack (uv sync → servers.yaml → kg-server+ingest → run_workflow → dashboard → mcp-wiring pointer) |
| registry.md | keep+orient | `Registry Library (dist_stack.registry)` | 3-line what/when opener + Related line |
| manifest.md | keep+orient | `Manifest Library (dist_stack.manifest)` | same pattern |
| runstore.md | keep+orient | `Runstore Library (dist_stack.runstore)` | same pattern |
| kg.md | keep+retitle+note | `KG Library (dist_stack.kg)` | prominent note: "this is the LIBRARY; for the MCP server see {doc}`kg-server`" |
| kg-server.md | keep+retitle+mirror note | `KG Server (dist-kg)` | reciprocal note + "when to use: agents; humans → dashboard" |
| runner.md | keep+retitle+related | `Workflow Runner Server (dist-workflow-runner)` | pointer to runstore.md + usage-scenarios |
| dashboard.md | keep+retitle+related | `Dashboard App (dist-dashboard)` | pointer to usage-scenarios |
| conventions.md | keep+retitle+audience box | `MCP Server Conventions (contributors)` | "who this is for" note; moves to end of Core Library |
| ecosystem.md | rewrite (slim to map) | `The Ecosystem at a Glance` | keep 8-server table + provenance spine + DBs; move scenarios → usage-scenarios; dedupe specs table (already in references); add "this page vs wiring" box |
| mcp-wiring.md | keep+retitle+trim | `LLM Client Wiring (opencode)` | moves to Usage & Ecosystem last; intro box "operational companion to ecosystem"; slim §2 to one canonical prompt + pointer |
| references.md | keep | `Reference` | no content change (holds API tables + error hierarchy + specs index) |
| examples/*.ipynb | keep | — | unchanged |
| architecture-assessment/01–13 | keep | — | part caption becomes "(spec archive)" |

## D. New pages

- `docs/overview.md` — full draft in B.
- `docs/architecture.md` — ~60 lines: members table, data spine diagram (`domain tools → artifact + .manifest.json → runstore → kg.ingest → KG → consumers`), process model, shared-state DBs + reset, repo layout sketch.
- `docs/usage-scenarios.md` — decision table (task → tool) + 3 intent-first journeys (run a study end-to-end; see/trace results; run a reusable workflow), each with when/steps/verify.

## E. Naming scheme

Every H1: **`<Role> <Noun> (<identifier>)`** — `Registry Library (dist_stack.registry)`, `KG Server (dist-kg)`, `Dashboard App (dist-dashboard)`. Library/Server/App always in the H1. Library pages end with "Related"; server/app pages start with "Backing library". Only file rename: `intro.md → library.md` (2 cross-refs: quickstart.md:11, ecosystem.md:183).

## F. Execution order

1. `_config.yml`: root: overview + title.
2. Create overview.md, architecture.md, usage-scenarios.md.
3. `git mv intro.md library.md`; rewrite; fix the two `{doc}`intro`` refs.
4. Edit existing pages (orientation blocks, retitles, note boxes, ecosystem slim, mcp-wiring trim).
5. Rewrite `_toc.yml` per A.
6. Cross-ref sweep `grep -rn "{doc}\`" docs/ --include="*.md"` (exclude _build).
7. `uv run jupyter-book build docs` → 0 warnings; spot-check overview/library/usage-scenarios HTML + 6-part sidebar.
8. Consistency pass (dashboard.md model_registry.db vs registry.db discrepancy — reconcile one line).

**Files:** 3 created, 1 renamed, 12 edited, 2 config, 0 deleted. Archive + notebook untouched.
