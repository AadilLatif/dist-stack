# Run Store (`dist_stack.runstore`)

The runstore is the **run-state + artifact store**: every tool run gets a
`runs` row, and every artifact it produces gets an `artifacts` row indexed by
the manifest sidecar.

```{note}
Runstore errors subclass `RunstoreError(ValueError)`:
`RunstoreUnavailableError`, `RunNotFoundError`, `RunExistsError`,
`ArtifactPathNotFoundError`.
```

## Schema

### `runs`

| Column | Type | Notes |
|---|---|---|
| `run_id` | `TEXT` | PK — canonical `make_run_id(tool)` or caller-supplied |
| `tool` | `TEXT` | e.g. `run_ac_opf`, `run_simulation`, `convert_model` |
| `tool_version` | `TEXT` | repo `__version__` at write time |
| `run_type` | `TEXT` | manifest vocabulary: `gdm_flow_run`, `erad_simulation`, `ditto_conversion`, `shift_feeder`, `workflow_execution`, ... |
| `implementation` | `TEXT` | gdm-flow solver: `ac_opf`, `ac_pf`, `dc_opf`, `lindistflow`, `qsts`, `multiperiod` |
| `status` | `TEXT` | `pending` \| `running` \| `succeeded` \| `failed` \| `cancelled` |
| `message` | `TEXT` | failure detail / result message |
| `session_id` | `TEXT` | shift session, ditto name, runner session |
| `model_id` / `model_version` / `model_hash` | — | registry provenance |
| `payload` | `TEXT` | JSON; parsed to `dict` in `RunRecord.payload` |
| `created_at_utc` / `updated_at_utc` / `deleted_at_utc` | `TEXT` | timestamps |

### `artifacts`

| Column | Type | Notes |
|---|---|---|
| `artifact_id` | `TEXT` | PK — `art_<hex12>` |
| `run_id` | `TEXT` | FK `runs(run_id)` **`ON DELETE CASCADE`** |
| `artifact_path` | `TEXT` | absolute path |
| `artifact_type` | `TEXT` | from the sidecar manifest |
| `tool` / `tool_version` / `model_id` / `model_version` / `model_hash` | — | copied from the sidecar |
| `created_at_utc` / `deleted_at_utc` | `TEXT` | timestamps |

## Public API

### Create a run

```{eval-rst}
.. function:: create_run(tool, *, run_type, run_id=None, implementation=None, status=None, success=None, message=None, session_id=None, tool_version=None, model_id=None, model_version=None, model_hash=None, payload=None, runstore_db=None, env_var="DIST_STACK_RUNSTORE_DB") -> RunRecord

   Insert a run record (**NOT an upsert**). ``run_id=None`` mints
   ``make_run_id(tool)``. ``status`` defaults to ``'succeeded'``;
   ``success=True/False`` maps to ``'succeeded'``/``'failed'`` (passing both
   raises). Raises :class:`RunExistsError` when ``run_id`` already exists.
```

```python
from dist_stack import create_run

run = create_run(
    "run_ac_opf",
    run_type="gdm_flow_run",
    implementation="ac_opf",
    model_id="my-model",
    payload={"solver": "ac_opf"},
    runstore_db=run_db,
)
run.run_id        # minted: "run_ac_opf_<hex12>" (tool-prefixed)
```

### Read runs

```{eval-rst}
.. function:: get_run(run_id, *, runstore_db=None, env_var=...) -> RunRecord

   Fetch a non-deleted run; :class:`RunNotFoundError` on miss.

.. function:: list_runs(*, tool=None, run_type=None, status=None, implementation=None, session_id=None, include_deleted=False, limit=100, offset=0, runstore_db=None, env_var=...) -> list[RunRecord]

   All runs matching every provided filter, newest first
   (``created_at_utc DESC, run_id DESC``). Soft-deleted rows excluded unless
   ``include_deleted=True``.
```

```python
from dist_stack import get_run, list_runs

run = get_run("sim_000000000001", runstore_db=run_db)
run.success           # True for status='succeeded' (None for pending/running)

runs = list_runs(status="failed", runstore_db=run_db)
```

### Update / delete

```{eval-rst}
.. function:: update_run(run_id, *, status=None, message=None, implementation=None, session_id=None, model_id=None, model_version=None, model_hash=None, payload=None, runstore_db=None, env_var=...) -> RunRecord

   Update only the provided kwargs; always stamps ``updated_at_utc``.
   ``payload`` REPLACES the stored payload wholesale.
   :class:`RunNotFoundError` when no row matches.

.. function:: delete_run(run_id, *, soft=True, runstore_db=None, env_var=...) -> None

   ``soft=True`` stamps ``deleted_at_utc`` (re-delete re-stamps);
   ``soft=False`` hard-deletes — artifact rows cascade via the FK.
```

```python
from dist_stack import update_run, delete_run

update_run("sim_000000000001", status="succeeded", message="done", runstore_db=run_db)
delete_run("sim_000000000001", runstore_db=run_db)          # soft delete
delete_run("sim_000000000001", soft=False, runstore_db=run_db)  # hard delete
```

### Artifacts

```{eval-rst}
.. function:: attach_artifact(run_id, artifact_path, *, runstore_db=None, env_var=...) -> ArtifactRecord

   1. The artifact file must exist on disk → else :class:`ArtifactPathNotFoundError`.
   2. If a sidecar exists it is read and its fields copied verbatim; otherwise a
      new sidecar is written with ``artifact_type=run.run_type``,
      ``tool=run.tool``, ``config={"run_id": run_id}``, ``derived_from=[run_id]``.
   3. Inserts one ``artifacts`` row. The same artifact path may be attached to
      many runs.

.. function:: list_artifacts(run_id, *, include_deleted=False, runstore_db=None, env_var=...) -> list[ArtifactRecord]

   Artifacts attached to ``run_id``, newest first; ``[]`` for an unknown run.
```

```python
from dist_stack import attach_artifact, list_artifacts

rec = attach_artifact("sim_000000000001", "/data/result.json", runstore_db=run_db)
arts = list_artifacts("sim_000000000001", runstore_db=run_db)
```

### Id minting

```{eval-rst}
.. function:: make_run_id(prefix) -> str

   Mint a canonical 2-part id ``f"{prefix}_{uuid4().hex[:12]}"`` (16 chars).
   ``prefix`` must match ``^[a-z][a-z0-9]*$`` else :class:`RunstoreError`.
```

## Environment variable

`DIST_STACK_RUNSTORE_DB` — read lazily per call; `runstore_db` argument wins.

## Data models

```{eval-rst}
.. class:: RunRecord

   ``run_id, tool, run_type, status`` (required); ``implementation, message,
   session_id, tool_version, model_id, model_version, model_hash``,
   ``payload: dict``, ``created_at_utc, updated_at_utc, deleted_at_utc``.
   Property ``success`` maps terminal statuses: ``succeeded→True``,
   ``failed/cancelled→False``, non-terminal → ``None``.

.. class:: ArtifactRecord

   ``artifact_id, run_id, artifact_path`` (required); ``artifact_type, tool,
   tool_version, model_id, model_version, model_hash, created_at_utc,
   deleted_at_utc``.
```

## Notes

- Soft-deleted runs keep their artifacts (the FK cascade fires only on hard
  delete), and `list_runs(include_deleted=True)` shows them.
- The runstore is the primary ingestion source for the knowledge graph
  ingester — see {doc}`kg`.
