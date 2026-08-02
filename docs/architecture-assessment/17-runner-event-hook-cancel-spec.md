# Spec 17 — Engine event hook + cancellation (workflow_runner)

Status: **Draft for review** · Date: 2026-08-02 · Supersedes: none (new)
Required by: spec 16 §8 (live run streaming) and §6.5/§7 (cancel button).
Applies to: `packages/dist-workflow-runner` in the dist-stack monorepo.

## 1. Problem

Two capabilities the professional UI needs do not exist in the engine today:

1. **Per-step event emission.** `execute_workflow` (executor.py) writes to
   the runstore exactly twice: `create_run(status="running")` at start,
   `update_run` + artifact at the end. Per-step results exist only in the
   final `.execution.json`. A live timeline cannot be reconstructed by
   polling, and the spec-16 §8 SSE stream has nothing to subscribe to.
2. **Cancellation.** There is no cancel tool, no abort token, and no
   runstore write for it. If the surrounding task is cancelled mid-step, the
   runstore row stays `running` forever and the final `update_run`/artifact
   are skipped (the `except ValueError/ServerError` clauses don't catch
   `CancelledError`). The runstore schema already declares
   `'pending'|'running'|'succeeded'|'failed'|'cancelled'` — but nothing ever
   writes `cancelled`.

These are **engine changes in the monorepo** (platform code, not UI code).
They must be small, backward-compatible, and fully unit-tested.

## 2. Design

### 2.1 `on_step` event hook

Add one optional keyword parameter to `execute_workflow`:

```python
async def execute_workflow(
    workflow: WorkflowSpec,
    inputs: dict[str, Any],
    pool,
    *,
    runstore_db: str | None = None,
    run_id: str | None = None,
    tool_version: str | None = None,
    on_step: Callable[[StepResult], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> WorkflowExecution:
```

`on_step` is invoked **immediately after each `StepResult` is appended** to
the `steps` list — one call per step, for every status the executor can
produce: `succeeded`, `failed` (including substitution failures and
transport/timeout `ServerError`), and `skipped` (aborted). This gives the
caller exact parity with what the runstore will finally record.

- The callback is **sync** (`Callable[[StepResult], None]`), called inline
  in the async loop — cheap to call (append to a queue / notify an SSE
  broker), and avoids forcing async APIs on callers. The backend can wrap it
  to push into an anyio `MemoryObjectStream` if desired.
- **Default `None`**: existing callers are completely unaffected (no
  behavior change, no new imports required).
- The backend additionally emits run-level lifecycle events (created /
  cancelled / finished) around `execute_workflow`; the hook covers
  step-level only.

Placement (executor.py, after the existing `steps.append(...)`):

```python
        steps.append(StepResult(...))
        if on_step is not None:
            on_step(steps[-1])
```

### 2.2 Cancellation

Add an optional `cancel_event: asyncio.Event | None = None`. Semantics:

1. **Checked at each step boundary** — at the top of the loop, before a step
   starts (alongside the existing `abort` check):

```python
    for step in workflow.steps:
        if abort or (cancel_event is not None and cancel_event.is_set()):
            reason = ("run cancelled by user" if cancel_event is not None and cancel_event.is_set()
                      else "workflow aborted by prior step failure")
            steps.append(StepResult(step_id=step.id, server=step.server, tool=step.tool,
                                    args_resolved={}, status="skipped",
                                    error=reason))
            continue
```

2. **Best-effort, not mid-tool-call.** A stdio MCP tool call that is already
   in flight is not interrupted — it runs to completion (or its
   `timeout_s`). The flag is honored at the next step boundary. This is the
   honest contract for a sequential stdio executor and is documented as such.

3. **Terminal state.** After the loop, if the cancel flag was set (and no
   step failed), the execution status is `cancelled` rather than
   `succeeded`/`failed`:

```python
    if cancel_event is not None and cancel_event.is_set():
        execution_status = "cancelled"
    elif any(s.status == "failed" for s in steps):
        execution_status = "failed"
    else:
        execution_status = "succeeded"
```

4. **Persistence still runs.** The final `update_run(status="cancelled",
   message="cancelled by user")`, `_persist_execution` and
   `attach_artifact` all still execute — a cancelled run is a recorded,
   inspectable partial execution, not a dangling `running` row.

5. **External task cancellation safety.** Wrap the runstore-finalization
   block so that a true `CancelledError` (task cancelled from outside) also
   finalizes the row: `update_run(rid, status="cancelled", ...)` inside a
   `try/except CancelledError` in the `runstore_db` branch, re-raising after.
   Backward compatible; prevents orphaned `running` rows on backend crash or
   shutdown.

### 2.3 Backward compatibility & tests

- Both parameters default to `None`; all existing call sites and the MCP
  `run_workflow` tool are unchanged.
- New unit tests (executor tests):
  1. `on_step` called once per step with correct `StepResult` for a
     succeeded run, a failed run, and a skipped-step run.
  2. `on_step` not called when omitted (no-op path).
  3. Cancel flag set before step 2 → step 1 executes, steps 2..N `skipped`
     with `"run cancelled by user"`, execution status `cancelled`.
  4. Cancel flag never set → status `succeeded` (unchanged behavior).
  5. Runstore: cancelled run writes `status="cancelled"` and still produces
     `.execution.json` + artifact.
  6. External `CancelledError` mid-loop → runstore row finalized as
     `cancelled`, exception re-raised.

## 3. API surface (what the UI consumes)

The FastAPI backend (spec 16) wires these up:

- **SSE**: per-run broker subscribes via `on_step`; each call becomes an
  SSE event (`event: succeeded|failed|skipped`, `data: {step_id, tool,
  server, status, args_resolved, error, duration_ms}`).
- **Cancel**: `POST /runs/{run_id}/cancel` sets the run's
  `cancel_event`; the executor picks it up at the next boundary; the runstore
  row and SSE stream both report `cancelled`.

## 4. Scope guard

- No change to the workflow schema (`schema_version: 1`), the step
  semantics, or `run_workflow`'s response shape.
- No parallelism or branching is introduced (spec 16 §6.3 documents that the
  canvas must constrain to chain/toposort).
- The monorepo "no UI code" rule stands: this is engine capability, reviewed
  and merged upstream, independently unit-tested.
