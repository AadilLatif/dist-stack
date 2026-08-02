# 11. Runstore + Workflow-Runner Spec

**Status:** Implementation-ready design (oracle-verified against all 5 repos' source).
**Date:** 2026-07-31

---

# Phase-2 Design — `dist_stack.runstore` + `dist-workflow-runner`

Key verified facts: dist-stack `pyproject.toml` has zero dependencies (so no `mcp` import can live there); the registry's `migrate()`/`_connect()` patterns are the mirror; gdm-flow's `_update_manifest` passes no `model_id`/`model_version`/`model_hash` (the gap is real); qsts/multiperiod mint 3-part ids (`qsts_<solver>_<hex12>`) and write no manifests at all; erad `generate_id()` is `uuid4()[:8]`; mcp 2.0.0's `mcp.client.stdio.stdio_client` + `ClientSession` client API is present; `Manifest(**data)` in `read_manifest` means adding *values* to existing optional fields is backward-compatible.

---

## DELIVERABLE 1 — `dist_stack.runstore`

### 1.1 Module layout (mirrors `registry/`)

```
src/dist_stack/runstore/
├── __init__.py     # re-exports public API (mirror registry/__init__.py)
├── model.py        # RunRecord, ArtifactRecord (frozen dataclasses)
├── schema.py       # SCHEMA_VERSION, DDL_RUNS, DDL_ARTIFACTS, DDL_ALTERS, migrate()
├── sqlite.py       # _connect() — verbatim clone of registry/sqlite.py (WAL, busy_timeout=5000, per-call conn)
├── errors.py       # RunstoreError hierarchy (mirrors registry/errors.py)
└── api.py          # stateless functional API
```

dist-stack `pyproject.toml` unchanged — zero new dependencies (sqlite3 + stdlib only, same as registry).

### 1.2 Schema (exact DDL) + migration

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    tool            TEXT    NOT NULL,            -- tool name, e.g. 'run_ac_opf', 'run_simulation', 'convert_model'
    tool_version    TEXT,                        -- repo __version__ at write time
    run_type        TEXT    NOT NULL,            -- manifest artifact_type vocabulary: 'gdm_flow_run'|'erad_simulation'|'ditto_conversion'|'shift_feeder'|'workflow_execution'|...
    implementation  TEXT,                        -- gdm-flow solver: 'ac_opf'|'ac_pf'|'dc_opf'|'lindistflow'|'qsts'|'multiperiod'; NULL otherwise
    status          TEXT    NOT NULL DEFAULT 'succeeded',  -- 'pending'|'running'|'succeeded'|'failed'|'cancelled'
    message         TEXT,                        -- gdm-flow result.message / failure detail
    session_id      TEXT,                        -- shift session id, ditto name, runner session; NULL otherwise
    model_id        TEXT,                        -- registry provenance (fills the gdm-flow gap)
    model_version   INTEGER,
    model_hash      TEXT,
    payload         TEXT,                        -- JSON: erad {asset_system_id, hazard_system_id, curve_set, ...}, ditto {reader_type, source}, shift {graph_id, ...}, runner {workflow_id, inputs, ...}
    created_at_utc  TEXT NOT NULL,               -- ISO-8601 UTC, same convention as registry + gdm-flow
    updated_at_utc  TEXT,                        -- stamped on create and every update_run
    deleted_at_utc  TEXT                         -- soft delete
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at_utc);
CREATE INDEX IF NOT EXISTS idx_runs_tool        ON runs(tool);
CREATE INDEX IF NOT EXISTS idx_runs_status      ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_session     ON runs(session_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id    TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    artifact_path  TEXT NOT NULL,                -- absolute path
    artifact_type  TEXT,                         -- from the sidecar manifest
    tool           TEXT,
    tool_version   TEXT,
    model_id       TEXT,
    model_version  INTEGER,
    model_hash     TEXT,
    created_at_utc TEXT,
    deleted_at_utc TEXT
);
```

**Migration — exact mirror of registry/schema.py:**

```python
SCHEMA_VERSION = 1

DDL_ALTER_RUNS: tuple[str, ...] = ()        # additive ALTERs for future versions, each guarded
DDL_ALTER_ARTIFACTS: tuple[str, ...] = ()

def migrate(conn) -> None:
    """Idempotent create/migrate to SCHEMA_VERSION; safe to call on every open."""
    conn.execute(DDL_CREATE_RUNS)
    conn.execute(DDL_CREATE_ARTIFACTS)
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is not None and row[0] >= SCHEMA_VERSION:
        return
    for stmt in (*DDL_ALTER_RUNS, *DDL_ALTER_ARTIFACTS):
        try:
            conn.execute(stmt)
        except OperationalError:
            pass  # column already exists
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

`status` gets **no** CHECK constraint (a fixed IN-list would block future status values); the API enforces the Literal instead.

### 1.3 ID scheme — ONE shared format

**Canonical minted id:** `f"{prefix}_{uuid4().hex[:12]}"` — 2-part, 16 chars, exactly gdm-flow's `_make_run_id`. The shared store mints via `make_run_id(prefix)` and **accepts any caller-supplied run_id** (non-empty, ≤128 chars, no whitespace).

**Registered prefixes:**

| Prefix | Owner | Used for |
|---|---|---|
| `ac` `pf` `dc` `lindistflow` `qsts` `mp` | gdm-flow | existing, unchanged |
| `sim` | erad | `run_simulation` |
| `conv` | ditto | conversions + store events |
| `feeder` `graph` | shift | feeder builds, graph builds |
| `wf` | workflow-runner | workflow executions |

**erad 8-char collision risk:** `uuid4()[:8]` = 32 bits; 50% collision at ≈77k ids; erad draws 4 id types from the same space. Fix: (a) bump `ServerState.generate_id()` to `uuid4().hex[:12]` — one line, zero breakage; (b) canonical persisted ids from the runstore (`sim_<hex12>`), short `simulation_id` kept inside `payload`.

### 1.4 Public API — exact signatures (registry style: stateless-per-call, WAL, busy_timeout=5000, env-lazy)

`errors.py`: `RunstoreError(ValueError)` base; `RunstoreUnavailableError`, `RunNotFoundError`, `RunExistsError`, `ArtifactPathNotFoundError`.

```python
@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tool: str
    run_type: str
    status: str
    implementation: str | None = None
    message: str | None = None
    session_id: str | None = None
    tool_version: str | None = None
    model_id: str | None = None
    model_version: int | None = None
    model_hash: str | None = None
    payload: dict = field(default_factory=dict)
    created_at_utc: str | None = None
    updated_at_utc: str | None = None
    deleted_at_utc: str | None = None

    @property
    def success(self) -> bool | None:
        return {"succeeded": True, "failed": False, "cancelled": False}.get(self.status)

@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    artifact_path: str
    artifact_type: str | None = None
    tool: str | None = None
    tool_version: str | None = None
    model_id: str | None = None
    model_version: int | None = None
    model_hash: str | None = None
    created_at_utc: str | None = None
    deleted_at_utc: str | None = None

# api.py
DEFAULT_ENV_VAR = "DIST_STACK_RUNSTORE_DB"

def get_runstore_path(runstore_db=None, *, env_var=DEFAULT_ENV_VAR) -> str
def ensure_schema(db_path) -> None
def make_run_id(prefix: str) -> str
def create_run(tool: str, *, run_type: str, run_id: str | None = None,
               implementation=None, status=None, success=None, message=None,
               session_id=None, tool_version=None, model_id=None, model_version=None,
               model_hash=None, payload=None, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> RunRecord
def get_run(run_id, *, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> RunRecord
def list_runs(*, tool=None, run_type=None, status=None, implementation=None,
              session_id=None, include_deleted=False, limit=100, offset=0,
              runstore_db=None, env_var=DEFAULT_ENV_VAR) -> list[RunRecord]
def update_run(run_id, *, status=None, message=None, implementation=None,
               session_id=None, model_id=None, model_version=None, model_hash=None,
               payload=None, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> RunRecord
def delete_run(run_id, *, soft=True, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> None
def attach_artifact(run_id, artifact_path, *, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> ArtifactRecord
def list_artifacts(run_id, *, include_deleted=False, runstore_db=None, env_var=DEFAULT_ENV_VAR) -> list[ArtifactRecord]
```

Key semantics:
- `create_run`: status default `'succeeded'`; **raises `RunExistsError` on existing run_id** (runs are NOT upserts).
- `get_run`: excludes soft-deleted rows.
- `update_run`: only provided kwargs written; stamps `updated_at_utc`; `payload` REPLACES.
- `delete_run`: soft default (stamp `deleted_at_utc`); hard DELETE cascades artifacts.
- `attach_artifact`: (1) artifact file must exist → else `ArtifactPathNotFoundError`; (2) read manifest sidecar via `dist_stack.manifest.read_manifest` if present, else `write_manifest(artifact_path, artifact_type=run.run_type, tool=run.tool, tool_version=run.tool_version, config={"run_id": run_id}, derived_from=[run_id])`; (3) insert artifacts row copying fields from the sidecar. One artifact_path may be attached to many runs (no uniqueness constraint).

**Artifacts decision:** separate `artifacts` table whose rows are **indexes of manifest sidecars** — `dist_stack.manifest` remains the artifact-level authority; sidecar stays the portable, KG-ingestible provenance record.

### 1.5 Adoption plan per repo — all additive, nothing breaks

| Repo | Changes | Breaks? |
|---|---|---|
| **gdm-flow** | `export_*_result_to_sqlite` + `export_all_results_to_sqlite` gain additive `model_id/model_version/model_hash` kwargs + best-effort `create_run(...)` (run_type=`gdm_flow_run`, implementation=RunType value, status from `result.success`, payload=`{"solver": ...}`) wrapped in `try/except RunstoreUnavailableError: logger.warning(...)`. `_update_manifest` passes provenance through. `time_series.py::run_qsts`/`multiperiod.py` same best-effort mirror + write manifest sidecars (they write none today). `mcp/common.py`: new `_resolve_provenance(model_ref)` — when `model_id` present, `registry.lookup()` for version/hash (catch `ModelNotFoundError` → all-None). | None |
| **erad** | `state.py`: `generate_id()` → `uuid4().hex[:12]`. `load_distribution_model`/`load_hazard_model`: record provenance into state. `run_simulation`: gain `run_id` param; mint `sim_<hex12>` via `make_run_id("sim")`; best-effort `create_run(run_type="erad_simulation", payload={...})`; `attach_artifact(output_path)` + add model provenance to the existing `write_manifest` call; return `run_id` in response. | None |
| **ditto** | `AppState.store`: best-effort `create_run(tool="read_model", run_type="ditto_conversion", run_id="conv_<hex12>", session_id=name, payload={reader_type, name})`. `mcp/tools/writers.py` (both `write_manifest` sites): best-effort `create_run(tool="convert_model", ...)` + `attach_artifact(primary_output)`. | None |
| **shift** | `AppContext` gains `session_id: str = field(default_factory=lambda: uuid4().hex[:12])`. In `tools/graph/builder.py` + `tools/system/builder.py` completion paths: best-effort `create_run(tool=..., run_type="shift_feeder"/"shift_graph", run_id=minted, session_id=ctx...session_id, payload={graph_id, counts})`; `tools/system/export.py`: `attach_artifact(export_path)`. | None |

The mirror is deliberately **write-only**: runstore records *that* runs happened with *what* provenance; the four local stores keep their role as session working state.

**Provenance gap-fill:** every repo that accepts `model_ref` (gdm-flow, erad) resolves `model_id/model_version/model_hash` at load time via the registry and carries them through (a) the manifest sidecar, (b) the runstore row. Path-only refs → NULL (honest, not fabricated).

### 1.6 Test strategy

- `test_runstore_api.py` — CRUD round-trips, status transitions, `success` property, payload JSON round-trip, soft delete + `include_deleted`, `list_runs` filter matrix, `RunExistsError`, `RunNotFoundError`, external run_id acceptance (3-part `qsts_<solver>_<hex12>`).
- `test_runstore_schema.py` — fresh DB → `user_version == 1`; reopen idempotent; migrate on every open.
- `test_runstore_env_laziness.py` — env unset → `RunstoreUnavailableError` at call time, never at import.
- `test_runstore_artifacts.py` — attach with existing sidecar (fields copied); without sidecar (creates one); missing path → `ArtifactPathNotFoundError`; FK cascade on hard delete.
- `test_runstore_thread_safety.py` — concurrent writers on one DB.
- `test_runstore_migration.py` — simulate future additive ALTER proving the guarded-ALTER path.

---

## DELIVERABLE 2 — `dist-workflow-runner` MCP server

### 2.1 Home decision — **new sibling repo `dist-workflow-runner`**

Rejected: inside dist-stack (dependency-free by charter, needs mcp for server AND client); inside a domain repo (must import zero domain logic). **Chosen:** new sibling repo. Dependencies: `mcp>=2.0,<3`, `dist-stack-model-registry`, `PyYAML` (only non-mcp third-party dep; JSON fallback).

### 2.2 Package layout (CONVENTIONS.md shape)

```
dist-workflow-runner/
├── pyproject.toml            # deps: mcp>=2.0,<3; dist-stack-model-registry; pyyaml
├── README.md
├── servers.yaml.example
├── workflows/                # checked-in versioned workflow templates (JSON)
│   ├── run_ac_pf_workflow.json
│   └── feasibility_study.json
├── src/workflow_runner/
│   ├── __init__.py           # __version__
│   ├── __main__.py           # main → create_server().run(transport="stdio")
│   ├── server.py             # create_server(): MCPServer + lifespan (ServerPool lifecycle) + register() calls
│   ├── config.py             # ServerSpec dataclass; load_servers_config(path) -> RunnerConfig
│   ├── models.py             # RunnerConfig, ServerSpec, WorkflowSpec, WorkflowStep, StepResult, WorkflowExecution (frozen dataclasses)
│   ├── client.py             # ServerPool: lazy stdio spawn per spec, keep-alive, close_all(); list_tools(); call_tool() with anyio.fail_after(timeout)
│   ├── templates.py          # load_workflow(workflow_id) from workflows/ dir; validate against WorkflowSpec
│   ├── executor.py           # execute_workflow(workflow, inputs, pool) — sequential engine, ${var} substitution, capture, on_failure
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── servers.py        # register(mcp): list_servers, list_tools
│   │   ├── workflows.py      # register(mcp): create_workflow, get_workflow, list_workflows
│   │   └── runs.py           # register(mcp): run_workflow, get_run, list_runs
│   ├── resources/
│   │   ├── __init__.py
│   │   └── index.py          # register(mcp): "workflow-runner://workflows", "workflow-runner://servers"
│   └── prompts/
│       ├── __init__.py
│       └── workflows.py      # register(mcp): describe_workflow(workflow_id) — optional in v1
└── tests/
    ├── conftest.py           # FakePool, tmp runstore DB fixtures
    ├── fake_server.py        # REAL MCPServer with scripted tools (echo/add/fail) for integration tests
    ├── test_config.py
    ├── test_templates.py
    ├── test_executor.py      # substitution/capture/failure via FakePool
    ├── test_client.py        # stdio spawn against fake_server.py (real SDK path)
    ├── test_tools.py
    └── test_integration.py   # end-to-end: run_workflow → fake_server → runstore rows + artifact
```

### 2.3 Client architecture

- **Transport:** `mcp.client.stdio.stdio_client(StdioServerParameters(command, args, env, cwd))` + `mcp.client.session.ClientSession`. One subprocess per domain server, spawned **lazily on first use**, **kept alive for the runner process lifetime**, torn down in lifespan teardown.
- **Env inheritance:** SDK merges `get_default_environment() | server.env` — config's `env` block is where `DIST_STACK_MODEL_REGISTRY_DB` and `DIST_STACK_RUNSTORE_DB` get passed to each domain server.
- **ServerPool:** `connect(name)`, `list_tools(name)`, `call_tool(name, tool, arguments, timeout_s)` — wraps `session.call_tool` in `anyio.fail_after(timeout_s)` (default 300s). **v1 sequential** — one in-flight call per server, one at a time overall.
- **Consequence, documented:** domain-server session state lives in spawned subprocess; a runner restart loses in-session state — the runstore is the durable layer.

### 2.4 Config format — `servers.yaml`

```yaml
runstore_db: ~/.cache/dist-stack/runstore.db   # optional; overrides DIST_STACK_RUNSTORE_DB
workflow_dir: ./workflows                       # optional; default: packaged workflows/ dir

servers:
  gdm:      { command: gdm-mcp, env: { DIST_STACK_MODEL_REGISTRY_DB: ~/.cache/dist-stack/registry.db } }
  gdm_flow: { command: python, args: ["-m", "gdm_flow.mcp"], cwd: ~/repos/gdm-flow }
  erad:     { command: python, args: ["-m", "erad.mcp"] }
  ditto:    { command: python, args: ["-m", "ditto.mcp.server"] }
  shift:    { command: python, args: ["-m", "shift.mcp_server"] }
```

`ServerSpec`: `name, command, args: list[str] = [], cwd: str | None = None, env: dict[str, str] = {}, timeout_s: int = 300`. Config resolves: `--config` arg > `WORKFLOW_RUNNER_CONFIG` env > `./servers.yaml`. Validates: name uniqueness, non-empty command, no NUL bytes.

### 2.5 Workflow model — exact JSON

```json
{
  "schema_version": 1,
  "workflow_id": "feasibility_study",
  "version": "1.0.0",
  "name": "Feasibility study",
  "description": "Load a system, run AC PF, summarize.",
  "source_prompt": "gdm-flow://run_ac_pf_workflow@1",
  "inputs": [ { "name": "system_path", "type": "string", "required": true } ],
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
  "outputs": [ { "name": "pf_summary", "from": "pf_result" } ]
}
```

- **Substitution:** `${name}` where `name` is an input or a prior `capture`; captures support dotted dict paths (`capture_var.key.subkey`). v1: no expressions, no string interpolation inside JSON values.
- **`on_failure`:** `"fail"` (default) aborts → status `failed`, graph persisted; `"continue"` records error and proceeds.
- **Execution graph artifact** (`artifact_type="workflow_execution"`): `{workflow_id, workflow_version, source_prompt, inputs_resolved, run_id, status, started_at_utc, finished_at_utc, steps: [...], outputs}` — written to `<runstore-artifact-dir>/wf_<hex12>.execution.json` + manifest sidecar (`derived_from=[source_prompt]`, `config={workflow_id, workflow_version, run_id}`), then `attach_artifact(run_id, path)`.
- **Versioned prompts as step source:** each template's `source_prompt` URI records which repo prompt it was derived from. Compiling prompt text into steps automatically is **not** in v1 (v2 dynamic-planning bridge).

### 2.6 Runner tool surface (CONVENTIONS: verb-first, JSON-string returns, `{"success": False, "error": ...}`)

- `list_servers()` → `[{"name", "status": "connected"|"unavailable", "error"?, "tool_count", "server_version"}]`
- `list_tools(server)` → `[{"name", "description", "required_params"}]`
- `create_workflow(workflow_json, *, overwrite=False)` → validates + writes `<workflow_dir>/<workflow_id>.json`
- `get_workflow(workflow_id)`
- `list_workflows()` → `[{"workflow_id", "version", "name", "step_count", "source_prompt"}]`
- `run_workflow(workflow_id, inputs, *, run_id=None)` → synchronous v1: create_run(status="running") → execute → update_run → persist execution-graph artifact + manifest → attach_artifact → `{"success", "run_id", "status", "outputs", "steps": [summaries]}`
- `get_run(run_id)` → runstore.get_run + list_artifacts merged
- `list_runs(status=None, workflow_id=None, limit=100)` → workflow_id filtered via `json_extract(payload, '$.workflow_id')` (JSON1)

### 2.7 Sequencing / phasing — v1 scope (recommended, adopt as-is)

| Feature | v1 | v2+ |
|---|---|---|
| Workflow source | static versioned JSON | dynamic planning from prompt text |
| Execution | sequential | parallel fan-out, DAG-aware |
| Failure handling | `on_failure: fail/continue` | retries with backoff |
| Cancellation | none (synchronous) | `cancel_run` |
| Result size | full JSON in artifact | truncation + streaming progress |
| State sharing | per-runner-process sessions (documented) | session pinning by workflow id |

### 2.8 Test strategy

- **Executor:** `FakePool` (in-memory stub) — substitution (input + dotted capture), capture JSON decode, `on_failure` both policies, timeout, runstore lifecycle.
- **Client integration:** `tests/fake_server.py` — a *real* `MCPServer` exposing `echo`/`add`/`fail_on_demand`; spawn via the same production path (`stdio_client` + `ClientSession`).
- **Tool-level:** direct function calls (doc 10 pattern).
- **End-to-end:** `run_workflow` against fake_server with tmp runstore DB; assert runs row, execution-graph artifact (golden snapshot), manifest sidecar, artifacts row.
- **Config:** servers.yaml fixtures (valid, missing command, duplicate names, NUL bytes).
- **Real-repo smoke tests** (optional, `@pytest.mark.integration`).

---

## Cross-cutting: runstore ↔ runner interaction & roadmap

**Interaction:** the runner is the runstore's *first mandatory consumer* (domain repos mirror best-effort; the runner's `run_workflow` requires `DIST_STACK_RUNSTORE_DB` or `config.runstore_db`). The runner mints `wf_<hex12>` run_ids, drives `pending→running→succeeded/failed`, persists execution graphs via `attach_artifact`. Domain-server mirrors attach their own artifacts to the same runstore → doc-08 provenance chain (`generated_by`/`derived_from`) becomes queryable in one place.

**Roadmap updates:** (1) new doc `11-runstore-and-workflow-runner-spec.md` (= this); (2) doc 06 Phase 2 bullet gains "new repo `dist-workflow-runner`"; (3) doc 08 §8.4 diagram gains a workflow-runner box; (4) doc 02 repo inventory gains the new repo. Implementation order: runstore first (dist-stack), then per-repo mirrors (each independent and additive), then the runner consumes the stable API.
