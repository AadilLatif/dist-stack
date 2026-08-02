"""Workflow template loading, listing, validation, and persistence."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import WorkflowSpec, WorkflowStep

_WORKFLOW_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_CAPTURE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class WorkflowError(ValueError):
    """Raised for invalid or missing workflow templates."""


def default_workflow_dir() -> Path:
    """The packaged ``workflows/`` directory shipped with this repo."""
    candidate = Path(__file__).resolve().parent.parent.parent / "workflows"
    if candidate.is_dir():
        return candidate
    return Path.cwd() / "workflows"


def resolve_workflow_dir(workflow_dir: str | None = None) -> Path:
    if workflow_dir:
        return Path(workflow_dir).expanduser()
    return default_workflow_dir()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_workflow(data) -> WorkflowSpec:
    """Validate a raw workflow dict against the ``WorkflowSpec`` model.

    Raises :class:`WorkflowError` on any structural violation.
    """
    if not isinstance(data, dict):
        raise WorkflowError("workflow must be a JSON object")

    try:
        schema_version = int(data.get("schema_version"))
    except (TypeError, ValueError):
        raise WorkflowError("'schema_version' must be an integer") from None
    if schema_version != 1:
        raise WorkflowError(f"unsupported schema_version {schema_version!r} (expected 1)")

    workflow_id = data.get("workflow_id")
    if (
        not isinstance(workflow_id, str)
        or not workflow_id.strip()
        or not _WORKFLOW_ID_RE.fullmatch(workflow_id)
    ):
        raise WorkflowError(
            "'workflow_id' must be a non-empty slug (letters/digits/_ . -)"
        )

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise WorkflowError("'version' must be a non-empty string")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("'name' must be a non-empty string")

    description = data.get("description") or ""
    if not isinstance(description, str):
        raise WorkflowError("'description' must be a string")

    source_prompt = data.get("source_prompt")
    if source_prompt is not None and not isinstance(source_prompt, str):
        raise WorkflowError("'source_prompt' must be a string")

    inputs = data.get("inputs", [])
    if not isinstance(inputs, list):
        raise WorkflowError("'inputs' must be a list")
    for idx, inp in enumerate(inputs):
        if not isinstance(inp, dict) or not isinstance(inp.get("name"), str) or not inp["name"]:
            raise WorkflowError(f"inputs[{idx}]: each input must be an object with a non-empty 'name'")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError("'steps' must be a non-empty list")

    seen_ids: set[str] = set()
    steps = []
    for idx, raw in enumerate(raw_steps):
        steps.append(_validate_step(raw, idx, seen_ids))

    outputs = data.get("outputs", [])
    if not isinstance(outputs, list):
        raise WorkflowError("'outputs' must be a list")
    for idx, out in enumerate(outputs):
        if not isinstance(out, dict) or not isinstance(out.get("name"), str) or not out["name"]:
            raise WorkflowError(f"outputs[{idx}]: each output must be an object with a non-empty 'name'")

    return WorkflowSpec(
        schema_version=1,
        workflow_id=workflow_id,
        version=version,
        name=name,
        description=description,
        source_prompt=source_prompt,
        inputs=inputs,
        steps=steps,
        outputs=outputs,
    )


def _validate_step(raw, idx: int, seen_ids: set[str]) -> WorkflowStep:
    if not isinstance(raw, dict):
        raise WorkflowError(f"steps[{idx}]: step must be an object")
    step_id = raw.get("id")
    if not isinstance(step_id, str) or not step_id.strip():
        raise WorkflowError(f"steps[{idx}]: 'id' must be a non-empty string")
    if step_id in seen_ids:
        raise WorkflowError(f"duplicate step id {step_id!r}")
    seen_ids.add(step_id)

    server = raw.get("server")
    tool = raw.get("tool")
    if not isinstance(server, str) or not server.strip():
        raise WorkflowError(f"step {step_id!r}: 'server' must be a non-empty string")
    if not isinstance(tool, str) or not tool.strip():
        raise WorkflowError(f"step {step_id!r}: 'tool' must be a non-empty string")

    args = raw.get("args", {})
    if not isinstance(args, dict):
        raise WorkflowError(f"step {step_id!r}: 'args' must be an object")

    capture = raw.get("capture")
    if capture is not None and (not isinstance(capture, str) or not _CAPTURE_RE.fullmatch(capture)):
        raise WorkflowError(f"step {step_id!r}: 'capture' must be a valid variable name")

    on_failure = raw.get("on_failure", "fail")
    if on_failure not in ("fail", "continue"):
        raise WorkflowError(f"step {step_id!r}: 'on_failure' must be 'fail' or 'continue'")

    return WorkflowStep(id=step_id, server=server, tool=tool, args=args, capture=capture, on_failure=on_failure)


# ---------------------------------------------------------------------------
# File IO
# ---------------------------------------------------------------------------


def _read_workflow_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"cannot read workflow file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"invalid JSON in workflow file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError(f"workflow file {path} must contain a JSON object")
    return data


def load_workflow(workflow_id: str, *, workflow_dir: str | None = None) -> WorkflowSpec:
    """Load a workflow template by id from the workflow directory."""
    wdir = resolve_workflow_dir(workflow_dir)
    path = wdir / f"{workflow_id}.json"
    if not path.is_file():
        raise WorkflowError(f"no workflow named {workflow_id!r} in {wdir}")
    return validate_workflow(_read_workflow_file(path))


def list_workflows(*, workflow_dir: str | None = None) -> list[WorkflowSpec]:
    """List valid workflow templates, sorted by workflow_id."""
    wdir = resolve_workflow_dir(workflow_dir)
    if not wdir.is_dir():
        return []
    specs = []
    for path in sorted(wdir.glob("*.json")):
        try:
            specs.append(validate_workflow(_read_workflow_file(path)))
        except WorkflowError:
            continue  # skip malformed templates
    return specs


def create_workflow(
    workflow_json: str | dict,
    *,
    overwrite: bool = False,
    workflow_dir: str | None = None,
) -> WorkflowSpec:
    """Validate and write a workflow template to ``<workflow_dir>/<workflow_id>.json``."""
    if isinstance(workflow_json, str):
        try:
            data = json.loads(workflow_json)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"workflow_json is not valid JSON: {exc}") from exc
    else:
        data = workflow_json

    spec = validate_workflow(data)

    wdir = resolve_workflow_dir(workflow_dir)
    wdir.mkdir(parents=True, exist_ok=True)
    path = wdir / f"{spec.workflow_id}.json"
    if path.exists() and not overwrite:
        raise WorkflowError(
            f"workflow {spec.workflow_id!r} already exists (pass overwrite=True to replace it)"
        )
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return spec


def workflow_to_dict(spec: WorkflowSpec) -> dict:
    """The full template as a JSON-friendly dict (for ``get_workflow``)."""
    return {
        "schema_version": spec.schema_version,
        "workflow_id": spec.workflow_id,
        "version": spec.version,
        "name": spec.name,
        "description": spec.description,
        "source_prompt": spec.source_prompt,
        "inputs": spec.inputs,
        "steps": [
            {
                "id": s.id,
                "server": s.server,
                "tool": s.tool,
                "args": s.args,
                "capture": s.capture,
                "on_failure": s.on_failure,
            }
            for s in spec.steps
        ],
        "outputs": spec.outputs,
    }
