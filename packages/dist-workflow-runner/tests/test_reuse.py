"""Tests for the ``reuse_run_id`` hook on ``run_workflow`` (doc 12 §D).

Direct function calls with FakePool + a tmp runstore DB, matching the doc-10
pattern in ``test_tools.py``.
"""

from __future__ import annotations

import asyncio
import json

from dist_stack.runstore import create_run

from conftest import MockContext
from workflow_runner.models import AppContext, RunnerConfig
from workflow_runner.server import create_server


def _fn(name: str):
    mcp = create_server()
    return mcp._tool_manager._tools[name].fn


def run(coro):
    return asyncio.run(coro)


def _demo_workflow_json(workflow_id: str = "demo_flow") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "version": "1.0.0",
            "name": "Demo",
            "description": "demo",
            "source_prompt": "fake://demo@1",
            "inputs": [{"name": "greeting", "type": "string", "required": True}],
            "steps": [
                {
                    "id": "s1",
                    "server": "fake_server",
                    "tool": "echo",
                    "args": {"text": "${greeting}"},
                    "capture": "echoed",
                    "on_failure": "fail",
                },
                {
                    "id": "s2",
                    "server": "fake_server",
                    "tool": "add",
                    "args": {"a": 1, "b": 2},
                    "on_failure": "fail",
                },
            ],
            "outputs": [{"name": "echoed", "from": "echoed"}],
        }
    )


def _failing_workflow_json() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "workflow_id": "failing_flow",
            "version": "1.0.0",
            "name": "Failing",
            "description": "x",
            "source_prompt": "fake://f@1",
            "inputs": [],
            "steps": [
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "fail"}
            ],
        }
    )


def _setup(app_ctx: AppContext, fake_pool, workflow_dir, runstore_db, workflow_json: str) -> None:
    app_ctx.pool = fake_pool
    app_ctx.config = RunnerConfig(
        runstore_db=str(runstore_db),
        workflow_dir=str(workflow_dir),
        servers=app_ctx.config.servers,
    )
    created = json.loads(_fn("create_workflow")(MockContext(app_ctx), workflow_json=workflow_json))
    assert created["success"] is True


class TestReuseRunId:
    def test_reuse_happy_path(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _demo_workflow_json())
        first = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        assert first["success"] is True
        rid = first["run_id"]

        second = json.loads(
            run(
                _fn("run_workflow")(
                    MockContext(app_ctx),
                    workflow_id="demo_flow",
                    inputs={"greeting": "hi"},
                    reuse_run_id=rid,
                )
            )
        )
        assert second["success"] is True
        assert second["run_id"] != rid

        prior = second["prior_graph"]
        assert prior["workflow_id"] == "demo_flow"
        assert prior["workflow_version"] == "1.0.0"
        assert prior["source_prompt"] == "fake://demo@1"
        assert prior["status"] == "succeeded"
        assert prior["inputs_resolved"] == {"greeting": "hi"}
        assert len(prior["steps"]) == 2

        step = prior["steps"][0]
        assert step == {
            "step_id": "s1",
            "server": "fake_server",
            "tool": "echo",
            "args_resolved": {"text": "hi"},
            "status": "succeeded",
            "duration_ms": step["duration_ms"],
        }
        # results (and timestamps) are omitted — size guard
        assert "result" not in step
        assert "started_at_utc" not in step
        assert "finished_at_utc" not in step
        assert step["duration_ms"] >= 0

    def test_reuse_wrong_workflow_id(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _demo_workflow_json())
        first = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        rid = first["run_id"]

        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json("other_flow"))
        result = json.loads(
            run(
                _fn("run_workflow")(
                    MockContext(app_ctx),
                    workflow_id="other_flow",
                    inputs={"greeting": "hi"},
                    reuse_run_id=rid,
                )
            )
        )
        assert result["success"] is False
        assert "does not match" in result["error"]

    def test_reuse_non_execution_run(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _demo_workflow_json())
        create_run(
            "gdm_flow",
            run_type="gdm_flow_run",
            run_id="gdm_test_00000001",
            status="succeeded",
            runstore_db=str(runstore_db),
        )
        result = json.loads(
            run(
                _fn("run_workflow")(
                    MockContext(app_ctx),
                    workflow_id="demo_flow",
                    inputs={"greeting": "hi"},
                    reuse_run_id="gdm_test_00000001",
                )
            )
        )
        assert result["success"] is False
        assert result["error"] == "reuse_run_id is not a workflow_execution run"

    def test_reuse_failed_run(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _failing_workflow_json())
        failed = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="failing_flow", inputs={}))
        )
        assert failed["success"] is False
        rid = failed["run_id"]

        result = json.loads(
            run(
                _fn("run_workflow")(
                    MockContext(app_ctx),
                    workflow_id="failing_flow",
                    inputs={},
                    reuse_run_id=rid,
                )
            )
        )
        assert result["success"] is False
        assert result["error"] == "reuse_run_id has status failed, not succeeded"

    def test_reuse_unknown_run_id(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _demo_workflow_json())
        result = json.loads(
            run(
                _fn("run_workflow")(
                    MockContext(app_ctx),
                    workflow_id="demo_flow",
                    inputs={"greeting": "hi"},
                    reuse_run_id="wf_nope0000000000",
                )
            )
        )
        assert result["success"] is False
        assert result["error"] == "reuse_run_id not found"

    def test_reuse_unset(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        _setup(app_ctx, fake_pool, workflow_dir, runstore_db, _demo_workflow_json())
        result = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        assert result["success"] is True
        assert "prior_graph" not in result
