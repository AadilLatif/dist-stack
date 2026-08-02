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


class TestOnStepHook:
    """Spec 17 §2.1: per-step sync event hook (on_step)."""

    def test_succeeded_run_calls_hook_once_per_step(self, fake_pool):
        seen = []
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "echo", "args": {"text": "${greeting}"}, "capture": "echoed", "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "again"}, "on_failure": "fail"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "hi"}, fake_pool, on_step=seen.append))
        assert execution.status == "succeeded"
        assert len(seen) == 2
        assert [s.step_id for s in seen] == ["s1", "s2"]
        assert [s.status for s in seen] == ["succeeded", "succeeded"]
        assert seen[0].result == {"success": True, "text": "hi"}
        assert seen[1].result == {"success": True, "text": "again"}
        # the hook receives exactly the StepResult objects persisted on the execution
        assert seen == execution.steps

    def test_failed_run_calls_hook(self, fake_pool):
        seen = []
        wf = _wf(steps=[{"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "fail"}])
        execution = run(execute_workflow(wf, {"greeting": "x"}, fake_pool, on_step=seen.append))
        assert execution.status == "failed"
        assert len(seen) == 1
        assert seen[0].step_id == "s1"
        assert seen[0].status == "failed"
        assert "deliberate failure" in (seen[0].error or "")

    def test_skipped_steps_call_hook(self, fake_pool):
        """Aborted/skipped steps also emit an on_step event."""
        seen = []
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "never"}, "on_failure": "fail"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, fake_pool, on_step=seen.append))
        assert execution.status == "failed"
        assert len(seen) == 2
        assert [s.status for s in seen] == ["failed", "skipped"]
        assert seen[1].error == "workflow aborted by prior step failure"
        assert seen == execution.steps

    def test_hook_not_called_when_omitted(self, fake_pool):
        """Default None is a no-op path — unchanged behavior for existing callers."""
        wf = _wf(steps=[{"id": "s1", "server": "fake_server", "tool": "echo", "args": {"text": "${greeting}"}}])
        execution = run(execute_workflow(wf, {"greeting": "hi"}, fake_pool))
        assert execution.status == "succeeded"
        assert execution.steps[0].status == "succeeded"


class TestCancellation:
    """Spec 17 §2.2: cancel_event honored at step boundaries."""

    @staticmethod
    def _cancel_pool():
        """A FakePool whose set_cancel tool raises ``cancel`` as a side effect."""
        from conftest import FakePool

        cancel = asyncio.Event()
        pool = FakePool()
        pool.add_server(
            "fake_server",
            {
                "set_cancel": lambda text: (cancel.set(), {"success": True, "text": text})[1],
                "echo": lambda text: {"success": True, "text": text},
            },
        )
        return pool, cancel

    def test_cancel_before_step_2_skips_remaining(self):
        """Flag set mid-step-1 (in-flight, best-effort) → steps 2..N skipped."""
        pool, cancel = self._cancel_pool()
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "set_cancel", "args": {"text": "go"}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "two"}, "on_failure": "fail"},
                {"id": "s3", "server": "fake_server", "tool": "echo", "args": {"text": "three"}, "on_failure": "fail"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, pool, cancel_event=cancel))
        assert execution.status == "cancelled"
        assert [s.status for s in execution.steps] == ["succeeded", "skipped", "skipped"]
        # in-flight step 1 ran to completion (not interrupted)…
        assert execution.steps[0].result == {"success": True, "text": "go"}
        # …and the flag was honored at the next step boundary.
        assert execution.steps[1].error == "run cancelled by user"
        assert execution.steps[2].error == "run cancelled by user"
        assert execution.steps[1].args_resolved == {}
        assert execution.steps[2].args_resolved == {}

    def test_cancel_set_before_first_step_skips_all(self):
        pool, cancel = self._cancel_pool()
        cancel.set()
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "echo", "args": {"text": "one"}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "two"}, "on_failure": "fail"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, pool, cancel_event=cancel))
        assert execution.status == "cancelled"
        assert [s.status for s in execution.steps] == ["skipped", "skipped"]
        assert all(s.error == "run cancelled by user" for s in execution.steps)

    def test_cancelled_takes_precedence_over_failed(self):
        """A prior step failure does not downgrade a user cancellation."""
        from conftest import FakePool

        cancel = asyncio.Event()
        pool = FakePool()
        pool.add_server(
            "fake_server",
            {
                "set_cancel": lambda text: (cancel.set(), {"success": True, "text": text})[1],
                "fail_on_demand": lambda note="deliberate failure": {"success": False, "error": note},
                "echo": lambda text: {"success": True, "text": text},
            },
        )
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "fail_on_demand", "args": {}, "on_failure": "continue"},
                {"id": "s2", "server": "fake_server", "tool": "set_cancel", "args": {"text": "go"}, "on_failure": "continue"},
                {"id": "s3", "server": "fake_server", "tool": "echo", "args": {"text": "three"}, "on_failure": "continue"},
            ]
        )
        execution = run(execute_workflow(wf, {"greeting": "x"}, pool, cancel_event=cancel))
        assert [s.status for s in execution.steps] == ["failed", "succeeded", "skipped"]
        assert execution.status == "cancelled"  # cancelled wins when the flag is set

    def test_cancel_never_set_status_succeeded(self, fake_pool):
        cancel = asyncio.Event()
        wf = _wf(steps=[{"id": "s1", "server": "fake_server", "tool": "echo", "args": {"text": "${greeting}"}}])
        execution = run(execute_workflow(wf, {"greeting": "hi"}, fake_pool, cancel_event=cancel))
        assert execution.status == "succeeded"
        assert execution.steps[0].status == "succeeded"

    def test_cancel_runstore_finalized(self, runstore_db):
        """A cancelled run is a recorded partial execution: row + artifact."""
        pool, cancel = self._cancel_pool()
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "set_cancel", "args": {"text": "go"}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "two"}, "on_failure": "fail"},
            ],
            workflow_id="cancelled",
        )
        execution = run(
            execute_workflow(
                wf,
                {"greeting": "x"},
                pool,
                runstore_db=str(runstore_db),
                run_id="wf_cancel000000001",
                tool_version="0.1.0",
                cancel_event=cancel,
            )
        )
        assert execution.status == "cancelled"
        assert execution.run_id == "wf_cancel000000001"

        rec = get_run("wf_cancel000000001", runstore_db=str(runstore_db))
        assert rec.status == "cancelled"
        assert rec.message == "cancelled by user"
        assert rec.payload["status"] == "cancelled"

        artifacts = list_artifacts("wf_cancel000000001", runstore_db=str(runstore_db))
        assert len(artifacts) == 1
        artifact = Path(artifacts[0].artifact_path)
        assert artifact.name == "wf_cancel000000001.execution.json"
        assert artifact.is_file()
        graph = json.loads(artifact.read_text())
        assert graph["status"] == "cancelled"
        assert [s["status"] for s in graph["steps"]] == ["succeeded", "skipped"]
        assert Path(str(artifact) + ".manifest.json").is_file()

    def test_external_cancel_finalizes_runstore_and_reraises(self, runstore_db):
        """task.cancel() mid-loop → row finalized as cancelled, exception re-raised."""
        from conftest import FakePool

        pool = FakePool(default_timeout=300.0)
        pool.add_server(
            "fake_server",
            {
                "slow": lambda: asyncio.sleep(5),
                "echo": lambda text: {"success": True, "text": text},
            },
        )
        wf = _wf(
            steps=[
                {"id": "s1", "server": "fake_server", "tool": "slow", "args": {}, "on_failure": "fail"},
                {"id": "s2", "server": "fake_server", "tool": "echo", "args": {"text": "never"}, "on_failure": "fail"},
            ],
            workflow_id="ext_cancel",
        )

        async def run_and_cancel():
            task = asyncio.create_task(
                execute_workflow(
                    wf,
                    {"greeting": "x"},
                    pool,
                    runstore_db=str(runstore_db),
                    run_id="wf_extcancel00001",
                    tool_version="0.1.0",
                )
            )
            await asyncio.sleep(0.05)  # let step 1 enter its in-flight slow call
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        run(run_and_cancel())

        rec = get_run("wf_extcancel00001", runstore_db=str(runstore_db))
        assert rec.status == "cancelled"
        assert rec.message == "cancelled by user"
        artifacts = list_artifacts("wf_extcancel00001", runstore_db=str(runstore_db))
        assert len(artifacts) == 1
        graph = json.loads(Path(artifacts[0].artifact_path).read_text())
        assert graph["status"] == "cancelled"
