"""Workflow template loading/validation tests."""

from __future__ import annotations

import json

import pytest

from workflow_runner.templates import (
    WorkflowError,
    create_workflow,
    list_workflows,
    load_workflow,
    resolve_workflow_dir,
    validate_workflow,
)


VALID = {
    "schema_version": 1,
    "workflow_id": "demo",
    "version": "1.0.0",
    "name": "Demo",
    "description": "A demo workflow.",
    "source_prompt": "gdm-flow://run_ac_pf@1",
    "inputs": [{"name": "path", "type": "string", "required": True}],
    "steps": [
        {
            "id": "step_1",
            "server": "gdm",
            "tool": "get_system_summary",
            "args": {"system_path": "${path}"},
            "capture": "summary",
            "on_failure": "fail",
        }
    ],
    "outputs": [{"name": "out", "from": "summary"}],
}


class TestValidate:
    def test_valid(self):
        spec = validate_workflow(VALID)
        assert spec.workflow_id == "demo"
        assert spec.step_count == 1
        assert spec.steps[0].capture == "summary"

    def test_schema_version(self):
        with pytest.raises(WorkflowError, match="schema_version"):
            validate_workflow({**VALID, "schema_version": 2})
        with pytest.raises(WorkflowError, match="schema_version"):
            validate_workflow({**VALID, "schema_version": "x"})

    def test_missing_steps(self):
        with pytest.raises(WorkflowError, match="steps"):
            validate_workflow({k: v for k, v in VALID.items() if k != "steps"})

    def test_empty_steps(self):
        with pytest.raises(WorkflowError, match="steps"):
            validate_workflow({**VALID, "steps": []})

    def test_step_missing_id(self):
        bad = json.loads(json.dumps(VALID))
        del bad["steps"][0]["id"]
        with pytest.raises(WorkflowError, match="'id'"):
            validate_workflow(bad)

    def test_duplicate_step_ids(self):
        bad = json.loads(json.dumps(VALID))
        bad["steps"].append(bad["steps"][0])
        with pytest.raises(WorkflowError, match="duplicate step id"):
            validate_workflow(bad)

    def test_bad_on_failure(self):
        with pytest.raises(WorkflowError, match="on_failure"):
            validate_workflow(
                {**VALID, "steps": [{**VALID["steps"][0], "on_failure": "retry"}]}
            )

    def test_missing_server(self):
        with pytest.raises(WorkflowError, match="'server'"):
            validate_workflow(
                {**VALID, "steps": [{**VALID["steps"][0], "server": ""}]}
            )

    def test_bad_workflow_id(self):
        with pytest.raises(WorkflowError, match="workflow_id"):
            validate_workflow({**VALID, "workflow_id": "../evil"})

    def test_non_object(self):
        with pytest.raises(WorkflowError, match="JSON object"):
            validate_workflow([1, 2, 3])


class TestFiles:
    def test_load_checked_in_templates(self):
        """The two checked-in templates must load and validate."""
        for wf_id in ("run_ac_pf_workflow", "feasibility_study"):
            spec = load_workflow(wf_id)
            assert spec.workflow_id == wf_id
            assert spec.version == "1.0.0"
            assert spec.step_count >= 1

    def test_list_workflows(self):
        ids = {s.workflow_id for s in list_workflows()}
        assert {"run_ac_pf_workflow", "feasibility_study"} <= ids

    def test_load_missing(self, workflow_dir):
        with pytest.raises(WorkflowError, match="no workflow named"):
            load_workflow("nope", workflow_dir=str(workflow_dir))

    def test_custom_workflow_dir(self, workflow_dir):
        (workflow_dir / "custom.json").write_text(json.dumps(VALID), encoding="utf-8")
        spec = load_workflow("custom", workflow_dir=str(workflow_dir))
        assert spec.workflow_id == "demo"


class TestCreate:
    def test_create_writes_file(self, workflow_dir):
        spec = create_workflow(json.dumps(VALID), workflow_dir=str(workflow_dir))
        assert spec.workflow_id == "demo"
        path = workflow_dir / "demo.json"
        assert path.is_file()
        assert json.loads(path.read_text())["name"] == "Demo"

    def test_create_no_overwrite(self, workflow_dir):
        create_workflow(json.dumps(VALID), workflow_dir=str(workflow_dir))
        with pytest.raises(WorkflowError, match="already exists"):
            create_workflow(json.dumps(VALID), workflow_dir=str(workflow_dir))

    def test_create_overwrite(self, workflow_dir):
        create_workflow(json.dumps(VALID), workflow_dir=str(workflow_dir))
        changed = {**VALID, "name": "Renamed"}
        spec = create_workflow(
            json.dumps(changed), overwrite=True, workflow_dir=str(workflow_dir)
        )
        assert spec.name == "Renamed"

    def test_create_invalid_json(self, workflow_dir):
        with pytest.raises(WorkflowError, match="not valid JSON"):
            create_workflow("{nope", workflow_dir=str(workflow_dir))


def test_resolve_workflow_dir_default():
    assert resolve_workflow_dir() is not None
