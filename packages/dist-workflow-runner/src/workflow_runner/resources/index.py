"""Static runner index resources.

``workflow-runner://workflows`` and ``workflow-runner://servers``. Static
resources take no params in mcp 2.0, so the lifespan AppContext is reached via
a module-level reference set during the lifespan (the same pattern as
``shift.mcp_server.resources.docs``).
"""

from __future__ import annotations

import json

from mcp.server import MCPServer

from workflow_runner.models import AppContext

_app_ctx: AppContext | None = None


def set_app_context(ctx: AppContext) -> None:
    global _app_ctx
    _app_ctx = ctx


def get_app_context() -> AppContext | None:
    """The lifespan AppContext, or None before/after a session."""
    return _app_ctx


def register(mcp: MCPServer) -> None:
    @mcp.resource("workflow-runner://workflows")
    def list_workflows_resource() -> str:
        """List available workflow templates."""
        from workflow_runner.templates import list_workflows

        app = _app_ctx
        if app is None:
            return json.dumps({"workflows": [], "count": 0})
        specs = list_workflows(workflow_dir=app.workflow_dir)
        return json.dumps(
            {
                "workflows": [
                    {
                        "workflow_id": s.workflow_id,
                        "version": s.version,
                        "name": s.name,
                        "step_count": s.step_count,
                        "source_prompt": s.source_prompt,
                    }
                    for s in specs
                ],
                "count": len(specs),
            }
        )

    @mcp.resource("workflow-runner://servers")
    def list_servers_resource() -> str:
        """List configured domain servers."""
        app = _app_ctx
        if app is None:
            return json.dumps({"servers": [], "count": 0})
        servers = [
            {
                "name": s.name,
                "command": s.command,
                "args": s.args,
                "cwd": s.cwd,
                "env": s.env,
                "timeout_s": s.timeout_s,
            }
            for s in app.config.servers
        ]
        return json.dumps({"servers": servers, "count": len(servers)})
