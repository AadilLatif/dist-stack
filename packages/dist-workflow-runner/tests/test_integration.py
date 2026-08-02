"""End-to-end integration: ``run_workflow`` → real fake_server subprocess →
runstore rows + execution-graph artifact + manifest sidecar + artifacts row.

Each scenario binds the pool to a task group (as the production lifespan does)
and runs inside a SINGLE event loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import anyio

from dist_stack.runstore import get_run, list_artifacts

from conftest import FAKE_SERVER_PATH, MockContext
from workflow_runner.client import ServerPool
from workflow_runner.models import AppContext, RunnerConfig, ServerSpec
from workflow_runner.server import create_server

EXECUTION_GRAPH_KEYS = {
    "workflow_id",
    "workflow_version",
    "source_prompt",
    "inputs_resolved",
    "run_id",
    "status",
    "started_at_utc",
    "finished_at_utc",
    "steps",
    "outputs",
}
STEP_KEYS = {
    "step_id",
    "server",
    "tool",
    "args_resolved",
    "status",
    "error",
    "result",
    "started_at_utc",
    "finished_at_utc",
    "duration_ms",
}


def _write_workflow(workflow_dir: Path, wf: dict) -> None:
    (workflow_dir / f"{wf['workflow_id']}.json").write_text(
        json.dumps(wf, indent=2) + "\n", encoding="utf-8"
    )


def _pool() -> ServerPool:
    return ServerPool(
        [
            ServerSpec(
                name="fake_server",
                command=sys.executable,
                args=[str(FAKE_SERVER_PATH)],
            )
        ]
    )


def _app(runstore_db: Path, workflow_dir: Path, pool: ServerPool) -> AppContext:
    config = RunnerConfig(
        runstore_db=str(runstore_db),
        workflow_dir=str(workflow_dir),
        servers=list(pool._specs.values()),
    )
    return AppContext(config=config, pool=pool)


def run(coro):
    return asyncio.run(coro)


SUCCESS_WORKFLOW = {
    "schema_version": 1,
    "workflow_id": "integration_flow",
    "version": "1.0.0",
    "name": "Integration flow",
    "description": "Exercise the full runner path against the fake server.",
    "source_prompt": "fake://integration@1",
    "inputs": [
        {"name": "greeting", "type": "string", "required": True},
        {"name": "x", "type": "number", "required": True},
        {"name": "y", "type": "number", "required": True},
    ],
    "steps": [
        {
            "id": "step_1",
            "server": "fake_server",
            "tool": "echo",
            "args": {"text": "${greeting}"},
            "capture": "echoed",
            "on_failure": "fail",
        },
        {
            "id": "step_2",
            "server": "fake_server",
            "tool": "add",
            "args": {"a": "${x}", "b": "${y}"},
            "capture": "sum",
            "on_failure": "fail",
        },
        {
            "id": "step_3",
            "server": "fake_server",
            "tool": "echo",
            "args": {"text": "${echoed.text}"},
            "capture": "echoed_again",
            "on_failure": "fail",
        },
    ],
    "outputs": [
        {"name": "sum", "from": "sum"},
        {"name": "echoed", "from": "echoed"},
        {"name": "echoed_again", "from": "echoed_again"},
    ],
}


def test_end_to_end_success(tmp_path):
    runstore_db = tmp_path / "runstore.db"
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    _write_workflow(workflow_dir, SUCCESS_WORKFLOW)

    run_workflow = create_server()._tool_manager._tools["run_workflow"].fn

    async def scenario():
        pool = _pool()
        ctx = MockContext(_app(runstore_db, workflow_dir, pool))
        async with anyio.create_task_group() as tg:
            pool.start(tg)
            try:
                result = json.loads(
                    await run_workflow(
                        ctx,
                        workflow_id="integration_flow",
                        inputs={"greeting": "hello", "x": 3, "y": 4},
                    )
                )
            finally:
                await pool.close_all()
        return result

    result = run(scenario())

    # -- tool result ----------------------------------------------------------
    assert result["success"] is True
    rid = result["run_id"]
    assert rid.startswith("wf_")
    assert result["status"] == "succeeded"
    assert result["outputs"]["echoed"]["text"] == "hello"
    assert result["outputs"]["sum"]["sum"] == 7.0
    # dotted capture: ${echoed.text} resolved from the prior capture
    assert result["outputs"]["echoed_again"]["text"] == "hello"
    assert len(result["steps"]) == 3
    assert result["steps"][0]["status"] == "succeeded"

    # -- runs row -------------------------------------------------------------
    rec = get_run(rid, runstore_db=str(runstore_db))
    assert rec.status == "succeeded"
    assert rec.tool == "run_workflow"
    assert rec.run_type == "workflow_execution"
    assert rec.payload["workflow_id"] == "integration_flow"
    assert rec.payload["workflow_version"] == "1.0.0"
    assert rec.created_at_utc and rec.updated_at_utc

    # -- artifacts row --------------------------------------------------------
    artifacts = list_artifacts(rid, runstore_db=str(runstore_db))
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.artifact_type == "workflow_execution"
    assert art.tool == "run_workflow"

    # -- execution-graph artifact ----------------------------------------------
    artifact_path = Path(art.artifact_path)
    assert artifact_path.is_file()
    assert artifact_path.name == f"{rid}.execution.json"
    graph = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert set(graph.keys()) == EXECUTION_GRAPH_KEYS
    assert graph["run_id"] == rid
    assert graph["workflow_id"] == "integration_flow"
    assert graph["workflow_version"] == "1.0.0"
    assert graph["source_prompt"] == "fake://integration@1"
    assert graph["status"] == "succeeded"
    assert graph["inputs_resolved"] == {"greeting": "hello", "x": 3, "y": 4}
    assert len(graph["steps"]) == 3
    step = graph["steps"][0]
    assert set(step.keys()) == STEP_KEYS
    assert step["tool"] == "echo"
    assert step["server"] == "fake_server"
    assert step["args_resolved"] == {"text": "hello"}
    assert step["status"] == "succeeded"
    assert step["result"]["text"] == "hello"
    assert step["duration_ms"] is not None
    assert graph["outputs"]["echoed"]["text"] == "hello"
    assert graph["outputs"]["sum"]["sum"] == 7.0

    # -- manifest sidecar ------------------------------------------------------
    manifest_path = Path(str(artifact_path) + ".manifest.json")
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "workflow_execution"
    assert manifest["tool"] == "run_workflow"
    assert manifest["config"] == {
        "workflow_id": "integration_flow",
        "workflow_version": "1.0.0",
        "run_id": rid,
    }
    assert manifest["derived_from"] == ["fake://integration@1"]
    assert manifest["artifact_path"] == str(artifact_path)


def test_end_to_end_failure_aborts(tmp_path):
    runstore_db = tmp_path / "runstore.db"
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    wf = {
        "schema_version": 1,
        "workflow_id": "boom_flow",
        "version": "1.0.0",
        "name": "Boom",
        "description": "First step fails; second step must be skipped.",
        "source_prompt": "fake://boom@1",
        "inputs": [],
        "steps": [
            {
                "id": "s1",
                "server": "fake_server",
                "tool": "fail_on_demand",
                "args": {"note": "kaput"},
                "on_failure": "fail",
            },
            {
                "id": "s2",
                "server": "fake_server",
                "tool": "echo",
                "args": {"text": "never runs"},
                "on_failure": "fail",
            },
        ],
    }
    _write_workflow(workflow_dir, wf)

    run_workflow = create_server()._tool_manager._tools["run_workflow"].fn

    async def scenario():
        pool = _pool()
        ctx = MockContext(_app(runstore_db, workflow_dir, pool))
        async with anyio.create_task_group() as tg:
            pool.start(tg)
            try:
                result = json.loads(await run_workflow(ctx, workflow_id="boom_flow", inputs={}))
            finally:
                await pool.close_all()
        return result

    result = run(scenario())

    assert result["success"] is False
    rid = result["run_id"]
    assert result["status"] == "failed"
    assert "kaput" in result["error"]

    rec = get_run(rid, runstore_db=str(runstore_db))
    assert rec.status == "failed"
    assert "kaput" in (rec.message or "")

    artifact_path = Path(list_artifacts(rid, runstore_db=str(runstore_db))[0].artifact_path)
    graph = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert graph["status"] == "failed"
    assert [s["status"] for s in graph["steps"]] == ["failed", "skipped"]


def test_end_to_end_runstore_env_var(tmp_path, monkeypatch):
    """runstore_db resolved from DIST_STACK_RUNSTORE_DB when config omits it."""
    runstore_db = tmp_path / "env-runstore.db"
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    _write_workflow(workflow_dir, SUCCESS_WORKFLOW)

    monkeypatch.setenv("DIST_STACK_RUNSTORE_DB", str(runstore_db))
    run_workflow = create_server()._tool_manager._tools["run_workflow"].fn

    async def scenario():
        pool = _pool()
        config = RunnerConfig(
            runstore_db=None,  # config omits it; env var wins
            workflow_dir=str(workflow_dir),
            servers=list(pool._specs.values()),
        )
        ctx = MockContext(AppContext(config=config, pool=pool))
        async with anyio.create_task_group() as tg:
            pool.start(tg)
            try:
                result = json.loads(
                    await run_workflow(
                        ctx,
                        workflow_id="integration_flow",
                        inputs={"greeting": "hi", "x": 1, "y": 2},
                    )
                )
            finally:
                await pool.close_all()
        return result

    result = run(scenario())

    assert result["success"] is True
    assert get_run(result["run_id"], runstore_db=str(runstore_db)).status == "succeeded"
