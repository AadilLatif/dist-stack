"""Tool-level tests: direct function calls (doc-10 pattern) on all 7 tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dist_stack.runstore import get_run, list_artifacts

from conftest import MockContext
from workflow_runner.models import AppContext, RunnerConfig, ServerSpec
from workflow_runner.server import create_server


def _fn(name: str):
    mcp = create_server()
    return mcp._tool_manager._tools[name].fn


def _app(pool, runstore_db=None, workflow_dir=None) -> AppContext:
    config = RunnerConfig(
        runstore_db=str(runstore_db) if runstore_db else None,
        workflow_dir=str(workflow_dir) if workflow_dir else None,
        servers=[ServerSpec(name="fake_server", command="python")],
    )
    return AppContext(config=config, pool=pool)


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
                }
            ],
            "outputs": [{"name": "echoed", "from": "echoed"}],
        }
    )


class TestServersTools:
    def test_list_servers(self, fake_pool, app_ctx):
        app_ctx.pool = fake_pool
        result = json.loads(run(_fn("list_servers")(MockContext(app_ctx))))
        assert result == [
            {
                "name": "fake_server",
                "status": "connected",
                "error": None,
                "tool_count": 5,
                "server_version": "0.0.0-test",
            }
        ]

    def test_list_servers_unavailable(self, fake_pool, app_ctx):
        fake_pool.add_server("ghost", {"echo": lambda text: {"success": True}})
        fake_pool.connect_errors.add("ghost")
        app_ctx.pool = fake_pool
        result = json.loads(run(_fn("list_servers")(MockContext(app_ctx))))
        by_name = {e["name"]: e for e in result}
        assert by_name["ghost"]["status"] == "unavailable"
        assert "spawn failure" in by_name["ghost"]["error"]
        assert by_name["fake_server"]["status"] == "connected"

    def test_list_tools(self, fake_pool, app_ctx):
        app_ctx.pool = fake_pool
        result = json.loads(run(_fn("list_tools")(MockContext(app_ctx), server="fake_server")))
        assert {t["name"] for t in result} == {
            "echo",
            "add",
            "fail_on_demand",
            "get_system_summary",
            "run_ac_pf",
        }

    def test_list_tools_unknown_server(self, fake_pool, app_ctx):
        app_ctx.pool = fake_pool
        result = json.loads(run(_fn("list_tools")(MockContext(app_ctx), server="nope")))
        assert result["success"] is False
        assert "nope" in result["error"]


class TestWorkflowsTools:
    def test_create_workflow(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        result = json.loads(_fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json()))
        assert result["success"] is True
        assert result["workflow_id"] == "demo_flow"
        assert (workflow_dir / "demo_flow.json").is_file()

    def test_create_workflow_invalid(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        result = json.loads(
            _fn("create_workflow")(MockContext(app_ctx), workflow_json=json.dumps({"nope": 1}))
        )
        assert result["success"] is False

    def test_get_workflow(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json())
        result = json.loads(_fn("get_workflow")(MockContext(app_ctx), workflow_id="demo_flow"))
        assert result["success"] is True
        assert result["workflow"]["workflow_id"] == "demo_flow"
        assert result["workflow"]["steps"][0]["tool"] == "echo"

    def test_get_workflow_missing(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        result = json.loads(_fn("get_workflow")(MockContext(app_ctx), workflow_id="nope"))
        assert result["success"] is False

    def test_list_workflows(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json("a"))
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json("b"))
        result = json.loads(_fn("list_workflows")(MockContext(app_ctx)))
        assert [w["workflow_id"] for w in result] == ["a", "b"]
        assert all("step_count" in w and "source_prompt" in w for w in result)


class TestRunsTools:
    def test_run_workflow(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(
            runstore_db=str(runstore_db),
            workflow_dir=str(workflow_dir),
            servers=app_ctx.config.servers,
        )
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json())
        result = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        assert result["success"] is True
        assert result["status"] == "succeeded"
        assert result["run_id"].startswith("wf_")
        assert result["outputs"]["echoed"]["text"] == "hi"
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert set(step.keys()) == {
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

    def test_run_workflow_requires_runstore(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json())
        result = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        assert result["success"] is False
        assert result["error"] == "runstore required: set DIST_STACK_RUNSTORE_DB or config.runstore_db"

    def test_run_workflow_failed_status(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(
            runstore_db=str(runstore_db),
            workflow_dir=str(workflow_dir),
            servers=app_ctx.config.servers,
        )
        failing = json.dumps(
            {
                "schema_version": 1,
                "workflow_id": "failing",
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
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=failing)
        result = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="failing", inputs={}))
        )
        assert result["success"] is False
        assert result["status"] == "failed"
        assert "deliberate failure" in result["error"]

    def test_get_run(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(
            runstore_db=str(runstore_db),
            workflow_dir=str(workflow_dir),
            servers=app_ctx.config.servers,
        )
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json())
        res = json.loads(
            run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="demo_flow", inputs={"greeting": "hi"}))
        )
        rid = res["run_id"]
        got = json.loads(_fn("get_run")(MockContext(app_ctx), run_id=rid))
        assert got["success"] is True
        assert got["run"]["run_id"] == rid
        assert got["run"]["tool"] == "run_workflow"
        assert got["run"]["run_type"] == "workflow_execution"
        assert got["run"]["payload"]["workflow_id"] == "demo_flow"
        assert len(got["artifacts"]) == 1
        assert got["artifacts"][0]["artifact_type"] == "artifact"

    def test_list_runs_filters(self, fake_pool, app_ctx, workflow_dir, runstore_db):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(
            runstore_db=str(runstore_db),
            workflow_dir=str(workflow_dir),
            servers=app_ctx.config.servers,
        )
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json("alpha"))
        _fn("create_workflow")(MockContext(app_ctx), workflow_json=_demo_workflow_json("beta"))
        run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="alpha", inputs={"greeting": "a"}))
        run(_fn("run_workflow")(MockContext(app_ctx), workflow_id="beta", inputs={"greeting": "b"}))

        all_runs = json.loads(_fn("list_runs")(MockContext(app_ctx)))
        assert len(all_runs) == 2
        assert all(r["tool"] == "run_workflow" for r in all_runs)

        by_wf = json.loads(_fn("list_runs")(MockContext(app_ctx), workflow_id="alpha"))
        assert len(by_wf) == 1
        assert by_wf[0]["payload"]["workflow_id"] == "alpha"

        by_status = json.loads(_fn("list_runs")(MockContext(app_ctx), status="succeeded"))
        assert len(by_status) == 2

        by_status_fail = json.loads(_fn("list_runs")(MockContext(app_ctx), status="failed"))
        assert by_status_fail == []

    def test_list_runs_requires_runstore(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        result = json.loads(_fn("list_runs")(MockContext(app_ctx)))
        assert result["success"] is False
        assert "runstore required" in result["error"]

    def test_get_run_requires_runstore(self, fake_pool, app_ctx, workflow_dir):
        app_ctx.pool = fake_pool
        app_ctx.config = RunnerConfig(workflow_dir=str(workflow_dir), servers=app_ctx.config.servers)
        result = json.loads(_fn("get_run")(MockContext(app_ctx), run_id="wf_x"))
        assert result["success"] is False
        assert "runstore required" in result["error"]


class TestToolSurface:
    def test_exactly_eight_tools(self):
        mcp = create_server()
        names = sorted(mcp._tool_manager._tools.keys())
        assert names == [
            "create_workflow",
            "get_run",
            "get_workflow",
            "list_runs",
            "list_servers",
            "list_tools",
            "list_workflows",
            "run_workflow",
        ]
