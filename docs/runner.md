# Workflow Runner (dist-workflow-runner)

`packages/dist-workflow-runner` (import name `workflow_runner`, dist name
`dist-workflow-runner`) is the **workflow-orchestration MCP server** of the
distribution suite. It is an MCP *client*: it runs versioned JSON workflow
templates sequentially across the five domain MCP servers (`gdm`, `gdm_flow`,
`erad`, `ditto`, `shift`) and records every execution in the shared runstore
with an execution-graph artifact plus a manifest sidecar for provenance
queries.

The runner holds **zero domain logic** — it never imports a domain model or
runs a solver. It speaks MCP to the domain servers over stdio (one subprocess
per server, kept alive for the runner's lifetime) and persists orchestration
state through the `dist_stack.runstore` library. Design spec:
`docs/architecture-assessment/11-runstore-and-workflow-runner-spec.md` §2.

This page covers the runner's MCP surface, the workflow JSON format, the
`servers.yaml` configuration, and the runstore lifecycle. For the runstore
library itself see {doc}`runstore`.

## The eight MCP tools

The server registers eight tools, split across three modules. The first five
need only the workflow templates on disk; the last three also require a
runstore (`DIST_STACK_RUNSTORE_DB` or `config.runstore_db`).

| Tool | Module | Purpose |
|---|---|---|
| `list_servers` | `tools/servers.py` | List configured domain servers with connection status, tool count, and server version. |
| `list_tools(server)` | `tools/servers.py` | List the tools a configured domain server exposes (`name` / `description` / `required_params`). |
| `create_workflow(workflow_json, *, overwrite=False)` | `tools/workflows.py` | Validate and write a workflow template (schema_version 1) to the workflow directory. |
| `get_workflow(workflow_id)` | `tools/workflows.py` | Fetch a workflow template by id. |
| `list_workflows` | `tools/workflows.py` | List available templates as `{workflow_id, version, name, step_count, source_prompt}` summaries. |
| `run_workflow(workflow_id, inputs=None, *, run_id=None, reuse_run_id=None)` | `tools/runs.py` | Run a workflow template synchronously (see [the runstore lifecycle](#the-runstore-lifecycle)). |
| `get_run(run_id)` | `tools/runs.py` | Get a run record plus its attached artifacts. |
| `list_runs(status=None, workflow_id=None, limit=100)` | `tools/runs.py` | List runs newest-first, with optional status / workflow_id filters (`workflow_id` via JSON1 `json_extract`). |

Two resources and one prompt round out the surface:

| Kind | Name | Purpose |
|---|---|---|
| Resource | `workflow-runner://workflows` | Static index of the available workflow templates. |
| Resource | `workflow-runner://servers` | Static index of the configured servers. |
| Prompt | `describe_workflow(workflow_id)` | Instructions for inspecting a workflow template. |

## Workflow JSON format

Workflow templates are plain JSON files (schema_version 1) stored in the
packaged `packages/dist-workflow-runner/workflows/` directory. The shipped
`feasibility_study.json` shows the full shape:

```json
{
  "schema_version": 1,
  "workflow_id": "feasibility_study",
  "version": "1.0.0",
  "name": "Feasibility study",
  "description": "Load a system, run AC PF, summarize.",
  "source_prompt": "gdm-flow://run_ac_pf_workflow@1",
  "inputs": [
    { "name": "system_path", "type": "string", "required": true }
  ],
  "steps": [
    {
      "id": "step_1",
      "server": "gdm",
      "tool": "get_system_summary",
      "args": { "system_path": "${system_path}" },
      "capture": "system_summary",
      "on_failure": "fail"
    },
    {
      "id": "step_2",
      "server": "gdm_flow",
      "tool": "run_ac_pf",
      "args": { "system_path": "${system_path}", "include_details": true },
      "capture": "pf_result",
      "on_failure": "fail"
    }
  ],
  "outputs": [
    { "name": "pf_summary", "from": "pf_result" }
  ]
}
```

Field-by-field:

- `schema_version` — always `1` (the only supported version).
- `workflow_id` — a non-empty slug (letters/digits/`_`/`.`/`-`); it names the
  file `<workflow_id>.json` and is validated by
  `workflow_runner.templates.validate_workflow`.
- `version` — the template's own semantic version.
- `name` / `description` — human-facing metadata.
- `source_prompt` — an optional provenance anchor for the execution-graph
  artifact's `derived_from`.
- `inputs` — `{name, type, required}` declarations. Missing `required` inputs
  fail `run_workflow` before any runstore row is created.
- `steps` — an ordered list of `{id, server, tool, args, capture, on_failure}`.
  `args` may reference `${var}`; the substitution environment is the inputs
  plus prior step captures, with dotted paths supported
  (`capture_var.key.subkey`). `capture` names the variable holding the step's
  decoded result. `on_failure` is `"fail"` (abort the run on this step's
  failure) or `"continue"` (record the error and proceed).
- `outputs` — `{name, from}` entries that read the final environment
  (inputs + captures) after all steps run.

## Configuration: `servers.yaml`

The runner reads one YAML file describing the domain servers it should spawn.
Copy `servers.yaml.example` to `./servers.yaml` (or point
`WORKFLOW_RUNNER_CONFIG` at it, or pass `--config`).

**Resolution order:** `--config` > `WORKFLOW_RUNNER_CONFIG` env > `./servers.yaml`.

```yaml
runstore_db: ~/.cache/dist-stack/runstore.db   # optional; overrides DIST_STACK_RUNSTORE_DB
workflow_dir: ./workflows                       # optional; default is the packaged workflows/ dir

servers:
  gdm:
    command: gdm-mcp
    env:
      DIST_STACK_MODEL_REGISTRY_DB: ~/.cache/dist-stack/registry.db
  gdm_flow:
    command: python
    args: ["-m", "gdm_flow.mcp"]
    cwd: ~/repos/gdm-flow
  erad:
    command: python
    args: ["-m", "erad.mcp"]
```

Each server entry becomes a `ServerSpec` (`workflow_runner.models`):

| Field | Meaning |
|---|---|
| `command` | The executable (required; non-empty). |
| `args` | Optional argument list appended to the command. |
| `cwd` | Optional working directory for the spawned subprocess (`~`-expanded). |
| `env` | Optional extra environment; the SDK merges it over the parent env. |
| `timeout_s` | Optional per-server tool-call timeout (default `300`). |

The loader (`workflow_runner.config.load_servers_config`) validates server
name uniqueness (duplicate YAML keys rejected), non-empty commands, and no NUL
bytes in command/args. `runstore_db`, `workflow_dir`, `cwd` and `env` values
are `~`-expanded so `~/.cache/...` paths survive the round trip.

Because each server's `env` block is merged into the spawned subprocess
environment, that is where the three shared database locations
`DIST_STACK_MODEL_REGISTRY_DB`, `DIST_STACK_RUNSTORE_DB`, and
`DIST_STACK_KG_DB` reach the domain servers.

## The runstore lifecycle

`run_workflow` executes the steps of a template in order against the live
server pool, and — when a runstore is configured — drives the full lifecycle
(`workflow_runner.executor.execute_workflow`):

```text
create_run("running", tool="run_workflow", run_type="workflow_execution")
  -> for each step: pool.call_tool(server, tool, args)  (${var} substitution)
  -> update_run(status="succeeded"|"failed", message=...)
  -> persist execution-graph artifact <runstore-dir>/wf_<hex12>.execution.json
     + a manifest sidecar (artifact_type="workflow_execution")
  -> attach_artifact(run_id, artifact_path)
```

Notes:

- The artifact directory is the runstore DB's parent directory; the file name
  is `wf_<hex12>.execution.json` where `<hex12>` is the run id suffix.
- The manifest sidecar records `tool="run_workflow"`, the workflow
  id/version/run id in `config`, and `derived_from=[source_prompt]`.
- Step failures are never raised — they are recorded on the `StepResult`s and
  reflected in the run status. `on_failure: "fail"` aborts the remaining
  steps (they become `skipped`); `"continue"` keeps going.
- Without a runstore, `run_workflow` / `get_run` / `list_runs` return a clean
  `{"success": false, "error": "runstore required: ..."}` payload; the other
  tools work normally.

## Dynamic planning with `reuse_run_id`

`run_workflow(..., reuse_run_id=...)` surfaces the prior run's execution graph
so the calling agent can plan the next run from what actually happened. The
prior run must satisfy all of:

- it exists in the runstore,
- it is a `workflow_execution` run with `status == "succeeded"`,
- it has an attached execution-graph artifact,
- the artifact's `workflow_id` matches the requested `workflow_id`.

On success the response includes `prior_graph` — a summarized execution graph
with step `result` values **omitted** (a size guard; the full record lives in
the artifact on disk). Reuse is read-only: the runner never rewrites the prior
run, and the agent decides whether and how to reuse its steps.

## Environment variables

| Variable | Purpose |
|---|---|
| `WORKFLOW_RUNNER_CONFIG` | Path to the `servers.yaml` config (between `--config` and `./servers.yaml` in resolution order). |
| `DIST_STACK_RUNSTORE_DB` | Runstore DB path for `run_workflow` persistence (also settable via `config.runstore_db`). |

The runner spawns each domain server with the parent environment plus the
server's `env` block, so the `DIST_STACK_MODEL_REGISTRY_DB` /
`DIST_STACK_RUNSTORE_DB` / `DIST_STACK_KG_DB` variables configured in
`servers.yaml` flow straight through to the spawned servers.

## Running it

```bash
uv run --project packages/dist-workflow-runner python -m workflow_runner --config servers.yaml
# or via the entry point:
uv run --project packages/dist-workflow-runner workflow-runner --config servers.yaml
```

See {doc}`mcp-wiring` for how the runner is wired into an LLM client alongside
the rest of the ecosystem, and {doc}`ecosystem` for the end-to-end workflow
scenario that exercises it.
