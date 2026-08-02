# Dist-Stack — Model Registry & Provenance for the Distribution Suite

Welcome to the **dist-stack monorepo**. Dist-stack is three things at once:

1. **A shared zero-dependency library** — `dist_stack` (dist name
   `dist-stack-model-registry`): the model-reference (`model_ref`) resolution
   contract plus four stdlib-only SQLite stores (registry, provenance
   manifests, runstore, knowledge graph) that the whole ecosystem shares.
2. **Orchestration apps** — two MCP servers
   (`packages/dist-workflow-runner`, `packages/dist-kg`) and a read-only
   Streamlit dashboard (`apps/dist-dashboard`).
3. **The wiring point** — `opencode.json` + `servers.yaml.example` for hooking
   the ecosystem into an LLM client, plus this Jupyter Book and the
   architecture-assessment spec archive.

The five domain repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`,
`shift`) stay **external** — dist-stack reaches them only over MCP, never by
importing their code.

## What belongs where

### In this monorepo

| Path | Role | Docs |
|---|---|---|
| `packages/dist-stack-model-registry` | shared library (`dist_stack`) | {doc}`library`, {doc}`registry`, {doc}`manifest`, {doc}`runstore`, {doc}`kg` |
| `packages/dist-workflow-runner` | MCP workflow orchestrator | {doc}`runner` |
| `packages/dist-kg` | MCP knowledge-graph server | {doc}`kg-server` |
| `apps/dist-dashboard` | read-only Streamlit visibility UI | {doc}`dashboard` |
| `docs/` | this Jupyter Book + the architecture-assessment archive | {doc}`architecture` |
| `opencode.json`, `servers.yaml.example` | LLM-client + runner wiring | {doc}`mcp-wiring` |

### External — reached only over MCP

| Repo | Role | Tools |
|---|---|---|
| `grid-data-models` | model authoring / validation / inspection | 28 |
| `gdm-flow` | power-flow solvers | 15 |
| `erad` | hazard simulation | 33 |
| `ditto` | model conversion | 14 |
| `shift` | feeder synthesis | 36 |
| `erad_plugins` | plugin infrastructure (library, no MCP server) | 0 |

## When to use what

| You want to... | Use |
|---|---|
| Register / write / query models, runs, or graph data **in code** | the library — {doc}`library` |
| Run a multi-step study across the domain servers | the runner — {doc}`runner` |
| Ask an agent about the knowledge graph | `dist-kg` — {doc}`kg-server` |
| See what happened (a human) | the dashboard — {doc}`dashboard` |
| Hook the ecosystem into an LLM client | {doc}`mcp-wiring` |

## Assumptions

- **Python >= 3.10** and **SQLite >= 3.24** (both shipped by a current
  Python).
- **[`uv`](https://docs.astral.sh/uv/)** for workspace setup (`uv sync`).
- The domain repos are **not** required to read this book; they **are**
  required to run the orchestration scenarios in {doc}`usage-scenarios`.

## Where to start

- New to dist-stack? → {doc}`architecture`, then {doc}`quickstart`.
- Using the library from code? → {doc}`library`.
- Running the servers / dashboard? → {doc}`runner`, {doc}`kg-server`,
  {doc}`dashboard`.
- Connecting an LLM client? → {doc}`mcp-wiring`.
- The full ecosystem picture → {doc}`ecosystem` and {doc}`usage-scenarios`.
