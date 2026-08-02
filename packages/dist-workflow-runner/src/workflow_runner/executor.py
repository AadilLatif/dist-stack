"""Sequential workflow execution engine with runstore lifecycle support.

``execute_workflow`` runs the steps of a :class:`WorkflowSpec` in order against
a server pool (the real :class:`~workflow_runner.client.ServerPool` in
production, a ``FakePool`` in tests). When ``runstore_db`` is provided it also
drives the runstore lifecycle:

    ``create_run(status="running")`` → steps → ``update_run(status)`` →
    persist the execution-graph artifact + manifest sidecar → ``attach_artifact``

Substitution: ``${name}`` resolves against inputs plus prior step captures;
captures support dotted dict paths (``capture_var.key.subkey``). No
expressions or nested interpolation in v1.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dist_stack.manifest import write_manifest
from dist_stack.runstore import (
    attach_artifact,
    create_run,
    ensure_schema,
    make_run_id,
    update_run,
)

from .client import ServerError
from .models import StepResult, WorkflowExecution, WorkflowSpec

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")

RUN_TOOL = "run_workflow"
RUN_TYPE = "workflow_execution"
ARTIFACT_TYPE = "workflow_execution"
EXECUTION_SUFFIX = ".execution.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


def _lookup(name: str, env: dict[str, Any]) -> Any:
    """Resolve ``name`` (possibly dotted, e.g. ``capture_var.key.subkey``)."""
    parts = name.split(".")
    if not parts or parts[0] not in env:
        raise ValueError(f"unknown variable {name!r}")
    value = env[parts[0]]
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ValueError(f"cannot resolve dotted path {name!r}")
    return value


def _substitute_str(text: str, env: dict[str, Any]) -> Any:
    """Substitute a string value.

    A value that is *exactly* ``${name}`` (no surrounding text) keeps its
    runtime type; otherwise ``${name}`` is replaced with ``str(...)``.
    """
    m = _VAR_RE.fullmatch(text)
    if m:
        return _lookup(m.group(1), env)
    return _VAR_RE.sub(lambda mm: str(_lookup(mm.group(1), env)), text)


def _substitute(value: Any, env: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _substitute_str(value, env)
    if isinstance(value, dict):
        return {k: _substitute(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, env) for v in value]
    return value


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


def _resolve_inputs(workflow: WorkflowSpec, inputs: Any) -> dict[str, Any]:
    """Validate required inputs and return the resolved inputs mapping."""
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be a mapping of name -> value")
    resolved = dict(inputs)
    for inp in workflow.inputs:
        name = inp.get("name")
        if name is None:
            continue
        if inp.get("required", True) and name not in resolved:
            raise ValueError(f"missing required input {name!r}")
        if name not in resolved:
            resolved[name] = None
    return resolved


def _build_outputs(workflow: WorkflowSpec, env: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for out in workflow.outputs:
        name = out.get("name")
        source = out.get("from")
        if not name:
            continue
        try:
            outputs[name] = _lookup(source, env) if source else None
        except ValueError:
            outputs[name] = None
    return outputs


# ---------------------------------------------------------------------------
# Step assessment
# ---------------------------------------------------------------------------


def _assess_result(result: Any) -> tuple[str, str | None, Any]:
    """(status, error, value) for a decoded tool result."""
    if not isinstance(result, dict):
        return "succeeded", None, result
    if result.get("success") is False:
        error = result.get("error") or "step failed"
        return "failed", str(error), result
    return "succeeded", None, result


def _duration_ms(started_at: str, finished_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return max(0, int((finish - start).total_seconds() * 1000))
    except ValueError:
        return 0


def _failure_message(execution: WorkflowExecution) -> str | None:
    failed = [s for s in execution.steps if s.status == "failed"]
    if not failed:
        return None
    return "; ".join(
        f"{s.step_id} ({s.server}/{s.tool}): {s.error or 'failed'}" for s in failed
    )


# ---------------------------------------------------------------------------
# Execution-graph artifact persistence
# ---------------------------------------------------------------------------


def _persist_execution(
    execution: WorkflowExecution,
    runstore_db: str,
    *,
    tool_version: str | None = None,
) -> str:
    """Write ``<artifact-dir>/wf_<hex12>.execution.json`` + manifest sidecar.

    The artifact directory is the runstore DB's parent directory.
    """
    artifact_dir = Path(runstore_db).expanduser().resolve().parent
    hex12 = execution.run_id.rsplit("_", 1)[-1]
    path = artifact_dir / f"wf_{hex12}{EXECUTION_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(execution.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    write_manifest(
        path,
        artifact_type=ARTIFACT_TYPE,
        tool=RUN_TOOL,
        tool_version=tool_version,
        config={
            "workflow_id": execution.workflow_id,
            "workflow_version": execution.workflow_version,
            "run_id": execution.run_id,
        },
        derived_from=[execution.source_prompt] if execution.source_prompt else [],
    )
    return str(path)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _build_execution(
    workflow: WorkflowSpec,
    inputs_resolved: dict[str, Any],
    rid: str,
    execution_status: str,
    started_at: str,
    steps: list[StepResult],
    env: dict[str, Any],
) -> WorkflowExecution:
    """Assemble a :class:`WorkflowExecution` from the accumulated step results."""
    outputs = _build_outputs(workflow, env)
    return WorkflowExecution(
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        source_prompt=workflow.source_prompt,
        inputs_resolved=inputs_resolved,
        run_id=rid,
        status=execution_status,
        started_at_utc=started_at,
        finished_at_utc=_now(),
        steps=steps,
        outputs=outputs,
    )


def _finalize_runstore(
    rid: str,
    execution: WorkflowExecution,
    payload: dict[str, Any],
    runstore_db: str,
    *,
    tool_version: str | None = None,
) -> None:
    """Write the terminal runstore state: update_run + artifact + manifest + attach.

    A ``cancelled`` execution is recorded with ``message="cancelled by user"``;
    any other terminal state uses the failure-message text (``None`` when clean).
    """
    if execution.status == "cancelled":
        message = "cancelled by user"
    else:
        message = _failure_message(execution)
    update_run(
        rid,
        status=execution.status,
        message=message,
        payload={**payload, "status": execution.status},
        runstore_db=runstore_db,
    )
    artifact_path = _persist_execution(execution, runstore_db, tool_version=tool_version)
    attach_artifact(rid, artifact_path, runstore_db=runstore_db)


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
    """Execute ``workflow`` sequentially against ``pool``.

    ``runstore_db``: when provided, the full runstore lifecycle (create_run →
    update_run → artifact + manifest → attach_artifact) is driven; the returned
    :class:`WorkflowExecution` carries the persisted ``run_id``.

    ``on_step``: optional synchronous callback invoked once per step, immediately
    after the :class:`StepResult` is appended, for every status the executor can
    produce (``succeeded``/``failed``/``skipped``). Default ``None`` — existing
    callers are completely unaffected.

    ``cancel_event``: optional cancellation flag. It is checked at each step
    boundary, before a step starts. When set, every remaining step is recorded as
    ``skipped`` with ``error="run cancelled by user"`` and the execution status
    becomes ``cancelled``. Cancelled takes precedence over both ``succeeded`` and
    ``failed`` when the flag is set (a prior step failure does not downgrade a
    user cancellation). Cancellation is best-effort: an in-flight tool call is
    NOT interrupted — the flag is honored at the next step boundary. A cancelled
    run is still finalized to the runstore (``update_run(status="cancelled")`` +
    execution artifact), so it is a recorded partial execution, not a dangling
    ``running`` row.

    If the surrounding task is cancelled externally (``asyncio.CancelledError``)
    mid-run, the partial execution is finalized to the runstore as ``cancelled``
    and the exception is re-raised.

    Raises :class:`ValueError` for input-validation errors (before any runstore
    row is created). Step failures are never raised — they are recorded in the
    steps and reflected in ``execution.status``.
    """
    inputs_resolved = _resolve_inputs(workflow, inputs)
    started_at = _now()
    rid = run_id or make_run_id("wf")
    payload = {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.version,
        "inputs": dict(inputs_resolved),
    }

    if runstore_db:
        ensure_schema(runstore_db)
        create_run(
            RUN_TOOL,
            run_type=RUN_TYPE,
            run_id=rid,
            status="running",
            tool_version=tool_version,
            payload=payload,
            runstore_db=runstore_db,
        )

    env: dict[str, Any] = dict(inputs_resolved)
    steps: list[StepResult] = []
    abort = False

    try:
        for step in workflow.steps:
            cancelled = cancel_event is not None and cancel_event.is_set()
            if abort or cancelled:
                reason = (
                    "run cancelled by user"
                    if cancelled
                    else "workflow aborted by prior step failure"
                )
                step_result = StepResult(
                    step_id=step.id,
                    server=step.server,
                    tool=step.tool,
                    args_resolved={},
                    status="skipped",
                    error=reason,
                )
                steps.append(step_result)
                if on_step is not None:
                    on_step(step_result)
                continue

            step_started = _now()
            args_resolved: dict[str, Any] = {}
            try:
                args_resolved = _substitute(step.args, env)
                result = await pool.call_tool(step.server, step.tool, args_resolved)
                status, error, value = _assess_result(result)
            except ValueError as exc:  # substitution failure (unknown/dotted var)
                status, error, value = "failed", f"argument substitution failed: {exc}", None
            except ServerError as exc:
                status, error, value = "failed", str(exc), None

            step_finished = _now()
            step_result = StepResult(
                step_id=step.id,
                server=step.server,
                tool=step.tool,
                args_resolved=args_resolved,
                status=status,
                error=error,
                result=value,
                started_at_utc=step_started,
                finished_at_utc=step_finished,
                duration_ms=_duration_ms(step_started, step_finished),
            )
            steps.append(step_result)
            if on_step is not None:
                on_step(step_result)
            if status == "failed":
                if step.on_failure == "fail":
                    abort = True
            elif step.capture:
                env[step.capture] = value
    except asyncio.CancelledError:
        # A true external cancellation (task.cancel()) also records the partial
        # run as a real 'cancelled' row + artifact instead of leaving it dangling.
        if runstore_db:
            execution = _build_execution(
                workflow, inputs_resolved, rid, "cancelled", started_at, steps, env
            )
            _finalize_runstore(rid, execution, payload, runstore_db, tool_version=tool_version)
        raise

    # Cancelled wins over both failed and succeeded when the flag is set.
    if cancel_event is not None and cancel_event.is_set():
        execution_status = "cancelled"
    elif any(s.status == "failed" for s in steps):
        execution_status = "failed"
    else:
        execution_status = "succeeded"

    execution = _build_execution(
        workflow, inputs_resolved, rid, execution_status, started_at, steps, env
    )

    if runstore_db:
        _finalize_runstore(rid, execution, payload, runstore_db, tool_version=tool_version)

    return execution
