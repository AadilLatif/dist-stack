"""Runstore-backed run tools: ``run_workflow``, ``get_run``, ``list_runs``.

``run_workflow`` requires a runstore (``DIST_STACK_RUNSTORE_DB`` env or
``config.runstore_db``); without one it returns a clean error payload so the
runner never crashes in a runstore-less deployment.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dist_stack.runstore.api import DEFAULT_ENV_VAR
from dist_stack.runstore import get_run as rs_get_run, list_artifacts as rs_list_artifacts
from dist_stack.runstore import RunRecord
from dist_stack.runstore import RunNotFoundError
from dist_stack.runstore import ensure_schema, make_run_id

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

from workflow_runner import __version__
from workflow_runner.executor import RUN_TOOL, execute_workflow
from workflow_runner.models import AppContext
from workflow_runner.templates import WorkflowError, load_workflow

RUNSTORE_REQUIRED_ERROR = (
    f"runstore required: set {DEFAULT_ENV_VAR} or config.runstore_db"
)


def _resolve_runstore(app: AppContext) -> str | None:
    db = app.config.runstore_db or os.getenv(DEFAULT_ENV_VAR)
    if not db:
        return None
    return str(Path(db).expanduser())


def _query_runs(
    runstore_db: str,
    *,
    status: str | None = None,
    workflow_id: str | None = None,
    limit: int = 100,
) -> list[RunRecord]:
    """Query runs with an optional ``workflow_id`` filter via JSON1.

    ``json_extract(payload, '$.workflow_id')`` is stdlib sqlite (JSON1 enabled
    by default). Newest first, mirroring ``dist_stack.runstore.list_runs``.
    """
    ensure_schema(runstore_db)
    clauses = ["deleted_at_utc IS NULL"]
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if workflow_id is not None:
        clauses.append("json_extract(payload, '$.workflow_id') = ?")
        params.append(workflow_id)
    limit = 100 if limit is None else max(0, int(limit))
    sql = (
        "SELECT * FROM runs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at_utc DESC, run_id DESC LIMIT ?"
    )
    params.append(limit)

    conn = sqlite3.connect(runstore_db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_run_record(r) for r in rows]


def _row_to_run_record(row: sqlite3.Row) -> RunRecord:
    payload: dict = {}
    raw = row["payload"]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError):
            pass
    return RunRecord(
        run_id=row["run_id"],
        tool=row["tool"],
        run_type=row["run_type"],
        status=row["status"],
        implementation=row["implementation"],
        message=row["message"],
        session_id=row["session_id"],
        tool_version=row["tool_version"],
        model_id=row["model_id"],
        model_version=row["model_version"],
        model_hash=row["model_hash"],
        payload=payload,
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
        deleted_at_utc=row["deleted_at_utc"],
    )


def _summarize_prior_graph(graph: dict) -> dict:
    """Strip step results from a persisted execution graph (size guard).

    The full record lives in the artifact on disk; the agent only needs the
    shape (what ran, against which server, with what args, and the outcome).
    ``error`` is kept when present, omitted when absent.
    """
    steps = []
    for step in graph.get("steps", []) or []:
        item = {
            "step_id": step.get("step_id"),
            "server": step.get("server"),
            "tool": step.get("tool"),
            "args_resolved": step.get("args_resolved"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        }
        if step.get("error") is not None:
            item["error"] = step["error"]
        steps.append(item)
    return {
        "workflow_id": graph.get("workflow_id"),
        "workflow_version": graph.get("workflow_version"),
        "source_prompt": graph.get("source_prompt"),
        "inputs_resolved": graph.get("inputs_resolved"),
        "status": graph.get("status"),
        "steps": steps,
    }


def _load_prior_graph(
    reuse_run_id: str,
    runstore_db: str,
    workflow_id: str,
) -> tuple[str | None, dict | None]:
    """Validate ``reuse_run_id`` and return ``(error, summarized prior graph)``.

    The prior run must exist, be a ``workflow_execution`` run in ``succeeded``
    status, expose an execution-graph artifact, and match ``workflow_id``.
    On any failure returns ``(message, None)``; on success ``(None, graph)``.
    """
    try:
        run = rs_get_run(reuse_run_id, runstore_db=runstore_db)
    except RunNotFoundError:
        return "reuse_run_id not found", None
    if run.run_type != "workflow_execution":
        return "reuse_run_id is not a workflow_execution run", None
    if run.status != "succeeded":
        return f"reuse_run_id has status {run.status}, not succeeded", None

    graph_path = None
    for artifact in rs_list_artifacts(reuse_run_id, runstore_db=runstore_db):
        if artifact.artifact_path.endswith(".execution.json"):
            graph_path = artifact.artifact_path
            break
    if graph_path is None:
        return "reuse_run_id has no execution-graph artifact", None
    try:
        graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"failed to read execution-graph artifact: {exc}", None
    if not isinstance(graph, dict) or graph.get("workflow_id") != workflow_id:
        return (
            f"reuse_run_id workflow_id {graph.get('workflow_id')!r} "
            f"does not match {workflow_id!r}",
            None,
        )
    return None, _summarize_prior_graph(graph)


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    async def run_workflow(
        ctx: Context[AppContext],
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
        reuse_run_id: str | None = None,
    ) -> str:
        """Run a workflow template synchronously.

        Sequential v1: creates a ``running`` run, executes each step against
        the configured domain servers, updates the run status, persists the
        execution-graph artifact (``wf_<hex12>.execution.json`` + manifest
        sidecar) and attaches it to the run.

        When ``reuse_run_id`` is provided, the prior run's execution graph is
        validated (must exist, be a succeeded ``workflow_execution`` for the
        same ``workflow_id``) and returned as ``prior_graph`` alongside the new
        run's result — step ``result`` values are omitted (size guard; the full
        record lives in the artifact on disk). No automatic rewriting: the
        agent decides whether to reuse/rewrite.

        Args:
            workflow_id: Id of the workflow template to run.
            inputs: Mapping of input name -> value.
            run_id: Optional caller-supplied run id; defaults to a minted
                ``wf_<hex12>``.
            reuse_run_id: Optional run id whose prior execution graph should be
                surfaced in the response as ``prior_graph``.

        Returns:
            JSON ``{"success", "run_id", "status", "outputs", "steps"}``, plus
            ``"prior_graph"`` when ``reuse_run_id`` is given and valid.
        """
        app: AppContext = ctx.request_context.lifespan_context
        runstore_db = _resolve_runstore(app)
        if not runstore_db:
            return json.dumps({"success": False, "error": RUNSTORE_REQUIRED_ERROR})
        try:
            workflow = load_workflow(workflow_id, workflow_dir=app.workflow_dir)
        except WorkflowError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        prior_graph: dict | None = None
        if reuse_run_id:
            error, prior_graph = _load_prior_graph(
                reuse_run_id, runstore_db, workflow_id
            )
            if error:
                return json.dumps({"success": False, "error": error})

        try:
            execution = await execute_workflow(
                workflow,
                inputs or {},
                app.pool,
                runstore_db=runstore_db,
                run_id=run_id,
                tool_version=__version__,
            )
        except ValueError as exc:  # input validation before any run row exists
            return json.dumps({"success": False, "error": str(exc)})
        except Exception as exc:
            return json.dumps(
                {"success": False, "run_id": run_id, "error": f"execution failed: {exc}"}
            )

        result = {
            "success": execution.status == "succeeded",
            "run_id": execution.run_id,
            "status": execution.status,
            "outputs": execution.outputs,
            "steps": [s.to_dict() for s in execution.steps],
        }
        if prior_graph is not None:
            result["prior_graph"] = prior_graph
        if execution.status != "succeeded":
            result["error"] = "; ".join(
                f"{s.step_id}: {s.error}" for s in execution.steps if s.status == "failed"
            ) or "workflow failed"
        return json.dumps(result)

    @mcp.tool()
    def get_run(ctx: Context[AppContext], run_id: str) -> str:
        """Get a run record plus its attached artifacts.

        Args:
            run_id: Run id (e.g. ``wf_<hex12>``).

        Returns:
            JSON ``{"success", "run", "artifacts"}``.
        """
        app: AppContext = ctx.request_context.lifespan_context
        runstore_db = _resolve_runstore(app)
        if not runstore_db:
            return json.dumps({"success": False, "error": RUNSTORE_REQUIRED_ERROR})
        try:
            run = rs_get_run(run_id, runstore_db=runstore_db)
        except RunNotFoundError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        artifacts = rs_list_artifacts(run_id, runstore_db=runstore_db)
        return json.dumps(
            {
                "success": True,
                "run": asdict(run),
                "artifacts": [asdict(a) for a in artifacts],
            }
        )

    @mcp.tool()
    def list_runs(
        ctx: Context[AppContext],
        status: str | None = None,
        workflow_id: str | None = None,
        limit: int = 100,
    ) -> str:
        """List runs, newest first, with optional filters.

        Args:
            status: Filter by run status (pending/running/succeeded/failed/cancelled).
            workflow_id: Filter by workflow_id via ``json_extract(payload,
                '$.workflow_id')`` (JSON1, stdlib sqlite).
            limit: Maximum number of runs to return (default 100).

        Returns:
            JSON array of run records.
        """
        app: AppContext = ctx.request_context.lifespan_context
        runstore_db = _resolve_runstore(app)
        if not runstore_db:
            return json.dumps({"success": False, "error": RUNSTORE_REQUIRED_ERROR})
        try:
            runs = _query_runs(
                runstore_db, status=status, workflow_id=workflow_id, limit=limit
            )
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})
        return json.dumps([asdict(r) for r in runs])
