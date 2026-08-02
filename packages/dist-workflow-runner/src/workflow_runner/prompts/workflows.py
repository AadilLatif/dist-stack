"""Prompt templates for the workflow-runner.

``describe_workflow(workflow_id)`` renders a workflow template as guidance
text for the LLM.

mcp 2.0 renders prompt functions in a worker thread without a request context,
so the lifespan AppContext is reached through the module-level reference
maintained by ``workflow_runner.resources.index``.
"""

from __future__ import annotations

from mcp.server import MCPServer

from workflow_runner.resources.index import get_app_context
from workflow_runner.templates import WorkflowError, load_workflow


def register(mcp: MCPServer) -> None:
    @mcp.prompt()
    def describe_workflow(workflow_id: str) -> str:
        """Describe a workflow template for the LLM.

        Args:
            workflow_id: Workflow template id.
        """
        app = get_app_context()
        try:
            wf = load_workflow(workflow_id, workflow_dir=app.workflow_dir if app else None)
        except WorkflowError as exc:
            return f"Workflow {workflow_id!r} is not available: {exc}"

        steps = "\n".join(
            f"  {i}. {s.id}: call `{s.tool}` on server `{s.server}` "
            f"(on_failure={s.on_failure})"
            for i, s in enumerate(wf.steps, start=1)
        )
        inputs = ", ".join(i.get("name", "?") for i in wf.inputs) if wf.inputs else "none"
        outputs = (
            ", ".join(o.get("name", "?") for o in wf.outputs) if wf.outputs else "none"
        )
        return (
            f"Workflow: {wf.name} (id={wf.workflow_id}, version={wf.version})\n"
            f"Description: {wf.description or 'n/a'}\n"
            f"Source prompt: {wf.source_prompt or 'n/a'}\n"
            f"Inputs: {inputs}\n"
            f"Steps ({wf.step_count}):\n{steps}\n"
            f"Outputs: {outputs}\n\n"
            f"Run it with `run_workflow(workflow_id={wf.workflow_id!r}, inputs={{...}})`."
        )
