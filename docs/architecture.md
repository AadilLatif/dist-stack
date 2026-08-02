# Architecture

Dist-stack is a Python monorepo organised as a `uv` workspace: one shared
zero-dependency library, two MCP servers, and a Streamlit dashboard. This page
is the map; each member has its own page.

## Members

| Member | Type | Process model |
|---|---|---|
| `packages/dist-stack-model-registry` | library (`dist_stack`) | imported by everything; stdlib-only, zero required runtime deps |
| `packages/dist-workflow-runner` | MCP server | **MCP client** orchestrator: spawns and keeps alive the domain servers; its lifespan owns a `ServerPool` |
| `packages/dist-kg` | MCP server | **stateless**: no lifespan; resolves `DIST_STACK_KG_DB` lazily per call |
| `apps/dist-dashboard` | Streamlit app | **read-only**: never writes, never creates a DB file |

## The data spine

Every artifact produced by any domain server flows through the same spine:

```
domain tools (external, over MCP)
   -> artifact file + *.manifest.json sidecar
   -> runstore.attach_artifact  (runs + artifacts rows)
   -> dist_stack.kg.ingest      (reads registry + runstore + sidecars)
   -> knowledge graph (dist_stack.kg)
   -> consumers: dist-kg server (agents) · dashboard (humans)
```

Writes flow left to right; reads happen at the two consumer endpoints.

## Shared-state databases

The three stores are plain SQLite files under `~/.cache/dist-stack/` by
convention:

| Env var | Store | Conventional path |
|---|---|---|
| `DIST_STACK_MODEL_REGISTRY_DB` | `dist_stack.registry` | `~/.cache/dist-stack/registry.db` |
| `DIST_STACK_RUNSTORE_DB` | `dist_stack.runstore` | `~/.cache/dist-stack/runstore.db` |
| `DIST_STACK_KG_DB` | `dist_stack.kg` | `~/.cache/dist-stack/kg.db` |

Paths are resolved **lazily per call** (explicit argument > env var). Resetting
the shared state is just file deletion: every store recreates its schema
idempotently on the next open, and the KG is a derived index rebuilt by
`dist_stack.kg.ingest`.

## Repo layout

```
dist-stack/
├── pyproject.toml                     # uv workspace root (virtual)
├── packages/
│   ├── dist-stack-model-registry/     # library (src/dist_stack)
│   ├── dist-workflow-runner/          # MCP server (src/workflow_runner)
│   └── dist-kg/                       # MCP server (src/kg_server)
├── apps/dist-dashboard/               # Streamlit app
├── docs/                              # this book + architecture-assessment archive
├── servers.yaml.example               # runner config template
└── opencode.json                      # LLM-client wiring
```

The five domain repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`,
`shift`) are external and reached only over MCP — see {doc}`ecosystem`.
