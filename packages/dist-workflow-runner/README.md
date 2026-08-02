# dist-workflow-runner

MCP workflow-runner for the NREL distribution suite. Runs versioned JSON
workflow templates (`workflows/*.json`) **sequentially** across the domain MCP
servers (`gdm`, `gdm_flow`, `erad`, `ditto`, `shift`), recording every
execution in a shared runstore (`dist_stack.runstore`) with an execution-graph
artifact + manifest sidecar for provenance queries.

Spec: `docs/architecture-assessment/11-runstore-and-workflow-runner-spec.md` §2.

## Install

```bash
pip install -e .            # deps: mcp>=2.0,<3, dist-stack-model-registry, pyyaml
```

## Configure

Copy `servers.yaml.example` to `./servers.yaml` and edit the server commands,
`workflow_dir`, and `runstore_db`. Resolution order:
`--config` > `WORKFLOW_RUNNER_CONFIG` env > `./servers.yaml`.

Each server's `env` block is merged into the spawned subprocess env — that's
where `DIST_STACK_MODEL_REGISTRY_DB` / `DIST_STACK_RUNSTORE_DB` reach the
domain servers.

## Run

```bash
python -m workflow_runner --config servers.yaml
# or
workflow-runner
```

## Tool surface

| Tool | Description |
|---|---|
| `list_servers` | Configured servers with connection status / tool count / version |
| `list_tools(server)` | Tools a configured domain server exposes |
| `create_workflow(workflow_json, *, overwrite)` | Validate + write a template |
| `get_workflow(workflow_id)` | Fetch a template |
| `list_workflows` | List templates |
| `run_workflow(workflow_id, inputs, *, run_id)` | Synchronous v1 execution (requires a runstore) |
| `get_run(run_id)` | Run record + attached artifacts |
| `list_runs(status, workflow_id, limit)` | Run history (`workflow_id` via JSON1 `json_extract`) |

Resources: `workflow-runner://workflows`, `workflow-runner://servers`.
Prompts: `describe_workflow(workflow_id)`.

## How a run works

`run_workflow` creates a runstore row (`status="running"`), executes each step
in order against the configured servers (`${var}` substitution from inputs +
prior captures, dotted paths supported), updates the row, then persists the
execution-graph artifact `<runstore-dir>/wf_<hex12>.execution.json` plus a
manifest sidecar and attaches it via `attach_artifact`. `on_failure: "fail"`
(default) aborts on the first failed step; `"continue"` records the error and
proceeds.

Without a configured runstore, `run_workflow`/`get_run`/`list_runs` return a
clean `{"success": false, "error": ...}` payload; the other tools work
normally.

## Layout

```
src/workflow_runner/
├── server.py     # create_server() + lifespan (ServerPool lifecycle)
├── config.py     # servers.yaml loader/validator
├── models.py     # frozen dataclasses (RunnerConfig, WorkflowSpec, …)
├── client.py     # ServerPool: lazy stdio spawn, keep-alive, close_all()
├── templates.py  # workflow load/list/validate
├── executor.py   # sequential engine + runstore lifecycle
├── tools/        # servers, workflows, runs
├── resources/    # static indexes
└── prompts/      # describe_workflow
workflows/        # checked-in templates (run_ac_pf_workflow, feasibility_study)
tests/            # pytest suite (FakePool, fake_server, integration)
```
