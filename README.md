# dist-stack monorepo

Monorepo for the NREL distribution-suite orchestration plane: a shared
model-registry library, the workflow-runner and knowledge-graph MCP servers, a
read-only visibility dashboard, and the Jupyter Book documentation (including
the architecture-assessment archive).

Consolidates five former repositories — `gdm-stack` (docs only),
`dist-stack`, `dist-workflow-runner`, `dist-kg`, `dist-dashboard` — into one
`uv` workspace with a single lockfile and a single `.venv`.

## Layout

```
dist-stack/
├── pyproject.toml                     # uv workspace root (virtual)
├── packages/
│   ├── dist-stack-model-registry/     # shared library (import name dist_stack)
│   ├── dist-workflow-runner/          # MCP workflow-orchestration server
│   └── dist-kg/                       # MCP knowledge-graph server
├── apps/
│   └── dist-dashboard/                # read-only Streamlit visibility UI
├── docs/                              # Jupyter Book (dist-stack + mcp-wiring + architecture-assessment)
├── servers.yaml.example               # workflow-runner config template
└── opencode.json                      # MCP wiring for an LLM client
```

## Packages

| Member | Dist name | Role |
|---|---|---|
| `packages/dist-stack-model-registry` | `dist-stack-model-registry` (import `dist_stack`) | model registry, runstore, provenance manifests, KG store, shared MCP client |
| `packages/dist-workflow-runner` | `dist-workflow-runner` (import `workflow_runner`) | runs versioned JSON workflows across the domain MCP servers |
| `packages/dist-kg` | `dist-kg` (import `kg_server`) | knowledge-graph MCP server over `dist_stack.kg` |
| `apps/dist-dashboard` | `dist-dashboard` (virtual) | read-only browser over the runstore / KG / registry |

The five domain repos (`grid-data-models`, `gdm-flow`, `erad`, `ditto`,
`shift`) stay external.

## Develop

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12).

```bash
uv sync            # create .venv + uv.lock
uv run pytest      # all four suites (registry 167 + runner 71 + kg 56 + dashboard 15)
uv run jupyter-book build docs   # docs → docs/_build/html/
```

Per-package:

```bash
uv run --project packages/dist-stack-model-registry pytest
uv run --project packages/dist-workflow-runner pytest
uv run --project packages/dist-kg pytest
uv run --project apps/dist-dashboard python -m unittest discover -s tests -v
```

## Run the servers

```bash
uv run --project packages/dist-workflow-runner python -m workflow_runner --config servers.yaml
uv run --project packages/dist-kg python -m kg_server
uv run --project apps/dist-dashboard streamlit run apps/dist-dashboard/app.py --server.headless=true
```

`WORKFLOW_RUNNER_CONFIG` (or `--config`) points the runner at `servers.yaml`;
copy `servers.yaml.example` to `servers.yaml` and edit the server commands for
your machine.
