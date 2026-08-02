"""Workflow template management tools.

``create_workflow``, ``get_workflow``, ``list_workflows`` — no runstore needed.
"""

from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context

from workflow_runner.models import AppContext
from workflow_runner.templates import (
    WorkflowError,
    create_workflow as _create_workflow,
    list_workflows as _list_workflows,
    load_workflow,
    workflow_to_dict,
)


def _summary(spec) -> dict:
    return {
        "workflow_id": spec.workflow_id,
        "version": spec.version,
        "name": spec.name,
        "step_count": spec.step_count,
        "source_prompt": spec.source_prompt,
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def create_workflow(
        ctx: Context[AppContext], workflow_json: str, overwrite: bool = False
    ) -> str:
        """Create a workflow template from a JSON document.

        Args:
            workflow_json: Full workflow template JSON (schema_version 1).
            overwrite: Overwrite an existing template with the same workflow_id.

        Returns:
            JSON confirmation of the created workflow.
        """
        app: AppContext = ctx.request_context.lifespan_context
        try:
            spec = _create_workflow(workflow_json, overwrite=overwrite, workflow_dir=app.workflow_dir)
        except WorkflowError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        return json.dumps(
            {
                "success": True,
                **{k: v for k, v in _summary(spec).items() if k != "step_count"},
                "step_count": spec.step_count,
                "path": f"{spec.workflow_id}.json",
            }
        )

    @mcp.tool()
    def get_workflow(ctx: Context[AppContext], workflow_id: str) -> str:
        """Get a workflow template by id.

        Args:
            workflow_id: Workflow template id.

        Returns:
            JSON object with the full workflow template.
        """
        app: AppContext = ctx.request_context.lifespan_context
        try:
            spec = load_workflow(workflow_id, workflow_dir=app.workflow_dir)
        except WorkflowError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        return json.dumps({"success": True, "workflow": workflow_to_dict(spec)})

    @mcp.tool()
    def list_workflows(ctx: Context[AppContext]) -> str:
        """List available workflow templates.

        Returns:
            JSON array of ``{"workflow_id", "version", "name", "step_count",
            "source_prompt"}``.
        """
        app: AppContext = ctx.request_context.lifespan_context
        return json.dumps([_summary(s) for s in _list_workflows(workflow_dir=app.workflow_dir)])
