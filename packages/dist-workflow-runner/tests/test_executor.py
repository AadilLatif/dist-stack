"""Executor tests: substitution, capture, on_failure policies, timeout,
runstore lifecycle — all via the FakePool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dist_stack.runstore import get_run, list_artifacts

from workflow_runner.client import ToolCallTimeout
from workflow_runner.executor import execute_workflow
from workflow_runner.models import WorkflowSpec, WorkflowStep
from workflow_runner.templates import validate_workflow


def _wf(steps, inputs=None, outputs=None, **kw) -> WorkflowSpec:
    return validate_workflow(
        {
            "schema_version": 1,
            "workflow_id": kw.get("workflow_id", "exec_flow"),
            "version": "1.0.0",
            "name": "Exec flow",
            "description": "test",
            "source_prompt": kw.get("source_prompt", "fake://test@1"),
            "inputs": inputs or [{"name": "greeting", "type": "string", "required": True}],
            "steps": steps,
            "outputs": outputs or [],
        }
    )


def run(coro):
    return asyncio.run(coro)


class TestSubstitution:
    def test_input_substitution_and_capture(self, fake_pool):
        wf = _wf(
            steps=[
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
                    "tool": "echo",
                    "args": {"text": "got ${echoed.text}"},
                    "capture": "echoed2",
                    "on_failure": "fail",
                },
            ],
            outputs=[{"name": "final", "from": "echoed2"}],
        )
        execution = run(execute_workflow(wf, {"greeting": "hello"}, fake_pool))
        assert execution.status == "succeeded"
        assert [s.status for s in execution.steps] == ["succeeded", "succeeded"]
        assert execution.steps[0].result == {"success": True, "text": "hello"}
        assert execution.outputs["final"] == {"success": True, "text": "got hello"}

    def test_dotted_capture_path(self, fake_pool):
        """A prior capture's dict values are reachable via ``capture.key``."""
        wf = _wf(
            steps=[
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
                    "tool": "echo",
                    "args": {"text": "${echoed.text}"},
                    "on_failure": "fail",
                },
            ],
        )
        execution = run(execute_workflow(wf, {"greeting": "hello"}, fake_pool))
        assert execution.status == "succeeded"
        args = execution.steps[1].args_resolved
        assert args == {"text": "hello"}

    def test_capture_json_decode(self, fake_pool):
        """A capture stores the decoded dict, not the raw JSON string."""
        wf = _wf(
            steps=[
                {
                    "id": "s1",
                    "server": "fake_server",
                    "tool": "run_ac_pf",
                    "args": {"system_path": "${greeting}"},
                    "capture": "pf",
                    "on_failure": "fail",
                }
            ],
            outputs=[{"name": "vmag", "from": "pf.vmag"}],
        )
        execution = run(execute_workflow(wf, {"greeting": "/tmp/sys.json"}, fake_pool))
        assert execution.status == "succeeded"
        assert execution.steps[0].result["vmag"] == [1.0, 0.98]
        assert execution.outputs["vmag"] == [1.0, 0.98]

    def test_unknown_variable_fails_step(self, fake_pool):
        wf = _wf(
            steps=[
                {
                    "id": "s1",
                    "server": "fake_server",
                    "tool": "echo",
                    "args": {"text": "${nope}"},
                    "on_failure": "fail",
                }
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "hi"}, fake_pool))
        assert execution.status == "failed"
        assert "unknown variable" in execution.steps[0].error

    def test_missing_required_input_raises(self, fake_pool):
        wf = _wf(steps=[{"id": "s1", "server": "fake_server", "tool": "echo", "args": {}}])
        with pytest.raises(ValueError, match="missing required input"):
            run(execute_workflow(wf, {}, fake_pool))


class TestOnFailure:
    def test_fail_policy_aborts(self, fake_pool):
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "never"}, "on_failure": "fail"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, fake_pool))
        assert execution.status == "failed"
        assert execution.steps[0].status == "failed"
        assert execution.steps[1].status == "skipped"
        assert "deliberate failure" in execution.steps[0].error
        assert execution.outputs == {}

    def test_continue_policy_proceeds(self, fake_pool):
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {"note": "oops"}, "on_failure": "continue"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "still runs"}, "capture": "echoed2", "on_failure": "continue"},
            ],
            outputs=[{"name": "echoed", "from": "echoed2"}],
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, fake_pool))
        assert execution.status == "failed"  # a step failed, but all steps ran
        assert [s.status for s in execution.steps] == ["failed", "succeeded"]
        assert execution.steps[1].result["text"] == "still runs"
        assert execution.outputs["echoed"]["text"] == "still runs"


class TestTimeout:
    def test_timeout_marks_step_failed(self):
        from conftest import FakePool

        pool = FakePool(default_timeout=0.05)
        pool.add_server(
            "fake_server",
            {"slow": lambda: asyncio.sleep(5)},
        )
        wf = _wf(
            steps=[{"id": "s1", "server": "fake_server", "tool": "slow", "args": {}, "on_failure": "fail"}]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, pool))
        assert execution.status == "failed"
        assert "timed out" in execution.steps[0].error

    def test_call_tool_raises_timeout(self):
        from conftest import FakePool

        pool = FakePool(default_timeout=0.05)
        pool.add_server(
            "fake_server",
            {"slow": lambda: asyncio.sleep(5)},
        )
        with pytest.raises(ToolCallTimeout):
            run(pool.call_tool("fake_server", "slow", {}))


class TestRunstoreLifecycle:
    def test_full_lifecycle(self, fake_pool, runstore_db):
        """create_run(running) → update_run → artifact + manifest → attach."""
        wf = _wf(
            steps=[
                {
                    "id": "s1",
                    "server": "fake_server",
                    "tool": "echo",
                    "args": {"text": "${greeting}"},
                    "capture": "echoed",
                    "on_failure": "fail",
                }
            ],
            outputs=[{"name": "out", "from": "echoed"}],
            workflow_id="lifecycle",
        )
        execution = run(
            execute_workflow(
                wf,
                {"greeting": "hi"},
                fake_pool,
                runstore_db=str(runstore_db),
                run_id="wf_life0000000001",
                tool_version="0.1.0",
            )
        )
        assert execution.status == "succeeded"
        assert execution.run_id == "wf_life0000000001"

        rec = get_run("wf_life0000000001", runstore_db=str(runstore_db))
        assert rec.status == "succeeded"
        assert rec.tool == "run_workflow"
        assert rec.run_type == "workflow_execution"
        assert rec.tool_version == "0.1.0"
        assert rec.payload["workflow_id"] == "lifecycle"
        assert rec.payload["status"] == "succeeded"
        assert rec.created_at_utc is not None
        assert rec.updated_at_utc is not None

        artifacts = list_artifacts("wf_life0000000001", runstore_db=str(runstore_db))
        assert len(artifacts) == 1
        artifact = Path(artifacts[0].artifact_path)
        assert artifact.name == "wf_life0000000001.execution.json"
        assert artifact.is_file()
        graph = json.loads(artifact.read_text())
        assert graph["run_id"] == "wf_life0000000001"
        assert graph["status"] == "succeeded"
        assert graph["outputs"]["out"]["text"] == "hi"
        assert Path(str(artifact) + ".manifest.json").is_file()

    def test_failed_run_status_and_message(self, fake_pool, runstore_db):
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "fail"}
            ],
            workflow_id="failing",
        )
        execution = run(
            execute_workflow(
                wf,
                {"greeting": "x"},
                fake_pool,
                runstore_db=str(runstore_db),
                run_id="wf_fail0000000001",
                tool_version="0.1.0",
            )
        )
        assert execution.status == "failed"
        rec = get_run("wf_fail0000000001", runstore_db=str(runstore_db))
        assert rec.status == "failed"
        assert "deliberate failure" in (rec.message or "")

    def test_minted_run_id(self, fake_pool, runstore_db):
        wf = _wf(steps=[{"id": "s1", "server": "fake_server", "tool": "echo", "args": {"text": "x"}}])
        execution = run(
            execute_workflow(wf, {"greeting": "x"}, fake_pool, runstore_db=str(runstore_db))
        )
        assert execution.run_id.startswith("wf_")
