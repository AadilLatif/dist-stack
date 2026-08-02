# Quickstart

A five-minute tour. Two tracks — pick the one that matches what you want to do.

```{note}
**Choose your track**

- **Track A — use the library in code:** register a model → write a provenance
  sidecar → create a run and attach an artifact → ingest everything into the
  knowledge graph → query the provenance chain. Every snippet uses temporary
  file paths so it runs anywhere, exactly as in the walkthrough notebook
  ({doc}`examples/provenance_walkthrough`).
- **Track B — run the orchestration stack:** sync the workspace, configure the
  runner, start the KG server and ingest, run a packaged workflow, and open
  the dashboard.
```

```{warning}
The Track A examples pass explicit `*_db=` paths for clarity. In production you
would instead set the environment variables documented in {doc}`library` and
omit the arguments.
```

## Track A — use the library in code

### A1. Imports

```python
import tempfile
from pathlib import Path

from dist_stack import (
    register, lookup,
    write_manifest,
    create_run, attach_artifact,
    ingest, get_provenance_chain,
)

work = Path(tempfile.mkdtemp(prefix="dist-stack-"))
reg_db = work / "registry.sqlite"
run_db = work / "runstore.sqlite"
kg_db = work / "kg.sqlite"
```

### A2. Register a model

```python
model_file = work / "model.json"
model_file.write_text("{}")

record = register(
    "my-model",
    stored_path=model_file,
    metadata={"tool": "save_system", "package": "grid-data-models"},
    registry_db=reg_db,
)
assert record.model_id == "my-model"
assert record.version == 1
```

Look it back up — the latest non-deleted version by default:

```python
record = lookup("my-model", registry_db=reg_db)
record.version          # 1
record.stored_path      # absolute path, resolved against the DB parent
```

### A3. Write a provenance sidecar

A manifest is a frozen JSON file at `{artifact_path}{MANIFEST_SUFFIX}`
(`.manifest.json`). It records what produced the artifact, from what, and when.

```python
artifact = work / "result.json"
artifact.write_text("{}")

write_manifest(
    artifact,
    artifact_type="erad_simulation",
    tool="run_simulation",
    tool_version="0.3.0",
    model_id="my-model",
    config={"hazard_system_id": "h-1"},
    derived_from=[],
)
```

### A4. Create a run and attach the artifact

Runs are **not** upserts — `create_run` raises `RunExistsError` if the
`run_id` already exists. `attach_artifact` reads the sidecar you just wrote and
copies its fields into the `artifacts` row.

```python
run = create_run(
    "run_simulation",
    run_type="erad_simulation",
    run_id="sim_000000000001",
    model_id="my-model",
    status="succeeded",
    payload={"hazard_system_id": "h-1"},
    runstore_db=run_db,
)

art = attach_artifact("sim_000000000001", artifact, runstore_db=run_db)
art.artifact_id            # "art_<hex12>"
art.model_id               # "my-model" (from the sidecar)
```

### A5. Ingest into the knowledge graph

`dist_stack.kg.ingest` reads the registry, the runstore, and the sidecars, then
writes `model:`/`run:`/`artifact:` nodes and `has_artifact` / `generated_by` /
`derived_from` / `references` edges.

```python
report = ingest(
    kg_db=kg_db,
    runstore_db=run_db,
    registry_db=reg_db,
)
report.nodes_created       # 3  (model + run + artifact)
report.edges_created       # 5  (has_artifact, generated_by, references x2, derived_from)
report.errors              # []
```

Re-running against unchanged sources is a no-op:

```python
report2 = ingest(kg_db=kg_db, runstore_db=run_db, registry_db=reg_db)
report2.nodes_created      # 0
report2.edges_created      # 0
```

### A6. Query the provenance chain

Walk `up` (what produced a node) or `down` (what a node produced) as a list of
lists grouped by depth:

```python
chain = get_provenance_chain(
    f"artifact:{artifact}",
    direction="up",
    kg_db=kg_db,
)
[level[0].node_id for level in chain]
# ['artifact:/tmp/.../result.json', 'run:sim_000000000001', 'model:my-model']
```

```{note}
`get_provenance_chain` walks only provenance relations: `up` uses
`('derived_from', 'generated_by', 'references')` on incoming edges, `down` uses
`('derived_from', 'has_artifact')` on outgoing edges. Traversal is cycle-safe
and depth-capped.
```

## Track B — run the orchestration stack

1. **Sync the workspace.** From the monorepo root, `uv sync` installs the
   library and the orchestration apps into one `.venv`.
2. **Configure the runner.** Copy `servers.yaml.example` to `servers.yaml` and
   edit the server commands for your machine (see {doc}`runner`).
3. **Start the KG server and ingest.** Export `DIST_STACK_KG_DB`, run
   `uv run --project packages/dist-kg python -m kg_server`, and build the graph
   from the runstore + registry + sidecars with its `ingest` tool (see
   {doc}`kg-server`).
4. **Run the packaged workflow.** Call the runner's `run_workflow` with the
   shipped `run_ac_pf_workflow` template (or `feasibility_study`), then inspect
   the result with `get_run(<run_id>)`.
5. **Open the dashboard.** `uv run --project apps/dist-dashboard streamlit run
   apps/dist-dashboard/app.py` — the read-only browser over the runstore, KG,
   and registry (see {doc}`dashboard`).

To drive the whole stack from an LLM client instead of the terminal, wire it up
per {doc}`mcp-wiring`.

## What's next

- {doc}`registry` — the `models(model_id, version, stored_path)` contract.
- {doc}`manifest` — the sidecar schema and `MANIFEST_SUFFIX`.
- {doc}`runstore` — runs and artifacts, soft delete, and idempotency.
- {doc}`kg` — nodes/edges, node-id schemes, and the ingester.
- {doc}`examples/provenance_walkthrough` — the full walkthrough notebook.
